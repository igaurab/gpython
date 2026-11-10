"""Python environments: find them, create them, install packages into them.

Finding is filesystem-only (no subprocess): venvs are recognised by their `pyvenv.cfg`,
which also holds the Python version, and uv's managed interpreters by their folder name.
That keeps the env picker instant. Creating envs and installing packages run `uv`
(or `python -m venv` / `pip` when asked or when uv is missing) and stream their output.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

LineCallback = Callable[[str], None]

BIN = "Scripts" if os.name == "nt" else "bin"
EXE = "python.exe" if os.name == "nt" else "python"
VENV_NAMES = (".venv", "venv", "env", ".env")
_VERSION = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class Env:
    """One interpreter a kernel can run in.

    `python` is kept unresolved: a venv's bin/python is a symlink to the base
    interpreter, and resolving it would lose the venv.
    """

    python: Path
    name: str
    kind: str
    version: str = ""
    prefix: Path | None = None
    project: Path | None = None
    uv_args: tuple[str, ...] = ()

    @property
    def is_venv(self) -> bool:
        return self.kind in ("uv", "venv", "conda", "builtin", "uv-project") and self.prefix is not None

    @property
    def has_ipykernel(self) -> bool | None:
        """True/False for envs we can look inside cheaply; None when only running it would tell.
        A uv project gets ipykernel from uv's cache (`uv run --with ipykernel`), never in its venv."""
        if self.kind in ("builtin", "uv-project"):
            return True
        if self.prefix is None:
            return None
        lib = self.prefix / "Lib" if os.name == "nt" else self.prefix / "lib"
        return any(lib.glob("site-packages/ipykernel" if os.name == "nt" else "python*/site-packages/ipykernel"))

    @property
    def short(self) -> str:
        """For the header chip: 'Python 3.13 · .venv'."""
        version = ".".join(self.version.split(".")[:2])
        py = f"Python {version}" if version else "Python"
        return py if self.kind in ("system", "uv-python") else f"{py} · {self.name}"

    def same(self, other: Env | None) -> bool:
        return other is not None and _key(self) == _key(other)


def _key(env: Env) -> str:
    where = str(env.prefix) if env.prefix is not None else os.path.realpath(env.python)
    return f"{where} uv {' '.join(env.uv_args)}" if env.kind == "uv-project" else where


@cache
def uv() -> str | None:
    """Path to the uv binary, or None."""
    return shutil.which("uv")


def _read_cfg(path: Path) -> dict[str, str]:
    out = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            k, sep, v = line.partition("=")
            if sep:
                out[k.strip().lower()] = v.strip()
    except OSError:
        pass
    return out


def venv_at(folder: Path, name: str | None = None) -> Env | None:
    """The venv in `folder`, or None if it is not one (no pyvenv.cfg or no interpreter)."""
    cfg = folder / "pyvenv.cfg"
    python = folder / BIN / EXE
    if not cfg.is_file():
        conda = folder / "conda-meta"
        if conda.is_dir() and python.exists():
            return Env(python, name or folder.name, "conda", _version_of(python), folder)
        return None
    if not python.exists():
        return None
    data = _read_cfg(cfg)
    version = data.get("version_info") or data.get("version") or ""
    kind = "uv" if "uv" in data else "venv"
    return Env(python, name or folder.name, kind, version, folder)


def _version_of(python: Path) -> str:
    """Best guess from file names: python3.12 → 3.12, or the resolved Homebrew/uv path."""
    for candidate in (python.name, os.path.realpath(python)):
        if m := _VERSION.search(Path(candidate).name.removeprefix("python")) or _VERSION.search(str(candidate)):
            return m.group(1)
    return ""


def project_root(start: Path) -> Path:
    """Nearest folder at or above `start` with pyproject.toml, uv.lock or .git; `start` if none."""
    start = start.resolve()
    home = Path.home()
    for folder in (start, *start.parents):
        if any((folder / m).exists() for m in ("pyproject.toml", "uv.lock", ".git")):
            return folder
        if folder == home:
            break
    return start


def _children(folder: Path):
    try:
        return [p for p in folder.iterdir() if p.is_dir()]
    except OSError:
        return []


def builtin() -> Env:
    """The interpreter gpython itself runs in (always has ipykernel)."""
    prefix = Path(sys.prefix) if sys.prefix != sys.base_prefix else None
    v = sys.version_info
    return Env(Path(sys.executable), "built-in", "builtin", f"{v.major}.{v.minor}.{v.micro}", prefix)


def discover(start: Path | None = None) -> list[Env]:
    """Every environment we can find, the most likely first. Filesystem only, no subprocess."""
    start = (start or Path.cwd()).resolve()
    found: list[Env] = []
    seen: set[str] = set()

    def add(env: Env | None) -> None:
        if env is not None and (k := _key(env)) not in seen:
            seen.add(k)
            found.append(env)

    for env in project_venvs(start):
        add(env)
    add(builtin())
    for var in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        if p := os.environ.get(var):
            add(venv_at(Path(p)))
    home = Path.home()
    homes = [os.environ.get("WORKON_HOME"), home / ".virtualenvs", home / ".venvs", home / ".local/share/virtualenvs"]
    homes += [home / d / "envs" for d in ("miniconda3", "anaconda3", "miniforge3", "mambaforge", ".conda")]
    for base in filter(None, homes):
        for child in sorted(_children(Path(base))):
            add(venv_at(child))
    uv_dir = Path(os.environ.get("UV_PYTHON_INSTALL_DIR") or home / ".local/share/uv/python")
    for child in sorted(_children(uv_dir), reverse=True):
        python = child / BIN / "python3"
        if python.exists() and (m := _VERSION.search(child.name)):
            add(Env(python, "-".join(child.name.split("-")[:2]), "uv-python", m.group(1)))
    for env in _path_pythons():
        add(env)
    return found


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root.parent))
    except ValueError:
        return path.name


def _path_pythons() -> list[Env]:
    out = []
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder or "/.venv/" in folder + "/" or (Path(folder).parent / "pyvenv.cfg").exists():
            continue
        try:
            names = sorted(n for n in os.listdir(folder) if re.fullmatch(r"python3(\.\d+)?", n))
        except OSError:
            continue
        for n in names:
            python = Path(folder) / n
            if os.access(python, os.X_OK):
                out.append(Env(python, python.name, "system", _version_of(python)))
    return out


def from_python(python: Path, envs: list[Env] | None = None) -> Env:
    """The Env for an interpreter path (e.g. remembered in config, or typed by the user)."""
    python = Path(python).expanduser()
    if python.is_dir():
        if env := venv_at(python):
            return env
        python = python / BIN / EXE
    for env in envs or ():
        if env.python == python:
            return env
    if python.parent.name == BIN and (env := venv_at(python.parent.parent)):
        return env
    if python == Path(sys.executable):
        return builtin()
    return Env(python, python.name, "system", _version_of(python))


def project_venvs(start: Path) -> list[Env]:
    """Venvs next to `start` and in each folder up to the project root, nearest first (a few stat calls)."""
    start = start.resolve()
    root = project_root(start)
    out = []
    for folder in (start, *start.parents):
        out += filter(None, (venv_at(folder / n, _rel(folder / n, root)) for n in VENV_NAMES))
        if folder == root or not folder.is_relative_to(root):
            break
    return out


launch_args: tuple[str, ...] = ()
"""`uv run` flags given on gpython's command line (`--extra`, `--with`, ...), for this launch."""


def is_uv_project(root: Path) -> bool:
    return uv() is not None and (root / "pyproject.toml").is_file()


def project_env_dir(root: Path) -> Path:
    """Where uv keeps the project's environment: `UV_PROJECT_ENVIRONMENT`, else `<root>/.venv`."""
    custom = os.environ.get("UV_PROJECT_ENVIRONMENT")
    return (root / custom).resolve() if custom else root / ".venv"


def uv_project(root: Path, uv_args: tuple[str, ...] = ()) -> Env:
    """The project's own environment, run through `uv run --with ipykernel`: uv syncs what the
    project declares (removing nothing) and lays ipykernel over it from its cache."""
    prefix = project_env_dir(root)
    venv = venv_at(prefix)
    version = venv.version if venv is not None else ""
    return Env(prefix / BIN / EXE, f"{_rel(prefix, root)} (uv)", "uv-project", version, prefix, root, tuple(uv_args))


def saved_uv_args(root: Path) -> tuple[str, ...]:
    """The extras and groups synced into this project from gpython (`:sync`), as uv flags; later
    launches pass them so a plain `uv sync` that dropped them is undone."""
    from . import config

    saved = config.load().get(f"uv:{root}")
    return tuple(saved.split()) if isinstance(saved, str) else ()


def remember_sync(root: Path, flags: list[str] | tuple[str, ...]) -> None:
    from . import config

    kept = [*saved_uv_args(root), *(f for f in flags if f.startswith(("--extra=", "--group=")))]
    config.save({f"uv:{root}": " ".join(dict.fromkeys(kept))})


def sync_flags(where: tuple[str, str]) -> list[str]:
    """`packages.declared`'s answer as `uv sync` flags: [] for the main dependencies."""
    kind, name = where
    return [f"--{kind}={name}"] if kind in ("extra", "group") else []


def sync_argv(root: Path, flags: list[str] | tuple[str, ...]) -> list[str]:
    """Install what the project declares, removing nothing (`--inexact`)."""
    return [uv() or "uv", "sync", "--inexact", "--project", str(root), *flags]


async def sync(env: Env, root: Path, flags: list[str], on_line: LineCallback | None = None) -> None:
    await _check(sync_argv(root, flags), on_line, cwd=root, env=kernel_env(env))
    remember_sync(root, flags)


def runnable(env: Env, start: Path, uv_args: tuple[str, ...] = ()) -> Env:
    """`env` itself, or the uv project around `start` when env is that project's venv without
    ipykernel: the kernel then runs there without installing anything into it."""
    if env.has_ipykernel is not False or env.prefix is None:
        return env
    root = project_root(start)
    if is_uv_project(root) and env.prefix == project_env_dir(root):
        return uv_project(root, saved_uv_args(root) + tuple(uv_args))
    return env


def default_for(file: Path | None, remembered: str | None = None) -> tuple[Env, Env | None]:
    """The env a notebook starts in, and a project venv worth suggesting (it lacks ipykernel).

    Order: uv flags on the command line (the uv project, whatever else is there), the env
    last chosen for this notebook, the nearest project venv that has ipykernel (like VS Code
    picks .venv), the uv project through `uv run --with ipykernel` once its environment exists
    (a project never synced is suggested, not built), then gpython's own interpreter.
    Nothing is ever installed into a project to pick it.
    """
    start = file.resolve().parent if file else Path.cwd()
    root = project_root(start)
    uv_ok = is_uv_project(root)
    if launch_args and uv_ok:
        return uv_project(root, saved_uv_args(root) + launch_args), None
    if remembered and Path(remembered).exists():
        return runnable(from_python(Path(remembered)), start), None
    near = project_venvs(start)
    for env in near:
        if env.has_ipykernel:
            return env, None
    if uv_ok and project_env_dir(root).is_dir():
        return uv_project(root, saved_uv_args(root)), None
    if uv_ok:
        return builtin(), uv_project(root)
    return builtin(), (near[0] if near else None)


def launch_log(env: Env) -> Path | None:
    """For a uv project, a fresh file for uv's output: it is what explains a kernel that did not start."""
    if env.kind != "uv-project":
        return None
    import secrets
    import tempfile

    return Path(tempfile.gettempdir()) / f"gpython-uv-{os.getpid()}-{secrets.token_hex(4)}.log"


def read_log(log: Path | None) -> list[str]:
    """uv's complaint from a launch log, as `explain` sentences; the file is removed."""
    if log is None:
        return []
    try:
        lines = log.read_text(errors="replace").splitlines()
        log.unlink()
    except OSError:
        return []
    return explain(lines)


def fallback(env: Env) -> Env | None:
    """The next way to start a uv project whose start failed, or None when there is none left:
    first without syncing and from uv's cache only (a sync usually fails because the network
    is down), then without syncing but online (ipykernel was not in the cache)."""
    from dataclasses import replace

    if env.kind != "uv-project":
        return None
    if "--no-sync" not in env.uv_args:
        return replace(env, uv_args=(*env.uv_args, "--no-sync", "--offline"))
    if "--offline" in env.uv_args:
        return replace(env, uv_args=tuple(a for a in env.uv_args if a != "--offline"))
    return None


def kernel_argv(env: Env, connection_file: str) -> list[str]:
    """The command that starts ipykernel in env."""
    launcher = ["-m", "ipykernel_launcher", "-f", connection_file]
    if env.kind == "uv-project" and env.project is not None and uv():
        return [uv(), "run", "--project", str(env.project), *env.uv_args, "--with", "ipykernel", "python", *launcher]
    return [str(env.python), *launcher]


def kernel_env(env: Env) -> dict[str, str]:
    """Environment variables for a kernel running in `env`, so `!pip`, `uv` and subprocesses match it."""
    out = dict(os.environ)
    out.pop("PYTHONHOME", None)
    if (own := os.environ.get("VIRTUAL_ENV")) and Path(own) != env.prefix:
        out.pop("VIRTUAL_ENV", None)
        out["PATH"] = os.pathsep.join(p for p in out.get("PATH", "").split(os.pathsep) if p != str(Path(own) / BIN))
    if env.is_venv and env.prefix is not None:
        out["VIRTUAL_ENV"] = str(env.prefix)
        out["PATH"] = str(env.prefix / BIN) + os.pathsep + out.get("PATH", "")
    if uv():
        out["GPYTHON_UV"] = uv()
    return out


async def run(argv: list[str], on_line: LineCallback | None = None, cwd: Path | None = None, env: dict | None = None) -> int:
    """Run a command, sending each output line (stdout and stderr) to on_line. Returns the exit code."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as e:
        if on_line:
            on_line(f"{argv[0]}: {e.strerror or e}")
        return 127
    assert proc.stdout is not None
    buf = b""
    while chunk := await proc.stdout.read(4096):
        buf += chunk
        *lines, buf = re.split(rb"\r\n|\r|\n", buf)
        for line in lines:
            if on_line and (text := line.decode(errors="replace").rstrip()):
                on_line(text)
    if on_line and (text := buf.decode(errors="replace").rstrip()):
        on_line(text)
    return await proc.wait()


class EnvError(Exception):
    """A uv/pip/venv command failed. The message names the command, its exit code and, on
    the following lines, what it said (see `explain`)."""


_DECORATION = re.compile(r"^[\s×╰╭╮╯─│┃▶→•]+")
_MARKER = re.compile(r"^\s*(×|╰─▶|help:|hint:|error:|warning:|ERROR:|WARNING:)", re.IGNORECASE)
_KEPT_FROM = re.compile(r"×|error|no solution|not found|failed|unsatisfiable", re.IGNORECASE)


def explain(lines: list[str]) -> list[str]:
    """uv's and pip's error output as plain sentences: decorations stripped, wrapped lines joined,
    everything from the first error on (the last three items when nothing looks like one)."""
    items: list[str] = []
    for raw in lines:
        if not raw.strip():
            continue
        text = _DECORATION.sub("", raw).strip()
        if not text:
            continue
        if items and not _MARKER.match(raw) and raw.startswith(" "):
            items[-1] = f"{items[-1]} {text}"
        else:
            items.append(text)
    first = next((i for i, item in enumerate(items) if _KEPT_FROM.search(item)), None)
    return items[first:] if first is not None else items[-3:]


def _shown(argv: list[str]) -> str:
    return shlex.join(argv).replace(str(Path.home()), "~")


async def _check(argv: list[str], on_line: LineCallback | None, **kw) -> None:
    lines: list[str] = []

    def tee(line: str) -> None:
        lines.append(line)
        del lines[:-200]
        if on_line:
            on_line(line)

    code = await run(argv, tee, **kw)
    if code != 0:
        raise EnvError("\n".join([f"{_shown(argv)}  (exit code {code})", *explain(lines)]))


async def create(
    folder: Path, tool: str = "uv", python: str | None = None, on_line: LineCallback | None = None
) -> Env:
    """Make a venv at `folder` with ipykernel in it. tool: "uv" (fast) or "venv" (python -m venv + pip)."""
    folder = folder.expanduser().resolve()
    if tool == "uv" and uv():
        argv = [uv(), "venv", str(folder), "--no-project"]
        if python:
            argv += ["--python", python]
        await _check(argv, on_line)
    else:
        base = python or _base_python()
        await _check([base, "-m", "venv", str(folder)], on_line)
    env = venv_at(folder)
    if env is None:
        raise EnvError(f"The command did not make an environment at {folder}")
    await install(env, ["ipykernel"], on_line, tool=tool)
    return venv_at(folder) or env


def _base_python() -> str:
    """For `python -m venv`: the interpreter gpython's own venv was made from."""
    if sys.prefix != sys.base_prefix:
        cfg = _read_cfg(Path(sys.prefix) / "pyvenv.cfg")
        if home := cfg.get("home"):
            v = sys.version_info
            for name in (f"python{v.major}.{v.minor}", f"python{v.major}", "python"):
                if (p := Path(home) / name).exists():
                    return str(p)
    return sys.executable


async def install(env: Env, packages: list[str], on_line: LineCallback | None = None, tool: str = "uv") -> None:
    """Install packages into env: `uv pip install --python …`, or `python -m pip install` without uv."""
    if not packages:
        return
    if tool == "uv" and uv():
        await _check([uv(), "pip", "install", "--python", str(env.python), *packages], on_line)
    else:
        if env.prefix is not None and not _has_pip(env):
            await _check([str(env.python), "-m", "ensurepip", "--upgrade"], on_line)
        await _check([str(env.python), "-m", "pip", "install", "--disable-pip-version-check", *packages], on_line)


def _has_pip(env: Env) -> bool:
    lib = env.prefix / "lib" if env.prefix else None
    return bool(lib and any(lib.glob("python*/site-packages/pip")))


async def add(env: Env, packages: list[str], start: Path, on_line: LineCallback | None = None) -> bool:
    """`uv add` into the project around `start`, synced into env. Returns False if there is no
    pyproject.toml (the caller then falls back to a plain install)."""
    root = project_root(start)
    if not uv() or not (root / "pyproject.toml").exists():
        return False
    if env.prefix is not None and env.prefix == root / ".venv":
        await _check([uv(), "add", *packages], on_line, cwd=root)
    elif env.is_venv:
        await _check([uv(), "add", "--active", *packages], on_line, cwd=root, env=kernel_env(env))
    else:
        await _check([uv(), "add", "--no-sync", *packages], on_line, cwd=root)
        await install(env, packages, on_line)
    return True


async def ensure_ipykernel(env: Env, on_line: LineCallback | None = None) -> None:
    """Install ipykernel into a venv that lacks it. Bare interpreters are left alone (PEP 668)."""
    if env.has_ipykernel is False:
        await install(env, ["ipykernel"], on_line)
    elif env.has_ipykernel is None:
        code = "import importlib.util,sys; sys.exit(importlib.util.find_spec('ipykernel') is None)"
        if await run([str(env.python), "-c", code]) != 0:
            raise EnvError(
                f"{env.python} has no ipykernel. Make an environment from it (f6, then New uv environment)."
            )

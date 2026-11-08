"""Start the notebook's kernel process before the UI exists, so it is ready sooner.

`main()` spawns ipykernel for the notebook named on the command line a few milliseconds
after start, while Textual imports and paints the first frame (about 0.25 s); the kernel's
own start-up (about 0.5 s) then overlaps that instead of following it. `Kernel.start`
adopts the process when the pane's env and file match what was spawned (`take`); an
unclaimed spawn is killed at exit. Nothing here imports jupyter_client or textual.
"""

from __future__ import annotations

import atexit
import json
import os
import secrets
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import envs

PORTS = ("shell_port", "iopub_port", "stdin_port", "control_port", "hb_port")


@dataclass
class Spawned:
    env: envs.Env
    file: Path | None
    process: subprocess.Popen
    connection_file: Path


_pending: Spawned | None = None


def start(file: Path | None) -> None:
    """Spawn the kernel a NotebookPane for `file` will want, the way the pane picks its env."""
    global _pending
    try:
        from . import config

        remembered = config.load().get(f"env:{file.resolve()}") if file is not None else None
        env, _hint = envs.default_for(file, remembered)
        _pending = spawn(env, file)
    except Exception:
        _pending = None
        return
    atexit.register(_kill_unclaimed)


def spawn(env: envs.Env, file: Path | None) -> Spawned:
    """Launch ipykernel in `env` with a fresh connection file, as jupyter_client would."""
    info = {name: _free_port() for name in PORTS}
    info.update(ip="127.0.0.1", key=secrets.token_hex(16), signature_scheme="hmac-sha256", transport="tcp", kernel_name="python3")
    path = Path(tempfile.gettempdir()) / f"gpython-kernel-{os.getpid()}-{secrets.token_hex(4)}.json"
    with open(path, "w", opener=lambda name, flags: os.open(name, flags, 0o600)) as out:
        json.dump(info, out)
    environment = envs.kernel_env(env)
    environment["JPY_PARENT_PID"] = str(os.getpid())
    cwd = str(file.parent) if file is not None and file.parent.is_dir() else os.getcwd()
    process = subprocess.Popen(
        [str(env.python), "-m", "ipykernel_launcher", "-f", str(path)],
        cwd=cwd, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
    )
    return Spawned(env, file, process, path)


def take(env: envs.Env, file: Path | None) -> Spawned | None:
    """The spawned kernel for (env, file), once; None when there is none or it does not match."""
    global _pending
    spawned, _pending = _pending, None
    if spawned is None:
        return None
    if spawned.env.same(env) and _same_file(spawned.file, file) and spawned.process.poll() is None:
        return spawned
    discard(spawned)
    return None


def discard(spawned: Spawned) -> None:
    if spawned.process.poll() is None:
        spawned.process.kill()
        spawned.process.wait()
    try:
        spawned.connection_file.unlink()
    except OSError:
        pass


def _kill_unclaimed() -> None:
    if _pending is not None:
        discard(_pending)


def _same_file(a: Path | None, b: Path | None) -> bool:
    if a is None or b is None:
        return a is b
    return a.resolve() == b.resolve()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]

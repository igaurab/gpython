# 15 – uv projects: the kernel runs in the project, like `uv run`

In a uv project `uv run python` sees the project's packages, but gpython only uses a
`.venv` that already has ipykernel, so it falls back to its own interpreter and every
project import fails. gpython should behave like `uv run`, take the same flags, and still
ask before installing anything it suggests.

## Rule

gpython never writes ipykernel into a project. `.venv` and `pyproject.toml` only change
when the user runs something that changes them (`:pip`, `:sync`, or a picker row they
confirmed). The kernel is chosen in this order:

0. uv flags on the command line: the uv project, whatever else is there.
1. The env last chosen for this notebook (a project venv without ipykernel runs as in 3).
2. A project `.venv` that already has ipykernel (unchanged).
3. A uv project whose environment exists: the project's own interpreter via
   `uv run --with ipykernel`. ipykernel comes from uv's cache, never from `.venv`. A project
   never synced (no `.venv`) is suggested in a notification, not built at launch.
4. Built-in, gpython's own Python, as today. The hint names the project venv
   and says what to run (`install uv`, or f6). Built-in plus the project's
   site-packages on `sys.path` is not used: compiled packages are built for one Python
   (gpy is 3.13, llm-distillation is 3.12, so pymupdf's cp312 binary would not load), and
   gpython's own ipykernel/tornado/pyzmq would shadow the project's versions or be shadowed by them.

f6 picking a venv without ipykernel offers step 3 for uv projects instead of
`uv pip install ipykernel` into it; the old install stays as a second row for non-uv venvs.

## Behaviour

- **uv project** = `envs.project_root(start)` has `pyproject.toml` and `envs.uv()` is set.
  `default_for` then returns a `uv-project` Env (prefix `<root>/.venv`, `has_ipykernel`
  True) instead of falling back to built-in. The kernel command is
  `uv run --project <root> [flags] --with ipykernel python -m ipykernel_launcher -f {conn}`.
  The ipykernel layer is cached by uv and lives outside `.venv`, so `pyproject.toml` and
  `uv sync` never see it. Measured: 1.9 s on the first launch (download), 0.16 s after.
- **Launch sync**: plain `uv run` does an inexact sync (installs what the lock requires,
  removes nothing). That counts as the project's own declaration, so there is no prompt.
  The status line shows `syncing <project> with uv…` until the kernel is ready. If the
  sync fails, gpython starts again with `--no-sync` and shows `envs.explain` of the error.
- **CLI flags**, the same names as `uv run`, passed through as they are and applied to this
  launch only (not saved): `--extra X` (repeatable), `--all-extras`, `--group G`,
  `--with PKG` (repeatable, a temporary layer, `.venv` untouched), `--no-sync`.
  Typing a flag counts as asking, so there is no prompt.
- **Started by uv**: `uv run --extra synthesis gpy` needs nothing special: uv has synced the
  extra, and the kernel's own `uv run` is inexact, so it stays.
- **Missing module** (extends plan 08): `packages.declared(root, name)` reads
  `pyproject.toml` (`tomllib`, imported where used) and says where a distribution is
  declared: main dependencies, an extra, a group, or nowhere.
  - In an extra or group: hint `pymupdf is in the synthesis extra · :sync synthesis installs it`;
    the ask picker gets a first row `uv sync --extra synthesis --inexact`.
  - In the main dependencies but not installed: `:sync` → `uv sync --inexact`.
  - Not declared: as today (`uv add` / `uv pip install` rows). Everything asks first;
    `:set autoinstall` still skips the prompt.
  After a sync the kernel's import caches are invalidated and the failed cells re-run
  (no restart), as in plan 08.
- **Remembering**: an extra or group installed from a hint is saved per project in the
  config (`uv:<root>` → `"--extra=synthesis --group=dev"`) and added to later launches,
  so a plain `uv sync` removing it is undone on the next start. CLI flags add to it for one
  launch; f6 lists the saved extras and can drop them.
- **Display**: the header chip reads `Python 3.12 · proj/.venv (uv)`; f6 marks the project
  venv current with `uv run --with ipykernel <flags>` in its detail. Switching to another env in f6 drops the flags.
- **Not a uv project**: unchanged (nearest venv with ipykernel, else built-in, `:pip` hints).

## Files

- `envs.py`: `uv-project` kind (`project`, `uv_args`), `uv_project`, `runnable`,
  `kernel_argv` used by both launchers, `launch_log`/`read_log`/`without_sync` for the
  failed-sync fallback, `sync`, `remember_sync`/`saved_uv_args`; `project_env_dir` honours
  `UV_PROJECT_ENVIRONMENT`.
- `kernel.py`, `prespawn.py`: build argv with `envs.kernel_argv`; `take` matches on the flags
  (part of `Env`'s key); `discard` kills the process group (the kernel is uv's child); uv's
  stderr goes to a log read when the start fails.
- `__init__.py`: parse the flags into `opts["uv"]`, extend `USAGE`, hand them to
  `prespawn.start` and `GPython`.
- `packages.py`: `declared(root, distribution)`.
- `pane.py`, `envpicker.py`, `app.py`: the sync hint and picker row (`project_sync`),
  `:sync [names]`, the starting status line, the hints when flags do not apply.
- Tests: `tests/test_uv_projects.py` (two real `uv run` kernels, skipped without uv).

## Order

1. Kernel through `uv run` (`envs`, `kernel`, `prespawn`); bench before and after.
2. CLI flags and the `VIRTUAL_ENV` case.
3. `declared`, the `:sync` hint and row, remembered extras.

## Performance

The kernel is prespawned before the first frame, so `uv run` adds about 0.15 s to kernel
ready, not to `first_paint`. Check `first_paint` and `loaded_60` in `scripts/bench.py`; the
first-ever launch (ipykernel download) shows the status line instead of blocking.

## Unresolved questions

- `gpy --python 3.13` (pass through to uv) now or later?
- Should the launch sync be skipped unless `uv.lock` is newer than `.venv`, to save the
  check uv does on every start?
- `uv run` prints sync progress to the launch log only; show it live in the status line?
- Workspaces (a `[tool.uv.workspace]` root above the package): pass `--package`?
- `uv run` also reads `.env` files only with `--env-file`; pass that through too?
- Should a notebook's PEP 723 `# /// script` block (plain `.py` notebooks) be honoured via
  `uv run --script`?

# 08 – Missing packages: offer to install what a cell imports, then run it again (marimo's banner)

When a run ends in `ModuleNotFoundError`, marimo shows "Missing packages: numpy · Install"
and re-runs the cell after the install. gpython opens the same offer at the bottom; `enter`
installs with uv and re-runs. Nothing is installed without that keypress unless the user
opts in with `:set autoinstall`.

## Behaviour

- After a failed run, `packages.missing_module(cell.last_error)` names the module; the
  PyPI name comes from `packages.pypi_name(module, source)`: `packages.json`, a table of
  renamed packages (`cv2` → `opencv-python`, `sklearn` → `scikit-learn`, `PIL` → `pillow`),
  rules for the `google.cloud.*` and `azure.*` namespaces, the cell's own import statements
  to pick the longest known dotted name, else the top-level module (pip normalises `_`/`-`).
  PyPI has no lookup by import name (the search API was shut down in 2020; the JSON API is
  by distribution name only), so the table is the source of truth, as in marimo and pipreqs.
- A hint under the traceback: `numpy is not installed in .venv · :pip numpy installs it`.
  Standard-library modules the interpreter lacks get a different hint and nothing else.
- Default: the env picker opens in ask mode, prefilled with the packages, with one row per
  way to install: `uv add` (project; shown when uv and a `pyproject.toml` at the project
  root exist) and `uv pip install` (env only; pip without uv). `↑↓` pick: the chosen row
  carries ● and the title previews the exact command (`enter: uv add numpy → ~/p/pyproject.toml`)
  as the highlight or the typed names change; the pick is saved
  as `install_mode` (`envpicker.default_mode`: saved choice, else `uv pip install`; `uv add`
  rewrites pyproject.toml, which bit when the notebook lived in gpython's own checkout).
  `enter` runs `envs.add` or `envs.install` with progress
  in the bottom bar, invalidates the kernel's import caches and runs the failed cells again;
  `esc` declines. `:set autoinstall` (saved) skips the question and uses the default mode.
- A failure (`envs.EnvError`) carries the command, its exit code and `envs.explain(lines)`:
  uv's and pip's output with decorations stripped and wrapped lines joined, from the first
  error on. It is shown as a notification and written under each failed cell.
- Every run of the cell asks again (a declined prompt comes back on the next run, as in
  marimo). The re-run after an install leaves out the modules just tried
  (`NotebookPane.tried`, cleared on restart or env switch), so a wrong name cannot loop; a
  new module surfacing in that re-run is offered. With autoinstall, a module whose install
  failed opens the prompt on the next run instead of a silent retry. `:pip` with no
  argument prefills the current cell's package.
- No prompt while another modal is open; the hint still shows.

## Files

- `packages.py` (new): `missing_module`, `is_stdlib`, `pypi_name`, `PYPI_NAMES`.
- `cell.py`: `Cell.last_error`, `Cell.missing`, `Cell.show_hint`; `outputs.py`: `add_hint`.
- `pane.py`: `run_cells` collects missing modules, `envpicker.install_missing` acts on them.
- `envpicker.py`: `EnvPicker(prefill=…)`, `install()` returns success, `install_missing`,
  `ex_pip` prefill. `app.py`: `autoinstall` setting.
- Tests: `tests/test_packages.py` (no kernel), `tests/test_missing_module.py` (kernel; a fake
  `envs.install` writes the module next to the notebook so the re-run succeeds).

## Next

- Verified candidates: check the guessed names against `GET pypi.org/pypi/<name>/json`
  (exists, summary, version) and list them in the picker, table answer first, so the
  user picks a real package; offline shows the unverified guess. No fuzzy matching:
  the nearest name on PyPI is how typo-squatting works.

## Unresolved

- `:set autoinstall` trusts the import name: `import reqeusts` installs whatever PyPI
  serves under that name. That is why asking is the default.
- `uv add` to the project instead of `uv pip install` is not offered automatically.
- `packages.json` is a curated subset of marimo/pipreqs' list; the pypi-data bulk dataset
  could generate a far larger one offline.

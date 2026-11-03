# gpython implementation plans

gpython is a reactive Python notebook in the terminal (Textual UI, IPython kernel in
a subprocess, marimo-style dependency graph). These plans turn it into a fast,
pleasant notebook with vim bindings, colorschemes, markdown cells and the everyday
Jupyter/marimo features.

Each plan is written so that an implementation agent can execute it **without
asking questions**. Read this file first, then the plan you were assigned.

## Plans and order

| # | File | Scope | Depends on |
|---|------|-------|-----------|
| 01 | `01-performance.md` | Make the UI feel instant: output batching, cheap focus changes, fast startup, benchmark script | – |
| 02 | `02-themes.md` | Colorschemes (catppuccin ×4, gruvbox ×2, material ×4, nord, dracula, tokyo-night, solarized ×2, monokai), editor syntax themes, persistence, picker | 01 (file layout) |
| 03 | `03-keybindings-ux.md` | Jupyter run keys, cell command mode, move/undo/collapse, status bar, help, dirty tracking | 01, 02 |
| 04 | `04-markdown-cells.md` | Markdown cells, rich MIME outputs, cell kind toggling | 03, peer `CellSpec` |
| 05 | `05-peer-file-formats.md` | **Owned by the peer session** (notebook/graph/kernel): CellSpec, `.ipynb`, `--theme` flag. Reference only. | – |
| 06 | `06-marimo-format.md` | marimo notebooks: load/save round-trip, kernel shim | 05 |
| 07 | `07-windows.md` | File explorer, splits, tabs; `NotebookPane` owns the kernel and cells | 04 |
| 08 | `08-missing-packages.md` | `ModuleNotFoundError` → install the package into the kernel's env (uv) and run the cell again, like marimo | 07 |
| 09 | `09-outline.md` | Outline panel on the right: cells, their definitions with live types, dependency edges, jump-to (replaces the f2 variables table) | 08 |

Plans run **sequentially** in the order above because they all edit `app.py`.

## Two sessions work on this repo at once

Another Claude session ("the peer") is editing this working tree concurrently.
File ownership is strict:

- **Ours (you may edit):** `src/gpython/app.py`, `src/gpython/app.tcss`, `src/gpython/vim.py`,
  any **new** module under `src/gpython/` named in your plan (`themes.py`, `config.py`,
  `cell.py`, `outputs.py`, `markdown.py`, `statusbar.py`, `help.py`...), `tests/test_app.py`,
  `tests/test_vim.py`, new test files named in your plan, `scripts/`, `docs/`, `README.md`.
- **Peer's (never edit):** `src/gpython/graph.py`, `src/gpython/notebook.py`,
  `src/gpython/kernel.py`, `src/gpython/__init__.py`, `tests/test_graph.py`,
  `tests/test_notebook.py`, `tests/test_kernel.py`, `pyproject.toml`, `uv.lock`, `gpython` launcher.
  If you need a change there, finish everything else, then list the exact change you want
  in your final report under a heading **"Requests for peer-owned files"**.

Rules:

1. **Never run `git add`, `git commit`, `git stash`, `git checkout -- <file>` or `git reset`.**
   The peer commits from this same tree; the coordinating session commits our files explicitly.
2. If a peer-owned file changes under you mid-task, re-read it and adapt. Do not revert it.
3. Keep these public hooks; existing tests depend on them: `VimTextArea.enabled` (class flag),
   `Cell.output_text`, `Cell.editor`, `Cell.source`, `Cell.status`, `Cell.info`, `Cell.cid`,
   `GPython.cells()`, `GPython.current_cell()`, `GPython.run_cells()`, `GPython.kernel`,
   `GPython.markers`, `GPython.lazy`, messages `VimTextArea.EdgeReached / ExCommand / ModeChanged`,
   the `Prompt` modal screen, `CompletionPopup`.
4. Always use `uv run python3 ...` (never bare `python`). Tests: `uv run python3 -m pytest -q`
   (about 90 s; kernel tests dominate). Run a single file while iterating:
   `uv run python3 -m pytest -q tests/test_vim.py`.
5. All existing tests must pass when you finish, plus the new tests your plan names.
6. Benchmark before/after with `uv run python3 scripts/bench.py` (created by plan 01). No metric
   may regress by more than 10 % in plans 02–04; report the table in your final message.
7. Do not add dependencies. Available: textual 8.2.8 (`textual[syntax]`, so tree-sitter
   languages incl. `python` and `markdown`), rich, textual-image, pillow, jupyter_client, ipykernel.
   Python 3.13 (`tomllib` is in the stdlib).
8. Final report: what you changed (file by file), test results (paste the summary line),
   bench table, anything skipped and why, requests for peer-owned files.

## Facts about the environment (verified, do not re-derive)

- Textual 8.2.8. Builtin app themes: `textual-dark`, `textual-light`, `nord`, `gruvbox`,
  `catppuccin-mocha`, `catppuccin-latte`, `catppuccin-frappe`, `catppuccin-macchiato`, `dracula`,
  `tokyo-night`, `monokai`, `flexoki`, `solarized-light`, `solarized-dark`, `rose-pine`,
  `rose-pine-moon`, `rose-pine-dawn`, `atom-one-dark`, `atom-one-light`, `ansi-dark`, `ansi-light`.
  `App.theme` is a reactive; `App.register_theme(Theme)`; `App.action_change_theme()` opens the
  built-in theme palette with live preview; `App.theme_changed_signal` fires after a change.
- `TextAreaTheme.builtin_themes()` names: `css`, `monokai`, `dracula`, `vscode_dark`, `github_light`.
  `TextAreaTheme` fields: `name, base_style, gutter_style, cursor_style, cursor_line_style,
  cursor_line_gutter_style, bracket_matching_style, selection_style, syntax_styles: dict[str, Style]`.
  Syntax keys used by the Python highlighter: `string, string.documentation, comment, keyword,
  operator, conditional, keyword.function, keyword.return, keyword.operator, repeat, exception,
  include, number, float, class, type, type.class, type.builtin, function, function.call, method,
  method.call, boolean, constant.builtin, variable.builtin, punctuation.bracket,
  punctuation.delimiter, punctuation.special`. Markdown highlighter adds `heading, heading.marker,
  list.marker, inline_code, link.label, link.uri, bold, italic, strikethrough`.
  `TextArea.register_theme(theme)` is per instance; `TextArea.theme = "<name>"` switches.
- `TextArea(...).available_languages` includes `python` and `markdown`.
- The Textual Linux/macOS driver enables the **kitty keyboard protocol** (flags disambiguate +
  report-all-keys + associated-text). In kitty, ghostty, WezTerm, foot, iTerm2 ≥ 3.5, Alacritty ≥ 0.13
  and Windows Terminal preview, `shift+enter`, `ctrl+enter`, `ctrl+shift+enter`, `alt+enter`
  arrive as distinct key names of exactly those spellings. Terminal.app and older terminals send
  plain `enter` for all of them, so every run binding keeps a plain fallback (`ctrl+e`).
- `Widget.move_child(child, *, before=None, after=None)`;
  `Widget.scroll_visible(animate=True, ..., immediate=False)`.
- Headless UI tests use `async with app.run_test(size=(120, 40)) as pilot:`; `await pilot.press(...)`,
  `await pilot.pause()`, `await app.workers.wait_for_complete()`; kernel tests do
  `await asyncio.wait_for(app.kernel.ready.wait(), 60)` first.
- Measured baseline (headless, 60 cells of 10 lines, 140×45) before plan 01:
  mount 2.20 s · 30 keystrokes 1.99 s (≈17 ms CPU each + pilot waits) · 30 cell moves 5.27 s ·
  2 000 stream chunks into one cell 5.33 s · run trivial cell end-to-end 42 ms · `refresh_info` 6.6 ms ·
  kernel start 0.53 s · `import textual` 95 ms.

## Current architecture (after peer commit 8c80d5e)

- `graph.py` – `analyze()`, `mutations()`, `build(cells) -> Graph` with `defs/refs/mutates/owner/
  parents/children/errors`, `run_order()`, `sort()`, `expand_roots()`, `descendants()`, `ancestors()`.
- `kernel.py` – `Kernel(file=path)`: `start/restart/shutdown/interrupt`, `execute(code, on_output)`
  streaming iopub messages `(kind, content)` for `stream, execute_result, display_data, error,
  clear_output`; `delete(names)`, `variables(names)`, `complete(code, pos)`, `set_file(path)`.
- `notebook.py` – `load(path) -> (list[str], uses_markers)`, `save(path, sources, markers)`,
  `split_script()`, `parse()`, `dump()`. Peer will add `CellSpec(source, kind)` (plan 05).
- `vim.py` – `VimTextArea(TextArea)`: **global** vim mode (`VimTextArea.mode` is a class attribute;
  each editor re-applies it on focus via `_apply_mode()`), `VimTextArea.enabled` class flag, counts,
  motions, operators, registers, `:` → `ExCommand`, j/k at edge → `EdgeReached`, `escape`/`ctrl+[`
  (`_is_escape`), Tab + as-you-type completion via kernel → `CompletionPopup` (0.12 s timer), Python
  auto-indent (`indent_after`, `_newline`, `_dedent_on_backspace`). Theme hardcoded to `monokai` + a
  `monokai-normal` variant (yellow block cursor) registered per instance in `on_mount`.
- `app.py` – `Cell(Vertical)` = header `Static` + `VimTextArea` + output `Vertical` of `Static`/`Image`
  (`Cell.source` falls back to `initial_source` before compose); `GPython(App)`: bindings
  (`ctrl+enter`/`ctrl+e` run in place, `shift+enter` run + new cell below = `action_run_and_add`,
  `ctrl+n`, `ctrl+d`, `alt/ctrl+up/down`, `ctrl+s`, `ctrl+k`, `f2`, `f3`, `f4`), `run_cells()`, lazy mode,
  `Prompt` modal for `:` and save-as, a bottom `Static#status` line updated by
  `update_status(kernel_state=None)` showing mode/file/reactive|lazy/kernel state (plus `Footer`),
  **ex commands as a table** `ex_commands() -> [(names, desc, handler(arg))]` shared by `_run_ex` and the
  command palette (`get_system_commands`). To add a command, add one row there.
- `app.tcss` – layout + status border colors (`-ok -error -stale -running -queued -skipped` classes),
  `#status` docked bottom.

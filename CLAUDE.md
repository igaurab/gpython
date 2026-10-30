# gpython

Terminal notebook built on Textual. `uv run python3 -m pytest -q` runs the tests (~150 s);
`uv run python3 scripts/bench.py` is the headless benchmark and `uv run python3 scripts/ptybench.py`
times a real launch in a pseudo-terminal. Never run a benchmark while the tests run.

## Rules for every agent

- No comments in code. Do not write `#` comments in Python, `/* */` in CSS, or `#` lines in
  shell scripts (the shebang is fine). Explain with names, small functions and docstrings;
  anything that needs more goes in `docs/plans/`. The repo was stripped of comments on
  2026-10-30; keep it that way.
- Always use `uv run python3`.
- Plans: extremely concise; end with unresolved questions.
- Performance is a feature. gpython must feel instant: a keystroke paints in under 10 ms,
  the first frame is on screen within ~0.3 s of launch, a cell move costs under 20 ms of CPU.
  Before changing `app.py`, `pane.py`, `cell.py`, `vim.py`, `outputs.py` or `app.tcss`, run
  `uv run python3 scripts/bench.py` twice and keep the numbers; run it again when done. A
  feature that makes `first_paint`, `keys_30_net`, `moves_30_proc` or `loaded_60` worse is not
  finished: profile it (`--profile keys|mount|moves`) and fix it, or ask before merging.
  Things that cost the most, learned the hard way (details in `docs/plans/TODO.md` §1):
  importing a library at module level (import it where it is first used), work before the
  first frame (use `GPython.after_first_frame`), widgets that update with `layout=True`,
  CSS class changes on a cell move, whole-screen repaints on focus change, and anything that
  rebuilds itself on every keystroke (the Footer did). New widgets per cell cost ~1 ms each
  of CSS and layout at start-up; prefer painting into an existing one.

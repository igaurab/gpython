# gpython – open items (updated 2026-10-30)

State: plans 01–07 implemented (07 = file explorer, splits, tabs; see `docs/plans/07-windows.md`),
200 tests green (`uv run python3 -m pytest -q`, ~100 s). Read `docs/plans/README.md` first.

## 1. Performance round 2 (plan 01, steps 4–5 were only partly done)

Re-verified after all features landed (`uv run python3 scripts/bench.py`, headless, 60 cells):

| metric | perf round 1 | now |
|---|---|---|
| mount_60 (first frame) | 0.182 s | 0.151 s |
| keys_30_net (CPU for 30 keystrokes minus floor) | 0.361 s | 0.357 s |
| moves_30 (30 cell moves) | 1.428 s | 1.453 s |
| stream_2000 | 0.105 s | 0.047 s |
| run_trivial | 0.031 s | 0.029 s |
| run_print_3000 | 0.128 s | 0.153 s |

No regression from the features, but the plan 01 targets are not reached yet:
- keystroke: target ≤ 6 ms CPU each (now ≈ 12 ms). Profile `pilot.press` with cProfile; the
  remaining cost is the full-screen layout per edit in a `height: auto` TextArea (see plan 01 step 4
  and `VimTextArea._refresh_size` in vim.py, which already skips layout when the height is unchanged).
- cell moves: target 30 moves ≤ 0.60 s (now 1.45 s wall, ≈ 0.6 s CPU). Profile `action_focus_cell`
  / `GPython.select`; check CSS re-application on `-focused`/`-selected` class changes and the header
  re-render.
- `run_print_3000` got slower (0.128 → 0.153 s); check `OutputArea._flush` with the new MIME code.
- Verify in a REAL terminal (kitty/ghostty/iTerm2), not only headless: typing latency, scrolling a
  150-cell marimo notebook, theme switching with the picker.

## 2. Acceptance in a real terminal

- `./gpython ~/leetcode/notebooks/0001-two-sum.py`: run all (f5), problem statement renders as
  markdown, `:w` leaves the file byte-identical (headless check passes; confirm on screen).
- `./gpython ~/leetcode/problems/02-two-pointers/0015-3sum.py`: first cell shows as rendered
  markdown, `enter` edits it, `escape escape` renders.
- `ctrl+t` picker: preview while moving, `escape` restores, `enter` persists to
  `~/.config/gpython/config.toml`.
- Keys that need the kitty keyboard protocol (`shift+enter`, `ctrl+enter`, `ctrl+shift+enter`,
  `alt+enter`): confirm in the user's terminal; `ctrl+e` is the fallback.

## 3. README polish

Short sections for: themes (list + picker + config file), markdown cells (exists, review), marimo and
`.ipynb` support (what round-trips, what is lost: decorator kwargs other than `hide_code`, comments
after `return`), the `--theme` / `--no-vim` flags, the key table (generated from `keymap.py`).

## 4. Small follow-ups

- `tests/test_commands.py::test_shift_enter_keeps_the_viewport` was flaky; fixed by
  `GPython._keep_focus_visible` and passed 6/6 afterwards. Keep an eye on it.
- Window resizing (`ctrl+w + - < >`) changes weights by 10 % steps; no mouse drag of the
  separator yet.
- `:split` with no file opens an untitled notebook (vim shows the same buffer twice; we
  cannot, one kernel per window).
- `hide_code=True` marimo cells show their code; a collapsed-code mode could honour the flag.
- `.ipynb` outputs are dropped on load; consider showing stored outputs read-only.

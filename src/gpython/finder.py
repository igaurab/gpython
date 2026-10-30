"""Fuzzy file finder (space f f, :find, or the command palette), like telescope.nvim.

A centered popup: a prompt over a results list that is filtered as you type. The query
matches when its letters appear in order in the path (a subsequence, like fzf). Matches
are ranked by quality: runs of consecutive letters, letters at the start of a folder or
file name, matches in the file name itself, and shorter paths win. The matched letters
are highlighted. enter opens the file in its own tab (like the explorer), esc closes.

Files come from the explorer's root folder: `git ls-files` when it is a git checkout (so
.gitignore is respected), else a walk that skips hidden and tool folders. The walk runs
in a thread, so the popup opens at once; the last list per folder is shown meanwhile.
"""

from __future__ import annotations

import asyncio
import heapq
import os
import subprocess
from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from .explorer import HIDDEN_DIRS

MAX_FILES = 20_000
MAX_ROWS = 200

_cache: dict[Path, list[str]] = {}


def _skipped(part: str) -> bool:
    return part.startswith(".") or part in HIDDEN_DIRS or part.endswith(".egg-info")


def _git_files(root: Path) -> list[str] | None:
    """Tracked and untracked-but-not-ignored files under root, or None outside a git checkout."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, timeout=10, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    files = []
    for name in result.stdout.decode(errors="replace").split("\0"):
        if name and not any(_skipped(p) for p in name.split("/")) and (root / name).is_file():
            files.append(name)
            if len(files) >= MAX_FILES:
                break
    return files


def _walk_files(root: Path) -> list[str]:
    files = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not _skipped(d)]
        rel = os.path.relpath(folder, root)
        prefix = "" if rel == "." else rel.replace(os.sep, "/") + "/"
        for name in names:
            if not name.startswith("."):
                files.append(prefix + name)
                if len(files) >= MAX_FILES:
                    return files
    return files


def list_files(root: Path) -> list[str]:
    """Paths relative to root ("src/app.py"), shallow ones first, at most MAX_FILES."""
    files = _git_files(root)
    if files is None:
        files = _walk_files(root)
    return sorted(files, key=lambda f: (f.count("/"), f.lower()))


def _positions(query: str, text: str, start: int) -> list[int] | None:
    """Where the query's letters sit in text[start:], or None if they do not all appear in order.

    Like fzf: a forward scan finds the earliest place the match can end, then a backward
    scan from there finds the tightest window, which keeps runs of letters together.
    """
    i = start - 1
    for ch in query:
        i = text.find(ch, i + 1)
        if i < 0:
            return None
    found, j = [], i + 1
    for ch in reversed(query):
        j = text.rfind(ch, start, j)
        found.append(j)
    found.reverse()
    return found


def fuzzy_match(query: str, path: str) -> tuple[float, list[int]] | None:
    """(score, matched positions) when the query's letters appear in order in path, else None.

    Case and spaces in the query are ignored. A match inside the file name is preferred
    to one spread over the folders.
    """
    query = query.replace(" ", "").lower()
    if not query:
        return 0.0, []
    text = path.lower()
    base = text.rfind("/") + 1
    found = _positions(query, text, base) or _positions(query, text, 0)
    if found is None:
        return None
    score, prev, run = 0.0, -2, 0
    for p in found:
        if p == prev + 1:
            run += 1
            score += 2 * run
        else:
            run = 0
            if prev >= 0:
                score -= 1 + min(p - prev - 1, 10) * 0.1
        before = path[p - 1] if p else "/"
        if before == "/":
            score += 4
        elif before in "_-. ":
            score += 3
        elif before.islower() and path[p].isupper():
            score += 2
        if p >= base:
            score += 1
        prev = p
    if found[0] == base:
        score += 3
    return score - len(path) * 0.05, found


def rank(query: str, files: list[str], limit: int = MAX_ROWS) -> tuple[list[tuple[str, list[int]]], list[str]]:
    """The best `limit` matches (path, matched positions), best first, and every file that matched."""
    if not query.replace(" ", ""):
        return [(f, []) for f in files[:limit]], files
    scored = []
    for f in files:
        if (m := fuzzy_match(query, f)) is not None:
            scored.append((m[0], -len(f), f, m[1]))
    best = heapq.nlargest(limit, scored, key=lambda s: (s[0], s[1]))
    return [(f, found) for _, _, f, found in best], [s[2] for s in scored]


def highlight(path: str, positions: list[int], style: Style | str = "bold") -> Text:
    """The path with its folders dim and the matched letters in `style`."""
    text = Text(path, no_wrap=True, overflow="ellipsis")
    text.stylize("dim", 0, path.rfind("/") + 1)
    for p in positions:
        text.stylize(style, p, p + 1)
    return text


class FileFinder(ModalScreen[Path | None]):
    """Centered popup: a prompt over the matching files. Dismisses with the chosen path."""

    DEFAULT_CSS = """
    FileFinder {
        align: center middle;
        background: transparent;
    }
    #finder {
        width: 90%;
        max-width: 110;
        height: 80%;
        background: $surface;
        border: round $accent;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 0 1;
    }
    #finder-prompt {
        height: 1;
    }
    #finder-prompt Label {
        color: $accent;
        text-style: bold;
    }
    #finder Input {
        border: none;
        height: 1;
        padding: 0;
        width: 1fr;
        background: transparent;
    }
    #finder-count {
        width: auto;
        color: $text-muted;
    }
    #finder OptionList {
        border: none;
        border-top: solid $panel-lighten-2;
        height: 1fr;
        background: transparent;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss(None)", show=False),
        Binding("up,ctrl+p,ctrl+k", "move(-1)", show=False, priority=True),
        Binding("down,ctrl+n,ctrl+j", "move(1)", show=False, priority=True),
    ]
    APP_ACTIONS_OFF = {"add_cell", "interrupt", "command_palette", "delete_cell", "run_cell", "window_prefix"}

    def __init__(self, root: Path, query: str = "") -> None:
        super().__init__()
        self.root = root
        self.files: list[str] = _cache.get(root, [])
        self._query = query
        self._shown: list[str] = []
        self._last: tuple[str, list[str]] = ("", self.files)

    def compose(self) -> ComposeResult:
        with Vertical(id="finder"):
            with Horizontal(id="finder-prompt"):
                yield Label("> ")
                yield Input(value=self._query, placeholder="Find files…  (enter open · esc close)", select_on_focus=False)
                yield Static(id="finder-count")
            yield OptionList()

    def on_mount(self) -> None:
        self.query_one("#finder").border_title = f" Find files · {_home(self.root)} "
        self._fill(self._query)
        self.query_one(Input).focus()
        self.run_worker(self._list(), group="finder")

    async def _list(self) -> None:
        files = await asyncio.to_thread(list_files, self.root)
        _cache[self.root] = files
        if files != self.files:
            self.files = files
            self._last = ("", files)
            self._fill(self.query_one(Input).value)

    def _style(self) -> Style:
        try:
            return Style(color=Color.parse(self.app.current_theme.accent).rich_color, bold=True)
        except Exception:
            return Style(bold=True, underline=True)

    def _fill(self, query: str) -> None:
        q = query.replace(" ", "").lower()
        last_query, last_matched = self._last
        pool = last_matched if last_query and q.startswith(last_query) else self.files
        rows, matched = rank(q, pool)
        self._last = (q, matched)
        style = self._style()
        self._shown = [f for f, _ in rows]
        options = self.query_one(OptionList)
        options.set_options([Option(highlight(f, found, style)) for f, found in rows])
        options.highlighted = 0 if rows else None
        if self.files:
            status = f"{len(matched)} / {len(self.files)}"
        else:
            status = "no files" if self.root in _cache else "listing…"
        self.query_one("#finder-count", Static).update(status)

    @on(Input.Changed)
    def _filter(self, event: Input.Changed) -> None:
        self._fill(event.value)

    def action_move(self, delta: int) -> None:
        options = self.query_one(OptionList)
        options.action_cursor_down() if delta > 0 else options.action_cursor_up()

    def current(self) -> Path | None:
        index = self.query_one(OptionList).highlighted
        return None if index is None or index >= len(self._shown) else self.root / self._shown[index]

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(self.current())

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(self.root / self._shown[event.option_index])


def _home(path: Path) -> str:
    text, home = str(path), str(Path.home())
    return "~" + text[len(home):] if text.startswith(home) else text


def open_finder(app, query: str = "") -> None:
    """Open the finder at the explorer's root (else the first notebook's folder or cwd)."""
    if isinstance(app.screen, FileFinder):
        return
    root = Path(app.explorer.path if app.explorer is not None else app.explorer_root).resolve()

    def chosen(path: Path | None) -> None:
        if path is not None:
            app.run_worker(app.open_file(path, "tab"))

    app.push_screen(FileFinder(root, query), chosen)

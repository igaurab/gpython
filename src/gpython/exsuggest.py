"""Suggestions for the ':' prompt: the command names that start with the typed text, and after
a command and a space, the arguments that command takes (options of :set, themes, files, open
notebooks ...). `suggester(app)` gives the function the Prompt calls on each key; the rest is
plain functions over text so they can be tested without the app.

Imported on the first ':' (never at start-up).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, NamedTuple

from .vim import _RANGE

MAX_FILES = 200


class Suggestion(NamedTuple):
    """`line` is what the prompt holds when the row is chosen; `label` and `detail` are shown."""

    line: str
    label: str
    detail: str = ""


Candidates = list[tuple[str, str]]

EDITOR_COMMANDS: list[tuple[tuple[str, ...], str]] = [
    (("s", "substitute"), "Replace text in the editor: :s/old/new/ on this line, :%s/old/new/g in all cells"),
    (("g", "global"), "Do a command on each line that has the text (:g/text/d deletes these lines)"),
    (("v", "vglobal"), "Do a command on each line that does not have the text (:v/text/d)"),
    (("d", "delete"), "Delete the lines (:3,5d)"),
    (("j", "join"), "Join the lines into one line"),
    ((">",), "Move the lines one indent to the right"),
    (("<",), "Move the lines one indent to the left"),
    (("m", "move"), "Move the lines to after line N (:m 0 moves them to the top)"),
    (("t", "co"), "Copy the lines to after line N"),
    (("norm", "normal"), "Do normal mode keys on each line (:%normal A;)"),
]

SET_OPTIONS: Candidates = [
    ("vim", "Use the vim keys in the editor"),
    ("novim", "Use the editor without the vim keys"),
    ("lazy", "Run mode lazy: the cells that use this cell become out of date"),
    ("nolazy", "Run mode reactive: the cells that use this cell run again"),
    ("concurrent", "Cells that share no state run at the same time"),
    ("noconcurrent", "Cells run one by one"),
    ("concurrency=", "The largest number of cells that run at the same time (empty: the default)"),
    ("concurrent_verify", "After a group, check the names its cells share"),
    ("noconcurrent_verify", "Do not check the shared names after a group"),
    ("nu", "Show the line numbers"),
    ("nonu", "Hide the line numbers"),
    ("time", "Show the run time in each cell header"),
    ("notime", "Hide the run time"),
    ("autoinstall", "Install a missing module without a question"),
    ("noautoinstall", "Ask before a missing module is installed"),
    ("statusbar", "Show the status bar"),
    ("nostatusbar", "Hide the status bar"),
    ("markers", "A .py file that has # %% lines keeps them"),
    ("nomarkers", "Each save writes a .py file without # %% lines"),
    ("icons", "The file tree shows folder pictures (needs a Nerd Font)"),
    ("noicons", "The file tree shows folders as text, for a font without the pictures"),
    ("cursor=", "The cursor in insert mode: bar, underline or block"),
    ("cursor=bar", "A thin line"),
    ("cursor=underline", "A line below the character"),
    ("cursor=block", "A full block"),
]

DEBUG_OPTIONS: dict[str, tuple[str, Candidates]] = {
    "detail": ("How much the debugger records", [
        ("full", "Keep all steps (the default)"),
        ("balanced", "Keep less for long runs"),
        ("lean", "Keep the least"),
        ("auto", "full until 10,000 steps, then balanced"),
    ]),
    "history": ("The largest size of the recorded history (200MB)", []),
    "events": ("The largest number of recorded steps", []),
    "thinning": ("Drop old steps of long loops", [("on", ""), ("off", "")]),
    "repr_chars": ("The largest number of characters kept for one value", []),
    "strip_items": ("The largest number of items shown for one list", []),
    "grid_size": ("The largest grid shown for a 2-D value", []),
    "stats": ("Statistics of arrays", [("watched", "Only the watched names"), ("cpu", "All arrays on the CPU"), ("off", "None")]),
}

STATIC_ARGUMENTS: dict[str, Candidates] = {
    "img": [
        ("save", "Save this cell's images as PNG next to the notebook"),
        ("save all", "Save the images of all cells"),
        ("open", "Open this cell's images in the browser"),
        ("open all", "Open the images of all cells in the browser"),
    ],
    "copy": [
        ("all", "Copy the notebook as code, with markdown as comments"),
        ("all metadata", "Copy the notebook and keep the # %% cell lines"),
    ],
    "foldclose": [("all", "Fold all cells")],
    "agent": [
        ("claude", "Start Claude Code"),
        ("codex", "Start Codex"),
        ("opencode", "Start OpenCode"),
        ("gemini", "Start Gemini"),
        ("pi", "Start pi"),
        ("new", "Start a new conversation with the same agent"),
        ("stop", "End the agent"),
        ("hide", "Hide the panel; the conversation stays"),
        ("link", "Let an agent in another terminal use the notebook (gpython mcp)"),
        ("model", "Choose the agent's model from a list (/model in the panel)"),
        ("default claude", "The agent that :agent starts (also codex, opencode, gemini, pi, or a name from the config)"),
        ("tools fold", "Tool calls show while the agent works, then one line says how many (the default)"),
        ("tools keep", "Tool calls stay in the conversation"),
        ("tools hide", "Tool calls never show"),
        ("thoughts fold", "Thoughts show while the agent works, then go (the default)"),
        ("thoughts keep", "Thoughts stay in the conversation"),
        ("thoughts hide", "Thoughts never show"),
    ],
    "update": [("stable", "The newest release on PyPI"), ("dev", "The newest code on GitHub")],
    "foldopen": [("all", "Unfold all cells")],
    "tabmove": [
        ("0", "To the start"),
        ("last", "To the end"),
        ("+1", "One place to the right"),
        ("-1", "One place to the left"),
    ],
    "tabc": [("!", "Close the tab and do not ask about unsaved changes")],
    "stop": [("if ", "Run until the expression that follows is true")],
    "uv": [
        ("add ", "Add a package to the uv project and install it"),
        ("pip install ", "Install a package into the kernel environment"),
        ("pip uninstall ", "Remove a package from the kernel environment"),
        ("pip list", "List the installed packages"),
        ("venv", "Make a new environment with uv and use it (:uv venv [folder] [python])"),
    ],
    "pip": [
        ("install ", "Install a package into the kernel environment"),
        ("uninstall ", "Remove a package from the kernel environment"),
        ("list", "List the installed packages"),
        ("freeze", "List the installed packages with their versions"),
        ("show ", "Show the details of a package"),
        ("tree", "Show which package needs which"),
        ("check", "Check that the installed packages agree"),
    ],
}

NOTEBOOK_FILE_COMMANDS = ("e", "e!", "sp", "vs", "tabnew", "w", "wq", "wqa")
SAVE_COMMANDS = ("w", "wq", "wqa")
FOLDER_COMMANDS = ("Ex",)
BUFFER_COMMANDS = ("b", "bd", "bd!")

_SPLIT_RANGE = re.compile(rf"({_RANGE})?(.*)$", re.DOTALL)
_NAME = re.compile(r"[A-Za-z]*!?$|[<>]$")


def split_range(line: str) -> tuple[str, str]:
    """`'<,'>d` as (`'<,'>`, `d`); a line with no range as ("", line)."""
    match = _SPLIT_RANGE.match(line)
    return (match.group(1) or "", match.group(2)) if match else ("", line)


def first_sentence(text: str) -> str:
    """The part of a description that fits on one row: up to the first full stop or bracket."""
    cut = min((i for i in (text.find(". "), text.find(" ("), text.find(": :")) if i > 0), default=len(text))
    return text[:cut]


def _matching_name(names: tuple[str, ...], typed: str) -> str | None:
    """The first name of a command that starts with `typed`; capital letters do not matter."""
    folded = typed.casefold()
    exact = next((n for n in names if n == typed), None)
    return exact or next((n for n in names if n.casefold().startswith(folded)), None)


def command_suggestions(typed: str, commands: list[tuple[tuple[str, ...], str]], prefix: str = "") -> list[Suggestion]:
    """One row for each command with a name that starts with `typed`. An exact name comes first,
    then the order of `commands`. `prefix` (a range) stays in front of the chosen name."""
    rows: list[Suggestion] = []
    seen: set[str] = set()
    for names, description in commands:
        name = _matching_name(names, typed)
        if name is None or name in seen:
            continue
        seen.add(name)
        rows.append(Suggestion(prefix + name, name, first_sentence(description)))
    rows.sort(key=lambda row: row.label != typed)
    return rows


def argument_suggestions(name: str, typed: str, candidates: Candidates) -> list[Suggestion]:
    """The candidates that start with the typed argument. A `key=value` candidate shows only
    after the `=` is typed, so the list of keys stays short."""
    folded = typed.casefold()
    rows = []
    for text, detail in candidates:
        value = text.partition("=")[2]
        if value and "=" not in typed:
            continue
        if text.casefold().startswith(folded):
            rows.append(Suggestion(f"{name} {text}", text, detail))
    return rows


def set_candidates() -> Candidates:
    """The options of :set: the on / off options, then each debug_<key>= with its values."""
    rows = list(SET_OPTIONS)
    for key, (detail, values) in DEBUG_OPTIONS.items():
        rows.append((f"debug_{key}=", detail))
        rows.extend((f"debug_{key}={value}", value_detail) for value, value_detail in values)
    return rows


def _typed_folder(typed: str, base: Path | None) -> tuple[str, Path]:
    """The folder part of a typed path as text (`sub/`, or nothing) and as the folder to read."""
    head, slash, _ = typed.rpartition("/")
    folder = Path(head + slash).expanduser() if slash else Path()
    if base is not None and not folder.is_absolute():
        folder = base / folder
    return head + slash, folder


def _names_in(folder: Path, hidden: bool, suffixes: tuple[str, ...] | None) -> tuple[list[str], list[str]]:
    """The folder names and the file names (those that end in one of `suffixes`) in `folder`, sorted."""
    with os.scandir(folder) as entries:
        visible = [entry for entry in entries if hidden or not entry.name.startswith(".")]
        folders = [entry.name for entry in visible if entry.is_dir()]
        files = [entry.name for entry in visible if suffixes and not entry.is_dir() and entry.name.endswith(suffixes)]
    return sorted(folders, key=str.casefold), sorted(files, key=str.casefold)


def path_candidates(typed: str, suffixes: tuple[str, ...] | None, base: Path | None = None) -> Candidates:
    """The folders (with a / at the end) and the files in the folder that `typed` points into.
    `suffixes` None: folders only. Hidden names show only when the typed name starts with a dot."""
    shown, folder = _typed_folder(typed, base)
    try:
        folders, files = _names_in(folder, typed[len(shown):].startswith("."), suffixes)
    except OSError:
        return []
    rows = [(f"{shown}{name}/", "folder") for name in folders] + [(f"{shown}{name}", "") for name in files]
    return rows[:MAX_FILES]


def buffer_candidates(app) -> Candidates:
    """The open notebooks by name; one with a name that is not unique goes by its number."""
    workspace = app.workspace
    shown = set(workspace.panes())
    names = [pane.name for pane in workspace.buffers]
    rows = []
    for number, pane in enumerate(workspace.buffers, 1):
        state = "this window" if pane is workspace.active else "shown" if pane in shown else "hidden"
        changed = " · unsaved changes" if pane.dirty else ""
        key = pane.name if names.count(pane.name) == 1 else str(number)
        rows.append((key, f"buffer {number} · {state}{changed}"))
    return rows


def _save_folder(app, command: str, typed: str) -> Path | None:
    """The scratchpad folder, when `command` saves an untitled notebook under a name with no folder of its own."""
    if command not in SAVE_COMMANDS or app.pane.path is not None:
        return None
    from . import scratchpad

    return None if scratchpad.names_its_own_folder(typed) else scratchpad.folder()


def argument_candidates(app, command: str, typed: str) -> Candidates:
    """What the command `command` (its first name in ex_commands) takes after the space."""
    if command in STATIC_ARGUMENTS:
        return STATIC_ARGUMENTS[command]
    if command == "set":
        return set_candidates()
    if command == "theme":
        return [(name, "the current theme" if name == app.theme else "") for name in sorted(app.available_themes)]
    if command in BUFFER_COMMANDS:
        return buffer_candidates(app)
    if command in NOTEBOOK_FILE_COMMANDS:
        from .explorer import NOTEBOOK_SUFFIXES

        return path_candidates(typed, tuple(NOTEBOOK_SUFFIXES), _save_folder(app, command, typed))
    if command in FOLDER_COMMANDS:
        return path_candidates(typed, None)
    return []


def suggest(line: str, commands: list[tuple[tuple[str, ...], str]], arguments: Callable[[str, str], Candidates]) -> list[Suggestion]:
    """The rows for the text in the ':' prompt. Before a space: command names (after a range,
    only the editor's line commands). After a command and a space: its arguments. A pattern
    command in progress (:s/old/...) and a cell number give nothing."""
    span, rest = split_range(line.lstrip())
    if _NAME.match(rest):
        if span:
            return command_suggestions(rest, EDITOR_COMMANDS, span)
        return command_suggestions(rest, commands + EDITOR_COMMANDS)
    name, space, typed = rest.partition(" ")
    if span or not space:
        return []
    command = next((names[0] for names, _ in commands if name in names), None)
    if command is None:
        return []
    typed = typed.lstrip()
    return argument_suggestions(name, typed, arguments(command, typed))


def suggester(app) -> Callable[[str], list[Suggestion]]:
    """The function the ':' prompt calls with its text. The command list is read once per prompt."""
    commands = [(names, description) for names, description, _ in app.ex_commands()]
    return lambda line: suggest(line, commands, lambda command, typed: argument_candidates(app, command, typed))

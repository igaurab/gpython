"""What a .py notebook keeps when another editor changed the file.

gpython finds the cells and the outputs of a .py file by the file's exact text (sidecar.py). A
file changed by VS Code, git or an agent has a new text, so nothing is found for it. This module
compares the new text with what gpython last stored for the same path:

carried_layout: where the cells of a plain script are now. Each line of the new text belongs to
the cell its old line was in. Lines changed or added inside a cell stay in that cell. Lines added
between cells, in place of lines from more than one cell, or in place of every line of a cell are
split into new cells the usual way (notebook.split_script). A cell with no lines left is gone.

carried_states: which run states (outputs, run number, run time) still have a cell. A cell with
the same source keeps its state. A cell whose source changed keeps it too, marked `outdated`: its
output is shown, and the cell is stale until it runs.

difflib is imported here, on the rare path, not at start-up.
"""

from __future__ import annotations

import ast
from difflib import SequenceMatcher

MOST_LINES = 20000
ALIKE = 0.4
ADDED = -1


def carried_layout(old_text: str, old_entries: list[dict], new_text: str) -> list[dict] | None:
    """Layout entries ({start, end, kind, hide_code}) for `new_text` that keep the cells of
    `old_entries`, or None when no cell of the old layout is left or a cell would not be whole code."""
    old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
    if len(old_lines) + len(new_lines) > MOST_LINES:
        return None
    try:
        owners = _line_owners(_old_owners(old_lines, old_entries), old_lines, new_lines)
    except (KeyError, TypeError, ValueError):
        return None
    _join_touching_lines(owners, new_lines)
    kept = _kept_ranges(owners, new_lines)
    if not kept:
        return None
    entries: list[dict] = []
    covered = 0
    for cell, (start, end) in sorted(kept.items(), key=lambda item: item[1]):
        entries.extend(_fresh_entries(new_lines, covered, start))
        entries.append(_kept_entry(new_lines, start, end, old_entries[cell]))
        covered = end
    entries.extend(_fresh_entries(new_lines, covered, len(new_lines)))
    return entries if _code_is_whole(new_text, new_lines, entries) else None


def _old_owners(old_lines: list[str], entries: list[dict]) -> list[int | None]:
    owners: list[int | None] = [None] * len(old_lines)
    for cell, entry in enumerate(entries):
        start, end = int(entry["start"]), int(entry["end"])
        if not 0 <= start <= end <= len(old_lines):
            raise ValueError("layout outside the text")
        owners[start:end] = [cell] * (end - start)
    return owners


def _line_owners(old_owners: list[int | None], old_lines: list[str], new_lines: list[str]) -> list[int | None]:
    """For each new line: the old cell it belongs to, ADDED for a line of no old cell, None for a line between cells."""
    owners: list[int | None] = [None] * len(new_lines)
    opcodes = SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes()
    still_there = _cells_with_a_line_left(opcodes, old_owners, old_lines)
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            owners[j1:j2] = old_owners[i1:i2]
        elif tag == "replace":
            owners[j1:j2] = [_edited_cell(old_owners[i1:i2], still_there)] * (j2 - j1)
        elif tag == "insert":
            owners[j1:j2] = [_cell_around(old_owners, i1)] * (j2 - j1)
    return owners


def _cells_with_a_line_left(opcodes: list[tuple], old_owners: list[int | None], old_lines: list[str]) -> set[int | None]:
    unchanged = [i for tag, i1, i2, _, _ in opcodes if tag == "equal" for i in range(i1, i2)]
    return {old_owners[i] for i in unchanged if old_lines[i].strip()}


def _edited_cell(replaced_owners: list[int | None], still_there: set[int | None]) -> int:
    """The one cell the replaced lines were in, when that cell has other lines left; else ADDED."""
    cells = {cell for cell in replaced_owners if cell is not None}
    return cells.pop() if len(cells) == 1 and cells <= still_there else ADDED


def _cell_around(old_owners: list[int | None], at: int) -> int:
    """The cell that has the old lines before and after position `at`, else ADDED."""
    if not 0 < at < len(old_owners):
        return ADDED
    before, after = old_owners[at - 1], old_owners[at]
    return before if before is not None and before == after else ADDED


def _join_touching_lines(owners: list[int | None], lines: list[str]) -> None:
    """Added lines with no blank line between them and a cell belong to that cell."""
    for j in range(1, len(lines)):
        _join(owners, lines, j, j - 1)
    for j in range(len(lines) - 2, -1, -1):
        _join(owners, lines, j, j + 1)


def _join(owners: list[int | None], lines: list[str], line: int, neighbour: int) -> None:
    touching = lines[line].strip() and lines[neighbour].strip()
    if owners[line] == ADDED and touching and owners[neighbour] not in (None, ADDED):
        owners[line] = owners[neighbour]


def _kept_ranges(owners: list[int | None], lines: list[str]) -> dict[int, tuple[int, int]]:
    """The lines (start, end) each old cell still has, first to last line with text."""
    ranges: dict[int, tuple[int, int]] = {}
    for j, cell in enumerate(owners):
        if cell is None or cell == ADDED or not lines[j].strip():
            continue
        start, _ = ranges.get(cell, (j, j))
        ranges[cell] = (start, j + 1)
    return ranges


def _kept_entry(lines: list[str], start: int, end: int, old: dict) -> dict:
    from .notebook import docstring_value

    kind = old.get("kind", "code")
    if kind == "markdown" and docstring_value("\n".join(lines[start:end])) is None:
        kind = "code"
    return {"start": start, "end": end, "kind": kind, "hide_code": bool(old.get("hide_code")) and kind == "code"}


def _fresh_entries(lines: list[str], start: int, end: int) -> list[dict]:
    """Entries for the lines of no old cell in lines[start:end], split as a script opened for the first time."""
    from .notebook import split_script

    region = lines[start:end]
    if not any(line.strip() for line in region):
        return []
    entries = []
    at = start
    for cell in split_script("\n".join(region)):
        chunk = cell.raw if cell.raw is not None else cell.source
        if not chunk.strip():
            continue
        while at < end and not lines[at].strip():
            at += 1
        height = chunk.count("\n") + 1
        entries.append({"start": at, "end": at + height, "kind": cell.kind, "hide_code": False})
        at += height
    return entries


def _code_is_whole(text: str, lines: list[str], entries: list[dict]) -> bool:
    """False when the file is valid Python but a code cell alone is not: a cell edge fell inside a statement."""
    if not _parses(text):
        return True
    return all(_parses("\n".join(lines[e["start"] : e["end"]])) for e in entries if e["kind"] == "code")


def _parses(code: str) -> bool:
    try:
        ast.parse(code)
    except (SyntaxError, ValueError):
        return False
    return True


def carried_states(old_sources: list[str], old_states: list[dict], new_sources: list[str]) -> list[dict] | None:
    """One run state per new cell: its old state when the source is the same, its old state with
    `outdated` when the source changed, else {}. None when no cell keeps a state."""
    old = [str(source).strip("\n") for source in old_sources]
    new = [source.strip("\n") for source in new_sources]
    states: list[dict] = [{} for _ in new]
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        for i, j in _same_cells(tag, old, new, (i1, i2), (j1, j2)):
            states[j] = _state_now(old_states[i], changed=tag == "replace")
    return states if any(states) else None


def _same_cells(tag: str, old: list[str], new: list[str], old_range: tuple[int, int], new_range: tuple[int, int]) -> list[tuple[int, int]]:
    if tag == "equal":
        return list(zip(range(*old_range), range(*new_range)))
    return _changed_pairs(old, new, old_range, new_range) if tag == "replace" else []


def _state_now(state: object, changed: bool) -> dict:
    if not isinstance(state, dict) or not state:
        return {}
    return {**state, "outdated": True} if changed else state


def _changed_pairs(old: list[str], new: list[str], old_range: tuple[int, int], new_range: tuple[int, int]) -> list[tuple[int, int]]:
    """(old, new) cells that are one cell before and after an edit, in order. One cell in place of
    one is that cell; among more, each new cell takes the old cell most like it, when alike enough."""
    (i1, i2), (j1, j2) = old_range, new_range
    if i2 - i1 == 1 and j2 - j1 == 1:
        return [(i1, j1)]
    pairs = []
    first = i1
    for j in range(j1, j2):
        scores = [(SequenceMatcher(None, old[i], new[j]).ratio(), -i) for i in range(first, i2)]
        if not scores:
            break
        score, negative = max(scores)
        if score >= ALIKE:
            pairs.append((-negative, j))
            first = -negative + 1
    return pairs

"""Ex commands on a cell that has no editor on screen.

Over the mounted-cells budget most cells are `CellState`s without a widget (cell.py). An
ex range that spans the notebook (`:%s`, `:g`, `:%d`, `:%>`) must still reach them, so
`SleepingLines` gives such a cell the handful of operations the ex commands use on a
`VimTextArea` (`substitute_rows`, `matching_rows`, `global_command`, `line_command`,
`document.get_line`, `document.line_count`), working on the state's text. A change replaces
the state's source and drops its undo history, which the editor cannot replay from text.
`:normal` needs a real editor and is refused.
"""

from __future__ import annotations

import re

from .cell import CellState
from .vim import INDENT, parse_line_command, parse_substitute, prepare_substitute


class SleepingLines:
    def __init__(self, state: CellState, pane) -> None:
        self.state = state
        self.pane = pane
        self.lines = state.source.split("\n")

    @property
    def document(self) -> SleepingLines:
        return self

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def get_line(self, row: int) -> str:
        return self.lines[row]

    def _commit(self) -> None:
        self.state.replace_source("\n".join(self.lines))
        self.pane.source_changed()

    def substitute_rows(self, top: int, bottom: int, regex: re.Pattern, build, flags: str) -> tuple[int, int, int | None]:
        count = changed = 0
        new_lines = []
        for row in range(top, bottom + 1):
            new, made = regex.subn(build, self.lines[row], count=0 if "g" in flags else 1)
            count += made
            changed += bool(made)
            new_lines.append(new)
        if count and "n" not in flags:
            self.lines[top : bottom + 1] = "\n".join(new_lines).split("\n")
            self._commit()
        return count, changed, None

    def matching_rows(self, top: int, bottom: int, regex: re.Pattern, invert: bool = False) -> list[int]:
        return [row for row in range(top, bottom + 1) if bool(regex.search(self.lines[row])) != invert]

    def global_command(self, rows: list[int], command: str) -> None:
        substitute = parse_substitute(command)
        prepared = prepare_substitute(*substitute[1:]) if substitute is not None else None
        line_command = None if substitute is not None else parse_line_command(command)
        if prepared is None and line_command is None:
            raise ValueError(f":g cannot run this command: {command}")
        shift = 0
        for row in rows:
            current = row + shift
            if not 0 <= current < len(self.lines):
                break
            before = len(self.lines)
            if prepared is not None:
                self.substitute_rows(current, current, *prepared)
            else:
                _, name, arg = line_command
                destination = self.line_command(current, current, name, arg)
                if name in ("t", "co") and destination is not None and destination > current:
                    continue
                if name in ("m", "move"):
                    if destination is not None and destination > current:
                        shift -= 1
                    continue
            shift += len(self.lines) - before

    def line_command(self, top: int, bottom: int, name: str, arg: str) -> int | None:
        last = len(self.lines) - 1
        top, bottom = max(0, top), min(last, bottom)
        if name in ("d", "delete"):
            del self.lines[top : bottom + 1]
            if not self.lines:
                self.lines = [""]
        elif name in ("j", "join"):
            self._join(top, max(bottom, top + 1))
        elif name[0] in "<>":
            self._shift(top, bottom, len(name), name[0] == ">")
        elif name in ("m", "move", "t", "co"):
            return self._move_or_copy(top, bottom, name, arg)
        elif name in ("norm", "normal"):
            raise ValueError(f":normal only runs on cells on screen; {self.state.label} is not")
        else:
            raise ValueError(f"Unknown command: {name}")
        self._commit()
        return None

    def _join(self, top: int, bottom: int) -> None:
        bottom = min(bottom, len(self.lines) - 1)
        if bottom <= top:
            return
        joined = self.lines[top]
        for row in range(top + 1, bottom + 1):
            line = self.lines[row].lstrip()
            sep = " " if joined and line and not joined.endswith((" ", "\t")) and not line.startswith(")") else ""
            joined += sep + line
        self.lines[top : bottom + 1] = [joined]

    def _shift(self, top: int, bottom: int, n: int, right: bool) -> None:
        width = n * len(INDENT)
        for row in range(top, bottom + 1):
            line = self.lines[row]
            if right:
                self.lines[row] = INDENT * n + line if line.strip() else line
            else:
                self.lines[row] = line[min(width, len(line) - len(line.lstrip(" "))) :]

    def _move_or_copy(self, top: int, bottom: int, name: str, arg: str) -> int:
        block = self.lines[top : bottom + 1]
        after = self._address(arg)
        if name in ("m", "move"):
            if top < after <= bottom:
                raise ValueError("Cannot move lines into themselves")
            rest = self.lines[:top] + self.lines[bottom + 1 :]
            at = after if after <= top else after - len(block)
            self.lines = rest[:at] + block + rest[at:]
        else:
            at = after
            self.lines = self.lines[:at] + block + self.lines[at:]
        self._commit()
        return at

    def _address(self, text: str) -> int:
        text = text.strip() or "."
        if text == ".":
            return 1
        if text == "$":
            return len(self.lines)
        if text.isdigit():
            return min(int(text), len(self.lines))
        raise ValueError(f"Incorrect line number or range: {text}")

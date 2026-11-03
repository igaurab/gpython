"""The debugger's state on gpython's side and the pictures drawn from it.

The kernel (kernel_side.Debugger) sends one JSON payload per stop: the stop's record, the stack,
the locals, the watches, a reply to the last private command, and the records made since the
previous stop. DebugSession keeps a mirror of those records so h / l can travel through what
already ran, and the render_* functions turn a record into Rich text: the loop stack, pointer
strips over sequences, grids and dict/set changes. Everything here is plain data in, Text out,
so the tests check it without a terminal. Imported on the first stop only.
"""

from __future__ import annotations

from rich.text import Text

BAR = 8
CHANGED = "bold black on yellow"
POINTED = "bold reverse"
WARN = "bold red"
ADDED = "bold green"
REMOVED = "red strike"


class DebugSession:
    """One debugging session as gpython sees it: the last payload, the mirrored records, the
    position of h / l time travel, and each local's values at the previous stops."""

    def __init__(self) -> None:
        self.session = None
        self.payload: dict = {}
        self.records: dict[int, dict] = {}
        self.total = 0
        self.first = 0
        self.view: int | None = None
        self.previous_now: dict | None = None
        self.values: dict[tuple[str, str], list[str]] = {}
        self.running = False

    def update(self, payload: dict, keep_view: bool = False) -> None:
        """A new stop: merge its records, remember the previous stop, extend each local's history.
        `keep_view` is for answers that did not run the program (a value, more history)."""
        if payload.get("session") != self.session:
            self.__init__()
            self.session = payload.get("session")
        elif not keep_view:
            self.previous_now = (self.payload or {}).get("now")
        self.payload = payload
        self.running = False
        history = payload.get("history") or {}
        self.total = history.get("total", self.total)
        self.first = history.get("first", self.first)
        for batch in history.get("batches", []):
            for offset, record in enumerate(batch.get("records", [])):
                self.records[batch["start"] + offset] = record
        for index in [i for i in self.records if i < self.first]:
            del self.records[index]
        now = payload.get("now") or {}
        if not keep_view:
            self.view = None
        func = now.get("func", "")
        if keep_view:
            return
        for name, _, shown, *_ in payload.get("locals", []):
            seen = self.values.setdefault((func, name), [])
            if not seen or seen[-1] != shown:
                seen.append(shown)
                del seen[:-12]

    @property
    def live(self) -> bool:
        return self.view is None

    @property
    def record(self) -> dict:
        """The record on screen: the stop, or the one h / l travelled to."""
        if self.view is None:
            return self.payload.get("now") or {}
        return self.records.get(self.view) or {}

    @property
    def before(self) -> dict | None:
        """What `record` is compared with for the change highlights."""
        if self.view is None:
            return self.previous_now
        return self.records.get(self.view - 1)

    def travel(self, step: int) -> tuple[int, int] | None:
        """Move through the recorded history; returns a (start, stop) range to fetch when the
        mirror lacks the record, else None. Moving forward past the stop is live again."""
        if not self.total:
            return None
        live = self.payload.get("index")
        end = live if live is not None else self.total
        target = (self.view if self.view is not None else end) + step
        if target >= end:
            self.view = None
            return None
        self.view = target = max(self.first, target)
        if target not in self.records:
            return max(self.first, target - 299), target + 1
        return None

    def missing(self) -> tuple[int, int] | None:
        """The newest range of records the mirror lacks, for the timeline."""
        gaps = [i for i in range(self.first, self.total) if i not in self.records]
        if not gaps:
            return None
        stop = gaps[-1] + 1
        return max(self.first, stop - 300), stop

    def history_of(self, name: str) -> list[str]:
        func = (self.payload.get("now") or {}).get("func", "")
        return self.values.get((func, name), [])


def bar(k: int | None, n: int | None, width: int = BAR) -> str:
    if k is None or not n:
        return ""
    filled = max(0, min(width, round(width * min(k, n) / n)))
    return "█" * filled + "░" * (width - filled)


def render_loops(record: dict, width: int = 78) -> list[Text]:
    """One line per active loop, outermost first: header, loop variables, k/n and a bar."""
    lines = []
    for depth, loop in enumerate(record.get("loops", [])):
        indent = "  " * depth
        header = loop["header"]
        room = max(12, width // 2 - len(indent))
        if len(header) > room:
            header = header[: room - 1] + "…"
        names = "  ".join(f"{name}={value}" for name, value in loop.get("vars", []))
        k, n = loop.get("k"), loop.get("n")
        if k is None:
            where = "?"
        elif n is None:
            where = f"#{k}"
        else:
            where = f"{k}/{n}"
        over = n is not None and k is not None and k > n
        text = Text.assemble(
            indent, (header.ljust(room), "bold"), "  ", (names, "cyan"), "  ",
            (where, WARN if over else ""), " ", (bar(k, n), "green" if not over else WARN),
        )
        text.truncate(width)
        lines.append(text)
    return lines


def seq_warnings(seq: dict, before: dict | None) -> list[str]:
    """Index pointers that are out of range, count from the end, or crossed since the last stop."""
    name, length = seq["name"], seq["len"]
    found = []
    for label, value in seq.get("ptrs", []):
        if value >= length:
            found.append(f"{label} = {value} is past the end of {name} (len {length})")
        elif value < -length:
            found.append(f"{label} = {value} is before the start of {name} (len {length})")
        elif value < 0:
            found.append(f"{label} = {value} counts from the end of {name}: {name}[{value}] is {name}[{length + value}]")
    if before is not None:
        old = dict((label, value) for label, value in before.get("ptrs", []))
        now = dict((label, value) for label, value in seq.get("ptrs", []))
        labels = [label for label in now if label in old]
        for i, a in enumerate(labels):
            for b in labels[i + 1:]:
                if (old[a] - old[b]) * (now[a] - now[b]) < 0:
                    found.append(f"{a} and {b} crossed: {a}={now[a]}, {b}={now[b]}")
    return found


def render_seq(seq: dict, before: dict | None, width: int = 78) -> list[Text]:
    """`nums  [-4][-1][ 0]` with index labels under it and an ↑ for each pointer; a pointer past
    the end is drawn after the last box in red, elements changed since `before` are highlighted."""
    name, items, start, length = seq["name"], list(seq["items"]), seq["start"], seq["len"]
    pointers = seq.get("ptrs", [])
    box = max([len(item) for item in items] + [len(str(start + len(items) - 1)) if items else 1, 1])
    box = min(box, 6)
    label = name if len(name) <= 10 else name[:9] + "…"
    prefix = len(label) + 2
    fit = max(1, (width - prefix - 4) // (box + 2))
    if len(items) > fit:
        inside = [v - start for _, v in pointers if 0 <= v - start < len(items)] or [0]
        cut = max(0, min(min(inside) - 1, len(items) - fit))
        if max(inside) >= cut + fit:
            cut = max(0, max(inside) - fit + 2)
        items = items[cut: cut + fit]
        start += cut
    stop = start + len(items)
    old = {}
    if before is not None and before.get("name") == name:
        old = {before["start"] + i: item for i, item in enumerate(before["items"])}
    strip = Text(label.ljust(prefix), style="bold")
    strip.append("…" if start > 0 else " ")
    for offset, item in enumerate(items):
        index = start + offset
        changed = index in old and old[index] != item
        strip.append("[", style="dim")
        strip.append(item.rjust(box)[-box:], style=CHANGED if changed else "")
        strip.append("]", style="dim")
    strip.append("…" if stop < length else " ")
    indexes = Text(" " * (prefix + 1), style="dim")
    for offset in range(len(items)):
        indexes.append(str(start + offset).center(box + 2), style="dim")
    lines = [strip, indexes]
    columns: list[tuple[int, str, str]] = []
    beyond = []
    for pointer, value in pointers:
        if start <= value < stop:
            columns.append((prefix + 1 + (value - start) * (box + 2) + (box + 2) // 2, pointer, "bold cyan"))
        elif value == length and stop == length:
            columns.append((prefix + 1 + len(items) * (box + 2) + 1, pointer, WARN))
        else:
            beyond.append((pointer, value))
    rows: list[list[tuple[int, str, str]]] = []
    for column, pointer, style in sorted(columns):
        for row in rows:
            if row[-1][0] + len(row[-1][1]) + 2 <= column:
                row.append((column, pointer, style))
                break
        else:
            rows.append([(column, pointer, style)])
    for row in rows:
        line = Text()
        for column, pointer, style in row:
            line.append(" " * max(0, column - len(line)))
            line.append("↑" + pointer, style=style)
        lines.append(line)
    if beyond:
        line = Text(" " * (prefix + 1))
        for pointer, value in beyond:
            out = value >= length or value < -length
            line.append(f"{pointer}={value} " + ("(out of range) " if out else "→ "), style=WARN if out else "cyan")
        lines.append(line)
    for warning in seq_warnings(seq, before):
        lines.append(Text("⚠ " + warning, style=WARN))
    for line in lines:
        line.truncate(width)
    return lines


def render_grid(grid: dict, before: dict | None, width: int = 78) -> list[Text]:
    """A 2-D list or array as a small table, the [i][j] cell reversed, changed cells highlighted."""
    cells, row0, col0 = grid["cells"], grid["row0"], grid["col0"]
    pointed = {(r, c) for _, _, r, c in grid.get("ptrs", [])}
    old = {}
    if before is not None and before.get("name") == grid["name"]:
        for r, line in enumerate(before["cells"]):
            for c, item in enumerate(line):
                old[(before["row0"] + r, before["col0"] + c)] = item
    box = max([len(item) for line in cells for item in line] + [len(str(col0 + 9)), 1])
    box = min(box, 5)
    side = len(str(row0 + len(cells))) + 1
    title = Text(f"{grid['name']}  {grid['rows']}×{grid['cols']}", style="bold")
    labels = ", ".join(f"[{r}][{c}]" for r, c, _, _ in grid.get("ptrs", []))
    if labels:
        title.append("  " + labels, style="cyan")
    lines = [title]
    header = Text(" " * side, style="dim")
    for c in range(len(cells[0]) if cells else 0):
        header.append(str(col0 + c).rjust(box + 1), style="dim")
    lines.append(header)
    for r, line in enumerate(cells):
        text = Text(str(row0 + r).rjust(side - 1) + " ", style="dim")
        for c, item in enumerate(line):
            key = (row0 + r, col0 + c)
            style = POINTED if key in pointed else CHANGED if key in old and old[key] != item else ""
            text.append(" ")
            text.append(item.rjust(box)[-box:], style=style)
        lines.append(text)
    for r_label, c_label, r, c in grid.get("ptrs", []):
        if not (0 <= r < grid["rows"]) or not (0 <= c < grid["cols"]):
            lines.append(Text(f"⚠ {grid['name']}[{r_label}][{c_label}] = [{r}][{c}] is outside {grid['rows']}×{grid['cols']}", style=WARN))
    for line in lines:
        line.truncate(width)
    return lines


def map_changes(current: dict, before: dict | None) -> tuple[list[str], list[str], list[str]]:
    """Keys added, removed and changed since `before` (by their shown text)."""
    now = dict((k, v) for k, v in current["items"])
    if before is None or before.get("name") != current["name"]:
        return [], [], []
    old = dict((k, v) for k, v in before["items"])
    added = [k for k in now if k not in old]
    removed = [k for k in old if k not in now]
    changed = [k for k in now if k in old and old[k] != now[k]]
    return added, removed, changed


def render_map(current: dict, before: dict | None, width: int = 78) -> list[Text]:
    added, removed, changed = map_changes(current, before)
    text = Text(f"{current['name']}  ", style="bold")
    text.append(f"{current['kind']} len {current['len']}  ", style="dim")
    for key, value in current["items"]:
        style = ADDED if key in added else CHANGED if key in changed else ""
        text.append(key if value is None else f"{key}: {value}", style=style)
        text.append("  ")
    for key in removed:
        text.append(key, style=REMOVED)
        text.append("  ")
    summary = []
    if added:
        summary.append(f"+{len(added)}")
    if changed:
        summary.append(f"~{len(changed)}")
    if removed:
        summary.append(f"-{len(removed)}")
    lines = [text]
    if summary:
        lines.append(Text("  " + " ".join(summary) + " since the last stop", style="dim"))
    for line in lines:
        line.truncate(width)
    return lines


def _named(items: list[dict], name: str) -> dict | None:
    return next((item for item in items if item.get("name") == name), None)


def render_record(record: dict, before: dict | None, width: int = 78) -> list[Text]:
    """The whole picture of one moment: loops, then pointer strips, grids and maps."""
    before = before or {}
    lines = render_loops(record, width)
    if lines:
        lines.append(Text(""))
    for seq in record.get("seqs", []):
        lines += render_seq(seq, _named(before.get("seqs", []), seq["name"]), width)
    for grid in record.get("grids", []):
        lines += render_grid(grid, _named(before.get("grids", []), grid["name"]), width)
    for current in record.get("maps", []):
        lines += render_map(current, _named(before.get("maps", []), current["name"]), width)
    return lines


def record_warnings(record: dict, before: dict | None = None) -> list[str]:
    before = before or {}
    found = []
    for seq in record.get("seqs", []):
        found += seq_warnings(seq, _named(before.get("seqs", []), seq["name"]))
    for grid in record.get("grids", []):
        for r_label, c_label, r, c in grid.get("ptrs", []):
            if not (0 <= r < grid["rows"]) or not (0 <= c < grid["cols"]):
                found.append(f"{grid['name']}[{r_label}][{c_label}] out of range")
    return found


class Iteration:
    """One row of the timeline: an iteration of a loop, its last record, what changed, its children."""

    def __init__(self, loop: dict, k, first: dict) -> None:
        self.loop, self.k = loop, k
        self.first = self.last = first
        self.children: list[Iteration] = []
        self.warnings: list[str] = []

    @property
    def index(self) -> int:
        return self.last.get("i", 0)

    def changed(self) -> list[tuple[str, str, str]]:
        old = dict(self.first.get("vars", []))
        loop_names = {name for entry in self.last.get("loops", []) for name, _ in entry.get("vars", [])}
        return [
            (name, old.get(name, ""), value) for name, value in self.last.get("vars", [])
            if name not in loop_names and old.get(name) != value
        ]

    def label(self) -> Text:
        loop = next((entry for entry in self.last.get("loops", []) if entry["line"] == self.loop["line"]), self.loop)
        head = "  ".join(f"{name}={value}" for name, value in loop.get("vars", [])) or "…"
        text = Text.assemble((f"{self.k if self.k is not None else '?':>4} ", "dim"), (head.ljust(14), "cyan"))
        for name, old, new in self.changed()[:4]:
            change = f"{old}→{new}" if old else new
            text.append(f"  {name}: " + (change if len(change) <= 26 else change[:25] + "…"))
        if self.warnings:
            text.append("  ⚠ " + self.warnings[0], style=WARN)
        return text


def timeline(records: list[dict]) -> list[Iteration]:
    """Group recorded moments into iterations: outer iterations hold their inner iterations. A
    record's place is the (loop line, k) of each of its active loops; a loop at 0 (on its header,
    before its first iteration) belongs to its parent, and records outside any loop are left out.
    A warning in any moment marks its iteration and every parent."""
    roots: list[Iteration] = []
    current: list[Iteration] = []
    previous = None
    for record in records:
        loops = record.get("loops", [])
        loops = loops[: next((i for i, loop in enumerate(loops) if loop.get("k") == 0), len(loops))]
        warnings = record_warnings(record, previous)
        previous = record
        if not loops:
            current = []
            continue
        siblings = roots
        for depth, loop in enumerate(loops):
            node = current[depth] if depth < len(current) else None
            if node is None or (node.loop["line"], node.k) != (loop["line"], loop.get("k")):
                node = Iteration(loop, loop.get("k"), record)
                siblings.append(node)
                del current[depth:]
                current.append(node)
            node.last = record
            for warning in warnings:
                if warning not in node.warnings:
                    node.warnings.append(warning)
            siblings = node.children
        del current[len(loops):]
    return roots

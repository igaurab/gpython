"""Tabs, splits and buffers: vim's tab pages, windows and buffers for notebook panes.

The Workspace widget holds every open NotebookPane as a direct child; that list is the
buffer list (vim's :ls). Where each pane is drawn comes from a small tree per tab (Leaf =
one pane, Branch = side-by-side or stacked children with weights), turned into screen
regions by WindowLayout. A pane in no tab is a hidden buffer: still mounted (display:
none), its cells, outputs and kernel intact, until :bd deletes it. Keeping the tree
outside the DOM means a split, a close or a buffer switch never re-mounts a pane.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.text import Text
from textual import events
from textual.geometry import NULL_OFFSET, NULL_SPACING, Region, Size
from textual.layout import ArrangeResult, Layout, WidgetPlacement
from textual.widget import Widget
from textual.widgets import Static

from .dom import forget_removed
from .pane import NotebookPane

GAP = 1
MIN_SIZE = 8


@dataclass
class Leaf:
    pane: NotebookPane
    weight: float = 1.0


@dataclass
class Branch:
    row: bool
    children: list[Node]
    weight: float = 1.0


Node = Leaf | Branch


@dataclass
class Tab:
    root: Node
    active: NotebookPane | None = None
    rects: dict[NotebookPane, Region] = field(default_factory=dict)


def leaves(node: Node) -> list[Leaf]:
    if isinstance(node, Leaf):
        return [node]
    return [leaf for child in node.children for leaf in leaves(child)]


def panes_of(node: Node) -> list[NotebookPane]:
    return [leaf.pane for leaf in leaves(node)]


def find(node: Node, pane: NotebookPane, parent: Branch | None = None) -> tuple[Leaf, Branch | None] | None:
    if isinstance(node, Leaf):
        return (node, parent) if node.pane is pane else None
    for child in node.children:
        found = find(child, pane, node)
        if found:
            return found
    return None


def split_leaf(root: Node, pane: NotebookPane, new: NotebookPane, row: bool, after: bool) -> Node:
    """Put `new` next to `pane` (side by side when `row`), halving its space. Returns the new root."""
    found = find(root, pane)
    if found is None:
        return root
    leaf, parent = found
    fresh = Leaf(new)
    if parent is not None and parent.row == row:
        index = parent.children.index(leaf)
        fresh.weight = leaf.weight = leaf.weight / 2
        parent.children.insert(index + 1 if after else index, fresh)
        return root
    branch = Branch(row, [leaf, fresh] if after else [fresh, leaf], weight=leaf.weight)
    leaf.weight = fresh.weight = 1.0
    if parent is None:
        return branch
    parent.children[parent.children.index(leaf)] = branch
    return root


def remove_leaf(root: Node, pane: NotebookPane) -> Node | None:
    """Take `pane` out of the tree; its space goes to its siblings. None when the tree is empty."""
    found = find(root, pane)
    if found is None:
        return root
    leaf, parent = found
    if parent is None:
        return None
    parent.children.remove(leaf)
    if len(parent.children) == 1:
        only = parent.children[0]
        only.weight = parent.weight
        grand = _parent_of(root, parent)
        if grand is None:
            return only
        grand.children[grand.children.index(parent)] = only
        if isinstance(only, Branch) and only.row == grand.row:
            index = grand.children.index(only)
            total = sum(c.weight for c in only.children) or 1.0
            for child in only.children:
                child.weight = only.weight * child.weight / total
            grand.children[index : index + 1] = only.children
    return root


def _parent_of(root: Node, target: Branch) -> Branch | None:
    if isinstance(root, Leaf):
        return None
    for child in root.children:
        if child is target:
            return root
        if isinstance(child, Branch) and (found := _parent_of(child, target)) is not None:
            return found
    return None


def arrange(node: Node, region: Region, out: dict[NotebookPane, Region]) -> None:
    """Split `region` among the node's children by weight; leaves get their rectangles."""
    if isinstance(node, Leaf):
        out[node.pane] = region
        return
    children = node.children
    gap = GAP if node.row else 0
    total = (region.width if node.row else region.height) - gap * (len(children) - 1)
    weights = [max(c.weight, 0.05) for c in children]
    whole = sum(weights)
    sizes = [max(1, int(total * w / whole)) for w in weights]
    sizes[-1] += total - sum(sizes)
    pos = region.x if node.row else region.y
    for child, size in zip(children, sizes):
        if node.row:
            arrange(child, Region(pos, region.y, size, region.height), out)
        else:
            arrange(child, Region(region.x, pos, region.width, size), out)
        pos += size + gap


class WindowLayout(Layout):
    """Places the active tab's panes at the rectangles computed from its split tree."""

    name = "windows"

    def arrange(self, parent: Widget, children: list[Widget], size: Size, greedy: bool = True) -> ArrangeResult:
        workspace: Workspace = parent
        tab = workspace.tab
        placements: list[WidgetPlacement] = []
        if tab is None:
            return placements
        rects: dict[NotebookPane, Region] = {}
        arrange(tab.root, Region(0, 0, size.width, size.height), rects)
        tab.rects = rects
        for widget in children:
            region = rects.get(widget)
            if region is not None:
                placements.append(WidgetPlacement(region, NULL_OFFSET, NULL_SPACING, widget, 0, False, False, False))
        return placements


class TabBar(Static):
    """vim's tabline: one entry per tab (click to switch), then the hidden buffers (click to show)."""

    def __init__(self) -> None:
        super().__init__(id="tabbar")
        self._ranges: list[tuple[int, int, str, int]] = []

    def show(self, tabs: list[Tab], current: int, hidden: list[tuple[int, NotebookPane]]) -> None:
        text = Text()
        self._ranges = []
        for i, tab in enumerate(tabs):
            panes = panes_of(tab.root)
            pane = tab.active if tab.active in panes else panes[0]
            dirty = any(p.dirty for p in panes)
            count = f"{len(panes)} " if len(panes) > 1 else ""
            label = f" {i + 1}: {count}{pane.name}{' •' if dirty else ''} "
            start = len(text)
            text.append(label, "bold reverse" if i == current else "dim")
            self._ranges.append((start, len(text), "tab", i))
            text.append(" ")
        if hidden:
            text.append("  hidden:", "dim italic")
            for number, pane in hidden:
                text.append(" ")
                start = len(text)
                text.append(f"{number} {pane.name}{' •' if pane.dirty else ''}", "dim")
                self._ranges.append((start, len(text), "buffer", number))
        self.update(text, layout=False)

    def on_click(self, event: events.Click) -> None:
        for start, end, kind, index in self._ranges:
            if start <= event.x < end:
                if kind == "tab":
                    self.app.tab_switch(index + 1, 0)
                else:
                    self.app.run_worker(self.app.show_buffer(index))
                return


class Workspace(Widget):
    """All open notebook panes; shows the current tab's panes in their split layout."""

    def __init__(self) -> None:
        super().__init__(id="workspace")
        self.tabs: list[Tab] = []
        self.index = 0
        self.buffers: list[NotebookPane] = []
        self._layout = WindowLayout()
        self.can_focus = False

    @property
    def layout(self) -> Layout:
        return self._layout

    @property
    def tab(self) -> Tab | None:
        return self.tabs[self.index] if self.tabs else None

    @property
    def active(self) -> NotebookPane | None:
        tab = self.tab
        if tab is None:
            return None
        panes = panes_of(tab.root)
        if tab.active not in panes:
            tab.active = panes[0] if panes else None
        return tab.active

    def panes(self) -> list[NotebookPane]:
        """Every pane shown in some tab (vim's active buffers), in tab then window order."""
        return [pane for tab in self.tabs for pane in panes_of(tab.root)]

    def hidden(self) -> list[NotebookPane]:
        """Buffers in no window (vim's hidden buffers)."""
        shown = set(self.panes())
        return [pane for pane in self.buffers if pane not in shown]

    def number(self, pane: NotebookPane) -> int:
        """The buffer number (1-based, in opening order)."""
        return self.buffers.index(pane) + 1

    def buffer(self, key: str) -> NotebookPane | None:
        """`:b 3` or `:b name` (a unique substring match on the file name / path)."""
        key = key.strip()
        if key.isdigit():
            n = int(key)
            return self.buffers[n - 1] if 1 <= n <= len(self.buffers) else None
        matches = [p for p in self.buffers if key.lower() in p.title.lower()]
        exact = [p for p in matches if p.name == key]
        if exact:
            return exact[0]
        return matches[0] if len(matches) == 1 else None

    def tab_panes(self) -> list[NotebookPane]:
        return panes_of(self.tab.root) if self.tab else []

    def tab_of(self, pane: NotebookPane) -> int | None:
        for i, tab in enumerate(self.tabs):
            if pane in panes_of(tab.root):
                return i
        return None


    async def _adopt(self, pane: NotebookPane) -> None:
        """Mount a new pane (a hidden buffer is already mounted) and register it as a buffer."""
        if pane not in self.buffers:
            self.buffers.append(pane)
        if pane.parent is not self:
            await self.mount(pane)
        pane.display = True

    async def add(self, pane: NotebookPane, how: str = "tab", anchor: NotebookPane | None = None) -> None:
        """Show `pane` in a new tab, or as a split/vsplit of `anchor` (default: the active pane).

        `pane` may be a hidden buffer (then it is simply shown again).
        """
        anchor = anchor or self.active
        if how == "tab" or anchor is None or not self.tabs:
            self.tabs.insert(self.index + 1 if self.tabs else 0, Tab(Leaf(pane), pane))
            await self._adopt(pane)
            self.switch(self.index + 1 if len(self.tabs) > 1 else 0)
            return
        tab = self.tabs[self.tab_of(anchor)]
        row = how == "vsplit"
        tab.root = split_leaf(tab.root, anchor, pane, row, after=True)
        await self._adopt(pane)
        self.set_active(pane)
        self._changed()

    async def replace(self, old: NotebookPane, new: NotebookPane) -> None:
        """Show `new` in `old`'s window (vim's :edit / :buffer).

        `old` stays open as a hidden buffer, unless it is a blank untitled notebook (vim
        drops an unnamed, unchanged buffer too) — then it is closed for good.
        """
        found = next(((tab, find(tab.root, old)) for tab in self.tabs if find(tab.root, old)), None)
        if found is None:
            await self.add(new)
            return
        tab, (leaf, _) = found
        leaf.pane = new
        await self._adopt(new)
        if tab.active is old:
            tab.active = new
        if old.is_blank:
            await self._drop(old)
        else:
            old.display = False
        self.set_active(new)
        self._changed()

    async def _drop(self, pane: NotebookPane) -> None:
        if pane in self.buffers:
            self.buffers.remove(pane)
        await pane.close()
        forget_removed(self)

    async def close(self, pane: NotebookPane, delete: bool = False) -> NotebookPane | None:
        """Close one window: its notebook becomes a hidden buffer (or is deleted with `delete`,
        and always when it is a blank untitled one). Returns the pane to focus next, or None
        when no window is left.
        """
        index = self.tab_of(pane)
        neighbour = None
        if index is not None:
            tab = self.tabs[index]
            root = remove_leaf(tab.root, pane)
            if root is None:
                del self.tabs[index]
                if self.index >= len(self.tabs):
                    self.index = max(0, len(self.tabs) - 1)
            else:
                tab.root = root
                neighbour = self._nearest(tab, pane) or panes_of(root)[0]
                if tab.active is pane:
                    tab.active = neighbour
        if delete or pane.is_blank:
            await self._drop(pane)
        else:
            pane.display = False
        if not self.tabs:
            self._changed()
            return None
        self.switch(self.index)
        return neighbour or self.active

    async def show(self, pane: NotebookPane, window: NotebookPane | None = None) -> None:
        """vim's :b N: show buffer `pane` in `window` (default: the active one)."""
        window = window or self.active
        if pane in self.panes():
            self.set_active(pane)
            return
        if window is None or not self.tabs:
            await self.add(pane, "tab")
            return
        await self.replace(window, pane)

    async def delete(self, pane: NotebookPane) -> NotebookPane | None:
        """vim's :bd: close every window showing `pane`, forget the buffer, stop its kernel.

        Returns the pane to focus next (None when no buffer is left: the caller opens a
        fresh untitled one).
        """
        nxt = await self.close(pane, delete=True)
        if pane in self.buffers:
            await self._drop(pane)
            self._changed()
        if nxt is None and self.buffers:
            await self.add(self.buffers[-1], "tab")
            nxt = self.active
        return nxt

    def _nearest(self, tab: Tab, pane: NotebookPane) -> NotebookPane | None:
        """The remaining pane whose rectangle was closest to `pane`'s."""
        gone = tab.rects.get(pane)
        candidates = [p for p in panes_of(tab.root) if p in tab.rects]
        if gone is None or not candidates:
            return None
        cx, cy = gone.center
        return min(candidates, key=lambda p: abs(tab.rects[p].center[0] - cx) + abs(tab.rects[p].center[1] - cy))

    async def only(self, keep: NotebookPane) -> None:
        """vim's :only: the other windows of this tab close; their notebooks stay as buffers."""
        for pane in [p for p in self.tab_panes() if p is not keep]:
            await self.close(pane)

    async def to_new_tab(self, pane: NotebookPane) -> None:
        """vim's ctrl+w T: move the window into a tab of its own."""
        index = self.tab_of(pane)
        if index is None or len(self.tab_panes()) < 2:
            return
        tab = self.tabs[index]
        tab.root = remove_leaf(tab.root, pane)
        if tab.active is pane:
            tab.active = panes_of(tab.root)[0]
        self.tabs.insert(index + 1, Tab(Leaf(pane), pane))
        self.switch(index + 1)

    def resize(self, pane: NotebookPane, row: bool, delta: float) -> None:
        """ctrl+w < > - +: change the weight of the nearest ancestor split in that direction."""
        tab = self.tab
        if tab is None:
            return
        node: Node = Leaf(pane)
        found = find(tab.root, pane)
        if found is None:
            return
        leaf, parent = found
        node = leaf
        while parent is not None and parent.row != row:
            node, parent = parent, _parent_of(tab.root, parent)
        if parent is None:
            return
        total = sum(c.weight for c in parent.children)
        node.weight = max(0.1, node.weight + delta * total)
        self._changed()

    def equalize(self) -> None:
        def walk(node: Node) -> None:
            if isinstance(node, Branch):
                for child in node.children:
                    child.weight = 1.0
                    walk(child)

        if self.tab:
            walk(self.tab.root)
            self._changed()


    def switch(self, index: int) -> None:
        """Show tab `index` (0-based): its panes are displayed, every other pane hidden."""
        if not self.tabs:
            return
        self.index = max(0, min(len(self.tabs) - 1, index))
        shown = set(panes_of(self.tab.root))
        for pane in self.buffers:
            pane.display = pane in shown
        active = self.active
        if active is not None:
            self.set_active(active)
        self._changed()

    def set_active(self, pane: NotebookPane) -> None:
        index = self.tab_of(pane)
        if index is None:
            return
        if index != self.index:
            self.switch(index)
            return
        tab = self.tab
        tab.active = pane
        for other in self.tab_panes():
            other.set_active(other is pane)
        self.app.workspace_changed()

    def _changed(self) -> None:
        multi = len(self.tab_panes()) > 1
        for pane in self.tab_panes():
            pane.show_status(multi)
        self.refresh(layout=True)
        self.app.workspace_changed()


    def neighbour(self, pane: NotebookPane, direction: str) -> NotebookPane | None:
        """The window next to `pane` in direction h/j/k/l (by screen geometry)."""
        tab = self.tab
        if tab is None or pane not in tab.rects:
            return None
        me = tab.rects[pane]
        best: tuple[int, int, NotebookPane] | None = None
        for other, rect in tab.rects.items():
            if other is pane or other not in panes_of(tab.root):
                continue
            match direction:
                case "h":
                    ok, gap = rect.right <= me.x, me.x - rect.right
                    overlap = min(me.bottom, rect.bottom) - max(me.y, rect.y)
                case "l":
                    ok, gap = rect.x >= me.right, rect.x - me.right
                    overlap = min(me.bottom, rect.bottom) - max(me.y, rect.y)
                case "k":
                    ok, gap = rect.bottom <= me.y, me.y - rect.bottom
                    overlap = min(me.right, rect.right) - max(me.x, rect.x)
                case _:
                    ok, gap = rect.y >= me.bottom, rect.y - me.bottom
                    overlap = min(me.right, rect.right) - max(me.x, rect.x)
            if ok and overlap > 0:
                key = (gap, -overlap, other)
                if best is None or key[:2] < best[:2]:
                    best = key
        return best[2] if best else None

    def cycle(self, pane: NotebookPane, step: int) -> NotebookPane | None:
        panes = self.tab_panes()
        if pane not in panes:
            return panes[0] if panes else None
        return panes[(panes.index(pane) + step) % len(panes)]

"""Workarounds for Textual caches that keep removed widgets alive."""

from __future__ import annotations

from textual.widget import Widget


def forget_removed(container: Widget) -> None:
    """Rebuild the container's cached list of displayed children.

    Textual's NodeList caches that list and rebuilds it only when it is next read, which
    happens on a mount or a style refresh but not on a removal: until then every child
    removed since the last read stays alive, with everything it holds. Call this after
    `await container.remove_children(...)` or after a child's `remove()` has completed.
    """
    len(container._nodes.displayed)

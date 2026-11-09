"""Which PyPI package provides an import, and which import a ModuleNotFoundError is about.

Import names and distribution names differ for some popular packages (`cv2` is
`opencv-python`), and namespaces like `google.cloud.*` and `azure.*` ship one
distribution per sub-package. `packages.json` next to this file lists the renamed ones
(import name, or dotted prefix, → distribution). Everything else is on PyPI under its own
name: pip treats `_`, `-` and `.` in a name alike, so `sentence_transformers` installs as is.
PyPI has no lookup by import name, so the table is the source of truth, as in marimo.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from functools import cache
from pathlib import Path

_NO_MODULE = re.compile(r"No module named '([^']+)'")


@cache
def pypi_names() -> dict[str, str]:
    """The import-name → distribution table, read from packages.json on first use."""
    with Path(__file__).with_name("packages.json").open("rb") as f:
        return json.load(f)


def missing_module(error: dict | None) -> str | None:
    """The dotted module a ModuleNotFoundError is about; None for any other error."""
    if not error or error.get("ename") != "ModuleNotFoundError":
        return None
    match = _NO_MODULE.search(error.get("evalue", ""))
    return match.group(1) if match else None


def is_stdlib(module: str) -> bool:
    """True for a standard-library module (missing means the interpreter was built without it)."""
    return module.split(".")[0] in sys.stdlib_module_names


def pypi_name(module: str, source: str = "") -> str:
    """The distribution to install for `module`, the dotted name a ModuleNotFoundError gave.

    The cell's own import statements refine a bare namespace root (`google`, `azure`) to
    the sub-package it actually imports.
    """
    candidates = [name for name in _imported(source) if _within(name, module)] + [module]
    for name in sorted(candidates, key=len, reverse=True):
        if (distribution := _lookup(name)) is not None:
            return distribution
    return module.split(".")[0]


def _within(name: str, module: str) -> bool:
    return name == module or name.startswith(module + ".")


def _lookup(name: str) -> str | None:
    parts = name.split(".")
    table = pypi_names()
    for n in range(len(parts), 0, -1):
        if (distribution := table.get(".".join(parts[:n]))) is not None:
            return distribution
    if parts[:2] == ["google", "cloud"] and len(parts) >= 3:
        return "google-cloud-" + parts[2].replace("_", "-")
    if parts[0] == "azure" and len(parts) >= 2:
        return "-".join(parts[:3]).replace("_", "-")
    return None


def _imported(source: str) -> list[str]:
    """Dotted names the source imports: `from a.b import c` gives a.b and a.b.c.

    A capitalised name after `import` is a class, not a sub-module, and is left out.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
            names += [f"{node.module}.{alias.name}" for alias in node.names if alias.name.islower()]
    return names


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


_REQUIREMENT = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _names(requirements) -> set[str]:
    out = set()
    for requirement in requirements or ():
        if isinstance(requirement, str) and (m := _REQUIREMENT.match(requirement)):
            out.add(_normal(m.group(1)))
    return out


def _pyproject(root: Path) -> dict:
    import tomllib

    try:
        with (root / "pyproject.toml").open("rb") as f:
            return tomllib.load(f)
    except (OSError, ValueError):
        return {}


def dependency_table(root: Path, name: str) -> str | None:
    """"extra" or "group" when `name` is one of the project's optional-dependency tables or
    dependency groups (an extra wins), else None."""
    data = _pyproject(root)
    if name in ((data.get("project") or {}).get("optional-dependencies") or {}):
        return "extra"
    return "group" if name in (data.get("dependency-groups") or {}) else None


def declared(root: Path, distribution: str) -> tuple[str, str] | None:
    """Where the project at `root` declares `distribution`: ("main", ""), ("extra", name),
    ("group", name), or None (no pyproject.toml, or not in it). Names compare as pip does."""
    data = _pyproject(root)
    if not data:
        return None
    wanted = _normal(distribution)
    project = data.get("project") or {}
    if wanted in _names(project.get("dependencies")):
        return ("main", "")
    for kind, table in (("extra", project.get("optional-dependencies")), ("group", data.get("dependency-groups"))):
        for name, requirements in (table or {}).items():
            if wanted in _names(requirements):
                return (kind, name)
    return None

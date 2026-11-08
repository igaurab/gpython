from gpython.graph import analyze, build, imports, mutations


def test_analyze_basic():
    assert analyze("x = 1\ny = x + z") == ({"x", "y"}, {"z"})


def test_analyze_imports_functions_classes():
    defs, refs = analyze("import numpy as np\nfrom os import path\ndef f(a): return a + k\nclass C: pass")
    assert defs == {"np", "path", "f", "C"}
    assert refs == {"k"}


def test_analyze_scopes():
    defs, refs = analyze("ys = [i * scale for i in data]\ndef g(n):\n    t = n\n    return t + offset")
    assert defs == {"ys", "g"}
    assert refs == {"scale", "data", "offset"}


def test_analyze_private_names():
    assert analyze("_tmp = a\nb = _tmp") == ({"b"}, {"a"})


def test_analyze_global_declaration():
    defs, _ = analyze("def setup():\n    global cfg\n    cfg = 1")
    assert defs == {"setup", "cfg"}


def test_run_order_follows_dependencies_not_display_order():
    g = build({"c1": "y = x + 1", "c2": "x = 1", "c3": "z = y * 2", "c4": "w = 5"})
    assert g.errors == {}
    assert g.run_order(["c2"]) == ["c2", "c1", "c3"]
    assert g.run_order(["c4"]) == ["c4"]
    assert g.run_order(["c1", "c2", "c3", "c4"]) == ["c2", "c1", "c3", "c4"]


def test_run_order_of_a_wide_fan_out_is_display_order_and_fast():
    import time

    cells = {"setup": "import math\nk = 2"} | {f"c{i}": f"r{i} = math.sqrt(k + {i})" for i in range(3000)}
    g = build(cells)
    start = time.perf_counter()
    order = g.run_order(list(cells))
    assert time.perf_counter() - start < 0.5
    assert order == list(cells)


def test_multiple_definitions_are_errors():
    g = build({"a": "x = 1", "b": "x = 2", "c": "y = 3"})
    assert set(g.errors) == {"a", "b"}


def test_identical_reimport_is_not_a_conflict():
    g = build({"a": "import seaborn as sns", "b": "import seaborn as sns\nsns.heatmap()", "c": "sns.countplot()"})
    assert g.errors == {}
    assert g.owner["sns"] == "a"
    assert g.parents["c"] == {"a"}
    assert g.children["b"] == set()


def test_reimport_under_a_different_module_is_a_conflict():
    g = build({"a": "import seaborn as sns", "b": "import seaborn.objects as sns"})
    assert set(g.errors) == {"a", "b"}
    g = build({"a": "import seaborn as sns", "b": "sns = 1"})
    assert set(g.errors) == {"a", "b"}
    g = build({"a": "import seaborn as sns", "b": "import seaborn as sns\nsns = sns.set_theme()"})
    assert set(g.errors) == {"a", "b"}


def test_imports_map_names_to_what_they_bind():
    assert imports("import a.b as c\nfrom a import b\nimport x.y\nfrom . import z") == {
        "c": "a.b", "b": "a.b", "x": "x", "z": "..z"
    }
    assert imports("try:\n    import ujson as json\nexcept ImportError:\n    import json") == {}
    assert imports("def f():\n    import os") == {}


def test_cycles_are_errors():
    g = build({"a": "x = y", "b": "y = x", "c": "z = 1"})
    assert set(g.errors) == {"a", "b"}


def test_syntax_error():
    g = build({"a": "x = (", "b": "y = 1"})
    assert "SyntaxError" in g.errors["a"]


def test_builtins_are_not_refs():
    assert analyze("print(len(data))") == (set(), {"data"})


def test_mutations_detected():
    src = "data.append(1)\nconfig['k'] = 2\nobj.attr = 3\ndel cache[0]\ndef f():\n    hidden.append(1)\n_p.append(1)"
    assert mutations(src) == {"data", "config", "obj", "cache"}


def test_mutation_orders_readers_and_reruns_owner():
    g = build({"own": "xs = []", "mut": "xs.append(1)", "read": "n = len(xs)", "other": "z = 1"})
    assert g.errors == {}
    assert g.mutates["mut"] == {"xs"}
    assert g.run_order(["own"]) == ["own", "mut", "read"]
    assert g.expand_roots(["mut"]) == ["mut", "own"]
    assert g.run_order(g.expand_roots(["mut"])) == ["own", "mut", "read"]


def test_reader_above_mutator_is_not_reordered():
    g = build({"own": "xs = []", "read": "n = len(xs)", "mut": "xs.append(1)"})
    assert g.errors == {}
    assert "mut" not in g.parents["read"]


def test_magics_are_ignored_for_analysis():
    assert analyze("%matplotlib inline\n!ls\nx = 1\nx?") == ({"x"}, set())


def test_marimo_path_cell_exports_only_run():
    src = (
        "import sys as _sys, pathlib as _pathlib\n"
        '_sys.path[:0] = [str(p) for p in _pathlib.Path(__file__).resolve().parents if (p / "lc.py").exists()]\n'
        "from lc import run"
    )
    assert analyze(src) == ({"run"}, set())

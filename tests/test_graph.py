from gpython.graph import analyze, build, mutations


def test_analyze_basic():
    assert analyze("x = 1\ny = x + z") == ({"x", "y"}, {"z"})


def test_analyze_imports_functions_classes():
    defs, refs = analyze("import numpy as np\nfrom os import path\ndef f(a): return a + k\nclass C: pass")
    assert defs == {"np", "path", "f", "C"}
    assert refs == {"k"}


def test_analyze_scopes():
    # Comprehension variables and function params are local, not uses.
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


def test_multiple_definitions_are_errors():
    g = build({"a": "x = 1", "b": "x = 2", "c": "y = 3"})
    assert set(g.errors) == {"a", "b"}


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
    # Reader runs after the mutator, so it sees the appended item.
    assert g.run_order(["own"]) == ["own", "mut", "read"]
    # Running the mutator alone would append twice, so the owner re-runs first.
    assert g.expand_roots(["mut"]) == ["mut", "own"]
    assert g.run_order(g.expand_roots(["mut"])) == ["own", "mut", "read"]


def test_reader_above_mutator_is_not_reordered():
    g = build({"own": "xs = []", "read": "n = len(xs)", "mut": "xs.append(1)"})
    assert g.errors == {}
    assert "mut" not in g.parents["read"]


def test_magics_are_ignored_for_analysis():
    assert analyze("%matplotlib inline\n!ls\nx = 1\nx?") == ({"x"}, set())

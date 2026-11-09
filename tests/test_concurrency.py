from gpython.concurrency import Access, accesses, conflict


def notebook(*sources: str) -> list[Access]:
    found = accesses({f"c{i}": source for i, source in enumerate(sources, 1)})
    return [found[f"c{i}"] for i in range(1, len(sources) + 1)]


def clash(sources: list[str], i: int, j: int) -> bool:
    cells = notebook(*sources)
    return conflict(cells[i - 1], cells[j - 1])


def test_independent_reads_share():
    cells = ["a = 1", "print(a)", "print(a + 1)"]
    assert not clash(cells, 2, 3)
    assert clash(cells, 1, 2)


def test_conflict_is_bernstein():
    reader = Access(frozenset({"x"}), frozenset(), "")
    writer = Access(frozenset(), frozenset({"x"}), "")
    other = Access(frozenset({"y"}), frozenset({"z"}), "")
    assert conflict(reader, writer) and conflict(writer, reader) and conflict(writer, writer)
    assert not conflict(reader, reader) and not conflict(reader, other)
    assert conflict(Access(frozenset(), frozenset(), "magic"), other)


def test_mutating_method_conflicts():
    assert clash(["xs = []", "xs.append(1)", "print(xs)"], 2, 3)
    assert not clash(["xs = []", "ys = xs.copy()", "print(xs)"], 2, 3)


def test_subscript_and_attribute_writes_conflict():
    assert clash(["d = {}", 'd["k"] = 1', "n = len(d)"], 2, 3)
    assert clash(["o = C()", "o.a = 1", "print(o)"], 2, 3)
    assert clash(["xs = [1]", "xs += [2]", "print(xs)"], 2, 3)


def test_module_call_is_not_a_write():
    cells = ["import requests", "r1 = requests.get(u)", "r2 = requests.get(u)"]
    assert not clash(cells, 2, 3)


def test_readonly_library_functions_share():
    cells = ["import numpy as np", "m = np.mean(x)", "s = np.std(x)", "np.sort(x, out=x)"]
    assert not clash(cells, 2, 3)
    assert clash(cells, 2, 4)


def test_effect_names():
    assert clash(["import random", "random.seed(1)", "v = random.random()"], 2, 3)
    assert clash(["import matplotlib.pyplot as plt", "plt.plot(x)", "plt.plot(y)"], 2, 3)
    assert clash(["import numpy as np", "np.random.seed(0)", "r = np.random.rand(3)"], 2, 3)
    assert not clash(["import os", "p = os.path.join(a, b)", "q = os.path.join(a, c)"], 2, 3)


def test_open_write_vs_read():
    assert clash(['open("x", "w").write(t)', 's = open("y").read()'], 1, 2)
    assert not clash(['s = open("x").read()', 'u = open("y").read()'], 1, 2)
    assert clash(['open(p, mode).write(t)', 's = open("y").read()'], 1, 2)


def test_file_methods_are_effects():
    assert clash(["df.to_csv('a.csv')", "import pandas as pd", "e = pd.read_csv('a.csv')"], 1, 3)


def test_function_mutating_global():
    cells = ["cache = []", "def f():\n    cache.append(1)", "f()", "print(cache)"]
    assert clash(cells, 3, 4)


def test_function_alias_of_global():
    cells = ["cache = []", "def f():\n    c = cache\n    c.append(1)", "f()", "print(cache)"]
    assert clash(cells, 3, 4)


def test_function_reading_global_only_shares():
    cells = ["data = [1]", "def total(xs):\n    return sum(xs) + len(data)", "a = total(data)", "b = total(data)"]
    assert not clash(cells, 3, 4)


def test_function_mutating_its_argument():
    cells = ["xs = []", "def push(v):\n    v.append(1)", "push(xs)", "print(xs)"]
    assert clash(cells, 3, 4)


def test_function_with_global_declaration():
    cells = ["n = 0", "def bump():\n    global n\n    n = n + 1", "bump()", "print(n)"]
    assert clash(cells, 3, 4)


def test_calls_through_functions_are_transitive():
    cells = ["xs = []", "def inner():\n    xs.append(1)", "def outer():\n    inner()", "outer()", "print(xs)"]
    assert clash(cells, 4, 5)


def test_alias_across_cells():
    assert clash(["a = []", "b = a", "b.append(1)", "print(a)"], 3, 4)
    assert clash(["rows = [[1]]", "for r in rows:\n    r.append(2)", "print(rows)"], 2, 3)


def test_rebinding_an_alias_does_not_write_the_source():
    assert not clash(["a = 1", "b = a", "print(a)"], 2, 3)


def test_unknown_call_writes_args():
    assert clash(["df = load()", "g(df)", "print(df)"], 2, 3)
    assert clash(["xs = []", 'getattr(xs, "append")(1)', "print(xs)"], 2, 3)


def test_private_names_conflict():
    assert clash(["_t = 1\nprint(_t)", "_t = 2\nprint(_t)"], 1, 2)


def test_exclusive_reasons():
    for source in ["%time 1", "!ls", "r = await f()", 'exec("1")', "v = input()", "breakpoint()",
                   "from os import *", "x = (", "g = globals()"]:
        assert notebook(source)[0].exclusive, source
    assert not notebook("a = 1")[0].exclusive


def test_exclusive_through_a_function():
    cells = ["def ask():\n    return input()", "v = ask()"]
    assert notebook(*cells)[1].exclusive
    assert not notebook(*cells)[0].exclusive


def test_cached_per_source():
    first = accesses({"c1": "a = 1", "c2": "print(a)"})
    second = accesses({"c1": "a = 1", "c2": "print(a)"})
    assert first == second


def test_readonly_method_on_a_computed_value():
    cells = ["A = [1]\nB = [2]", "def work():\n    return (A + B).count(1)", "r1 = work()", "r2 = work()"]
    assert not clash(cells, 3, 4)


def test_writing_a_file_keeps_no_reference_to_the_data():
    cells = ["blob = b'x'", "def save(path):\n    with open(path, 'rb') as f:\n        return f.read() + blob",
             "a = save('p')", "b = save('q')"]
    assert not clash(cells, 3, 4)
    assert clash(["xs = []", "q.append(xs)", "print(xs)"], 2, 3)


def test_network_cells_share():
    cells = ["import urllib.request\nURL = 'http://h/'",
             "def work(i):\n    return len(urllib.request.urlopen(f'{URL}{i}').read())",
             "r1 = work(1)", "r2 = work(2)"]
    assert not clash(cells, 3, 4)


def plan(sources: list[str], cap: int = 8) -> list[list[int]]:
    from gpython.concurrency import groups
    from gpython.graph import build

    cells = {f"c{i}": source for i, source in enumerate(sources, 1)}
    graph = build(cells)
    found = groups(graph.sort(set(cells)), accesses(cells), graph.parents, cap)
    return [[int(cid[1:]) for cid in group] for group in found]


def test_groups_parallel_siblings():
    assert plan(["a = 1", "b = a", "c = a"]) == [[1], [2, 3]]


def test_groups_follow_dependencies():
    assert plan(["a = 1", "b = a", "c = a", "d = b + c"]) == [[1], [2, 3], [4]]


def test_groups_exclusive_alone():
    assert plan(["a = 1", "b = a", "%time c = a", "d = a"]) == [[1], [2], [3], [4]]


def test_groups_cap():
    assert [len(g) for g in plan([f"v{i} = {i}" for i in range(5)], cap=2)] == [2, 2, 1]


def test_groups_conflict_keeps_source_order():
    assert plan(["xs = []", "xs.append(1)", "n = len(xs)"]) == [[1], [2], [3]]


def test_groups_cap_one_is_sequential():
    assert plan(["a = 1", "b = a", "c = a"], cap=1) == [[1], [2], [3]]


def test_report_names_groups_and_reasons():
    from gpython.concurrency import report

    cells = {"a": "import numpy as np\ndata = np.ones(3)", "b": "m = np.mean(data)", "c": "s = np.std(data)",
             "d": "data.sort()", "e": "%time 1"}
    labels = {"a": "c1", "b": "c2", "c": "c3", "d": "c4", "e": "c5"}
    text = report(cells, labels, 8)
    assert "group 2: c2 c3   together" in text
    assert "c4" in text and "both touch data" in text
    assert "alone: IPython magic" in text


def test_watched_names_are_shared_reads():
    from gpython.concurrency import watched

    cells = notebook("xs = []", "a = len(xs)", "b = len(xs) + 1")
    access = {"x": cells[1], "y": cells[2]}
    assert watched(["x", "y"], access) == ["xs"]

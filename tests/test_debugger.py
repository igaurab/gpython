"""The debugger's kernel half (kernel_side) driven in process, and its pictures (debugview).

The kernel half runs here exactly as in the kernel: a cell is registered under a code filename,
each top-level statement runs as its own code object from a skipped module (as IPython does),
breakpoint() goes through kernel_side.breakpointhook, and the prompts are answered by a script.
Standard library only."""

import ast
import builtins
import json
import linecache
import sys

import pytest

from gpython import debugview, kernel_side
from gpython.kernel_side import MARKER, CellInfo

RUNNER = {"__name__": "IPython.core.fake_runner"}
exec(compile("def run(codes, namespace):\n    for code in codes:\n        exec(code, namespace)\n", "<runner>", "exec"), RUNNER)


def register(source: str, cid: str) -> str:
    name = f"<cell {cid}>"
    kernel_side.cell_files[name] = cid
    kernel_side._infos.pop(name, None)
    linecache.cache[name] = (len(source), None, source.splitlines(True), name)
    return name


def run_cell(source: str, commands: list[str], cid: str = "c1", namespace: dict | None = None):
    """Run `source` as cell `cid`; every debugger prompt takes the next command. Returns the
    payloads of the stops and the exception the cell raised (or None)."""
    name = register(source, cid)
    tree = ast.parse(source)
    codes = [compile(ast.Module([node], []), name, "exec") for node in tree.body]
    stops, script = [], iter(commands)

    def answer(prompt=""):
        assert prompt.startswith(MARKER), prompt
        stops.append(json.loads(prompt[len(MARKER):]))
        return next(script)

    saved_input, saved_hook = builtins.input, sys.breakpointhook
    builtins.input, sys.breakpointhook = answer, kernel_side.breakpointhook
    kernel_side.new_run(cid)
    raised = None
    try:
        RUNNER["run"](codes, namespace if namespace is not None else {"__name__": "__main__"})
    except BaseException as error:
        raised = error
        sys.last_exc = error
    finally:
        sys.settrace(None)
        kernel_side.run_ended()
        builtins.input, sys.breakpointhook = saved_input, saved_hook
    return stops, raised


def postmortem(commands: list[str]):
    stops, script = [], iter(commands)

    def answer(prompt=""):
        stops.append(json.loads(prompt[len(MARKER):]))
        return next(script)

    saved = builtins.input
    builtins.input = answer
    try:
        kernel_side.postmortem()
    finally:
        builtins.input = saved
        sys.settrace(None)
    return stops


def loops(stop):
    return [(loop["header"], dict(loop["vars"]), loop["k"], loop["n"]) for loop in stop["now"]["loops"]]


def seq(stop, name):
    return next(s for s in stop["now"]["seqs"] if s["name"] == name)


NESTED = """nums = [3, 1, 4, 1, 5]
total = 0
breakpoint()
for i in range(len(nums)):
    for j in range(i + 1, len(nums)):
        total += nums[j] - nums[i]
done = True
"""


def test_variables_rows_carry_type_shape_value_and_category():
    import json as json_module

    def helper():
        pass

    namespace = {"xs": [1, 2, 3], "name": "abc", "table": {"a": 1}, "json": json_module, "helper": helper, "Thing": int, "n": 5}
    rows = {row[0]: row[1:] for row in kernel_side.variables(namespace, set(namespace) | {"missing"})}
    assert "missing" not in rows
    assert rows["xs"] == ["list", "[1, 2, 3]", "len 3", "data"]
    assert rows["name"] == ["str", "'abc'", "len 3", "data"]
    assert rows["table"][2:] == ["len 1", "data"]
    assert rows["n"] == ["int", "5", "", "data"]
    assert rows["json"][0] == "module" and rows["json"][3] == "module"
    assert rows["helper"][3] == "function" and rows["Thing"][3] == "class"
    big = list(range(1_000_000))
    shown = kernel_side.short(big)
    assert len(shown) <= 60 and shown.startswith("[0, 1, 2")
    assert kernel_side.full(big)["text"].endswith("998000 more")
    assert kernel_side.full([[1, 2], [3, 4]])["kind"] == "table"


def test_numpy_and_pandas_get_shape_and_dtype():
    numpy = pytest.importorskip("numpy")
    array = numpy.zeros((3, 4))
    assert kernel_side.shape_info(array) == "(3, 4) float64"
    assert kernel_side.full(array)["kind"] == "table"
    pandas = pytest.importorskip("pandas")
    frame = pandas.DataFrame({"a": [1, 2], "b": [0.5, 1.5]})
    assert kernel_side.shape_info(frame).startswith("(2, 2)")
    table = kernel_side.full(frame)
    assert table["kind"] == "table" and table["columns"] == ["a", "b"] and table["rows"][1] == ["2", "1.5"]


def test_pointers_and_grids_are_read_from_the_ast():
    info = CellInfo(
        "for i in range(n):\n    x = nums[i] + nums[j + 1] + nums[0]\n    part = nums[lo:hi]\n"
        "    dp[i][j] = grid[r, c] + d['k'] + words[len(words) - k]\n    while lo < hi:\n        lo += 1\n"
    )
    assert info.pointers["nums"] == ["i", "j + 1", "lo", "hi"]
    assert info.pointers["words"] == ["len(words) - k"]
    assert "d" not in info.pointers
    assert info.grids == {"dp": [("i", "j")], "grid": [("r", "c")]}
    assert [(loop.kind, loop.header, loop.targets) for loop in info.loops] == [
        ("for", "for i in range(n)", ["i"]), ("while", "while lo < hi", []),
    ]


def test_the_loop_stack_shows_nested_loops_with_positions():
    stops, raised = run_cell(NESTED, ["n", "n", "n", "n", "c"])
    assert raised is None
    assert loops(stops[0]) == []
    assert loops(stops[3]) == [
        ("for i in range(len(nums))", {"i": "0"}, 1, 5),
        ("for j in range(i + 1, len(nums))", {"j": "1"}, 1, 4),
    ]
    assert stops[3]["now"]["cell"] == "c1" and stops[3]["now"]["line"] == 6
    assert seq(stops[3], "nums")["ptrs"] == [["j", 1], ["i", 0]]
    picture = "\n".join(str(line) for line in debugview.render_loops(stops[3]["now"]))
    assert "1/5" in picture and "1/4" in picture and "i=0" in picture and "█" in picture


def test_next_iteration_of_the_inner_and_the_outer_loop():
    stops, _ = run_cell(NESTED, ["n", "n", "n", "_gp_next 0", "_gp_next 0", "_gp_next 1", "c"])
    inner = [loop[-1][1:] for loop in map(loops, stops[3:6]) if loop]
    assert inner[0] == ({"j": "1"}, 1, 4) and inner[1] == ({"j": "2"}, 2, 4) and inner[2] == ({"j": "3"}, 3, 4)
    outer = loops(stops[6])
    assert outer[0][1:] == ({"i": "1"}, 2, 5) and stops[6]["now"]["line"] == 5


def test_run_to_the_end_of_the_loop_and_to_an_iteration():
    stops, _ = run_cell(NESTED, ["n", "n", "n", "_gp_end", "_gp_goto 0 3", "c"])
    assert stops[4]["now"]["line"] == 4 and loops(stops[4])[0][1:] == ({"i": "0"}, 1, 5)
    assert stops[5]["now"]["line"] == 5 and loops(stops[5])[0][1:] == ({"i": "2"}, 3, 5)


def test_stop_if_checks_every_line_of_the_cell():
    stops, _ = run_cell(NESTED, ["_gp_until total > 5", "c"])
    now = stops[1]["now"]
    assert dict(now["vars"])["total"] == "6"
    assert loops(stops[1])[0][1] == {"i": "1"}
    bad, _ = run_cell(NESTED, ["_gp_until total >", "c"])
    assert bad[1]["reply"]["kind"] == "error" and "stop if" in bad[1]["reply"]["text"]


def test_loop_commands_outside_a_loop_answer_with_an_error():
    stops, _ = run_cell(NESTED, ["_gp_next 0", "c"])
    assert stops[1]["reply"] == {"kind": "error", "text": "not inside a loop"}


OFF_BY_ONE = """nums = [1, 3, 5, 7]
total = 0
breakpoint()
for i in range(len(nums) + 1):
    total += nums[i]
"""


def test_an_index_past_the_end_is_drawn_after_the_strip_with_a_warning():
    stops, raised = run_cell(OFF_BY_ONE, ["n", "n", "_gp_end", "c"])
    assert isinstance(raised, IndexError)
    stop = stops[3]
    assert stop["event"] == "exception" and stop["exception"].startswith("IndexError")
    nums = seq(stop, "nums")
    assert nums["ptrs"] == [["i", 4]] and nums["len"] == 4
    lines = [line.plain for line in debugview.render_seq(nums, None)]
    assert lines[0].startswith("nums") and "[1][3][5][7]" in lines[0]
    arrow = next(line for line in lines if "↑i" in line)
    assert arrow.index("↑") > lines[0].rindex("]")
    assert "⚠ i = 4 is past the end of nums (len 4)" in lines


def test_warnings_for_negative_and_crossed_pointers():
    before = {"name": "xs", "len": 6, "start": 0, "items": list("abcdef"), "ptrs": [["lo", 2], ["hi", 4]]}
    now = dict(before, ptrs=[["lo", 4], ["hi", 3]])
    assert any("crossed" in w for w in debugview.seq_warnings(now, before))
    assert debugview.seq_warnings(dict(before, ptrs=[["i", -1]]), None) == ["i = -1 counts from the end of xs: xs[-1] is xs[5]"]
    assert "before the start" in debugview.seq_warnings(dict(before, ptrs=[["i", -7]]), None)[0]


def test_changed_elements_grids_and_maps_are_highlighted():
    source = "grid = [[0] * 3 for _ in range(3)]\nseen = {}\nbreakpoint()\nfor r in range(3):\n    for c in range(3):\n        grid[r][c] = r + c + 1\n        seen[r] = c\n"
    stops, _ = run_cell(source, ["n", "n", "n", "n", "_gp_next 0", "c"])
    before, after = stops[3]["now"]["grids"][0], stops[5]["now"]["grids"][0]
    assert after["name"] == "grid" and after["ptrs"] == [["r", "c", 0, 1]]
    lines = debugview.render_grid(after, before)
    changed = [line.plain[span.start:span.end] for line in lines for span in line.spans if span.style == debugview.CHANGED]
    pointed = [line.plain[span.start:span.end] for line in lines for span in line.spans if span.style == debugview.POINTED]
    assert changed == ["1"] and pointed == ["0"]
    assert stops[5]["now"]["maps"][0]["items"] == [["0", "0"]]
    added, removed, modified = debugview.map_changes({"name": "seen", "items": [["0", "1"], ["2", "5"]]}, {"name": "seen", "items": [["0", "0"], ["1", "1"]]})
    assert (added, removed, modified) == (["2"], ["1"], ["0"])


def test_history_is_recorded_and_travelled_back_and_forward():
    stops, _ = run_cell(NESTED, ["n", "n", "n", "_gp_next 0", "_gp_next 0", "c"])
    session = debugview.DebugSession()
    for stop in stops:
        session.update(stop)
    assert session.total == stops[-1]["history"]["total"] >= 6
    assert set(session.records) == set(range(session.total))
    live = session.record
    assert session.travel(-1) is None and session.view == session.payload["index"] - 1
    back = session.record
    assert back["line"] != live["line"] or back["vars"] != live["vars"]
    session.travel(-100)
    assert session.view == 0 and session.record["line"] == 3
    session.travel(1000)
    assert session.live and session.record == live
    assert session.history_of("total")[-1] == dict(live["vars"])["total"]


def test_history_is_sent_in_batches_and_gaps_are_fetched():
    source = "breakpoint()\nacc = 0\nfor k in range(400):\n    acc += k\nend = acc\n"
    stops, _ = run_cell(source, ["n", "n", "n", "_gp_end", "_gp_history 0 300", "c"])
    session = debugview.DebugSession()
    for stop in stops[:5]:
        session.update(stop)
    assert session.total > 800 and len(session.records) == kernel_side.BATCH + 4
    assert session.missing() is not None
    session.update(stops[5], keep_view=True)
    assert all(i in session.records for i in range(300))


def test_the_timeline_groups_iterations_and_marks_warnings():
    stops, _ = run_cell(OFF_BY_ONE, ["n", "n", "_gp_end", "c"])
    session = debugview.DebugSession()
    for stop in stops:
        session.update(stop)
    roots = debugview.timeline([session.records[i] for i in sorted(session.records)])
    assert [node.k for node in roots] == [1, 2, 3, 4, 5]
    assert [node.warnings for node in roots[:4]] == [[], [], [], []]
    assert roots[4].warnings == ["i = 4 is past the end of nums (len 4)"]
    assert "i=4" in roots[4].label().plain and "⚠" in roots[4].label().plain
    assert "total: 1→4" in roots[1].label().plain
    nested, _ = run_cell(NESTED + "finish = done\n", ["_gp_until done", "c"])
    session = debugview.DebugSession()
    session.update(nested[1])
    tree = debugview.timeline([session.records[i] for i in sorted(session.records)])
    assert [len(node.children) for node in tree] == [4, 3, 2, 1, 0]


def test_post_mortem_opens_at_the_failing_line_with_the_pointer_past_the_end():
    _, raised = run_cell("ys = [1, 2]\nfor j in range(3):\n    z = ys[j]\n", [], cid="c9")
    assert isinstance(raised, IndexError)
    stops = postmortem(["_gp_next 0", "_gp_frame 0", "q"])
    stop = stops[0]
    assert stop["postmortem"] and stop["exception"] == "IndexError: list index out of range"
    assert stop["now"]["cell"] == "c9" and stop["now"]["line"] == 3
    assert loops(stop) == [("for j in range(3)", {"j": "2"}, 3, 3)]
    assert seq(stop, "ys")["ptrs"] == [["j", 2]]
    assert stops[1]["reply"]["kind"] == "error"


def test_post_mortem_keeps_the_history_of_a_recorded_run():
    _, raised = run_cell(OFF_BY_ONE, ["_gp_until i == 3", "c"], cid="c7")
    assert isinstance(raised, IndexError)
    stops = postmortem(["q"])
    assert stops[0]["history"]["total"] > 5 and stops[0]["now"]["cell"] == "c7"


def test_quit_aborts_the_cell_and_tracing_stops():
    stops, raised = run_cell(NESTED, ["n", "q"])
    assert len(stops) == 2 and raised is not None and type(raised).__name__ == "BdbQuit"
    assert sys.gettrace() is None


def test_values_of_a_local_and_watches():
    stops, _ = run_cell(NESTED, ["_gp_watch total * 2", "_gp_full nums", "_gp_unwatch total * 2", "c"])
    assert stops[1]["watches"] == [["total * 2", "0"]]
    assert stops[2]["reply"] == {"kind": "full", "name": "nums", "value": {"kind": "text", "text": "[3, 1, 4, 1, 5]"}}
    assert stops[3]["watches"] == []


def test_a_function_defined_in_a_cell_is_stepped_into_and_the_stack_shown():
    source = "def scale(xs):\n    out = []\n    for x in xs:\n        out.append(x * 2)\n    return out\nbreakpoint()\nresult = scale([1, 2])\n"
    stops, _ = run_cell(source, ["n", "s", "n", "n", "_gp_frame 0", "c"])
    inside = stops[4]
    assert inside["now"]["func"] == "scale" and [frame["func"] for frame in inside["stack"]] == ["<module>", "scale"]
    assert loops(inside) == [("for x in xs", {"x": "1"}, 1, 2)]
    assert seq(inside, "xs")["ptrs"] == [["x", 0]]
    assert stops[5]["frame"] == 0 and stops[5]["now"]["func"] == "<module>"

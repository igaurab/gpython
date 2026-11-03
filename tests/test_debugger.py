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
    """Run `source` as cell `cid`; every debugger prompt takes the next command (a callable gets
    the payload and returns the command). Returns the payloads of the stops and the exception
    the cell raised (or None)."""
    name = register(source, cid)
    tree = ast.parse(source)
    codes = [compile(ast.Module([node], []), name, "exec") for node in tree.body]
    stops, script = [], iter(commands)

    def answer(prompt=""):
        assert prompt.startswith(MARKER), prompt
        stops.append(json.loads(prompt[len(MARKER):]))
        command = next(script)
        return command(stops[-1]) if callable(command) else command

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


def session_of(stops, keep=()):
    session = debugview.DebugSession()
    for index, stop in enumerate(stops):
        session.update(stop, keep_view=index in keep)
    return session


def test_history_is_recorded_and_travelled_back_and_forward():
    stops, _ = run_cell(NESTED, ["n", "n", "n", "_gp_next 0", "_gp_next 0", "c"])
    session = session_of(stops)
    assert session.total == stops[-1]["history"]["steps"] >= 6
    assert session.order == list(range(session.total))
    live = session.record
    assert session.travel(-1) is None and session.view == session.payload["index"] - 1
    back = session.record
    assert back["line"] != live["line"] or back["vars"] != live["vars"]
    session.travel(-100)
    assert session.view == 0 and session.record["line"] == 3
    session.travel(1000)
    assert session.live and session.record == live
    assert session.history_of("total")[-1] == dict(live["vars"])["total"]


LONG = "breakpoint()\nacc = 0\nfor k in range({n}):\n    acc += k\nend = acc\n"


def fetch_all(session):
    """A command for run_cell: fetch what the mirror lacks, then continue."""

    def command(payload):
        session.update(payload, keep_view=session.session is not None)
        gap = session.missing()
        return f"_gp_history {gap}" if gap is not None else "c"

    return command


def test_history_is_sent_in_batches_and_gaps_are_fetched():
    session = debugview.DebugSession()
    fetch = fetch_all(session)
    stops, _ = run_cell(LONG.format(n=400), ["n", "n", "n", "_gp_end"] + [fetch] * 20)
    first = session_of(stops[:5])
    assert first.total > 800 and len(first.order) == kernel_side.BATCH + 4
    assert first.missing() is not None
    assert len(stops) > 6 and session.missing() is None
    assert session.order == list(range(session.total))


def test_replay_rebuilds_the_locals_from_the_keyframe_before():
    kernel_side.configure({"debug_detail": "balanced"})
    try:
        stops, _ = run_cell(LONG.format(n=300), ["n", "n", "n", "_gp_end", "c"])
    finally:
        kernel_side.configure({})
    session = session_of(stops)
    entries = session.entries
    assert stops[-1]["history"]["detail"] == "balanced"
    assert any(not entry["key"] for entry in entries.values()) and entries[min(entries)]["key"]
    live = session.payload["index"]
    assert dict(session.full(live)["vars"]) == dict(session.payload["now"]["vars"])
    deltas = sorted(i for i, entry in entries.items() if not entry["key"])
    middle = deltas[len(deltas) // 2]
    rebuilt = session.full(middle)
    assert set(dict(rebuilt["vars"])) == {"acc", "k"}
    position = int(dict(rebuilt["vars"])["k"])
    assert int(dict(rebuilt["vars"])["acc"]) in (sum(range(position)), sum(range(position + 1)))


def test_the_timeline_groups_iterations_and_marks_warnings():
    stops, _ = run_cell(OFF_BY_ONE, ["n", "n", "_gp_end", "c"])
    roots = debugview.timeline(session_of(stops).records())
    assert [node.k for node in roots] == [1, 2, 3, 4, 5]
    assert [node.warnings for node in roots[:4]] == [[], [], [], []]
    assert roots[4].warnings == ["i = 4 is past the end of nums (len 4)"]
    assert "i=4" in roots[4].label().plain and "⚠" in roots[4].label().plain
    assert "total: 1→4" in roots[1].label().plain
    nested, _ = run_cell(NESTED + "finish = done\n", ["_gp_until_rec done", "c"])
    tree = debugview.timeline(session_of(nested).records())
    assert [len(node.children) for node in tree] == [4, 3, 2, 1, 0]


def test_stop_if_does_not_record_on_the_way_unless_asked():
    quiet, _ = run_cell(LONG.format(n=50), ["_gp_until k == 40", "c"])
    loud, _ = run_cell(LONG.format(n=50), ["_gp_until_rec k == 40", "c"])
    assert quiet[1]["history"]["steps"] <= 3 and loud[1]["history"]["steps"] > 80
    assert dict(quiet[1]["now"]["vars"])["k"] == "40"


def test_stop_if_finds_a_nan_and_a_step():
    source = "breakpoint()\nloss = 1.0\nfor step in range(2000):\n    loss = float('nan') if step == 1500 else loss * 0.999\n"
    stops, _ = run_cell(source, ["_gp_until step == 1200", "_gp_until loss != loss", "c"])
    assert dict(stops[1]["now"]["vars"])["step"] == "1200"
    assert dict(stops[2]["now"]["vars"])["step"] == "1500" and dict(stops[2]["now"]["vars"])["loss"] == "nan"


def test_presets_and_overrides():
    limits, sources = kernel_side.resolve({})
    assert limits["detail"] == "full" and limits["history"] == 200 * kernel_side.MB and limits["thinning"] == "off"
    assert set(sources.values()) == {"full"}
    lean, _ = kernel_side.resolve({"debug_detail": "lean"})
    assert (lean["history"], lean["repr_chars"], lean["deltas"]) == (5 * kernel_side.MB, 40, "watched")
    balanced, sources = kernel_side.resolve({"debug_detail": "balanced", "debug_history": "1GB", "debug_stats": "off", "debug_strip_items": "x"})
    assert balanced["history"] == 1024 * kernel_side.MB and sources["history"] == "override"
    assert balanced["stats"] == "off" and balanced["strip_items"] == 40 and sources["strip_items"] == "balanced"
    assert kernel_side.parse_bytes("512kb") == 512 * 1024 and kernel_side.parse_bytes("20MB") == 20 * kernel_side.MB
    assert kernel_side.resolve({"debug_detail": "nonsense"})[0]["detail"] == "full"


def test_settings_apply_to_the_next_recording():
    before, _ = run_cell(NESTED, ["n", "c"])
    kernel_side.configure({"debug_detail": "lean", "debug_history": "2MB"})
    try:
        after, _ = run_cell(NESTED, ["n", "n", "n", "n", "c"])
    finally:
        kernel_side.configure({})
    assert before[0]["history"]["detail"] == "full"
    assert after[0]["history"]["detail"] == "lean" and after[0]["history"]["cap"] == 2 * kernel_side.MB
    session = session_of(after)
    assert all(not entry["seqs"] for entry in session.entries.values())


def test_full_keeps_every_step_of_a_thousand_iterations():
    stops, _ = run_cell(LONG.format(n=1000), ["n", "n", "n", "_gp_end", "c"])
    history = stops[-1]["history"]
    assert history["detail"] == "full" and history["thinned"] == 0 and history["dropped"] == 0
    assert history["steps"] >= 2000


def test_auto_is_full_for_short_runs_and_balanced_once_a_run_is_long():
    limits, sources = kernel_side.resolve({"debug_detail": "auto", "debug_history": "50MB"})
    assert limits["detail"] == "auto" and limits["thinning"] == "off" and limits["deltas"] == "off"
    assert limits["then"] == {"events": 200_000, "thinning": "on", "deltas": "on"} and sources["history"] == "override"
    recorder = kernel_side.Recorder(limits)
    loop = {"line": 2, "header": "for k in range(6000)", "kind": "for", "vars": [], "n": 6000}

    def add(k, line):
        recorder.add({"cell": "c1", "line": line, "func": "<module>", "loops": [dict(loop, k=k)], "vars": [["k", str(k)], ["line", str(line)]]})

    for k in range(1, 4000):
        add(k, 3), add(k, 4)
    assert recorder.summary()["detail"] == "auto" and recorder.summary()["steps"] == 7998 and recorder.thinned == 0
    for k in range(4000, 6001):
        add(k, 3), add(k, 4)
    summary = recorder.summary()
    assert summary["detail"] == "auto → balanced" and summary["cap"] == 50 * kernel_side.MB
    assert recorder.thinned > 0 and summary["steps"] < 12000
    assert recorder.entries[-1]["key"] is False and recorder.entries[-1]["loops"][0]["k"] == 6000
    kernel_side.configure({"debug_detail": "auto"})
    try:
        stops, _ = run_cell(NESTED, ["n", "n", "c"])
    finally:
        kernel_side.configure({})
    assert stops[-1]["history"]["detail"] == "auto"


def test_thinning_keeps_the_newest_iterations_in_full_detail():
    recorder = kernel_side.Recorder(kernel_side.resolve({"debug_detail": "balanced"})[0])
    loop = {"line": 2, "header": "for k in range(500)", "kind": "for", "vars": [], "n": 500}
    for k in range(1, 501):
        for line in (3, 4):
            recorder.add({"cell": "c1", "line": line, "func": "<module>", "loops": [dict(loop, k=k)], "vars": [["k", str(k)], ["line", str(line)]]})
    recorder.thin()
    by_k: dict[int, list] = {}
    for entry in recorder.entries:
        by_k.setdefault(entry["loops"][0]["k"], []).append(entry["line"])
    assert by_k[500] == [3, 4] and by_k[499] == [3, 4] and by_k[498] == [3, 4]
    assert by_k[497] == [4] and by_k[420] == [4]
    assert 393 not in by_k and 390 in by_k and recorder.thinned > 0
    session = debugview.DebugSession()
    session.update({"session": 1, "history": dict(recorder.summary(), batches=[recorder.fresh()])})
    state = dict(session.full(max(session.order))["vars"])
    assert state == {"k": "500", "line": "4"}


def test_the_byte_cap_holds_over_a_hundred_thousand_iterations():
    kernel_side.configure({"debug_detail": "lean", "debug_history": "100KB"})
    try:
        stops, _ = run_cell(LONG.format(n=100_000), ["n", "n", "n", "_gp_end", "c"])
    finally:
        kernel_side.configure({})
    history = stops[-1]["history"]
    assert history["bytes"] <= 100 * 1024 and history["dropped"] > 0
    assert "history full" in debugview.history_warning(history)


class FakeTensor:
    """Looks like a CUDA tensor; any call that would read its data counts and fails."""

    __module__ = "torch"

    def __init__(self):
        self.shape, self.dtype, self.device, self.touched = (1000, 512), "torch.float32", "cuda:0", []

    def _touch(self, name):
        self.touched.append(name)
        raise RuntimeError(f"{name} would sync the GPU")

    def mean(self):
        self._touch("mean")

    def item(self):
        self._touch("item")

    def tolist(self):
        self._touch("tolist")

    def __repr__(self):
        self._touch("repr")
        return "x"

    def __len__(self):
        return 1000

    def __getitem__(self, index):
        self._touch("getitem")


def test_a_tensor_is_summarised_from_its_metadata_unless_watched():
    tensor = FakeTensor()
    source = "breakpoint()\nfor step in range(3):\n    weights = tensor\n    step2 = step\n"
    stops, _ = run_cell(source, ["n", "n", "n", "n", "_gp_watch weights", "c"], namespace={"__name__": "__main__", "tensor": tensor})
    assert set(tensor.touched) <= {"mean", "min", "max", "getitem"}
    shown = dict(stops[4]["now"]["vars"])["weights"]
    assert shown.startswith("FakeTensor(1000, 512) torch.float32 cuda:0")
    assert stops[5]["watches"][0][1].startswith("FakeTensor(1000, 512)")
    tensor.touched.clear()
    assert kernel_side.short(tensor).startswith("FakeTensor(1000, 512)") and tensor.touched == []
    assert kernel_side.full(tensor)["kind"] == "text" and tensor.touched == []


def test_no_hook_stays_installed_when_idle():
    run_cell(NESTED, ["_gp_until total > 5", "c"])
    assert sys.gettrace() is None
    if hasattr(sys, "monitoring"):
        assert all(sys.monitoring.get_tool(tool) is None for tool in (3, 4, 5))


def test_a_loop_runs_as_fast_after_a_debugging_session():
    import time

    def loop():
        total = 0
        for k in range(100_000):
            total += k
        return total

    def best():
        times = []
        for _ in range(5):
            start = time.perf_counter()
            loop()
            times.append(time.perf_counter() - start)
        return min(times)

    plain = best()
    run_cell(NESTED, ["n", "_gp_next 0", "c"])
    after = best()
    assert after < plain * 1.5 + 0.002


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
    _, raised = run_cell(OFF_BY_ONE, ["_gp_until_rec i == 3", "c"], cid="c7")
    assert isinstance(raised, IndexError)
    stops = postmortem(["q"])
    assert stops[0]["history"]["steps"] > 5 and stops[0]["now"]["cell"] == "c7"


def test_quit_aborts_the_cell_and_tracing_stops():
    stops, raised = run_cell(NESTED, ["n", "q"])
    assert len(stops) == 2 and raised is not None and type(raised).__name__ == "BdbQuit"
    assert sys.gettrace() is None
    stops, raised = run_cell(NESTED, ["n", "n", "n", "_gp_next 0", "q"])
    assert len(stops) == 5 and type(raised).__name__ == "BdbQuit" and sys.gettrace() is None
    if hasattr(sys, "monitoring"):
        assert all(sys.monitoring.get_tool(tool) is None for tool in (3, 4, 5))


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


def test_steps_into_library_code_are_recorded_with_their_file():
    library_source = "def double(xs):\n    total = 0\n    for x in xs:\n        total += x * 2\n    return total\n"
    library = {"__name__": "mylib"}
    exec(compile(library_source, "/libs/mylib.py", "exec"), library)
    linecache.cache["/libs/mylib.py"] = (len(library_source), None, library_source.splitlines(True), "/libs/mylib.py")
    source = "breakpoint()\nanswer = double([1, 2])\n"
    stops, _ = run_cell(source, ["s", "s", "s", "s", "c"], cid="c9", namespace={"__name__": "__main__", "double": library["double"]})
    inside = stops[3]
    assert inside["now"]["func"] == "double" and inside["now"]["cell"] is None and inside["now"]["file"] == "/libs/mylib.py"
    assert inside["index"] is not None
    records = [record for stop in stops for batch in stop["history"]["batches"] for record in batch["records"]]
    in_library = [record for record in records if record.get("file") == "/libs/mylib.py"]
    assert in_library and all(record["func"] == "double" and record["cell"] is None for record in in_library)
    assert any("total" in record.get("vars", {}) for record in in_library)


class CpuTensor(FakeTensor):
    """A small 1-D tensor on the CPU, in a list of them too: still nothing of its data is read
    unless it is watched, in every preset."""

    __module__ = "torch"

    def __init__(self):
        super().__init__()
        self.shape, self.device, self.ndim = (8,), "cpu", 1

    def numel(self):
        return 8

    def __len__(self):
        return 8


@pytest.mark.parametrize("preset", ["full", "balanced", "lean"])
def test_arrays_on_the_cpu_and_lists_of_tensors_are_never_read_unless_watched(preset):
    tensor = CpuTensor()
    source = "breakpoint()\nlosses = []\nfor i in range(3):\n    w = t\n    losses.append(w)\n    x = w[i]\n"
    kernel_side.configure({"debug_detail": preset})
    try:
        stops, raised = run_cell(source, ["n", "n", "n", "n", "_gp_next 0", "_gp_until i == 2", "c"], namespace={"__name__": "__main__", "t": tensor})
    finally:
        kernel_side.configure({})
    assert isinstance(raised, RuntimeError) and tensor.touched == ["getitem"]
    strip = next(view for view in stops[-1]["now"]["seqs"] if view["name"] == "w")
    assert set(strip["items"]) == {"·"} and strip["ptrs"] == [["i", 0]]
    assert "losses" in dict(stops[-1]["now"]["vars"]) and "CpuTensor(8,)" in dict(stops[-1]["now"]["vars"])["losses"]


LAST_LOOP = "breakpoint()\nacc = 0\nfor k in range(5):\n    acc += k\n"


def test_loop_end_on_the_cells_last_statement_pauses_on_the_way_out():
    stops, raised = run_cell(LAST_LOOP, ["n", "n", "n", "_gp_end", "c"])
    assert raised is None and len(stops) == 5
    assert stops[4]["event"] == "return" and dict(stops[4]["now"]["vars"])["acc"] == "10"
    stops, _ = run_cell(LAST_LOOP + "after = acc\n", ["n", "n", "n", "_gp_end", "c"])
    assert stops[4]["event"] == "line" and stops[4]["now"]["line"] == 5
    stops, _ = run_cell(LAST_LOOP, ["n", "n", "n", "_gp_goto 0 9", "c"])
    assert stops[4]["event"] == "return" and sys.gettrace() is None


def test_an_armed_cell_that_never_runs_leaves_no_trace():
    kernel_side.arm("never")
    kernel_side.run_ended()
    assert sys.gettrace() is not None
    kernel_side.new_run("c1")
    kernel_side.run_ended()
    assert sys.gettrace() is None and kernel_side._state["armed"] is None


def test_pdb_breakpoints_of_a_cell_are_in_the_payload():
    stops, _ = run_cell(NESTED, ["b 7", "c", "c"])
    assert stops[1]["breaks"] == {"c1": [7]} and stops[2]["now"]["line"] == 7


def test_a_sequence_that_is_not_indexed_gets_no_warning_from_a_loop_counter():
    source = "found = []\nbreakpoint()\nfor a in range(2):\n    b = a\n"
    stops, _ = run_cell(source, ["n", "n", "c"])
    view = seq(stops[-1], "found")
    assert view["ptrs"] == [] and debugview.seq_warnings(view, None) == []


def test_loop_keys_stop_at_a_pdb_breakpoint_and_count_each_iteration_once():
    stops, _ = run_cell(NESTED, ["n", "n", "n", "b 7", "_gp_until total > 1000", "c"])
    assert stops[5]["now"]["line"] == 7 and stops[5]["breaks"] == {"c1": [7]}
    stops, _ = run_cell(NESTED, ["n", "n", "n", "b 7", "_gp_next 1", "_gp_next 1", "c", "c"])
    assert [loop[2] for loop in loops(stops[5])] == [2, 0] and [loop[2] for loop in loops(stops[6])] == [3, 0]

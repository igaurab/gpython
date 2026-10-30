from gpython.notebook import dump, parse


def test_roundtrip():
    cells = ["import math", "x = math.pi\nprint(x)", ""]
    assert parse(dump(cells)) == cells


def test_preamble_becomes_cell():
    assert parse("a = 1\n# %%\nb = 2\n") == ["a = 1", "b = 2"]


def test_empty_file():
    assert parse("") == [""]

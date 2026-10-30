from gpython.notebook import dump, parse, split_script


def test_roundtrip():
    cells = ["import math", "x = math.pi\nprint(x)", ""]
    assert parse(dump(cells)) == cells


def test_preamble_becomes_cell():
    assert parse("a = 1\n# %%\nb = 2\n") == ["a = 1", "b = 2"]


def test_empty_file():
    assert parse("") == [""]


SCRIPT = '''"""Docstring for the file."""
import os
import sys

# helper comment
def f(x):
    return x + 1


class C:
    pass

a = 1
b = 2

c = a + b
print(c)

if __name__ == "__main__":
    f(a)
'''


def test_split_script():
    cells = split_script(SCRIPT)
    assert cells == [
        '"""Docstring for the file."""',
        "import os\nimport sys",
        "# helper comment\ndef f(x):\n    return x + 1",
        "class C:\n    pass",
        "a = 1\nb = 2",
        "c = a + b\nprint(c)",
        'if __name__ == "__main__":\n    f(a)',
    ]


def test_split_script_roundtrip_without_markers():
    cells = split_script(SCRIPT)
    assert split_script(dump(cells, markers=False)) == cells


def test_split_invalid_script_is_one_cell():
    assert split_script("x = (\n") == ["x = ("]

from pathlib import Path

from harness.sandboxing.diff_applier import DiffApplier


def test_diff_applier_search_replace(tmp_path: Path):
    target = tmp_path / "calculator.py"
    target.write_text(
        """def add(a, b):
    return a + b

def divide(a, b):
    return a / b
""",
        encoding="utf-8",
    )

    diff_block = """<<<<<<< SEARCH
def divide(a, b):
    return a / b
=======
def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
>>>>>>> REPLACE"""

    ok, msg = DiffApplier.apply_diff(target, diff_block)
    assert ok is True
    updated = target.read_text(encoding="utf-8")
    assert 'raise ValueError("Cannot divide by zero")' in updated


def test_diff_applier_line_range(tmp_path: Path):
    target = tmp_path / "greeting.py"
    target.write_text(
        """line 1
line 2: hello
line 3
""",
        encoding="utf-8",
    )

    ok, msg = DiffApplier.replace_line_range(target, 2, 2, "hello", "world")
    assert ok is True
    updated = target.read_text(encoding="utf-8")
    assert "line 2: world" in updated

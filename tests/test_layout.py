"""Часть 3: раскладка. Каждая схема проверяется validate.check_diagram:
нет наложений, пересечений линий, висящих концов, слияний без точки."""
from pathlib import Path

import pytest

from flowgen import check_diagram, extract, layout_function, load_rules, load_style, parse_file
from randprog import program

ROOT = Path(__file__).resolve().parents[1]
STYLE = load_style()


def lay(ir, style=STYLE):
    D = layout_function(ir, style)
    probs = check_diagram(D, style)
    assert probs == [], probs
    return D


def kinds(D):
    return sorted(s.kind for s in D.shapes)


@pytest.mark.parametrize("ir", [p for p in extract(parse_file(ROOT / "examples" / "demo.cpp"), load_rules())],
                         ids=lambda ir: ir["name"])
def test_demo_functions(ir):
    lay(ir)


def test_terminals_main_and_function():
    D = lay({"name": "main", "is_main": True, "body": [{"type": "process", "text": "a = 1"}]})
    texts = [s.lines[0] for s in D.shapes if s.kind == "term"]
    assert texts == ["Начало", "Конец"]
    D = lay({"name": "f", "body": [{"type": "return", "value": "a + b"}]})
    assert [s.lines[0] for s in D.shapes if s.kind == "term"] == ["Вход", "Выход: a + b"]


def test_loop_limits_are_numbered():
    D = lay({"name": "f", "body": [
        {"type": "loop", "header": "i = 0, …, n − 1", "body": [
            {"type": "loop", "header": "j = 0, …, m − 1", "body": [{"type": "process", "text": "s = s + 1"}]}]},
        {"type": "loop", "header": "пока x > 0", "test": "post", "body": [{"type": "process", "text": "x = x − 1"}]},
    ]})
    starts = [" ".join(s.lines) for s in D.shapes if s.kind == "loop_start"]
    ends = [" ".join(s.lines) for s in D.shapes if s.kind == "loop_end"]
    assert starts == ["Ц1: i = 0, …, n − 1", "Ц2: j = 0, …, m − 1", "Ц3"]
    assert ends == ["Ц2", "Ц1", "Ц3: пока x > 0"]


def test_break_and_continue_get_lines_or_connectors():
    body = [{"type": "loop", "header": "пока ok", "body": [
        {"type": "if", "cond": "a", "yes": [{"type": "continue"}], "no": []},
        {"type": "process", "text": "x = 1"},
        {"type": "if", "cond": "b", "yes": [{"type": "break"}], "no": []},
        {"type": "process", "text": "y = 2"}]}]
    D = lay({"name": "f", "body": body})
    assert len(D.dots) >= 2           # две точки слияния: перед концом цикла и после него


def test_long_function_is_split_into_columns():
    body = [{"type": "process", "text": f"x{k} = {k}"} for k in range(40)]
    D = lay({"name": "main", "is_main": True, "body": body})
    assert D.columns >= 2
    letters = [s.lines[0] for s in D.shapes if s.kind == "connector"]
    assert letters and all(len(t) == 1 for t in letters)


def test_forced_columns():
    body = [{"type": "process", "text": f"x{k} = {k}"} for k in range(12)]
    D = lay({"name": "f", "columns": 3, "body": body})
    assert D.columns == 3
    st = load_style({"columns": {"mode": 1}})
    D = lay({"name": "f", "body": body * 4}, st)
    assert D.columns == 1


def test_lane_crossing_column_cut_becomes_connectors():
    inner = [{"type": "process", "text": f"x{k} = {k}"} for k in range(14)]
    body = [{"type": "loop", "header": "k = 1, …, 9", "body": [
        {"type": "if", "cond": "плохой файл", "yes": [{"type": "continue"}], "no": []}] + inner}]
    D = lay({"name": "main", "is_main": True, "columns": 2, "body": body})
    digits = [s.lines[0] for s in D.shapes if s.kind == "connector" and s.lines[0].isdigit()]
    assert len(digits) == 2 and digits[0] == digits[1]


def test_unknown_statement_is_drawn_with_warning():
    D = layout_function({"name": "f", "body": [{"type": "magic", "text": "?"}]}, STYLE)
    assert D.warnings


@pytest.mark.parametrize("seed", range(0, 160))
def test_random_programs(seed):
    lay(program(seed))

"""Часть 4 (форматы файлов), весь конвейер и командная строка."""
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from flowgen import generate, generate_from_code, generate_from_ir, layout_function, load_style
from flowgen.pipeline import parse_formats, safe_name
from flowgen.render_drawio import to_drawio

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo.cpp"
FUNCS = ["read_numbers", "index_of_max", "grade", "print_report", "main"]


def test_parse_formats():
    assert parse_formats("png, svg;pdf") == ["png", "svg", "pdf"]
    assert parse_formats(["PDF", ".drawio"]) == ["pdf", "drawio"]
    assert parse_formats("all") == ["png", "svg", "pdf", "drawio"]
    assert parse_formats(None) == ["png"]
    with pytest.raises(ValueError):
        parse_formats("bmp")


def test_safe_name():
    assert safe_name("Matrix::print") == "Matrix.print"
    assert safe_name("f#2") == "f_2"
    assert safe_name("operator<<") == "operator_shl"
    assert safe_name("Matrix::operator()") == "Matrix.operator_call"


def test_generate_all_formats(tmp_path):
    res = generate(DEMO, tmp_path, formats="all,layout")
    assert [r.function for r in res] == FUNCS
    for r in res:
        assert r.ok, (r.function, r.error, r.problems)
        names = sorted(f.name for f in r.files)
        base = safe_name(r.function)
        assert names == sorted([f"{base}.png", f"{base}.svg", f"{base}.pdf", f"{base}.drawio", f"{base}.layout.json"])
        for f in r.files:
            assert f.stat().st_size > 500
        assert r.json_path.exists()
    png = (tmp_path / "main.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert (tmp_path / "main.pdf").read_bytes()[:5] == b"%PDF-"
    svg = (tmp_path / "main.svg").read_text(encoding="utf-8")
    assert "<svg" in svg and "<text" not in svg          # текст — кривыми, шрифты не нужны


def test_drawio_structure():
    st = load_style()
    ir = {"name": "f", "body": [
        {"type": "io", "text": "Ввод n"},
        {"type": "loop", "header": "i = 0, …, n − 1", "body": [{"type": "call", "text": "g(i)"}]},
        {"type": "if", "cond": "n > 0", "yes": [{"type": "process", "text": "a = 1"}], "no": []}]}
    D = layout_function(ir, st)
    root = ET.fromstring(to_drawio(D, st))
    cells = root.findall(".//mxCell")
    vertices = [c for c in cells if c.get("vertex") == "1"]
    edges = [c for c in cells if c.get("edge") == "1"]
    styles = " ".join(c.get("style", "") for c in vertices)
    for key in ("shape=parallelogram", "shape=loopLimit", "flipV=1", "shape=process", "rhombus", "arcSize=50"):
        assert key in styles
    assert len(edges) == len(D.lines)
    ids = {c.get("id") for c in cells}
    for e in edges:                       # все привязки ведут к существующим фигурам
        for k in ("source", "target"):
            if e.get(k):
                assert e.get(k) in ids
    texts = [c.get("value") for c in vertices]
    assert "Ввод n" in texts and "Ц1: i = 0, …, n − 1" in texts


def test_generate_from_code_and_ir(tmp_path):
    res = generate_from_code("int sq(int x) { return x * x; }", tmp_path / "a", formats="svg")
    assert res[0].ok and res[0].files[0].suffix == ".svg"
    data = json.loads(res[0].json_path.read_text(encoding="utf-8"))
    assert data["body"] == [{"type": "return", "value": "x * x", "line": 1}]
    res = generate_from_ir({"name": "ручная", "title": "Моя схема",
                            "body": [{"type": "io", "text": "Ввод a"}, {"type": "return", "value": "a"}]},
                           tmp_path / "b", formats="png")
    assert res[0].ok and res[0].files[0].name == "ручная.png"


def test_hand_written_json_file(tmp_path):
    src = tmp_path / "my.json"
    src.write_text(json.dumps({"name": "main", "body": [{"type": "process", "text": "x = 1"}]}, ensure_ascii=False),
                   encoding="utf-8")
    res = generate(src, tmp_path / "out", formats="pdf")
    assert res[0].ok and res[0].json_path is None
    bad = tmp_path / "bad.json"
    bad.write_text('{"body": [{"type": "jump"}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="неизвестный тип"):
        generate(bad, tmp_path / "out2")


def run_cli(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "flowgen", *map(str, args)], capture_output=True, text=True,
                          cwd=cwd or ROOT, encoding="utf-8")


def test_cli_basic(tmp_path):
    r = run_cli(DEMO, "-o", tmp_path, "-f", "png,drawio", "-F", "grade,main")
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["grade.drawio", "grade.json", "grade.png",
                                                           "main.drawio", "main.json", "main.png"]
    assert "grade" in r.stdout and "main" in r.stdout


def test_cli_list_ast_json_only(tmp_path):
    r = run_cli(DEMO, "--list")
    assert r.returncode == 0 and "print_report (строка 42)" in r.stdout
    r = run_cli(DEMO, "--ast")
    assert r.returncode == 0 and "function_definition" in r.stdout
    r = run_cli(DEMO, "--json-only", "-o", tmp_path)
    assert r.returncode == 0
    assert sorted(p.suffix for p in tmp_path.iterdir()) == [".json"] * 5


def test_cli_errors(tmp_path):
    assert run_cli(DEMO, "-f", "bmp").returncode == 2
    r = run_cli(DEMO, "-F", "nope", "-o", tmp_path)
    assert r.returncode == 1 and "не найдены функции: nope" in r.stderr
    assert run_cli(tmp_path / "missing.cpp").returncode == 1
    assert run_cli().returncode == 2


def test_cli_init_config(tmp_path):
    r = run_cli("--init-config", tmp_path)
    assert r.returncode == 0
    assert (tmp_path / "style.json").exists() and (tmp_path / "rules.json").exists()
    style = json.loads((tmp_path / "style.json").read_text(encoding="utf-8"))
    style["labels"] = {"yes": "+", "no": "−"}
    (tmp_path / "style.json").write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "o"
    r = run_cli(DEMO, "-F", "grade", "-f", "layout", "--style", tmp_path / "style.json", "-o", out, "--no-json")
    assert r.returncode == 0, r.stderr
    lay = json.loads((out / "grade.layout.json").read_text(encoding="utf-8"))
    assert {l["text"] for l in lay["labels"] if l["kind"] == "label"} == {"+", "−"}

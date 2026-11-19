"""Сборка всех частей вместе.

    код C++ ──(1) cpp_parser──> дерево ──(2) extractor──> JSON для каждой функции
        ──(3) layout──> координаты ──(4) render_*──> png / svg / pdf / drawio

Главная функция — generate(): принимает файл .cpp (или JSON, написанный
вручную в том же формате, что выдаёт часть 2) и для каждой функции
делает отдельный рисунок.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from . import ir as irmod
from .cpp_parser import ParsedSource, parse_code, parse_file
from .extractor import extract
from .geometry import Diagram
from .layout import layout_function
from .style import load_rules, load_style
from .validate import check_diagram

FORMATS = ("png", "svg", "pdf", "drawio", "layout")
DEFAULT_FORMATS = ("png",)
CPP_EXT = {".cpp", ".cc", ".cxx", ".c++", ".cp", ".c", ".h", ".hpp", ".hh", ".hxx", ".ino"}

PathLike = Union[str, Path]


@dataclass
class Result:
    """Что получилось для одной функции."""
    function: str                       # имя (id) функции
    files: List[Path] = field(default_factory=list)
    json_path: Optional[Path] = None
    width: float = 0.0                  # размер схемы, дюймы
    height: float = 0.0
    columns: int = 1
    warnings: List[str] = field(default_factory=list)    # упрощения и пропуски при разборе
    problems: List[str] = field(default_factory=list)    # ошибки раскладки (обычно пусто)
    error: Optional[str] = None                          # не удалось нарисовать совсем

    @property
    def ok(self) -> bool:
        return self.error is None and not self.problems


def parse_formats(formats: Union[None, str, Iterable[str]]) -> List[str]:
    """'png,svg' или ['png', 'svg'] -> ['png', 'svg']; 'all' — все рисунки."""
    if formats is None:
        return list(DEFAULT_FORMATS)
    if isinstance(formats, str):
        items = [x for x in re.split(r"[,\s;]+", formats) if x]
    else:
        items = [str(x) for x in formats]
    out: List[str] = []
    for x in items:
        x = x.lower().lstrip(".")
        if x == "all":
            for f in ("png", "svg", "pdf", "drawio"):
                if f not in out:
                    out.append(f)
            continue
        if x == "jpg" or x == "jpeg":
            raise ValueError("формат jpg не поддерживается: используйте png")
        if x not in FORMATS:
            raise ValueError(f"неизвестный формат «{x}»; доступны: {', '.join(FORMATS)}, all")
        if x not in out:
            out.append(x)
    return out or list(DEFAULT_FORMATS)


_OPERATOR_WORDS = {
    "<<": "shl", ">>": "shr", "==": "eq", "!=": "ne", "<=": "le", ">=": "ge", "<": "lt", ">": "gt",
    "+": "plus", "-": "minus", "*": "mul", "/": "div", "%": "mod", "=": "assign", "[]": "index", "()": "call",
    "++": "inc", "--": "dec", "+=": "plus_assign", "-=": "minus_assign", "*=": "mul_assign", "/=": "div_assign",
    "!": "not", "&&": "and", "||": "or", "<=>": "cmp", "->": "arrow", "&": "amp", "|": "bar", "^": "xor", "~": "tilde",
}


def safe_name(name: str) -> str:
    """Имя функции -> имя файла (Matrix::print -> Matrix.print, f#2 -> f_2, operator<< -> operator_shl)."""
    def op(m):
        sym = m.group(1).replace(" ", "")
        return "operator_" + _OPERATOR_WORDS.get(sym, "_".join(f"{ord(c):x}" for c in sym))
    s = re.sub(r"operator\s*([^\w\s#][^\w#]*)", op, name)
    s = s.replace("::", ".").replace("#", "_").replace("~", "destructor_")
    s = re.sub(r'[<>:"/\\|?*\s]+', "_", s)
    s = s.strip("._ ")
    return s or "function"


# ------------------------------------------------------------------ части 1 и 2

def load_functions(source: PathLike, rules: Optional[dict] = None,
                   functions: Optional[Sequence[str]] = None) -> Tuple[List[dict], List[str]]:
    """Файл .cpp -> список JSON функций (части 1 и 2); файл .json читается как есть.
    Возвращает (функции, общие предупреждения)."""
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"файл не найден: {path}")
    if path.suffix.lower() == ".json":
        irs = irmod.load(path)
        for k, ir in enumerate(irs):
            ir.setdefault("id", ir.get("name") or f"{path.stem}_{k + 1}")
        return _select(irs, functions), []
    return functions_from_parsed(parse_file(path), rules, functions)


def functions_from_code(code: str, rules: Optional[dict] = None, functions: Optional[Sequence[str]] = None,
                        name: str = "<код>") -> Tuple[List[dict], List[str]]:
    return functions_from_parsed(parse_code(code, name), rules, functions)


def functions_from_parsed(ps: ParsedSource, rules: Optional[dict] = None,
                          functions: Optional[Sequence[str]] = None) -> Tuple[List[dict], List[str]]:
    rules = rules if rules is not None else load_rules()
    notes = []
    for ln, text in ps.errors[:10]:
        notes.append(f"строка {ln}: tree-sitter не понял код «{text}» — проверьте, компилируется ли программа")
    if len(ps.errors) > 10:
        notes.append(f"…и ещё мест с ошибками разбора: {len(ps.errors) - 10}")
    irs = extract(ps, rules)
    return _select(irs, functions), notes


class FunctionNotFound(LookupError):
    """Запрошенной функции нет в файле."""


def _select(irs: List[dict], functions: Optional[Sequence[str]]) -> List[dict]:
    if not functions:
        return irs
    wanted = list(functions)
    out = []
    for ir in irs:
        names = {ir.get("id"), ir.get("name"), str(ir.get("name", "")).split("::")[-1]}
        if names & set(wanted):
            out.append(ir)
    missing = [w for w in wanted if not any(w in {ir.get("id"), ir.get("name"), str(ir.get("name", "")).split("::")[-1]}
                                            for ir in irs)]
    if missing:
        have = ", ".join(str(ir.get("id")) for ir in irs) or "нет функций"
        raise FunctionNotFound(f"не найдены функции: {', '.join(missing)} (есть: {have})")
    return out


# ------------------------------------------------------------------ части 3 и 4

def layout(ir: dict, style: Optional[dict] = None) -> Diagram:
    return layout_function(ir, style if style is not None else load_style())


def render(D: Diagram, style: dict, path: PathLike, fmt: Optional[str] = None) -> Path:
    """Рисует готовую раскладку в файл нужного формата."""
    path = Path(path)
    fmt = (fmt or path.suffix.lstrip(".")).lower()
    if fmt in ("png", "svg", "pdf"):
        from .render_mpl import draw
        draw(D, style, str(path), fmt)
    elif fmt == "drawio":
        from .render_drawio import draw
        draw(D, style, str(path))
    elif fmt == "layout":
        path.write_text(json.dumps(D.to_dict(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    else:
        raise ValueError(f"неизвестный формат «{fmt}»")
    return path


def _ext(fmt: str) -> str:
    return "layout.json" if fmt == "layout" else fmt


# ------------------------------------------------------------------ всё вместе

def _prepare(formats, style, rules, columns, dpi, json_only, title=None):
    fmts = [] if json_only else parse_formats(formats)
    st = load_style(style)
    rl = load_rules(rules)
    if title is not None:
        st.setdefault("title", {})["show"] = bool(title)
    if columns is not None:
        st.setdefault("columns", {})["mode"] = "auto" if str(columns).lower() == "auto" else columns
    if dpi is not None:
        st.setdefault("output", {})["dpi"] = int(dpi)
    return fmts, st, rl


def generate(source: PathLike, out_dir: Optional[PathLike] = None, formats: Union[None, str, Iterable[str]] = None,
             style: Union[None, str, Path, dict] = None, rules: Union[None, str, Path, dict] = None,
             functions: Optional[Sequence[str]] = None, save_json: bool = True, json_only: bool = False,
             columns: Union[None, int, str] = None, check: bool = True, dpi: Optional[int] = None,
             title: Optional[bool] = None) -> List[Result]:
    """Строит блок-схемы всех (или выбранных) функций файла — по рисунку на функцию.

    source    — файл .cpp (.cc, .h, …) или .json (JSON в формате части 2, можно написанный вручную);
    out_dir   — папка для результатов (по умолчанию «<имя файла>_схемы» рядом с ним);
    formats   — 'png', 'svg', 'pdf', 'drawio', 'layout' или несколько: 'png,svg', ['png', 'pdf'], 'all';
    style     — свой style.json (путь или словарь) поверх стандартного;
    rules     — свой rules.json (словарь упрощений) поверх стандартного;
    functions — нарисовать только эти функции (по имени);
    save_json — сохранять JSON каждой функции (выход части 2);
    json_only — только JSON, без рисунков;
    columns   — число столбцов (1, 2, …) или 'auto';
    check     — проверять готовую раскладку на наложения и пересечения;
    dpi       — разрешение PNG;
    title     — подпись «имя(параметры)» над схемой (по умолчанию из style.json — нет).
    """
    src = Path(source)
    fmts, st, rl = _prepare(formats, style, rules, columns, dpi, json_only, title)
    irs, notes = load_functions(src, rl, functions)
    if src.suffix.lower() == ".json":
        save_json = False                 # JSON и так есть — это входной файл
    out = Path(out_dir) if out_dir is not None else src.with_name(f"{src.stem}_схемы")
    return _draw_all(irs, notes, out, st, fmts, save_json, json_only, check)


def generate_from_code(code: str, out_dir: PathLike, name: str = "code", formats=None, style=None, rules=None,
                       functions=None, save_json: bool = True, json_only: bool = False, columns=None,
                       check: bool = True, dpi: Optional[int] = None, title: Optional[bool] = None) -> List[Result]:
    """То же, что generate(), но код C++ передаётся строкой."""
    fmts, st, rl = _prepare(formats, style, rules, columns, dpi, json_only, title)
    irs, notes = functions_from_code(code, rl, functions, name)
    return _draw_all(irs, notes, Path(out_dir), st, fmts, save_json, json_only, check)


def generate_from_ir(irs: Union[dict, List[dict]], out_dir: PathLike, formats=None, style=None,
                     columns=None, check: bool = True, dpi: Optional[int] = None,
                     title: Optional[bool] = None) -> List[Result]:
    """Рисует уже готовый JSON (словарь или список словарей в формате части 2)."""
    items = [irs] if isinstance(irs, dict) else list(irs)
    for k, ir in enumerate(items):
        irmod.validate(ir, f"функция {k + 1}: ")
        ir.setdefault("name", f"function_{k + 1}")
        ir.setdefault("id", ir["name"])
    fmts, st, _ = _prepare(formats, style, None, columns, dpi, False, title)
    return _draw_all(items, [], Path(out_dir), st, fmts, False, False, check)


def _draw_all(irs: List[dict], notes: List[str], out: Path, st: dict, fmts: List[str], save_json: bool,
              json_only: bool, check: bool) -> List[Result]:
    out.mkdir(parents=True, exist_ok=True)
    results: List[Result] = []
    used = set()
    for ir in irs:
        fid = str(ir.get("id") or ir.get("name") or "function")
        base = safe_name(fid)
        while base.lower() in used:
            base += "_"
        used.add(base.lower())
        res = Result(function=fid, warnings=list(notes) + list(ir.get("warnings", [])))
        if save_json or json_only:
            res.json_path = out / f"{base}.json"
            irmod.dump({k: v for k, v in ir.items() if k != "id"}, res.json_path)
        if json_only:
            results.append(res)
            continue
        try:
            D = layout_function(ir, st)
            res.width, res.height, res.columns = D.width, D.height, D.columns
            res.warnings += [w for w in D.warnings if w not in res.warnings]
            if check:
                res.problems = check_diagram(D, st)
            for fmt in fmts:
                res.files.append(render(D, st, out / f"{base}.{_ext(fmt)}", fmt))
        except Exception as e:          # одна сломанная функция не мешает остальным
            res.error = f"{type(e).__name__}: {e}"
        results.append(res)
    return results

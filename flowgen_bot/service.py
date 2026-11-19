"""Рисование присланного кода — без Telegram, чтобы это можно было проверять отдельно.

render_job() выполняется в отдельном процессе: так тяжёлая или зависшая программа
не мешает боту отвечать другим людям и её можно прервать по времени.
"""
from __future__ import annotations

import hashlib
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

CODE_EXT = {".cpp", ".cc", ".cxx", ".c++", ".cp", ".c", ".h", ".hpp", ".hh", ".hxx", ".ino", ".txt"}
JSON_EXT = {".json"}

_CODE_HINT = re.compile(r"#\s*include|\bint\s+main\s*\(|"
                        r"\b(?:int|void|double|float|char|bool|long|unsigned|string|auto|size_t)[\s*&]+[A-Za-z_]\w*"
                        r"(?:::\w+)*\s*\([^;{}]*\)\s*(?:const\s*)?\{")


def looks_like_code(text: str) -> bool:
    return bool(_CODE_HINT.search(text)) and "{" in text


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class FunctionOut:
    function: str
    ok: bool
    width: float = 0.0
    height: float = 0.0
    columns: int = 1
    warnings: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    error: Optional[str] = None
    files: List[str] = field(default_factory=list)
    ir: Optional[str] = None                  # JSON функции (текст)


@dataclass
class JobOut:
    functions: List[FunctionOut] = field(default_factory=list)
    files: List[Tuple[str, bytes]] = field(default_factory=list)   # что отправить пользователю
    notes: List[str] = field(default_factory=list)                 # общие замечания
    total_functions: int = 0
    error: Optional[str] = None
    duration_ms: int = 0


def render_job(code: str, filename: str, settings: dict, max_functions: int = 40) -> JobOut:
    """Код (или JSON схемы) -> файлы схем по настройкам пользователя."""
    from flowgen.pipeline import generate, load_functions, safe_name
    from flowgen.style import load_rules

    from .settings import normalize, style_overrides

    t0 = time.monotonic()
    s = normalize(settings)
    out = JobOut()
    ext = Path(filename).suffix.lower()
    is_json = ext in JSON_EXT
    stem = safe_name(Path(filename).stem) or "code"
    try:
        with tempfile.TemporaryDirectory(prefix="flowgen_") as tmp:
            src = Path(tmp) / (stem + (".json" if is_json else ".cpp"))
            src.write_text(code, encoding="utf-8")
            irs, _ = load_functions(src, load_rules())
            out.total_functions = len(irs)
            if not irs:
                out.duration_ms = int((time.monotonic() - t0) * 1000)
                return out
            names = [ir.get("id") or ir.get("name") for ir in irs]
            only = None
            if len(names) > max_functions:
                only = names[:max_functions]
                out.notes.append(f"Функций {len(names)}, нарисованы первые {max_functions}.")
            res_dir = Path(tmp) / "out"
            results = generate(src, res_dir, formats=s["formats"] or None, style=style_overrides(s),
                               functions=only, save_json=True, json_only=not s["formats"],
                               columns=s["columns"], dpi=s["dpi"], title=s["title"] or None)
            if is_json:
                for r in results:          # у JSON на входе flowgen сам JSON не сохраняет
                    r.json_path = None
            for r in results:
                fo = FunctionOut(function=r.function, ok=r.error is None, width=round(r.width, 2),
                                 height=round(r.height, 2), columns=r.columns, warnings=list(r.warnings),
                                 problems=list(r.problems), error=r.error)
                if r.json_path is not None and r.json_path.exists():
                    fo.ir = r.json_path.read_text(encoding="utf-8")
                send = list(r.files)
                if s["json"] and r.json_path is not None:
                    send.append(r.json_path)
                for f in send:
                    out.files.append((f.name, f.read_bytes()))
                    fo.files.append(f.name)
                out.functions.append(fo)
    except Exception as e:                   # синтаксис JSON, нехватка памяти и т. п.
        out.error = f"{type(e).__name__}: {e}"
    out.duration_ms = int((time.monotonic() - t0) * 1000)
    return out

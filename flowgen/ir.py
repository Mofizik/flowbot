"""Промежуточный формат (JSON) — то, что выдаёт часть 2 и читает часть 3.

Этот же формат можно писать вручную и рисовать схему без кода на C++.

Файл описывает одну функцию:

    {
      "name": "main",              имя функции (для заголовка и имени файла)
      "params": ["a", "b"],        имена параметров (необязательно)
      "is_main": true,             true -> «Начало»/«Конец», иначе «Вход»/«Выход»
      "body": [ оператор, … ],
      необязательные поля:
      "title": "…",                свой заголовок над схемой (рисуется всегда, если задан)
      "start": "…", "end": "…",    свой текст терминаторов начала и конца
      "columns": 2                 число столбцов для этой схемы
    }

Операторы:
    {"type": "process", "text": "a = 0"}                 действие (прямоугольник)
    {"type": "io",      "text": "Вывод a"}               ввод/вывод (параллелограмм)
    {"type": "call",    "text": "f(a, b)"}               вызов своей функции
    {"type": "if", "cond": "a > 0", "yes": [...], "no": [...]}
    {"type": "loop", "header": "i = 0, …, n − 1", "body": [...],
                     "test": "pre" | "post", "infinite": false}
    {"type": "switch", "value": "x", "cases": [{"cond": "x = 1", "body": [...]}],
                       "default": [...]}
    {"type": "break"} / {"type": "continue"}
    {"type": "return", "value": "a" | null}              выход из функции
    {"type": "end", "text": "Конец"}                     терминатор со своим текстом
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

IR_VERSION = 1

STATEMENT_TYPES = ("process", "io", "call", "if", "loop", "switch", "break", "continue", "return", "end")


class IRError(ValueError):
    pass


def validate(ir: dict, where: str = "") -> None:
    """Проверяет JSON схемы и бросает IRError с понятным сообщением."""
    if not isinstance(ir, dict):
        raise IRError(f"{where}: ожидался объект JSON с полем body")
    if "body" not in ir or not isinstance(ir["body"], list):
        raise IRError(f"{where}: нет списка body")
    if "params" in ir and not isinstance(ir["params"], list):
        raise IRError(f"{where}: params должен быть списком строк")
    _check_list(ir["body"], f"{where}body")


def _check_list(stmts, path: str) -> None:
    if not isinstance(stmts, list):
        raise IRError(f"{path}: ожидался список операторов")
    for i, s in enumerate(stmts):
        p = f"{path}[{i}]"
        if not isinstance(s, dict) or "type" not in s:
            raise IRError(f"{p}: у оператора нет поля type")
        t = s["type"]
        if t not in STATEMENT_TYPES:
            raise IRError(f"{p}: неизвестный тип «{t}» (допустимы: {', '.join(STATEMENT_TYPES)})")
        if t in ("process", "io", "call", "end"):
            if not isinstance(s.get("text", ""), str):
                raise IRError(f"{p}: text должен быть строкой")
        if t == "if":
            if not isinstance(s.get("cond"), str):
                raise IRError(f"{p}: у if нет строки cond")
            _check_list(s.get("yes", []), p + ".yes")
            _check_list(s.get("no", []), p + ".no")
        if t == "loop":
            if not isinstance(s.get("header", ""), str):
                raise IRError(f"{p}: header должен быть строкой")
            if s.get("test", "pre") not in ("pre", "post"):
                raise IRError(f"{p}: test может быть только pre или post")
            _check_list(s.get("body", []), p + ".body")
        if t == "switch":
            cases = s.get("cases", [])
            if not isinstance(cases, list):
                raise IRError(f"{p}: cases должен быть списком")
            for j, c in enumerate(cases):
                if not isinstance(c, dict) or not isinstance(c.get("cond"), str):
                    raise IRError(f"{p}.cases[{j}]: нужна строка cond")
                _check_list(c.get("body", []), f"{p}.cases[{j}].body")
            if s.get("default") is not None:
                _check_list(s["default"], p + ".default")
        if t == "return" and s.get("value") is not None:
            if isinstance(s["value"], (int, float)) and not isinstance(s["value"], bool):
                s["value"] = str(s["value"])          # "value": 0 — тоже можно
            if not isinstance(s["value"], str):
                raise IRError(f"{p}: value у return должен быть строкой или null")


def load(path) -> List[dict]:
    """Читает JSON-файл: одна функция (объект) или несколько (список объектов)."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise IRError(f"{path}: ошибка JSON в строке {e.lineno}: {e.msg}") from e
    items = data if isinstance(data, list) else [data]
    for k, ir in enumerate(items):
        validate(ir, f"{path.name}" + (f"[{k}]" if isinstance(data, list) else "") + ": ")
        ir.setdefault("name", path.stem if len(items) == 1 else f"{path.stem}_{k + 1}")
        ir.setdefault("params", [])
        ir.setdefault("is_main", ir["name"] == "main")
    return items


def dump(ir: dict, path) -> None:
    Path(path).write_text(json.dumps(ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

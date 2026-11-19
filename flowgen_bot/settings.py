"""Настройки пользователя и меню из кнопок под сообщением."""
from __future__ import annotations

import copy
from typing import Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

FORMATS = ("png", "svg", "pdf", "drawio")
FORMAT_NAMES = {"png": "PNG", "svg": "SVG", "pdf": "PDF", "drawio": "draw.io"}
COLUMNS = ("auto", "1", "2", "3")
LABELS = (("да", "нет"), ("+", "−"), ("истина", "ложь"))
DPIS = (150, 250, 400)

DEFAULTS = {
    "formats": ["png"],
    "json": False,          # присылать JSON функций (часть 2)
    "columns": "auto",
    "title": False,         # подпись «имя(параметры)» над схемой
    "labels": ["да", "нет"],
    "arrows": "all",        # all — стрелки на всех линиях; gost — только где требует ГОСТ
    "dpi": 250,
}


def normalize(data: Optional[dict]) -> dict:
    """Настройки из базы + значения по умолчанию; неизвестное отбрасывается."""
    s = copy.deepcopy(DEFAULTS)
    if not data:
        return s
    fm = [f for f in data.get("formats", []) if f in FORMATS]
    s["formats"] = [f for f in FORMATS if f in fm]
    s["json"] = bool(data.get("json", s["json"]))
    if not s["formats"] and not s["json"]:
        s["formats"] = ["png"]
    if str(data.get("columns")) in COLUMNS:
        s["columns"] = str(data["columns"])
    s["title"] = bool(data.get("title", s["title"]))
    if tuple(data.get("labels", ())) in LABELS:
        s["labels"] = list(data["labels"])
    if data.get("arrows") in ("all", "gost"):
        s["arrows"] = data["arrows"]
    if data.get("dpi") in DPIS:
        s["dpi"] = data["dpi"]
    return s


def style_overrides(s: dict) -> dict:
    """Настройки -> поправки к style.json для flowgen."""
    return {"labels": {"yes": s["labels"][0], "no": s["labels"][1]},
            "arrow": {"only_when_needed": s["arrows"] == "gost"}}


def describe(s: dict) -> str:
    fm = ", ".join(FORMAT_NAMES[f] for f in s["formats"]) or "—"
    cols = "подбирается сам" if s["columns"] == "auto" else s["columns"]
    return ("<b>Настройки</b>\n"
            f"Форматы: {fm}{' + JSON' if s['json'] else ''}\n"
            f"Столбцов: {cols}\n"
            f"Заголовок над схемой: {'да' if s['title'] else 'нет'}\n"
            f"Подписи у ромба: «{s['labels'][0]}» / «{s['labels'][1]}»\n"
            f"Стрелки: {'на всех линиях' if s['arrows'] == 'all' else 'только где нужно по ГОСТ'}\n"
            f"Качество PNG: {s['dpi']} dpi\n\n"
            "Нажимайте кнопки — настройки сохраняются сразу.")


def keyboard(s: dict) -> InlineKeyboardMarkup:
    def mark(on: bool) -> str:
        return "✅ " if on else ""

    B = InlineKeyboardButton
    rows = [
        [B(text=mark(f in s["formats"]) + FORMAT_NAMES[f], callback_data=f"s:fmt:{f}") for f in FORMATS],
        [B(text=mark(s["json"]) + "JSON функций", callback_data="s:json")],
        [B(text=("✅ " if s["columns"] == c else "") + ("Столбцы: авто" if c == "auto" else c),
           callback_data=f"s:col:{c}") for c in COLUMNS],
        [B(text=f"Заголовок: {'да' if s['title'] else 'нет'}", callback_data="s:title"),
         B(text=f"Подписи: {s['labels'][0]}/{s['labels'][1]}", callback_data="s:labels")],
        [B(text=f"Стрелки: {'все' if s['arrows'] == 'all' else 'по ГОСТ'}", callback_data="s:arrows"),
         B(text=f"PNG: {s['dpi']} dpi", callback_data="s:dpi")],
        [B(text="↺ По умолчанию", callback_data="s:reset"),
         B(text="🖼 Перерисовать последний код", callback_data="s:again")],
        [B(text="Готово", callback_data="s:close")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def apply(s: dict, action: str) -> Tuple[dict, Optional[str]]:
    """Нажатие кнопки -> (новые настройки, всплывающее сообщение или None)."""
    s = copy.deepcopy(s)
    parts = action.split(":")
    key = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    if key == "fmt" and arg in FORMATS:
        if arg in s["formats"]:
            if len(s["formats"]) == 1 and not s["json"]:
                return s, "Нужен хотя бы один формат"
            s["formats"].remove(arg)
        else:
            s["formats"].append(arg)
        s["formats"] = [f for f in FORMATS if f in s["formats"]]
    elif key == "json":
        if s["json"] and not s["formats"]:
            return s, "Нужен хотя бы один формат"
        s["json"] = not s["json"]
    elif key == "col" and arg in COLUMNS:
        s["columns"] = arg
    elif key == "title":
        s["title"] = not s["title"]
    elif key == "labels":
        i = next((k for k, l in enumerate(LABELS) if list(l) == s["labels"]), 0)
        s["labels"] = list(LABELS[(i + 1) % len(LABELS)])
    elif key == "arrows":
        s["arrows"] = "gost" if s["arrows"] == "all" else "all"
    elif key == "dpi":
        i = DPIS.index(s["dpi"]) if s["dpi"] in DPIS else 1
        s["dpi"] = DPIS[(i + 1) % len(DPIS)]
    elif key == "reset":
        s = copy.deepcopy(DEFAULTS)
    else:
        return s, None
    return normalize(s), None

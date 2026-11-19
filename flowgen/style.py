"""Часть 5. Настройки: файл стиля (style.json) и словарь упрощений (rules.json).

Свои файлы не обязаны содержать все ключи: они накладываются поверх
стандартных из папки data/, недостающие значения берутся оттуда.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Union

DATA = Path(__file__).with_name("data")

Source = Union[None, str, Path, dict]


def deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load(default_name: str, user: Source) -> dict:
    base = json.loads((DATA / default_name).read_text(encoding="utf-8"))
    if user is None:
        return base
    if isinstance(user, dict):
        return deep_merge(base, user)
    path = Path(user)
    try:
        extra = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"файл настроек не найден: {path}") from None
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: ошибка JSON в строке {e.lineno}: {e.msg}") from None
    if not isinstance(extra, dict):
        raise ValueError(f"{path}: ожидался объект JSON")
    return deep_merge(base, extra)


def load_style(user: Source = None) -> dict:
    """Стиль рисования: шрифт, размеры блоков, отступы, подписи, столбцы."""
    return _load("style.json", user)


def load_rules(user: Source = None) -> dict:
    """Словарь упрощений текста блоков."""
    return _load("rules.json", user)


def default_style_path() -> Path:
    return DATA / "style.json"


def default_rules_path() -> Path:
    return DATA / "rules.json"

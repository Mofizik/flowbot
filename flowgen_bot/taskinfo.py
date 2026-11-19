"""Номер задания, вариант, лабораторная, автор и группа — из комментариев и строк кода.

Студенты обычно пишут их в начале программы:
    // Лабораторная работа №5, вариант 12
    cout << "Автор: Катеренюк Илья Андреевич гр. 6352\\n";
    cout << "Задание 8 пункт в\\n";
Ничего не нашлось — поле остаётся пустым.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

_NUM = r"(?:№|N|#|номер)?\s*"

_TASK = re.compile(r"(?<![а-яё\w])(?:задани[еяию]|задач[аи]|упражнени[ея]|task)\s*" + _NUM +
                   r"(\d{1,3}(?:\.\d{1,3})?)"
                   r"(?:\s*[,.]?\s*(?:пункт|подпункт|п\.)\s*([а-яёa-z0-9]{1,3})\b)?", re.I)
_SUBPOINT = re.compile(r"(?<![а-яё\w])(?:пункт|подпункт)\s*([а-яёa-z0-9]{1,3})(?![а-яё\w])", re.I)
_VARIANT = re.compile(r"(?<![а-яё\w])(?:вариант|вар\.|variant)\s*" + _NUM + r"(\d{1,3})", re.I)
_LAB = re.compile(r"(?:(?<![а-яё\w])лабораторн\w*\s+работ\w*|(?<![а-яё\w])лаб\.?\s*раб\w*|"
                  r"(?<![а-яёa-z\w])лр|(?<![а-яёa-z])lab(?:oratory)?\s*(?:work)?)\s*[-_]?\s*" + _NUM + r"(\d{1,2})(?!\d)",
                  re.I)
_GROUP = re.compile(r"(?<![а-яё\w])(?:группа|гр\.?)\s*" + _NUM + r"(\d{4})(?!\d)", re.I)
_AUTHOR = re.compile(r"(?<![а-яёА-ЯЁ\w])(?i:автор|выполнил[аи]?|студент(?:ка)?|разработчик)"
                     r"(?:[ \t]+(?i:студент(?:ка)?|группы|гр\.?)|[ \t]*\d{4})*[ \t]*[:\-—–]?[ \t]*"
                     r"([А-ЯЁ][а-яё]+(?:[ \t]+[А-ЯЁ](?:[а-яё]+|\.)){0,2}(?:[ \t]*[А-ЯЁ]\.)?)")


def task_info(code: str) -> Dict[str, Optional[str]]:
    """{'task', 'subpoint', 'variant', 'lab', 'group', 'author'}; чего нет — None."""
    # данные о задании пишут в начале; пробелы схлопываем — так регулярки работают быстро на любом тексте
    text = re.sub(r"[ \t\u00a0]+", " ", code[:30_000])
    info: Dict[str, Optional[str]] = {k: None for k in ("task", "subpoint", "variant", "lab", "group", "author")}
    m = _TASK.search(text)
    if m:
        info["task"] = m.group(1)
        info["subpoint"] = m.group(2)
        if info["subpoint"] is None:
            tail = text[m.end():m.end() + 40]
            m2 = _SUBPOINT.match(tail.lstrip(" ,.:—-"))
            if m2:
                info["subpoint"] = m2.group(1)
    for key, rx in (("variant", _VARIANT), ("lab", _LAB), ("group", _GROUP)):
        m = rx.search(text)
        if m:
            info[key] = m.group(1)
    m = _AUTHOR.search(text)
    if m:
        info["author"] = " ".join(m.group(1).split())
    if info["subpoint"]:
        info["subpoint"] = info["subpoint"].lower()
    return info

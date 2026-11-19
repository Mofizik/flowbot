"""Измерение и перенос текста. Ширина считается по тому же шрифту,
которым потом рисует matplotlib, поэтому текст точно влезает в блоки."""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import List, Sequence

from matplotlib.font_manager import FontProperties, findfont
from matplotlib.textpath import TextToPath

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

_T2P = TextToPath()


class Metrics:
    def __init__(self, family: Sequence[str], size: float, line_spacing: float = 1.2):
        self.family = list(family)
        self.size = float(size)
        self.fp = FontProperties(family=self.family, size=self.size)
        self.font_file = findfont(self.fp)
        self.line_h = self.size * line_spacing / 72.0       # высота строки, дюймы
        self.glyph_h = self.size * 0.78 / 72.0               # высота букв без интерлиньяжа
        self._w = lru_cache(maxsize=20000)(self._width)

    def _width(self, s: str) -> float:
        if not s:
            return 0.0
        w, _, _ = _T2P.get_text_width_height_descent(s, self.fp, ismath=False)
        return w / 72.0

    def width(self, s: str) -> float:
        return self._w(s)

    # ------------------------------------------------------------ перенос
    def longest_word(self, text: str) -> float:
        words = [w for w in re.split(r"\s+", text) if w]
        return max((self.width(w) for w in words), default=0.0)

    def wrap(self, text: str, max_w: float, balance: bool = True) -> List[str]:
        """Перенос по словам. Сначала стараемся не рвать отдельные
        действия «a = 0; b = 1», потом — слова. При balance строки
        выравниваются по длине (без «висящего» последнего слова)."""
        lines: List[str] = []
        for paragraph in text.split("\n"):
            par = paragraph.strip()
            got = self._wrap_paragraph(par, max_w)
            # строки из нескольких действий «a = 1; b = 2» уже разбиты по «;» — их не трогаем
            if balance and len(got) > 1 and ";" not in par:
                got = self._balance(par, max_w, len(got), got)
            lines.extend(got)
        return lines or [""]

    def _balance(self, par: str, max_w: float, n: int, got: List[str]) -> List[str]:
        """Самая узкая ширина, при которой абзац всё ещё занимает n строк."""
        lo = max(self.longest_word(par), max_w / (n + 1))
        hi = max_w
        best = got
        if lo >= hi:
            return got
        for _ in range(14):
            mid = (lo + hi) / 2
            trial = self._wrap_paragraph(par, mid)
            if len(trial) <= n and all(self.width(t) <= max_w + 1e-9 for t in trial):
                hi = mid
                best = trial
            else:
                lo = mid
        return best

    def _wrap_paragraph(self, par: str, max_w: float) -> List[str]:
        if not par:
            return [""]
        if self.width(par) <= max_w:
            return [par]
        parts = [p for p in re.split(r"(?<=;)\s+", par) if p]
        lines: List[str] = []
        cur = ""
        for part in parts:
            cand = f"{cur} {part}" if cur else part
            if self.width(cand) <= max_w:
                cur = cand
                continue
            if cur and self.width(part) <= max_w:
                lines.append(cur)
                cur = part
                continue
            # действие длиннее строки: переносим по словам
            for word in part.split(" "):
                cand = f"{cur} {word}" if cur else word
                if self.width(cand) <= max_w:
                    cur = cand
                    continue
                if cur:
                    lines.append(cur)
                if self.width(word) <= max_w:
                    cur = word
                else:
                    pieces = self._split_long(word, max_w)
                    lines.extend(pieces[:-1])
                    cur = pieces[-1]
        if cur:
            lines.append(cur)
        return lines

    def _split_long(self, word: str, max_w: float) -> List[str]:
        """Очень длинное «слово» без пробелов: режем после ( [ , . + − и т. п."""
        out: List[str] = []
        rest = word
        while rest and self.width(rest) > max_w:
            cut = 0
            for i in range(1, len(rest)):
                if self.width(rest[:i]) > max_w:
                    break
                if rest[i - 1] in "([{,.+-−*/=_:;<>" or i == 1:
                    cut = i
            if cut <= 1:
                # нет удобного места: режем по символам
                cut = 1
                while cut < len(rest) and self.width(rest[:cut + 1]) <= max_w:
                    cut += 1
            out.append(rest[:cut])
            rest = rest[cut:]
        out.append(rest)
        return out

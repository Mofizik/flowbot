"""Геометрические примитивы схемы. Координаты в дюймах, ось y направлена вниз."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

Point = Tuple[float, float]
Box = Tuple[float, float, float, float]   # x0, y0, x1, y1

EPS = 1e-6
SEG_HALF = 0.012       # «толщина» линии при проверке столкновений


@dataclass
class Shape:
    kind: str           # term, process, io, cond, call, loop_start, loop_end, connector
    x: float            # центр
    y: float
    w: float
    h: float
    lines: List[str]
    skew: float = 0.0   # для io: сдвиг параллелограмма
    inset: float = 0.0  # для call: отступ внутренних линий
    cut: float = 0.0    # для границ цикла: срез углов

    def bbox(self) -> Box:
        return (self.x - self.w / 2, self.y - self.h / 2, self.x + self.w / 2, self.y + self.h / 2)

    @property
    def top(self) -> float:
        return self.y - self.h / 2

    @property
    def bottom(self) -> float:
        return self.y + self.h / 2

    def port(self, side: str) -> Point:
        if side == "top":
            return (self.x, self.top)
        if side == "bottom":
            return (self.x, self.bottom)
        off = self.w / 2 - (self.skew / 2 if self.kind == "io" else 0.0)
        if side == "right":
            return (self.x + off, self.y)
        if side == "left":
            return (self.x - off, self.y)
        raise ValueError(side)

    def move(self, dx: float, dy: float) -> None:
        self.x += dx
        self.y += dy

    def outline(self) -> List[Point]:
        """Контур фигуры (для отрисовки и проверок)."""
        x0, y0, x1, y1 = self.bbox()
        k = self.kind
        if k == "io":
            s = self.skew
            return [(x0 + s, y0), (x1, y0), (x1 - s, y1), (x0, y1)]
        if k == "cond":
            return [(self.x, y0), (x1, self.y), (self.x, y1), (x0, self.y)]
        if k == "loop_start":        # срез углов как у фигуры loopLimit в draw.io: c по ширине, 0.8c по высоте
            c, v = self.cut, 0.8 * self.cut
            return [(x0 + c, y0), (x1 - c, y0), (x1, y0 + v), (x1, y1), (x0, y1), (x0, y0 + v)]
        if k == "loop_end":
            c, v = self.cut, 0.8 * self.cut
            return [(x0, y0), (x1, y0), (x1, y1 - v), (x1 - c, y1), (x0 + c, y1), (x0, y1 - v)]
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


@dataclass
class Line:
    pts: List[Point]
    arrow: bool = True

    def move(self, dx: float, dy: float) -> None:
        self.pts = [(x + dx, y + dy) for x, y in self.pts]

    def segments(self):
        for a, b in zip(self.pts, self.pts[1:]):
            if abs(a[0] - b[0]) > EPS or abs(a[1] - b[1]) > EPS:
                yield a, b


@dataclass
class Label:
    x: float
    y: float
    text: str
    anchor: str          # 'bl' — (x, y) левый нижний угол; 'tl' — левый верхний; 'c' — центр
    w: float
    h: float
    size: float          # кегль, пт
    kind: str = "label"  # label | title

    def bbox(self) -> Box:
        if self.anchor == "bl":
            return (self.x, self.y - self.h, self.x + self.w, self.y)
        if self.anchor == "tl":
            return (self.x, self.y, self.x + self.w, self.y + self.h)
        return (self.x - self.w / 2, self.y - self.h / 2, self.x + self.w / 2, self.y + self.h / 2)

    def move(self, dx: float, dy: float) -> None:
        self.x += dx
        self.y += dy


@dataclass
class Dot:
    x: float
    y: float

    def move(self, dx: float, dy: float) -> None:
        self.x += dx
        self.y += dy


def seg_box(a: Point, b: Point, half: float = SEG_HALF) -> Box:
    return (min(a[0], b[0]) - half, min(a[1], b[1]) - half, max(a[0], b[0]) + half, max(a[1], b[1]) + half)


def boxes_overlap(a: Box, b: Box, gx: float = 0.0, gy: float = 0.0) -> bool:
    return a[0] < b[2] + gx and b[0] < a[2] + gx and a[1] < b[3] + gy and b[1] < a[3] + gy


def box_contains(b: Box, p: Point, tol: float = 0.02) -> bool:
    return b[0] - tol <= p[0] <= b[2] + tol and b[1] - tol <= p[1] <= b[3] + tol


def union(boxes: List[Box]) -> Optional[Box]:
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


@dataclass
class Diagram:
    """Готовая раскладка одной функции — её получают отрисовщики."""
    name: str
    width: float
    height: float
    shapes: List[Shape] = field(default_factory=list)
    lines: List[Line] = field(default_factory=list)
    labels: List[Label] = field(default_factory=list)
    dots: List[Dot] = field(default_factory=list)
    columns: int = 1
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        r = lambda v: round(v, 4)
        return {
            "name": self.name, "width": r(self.width), "height": r(self.height), "columns": self.columns,
            "shapes": [{"kind": s.kind, "x": r(s.x), "y": r(s.y), "w": r(s.w), "h": r(s.h), "lines": s.lines}
                       for s in self.shapes],
            "lines": [{"points": [[r(x), r(y)] for x, y in l.pts], "arrow": l.arrow} for l in self.lines],
            "labels": [{"x": r(l.x), "y": r(l.y), "text": l.text, "anchor": l.anchor, "kind": l.kind}
                       for l in self.labels],
            "dots": [[r(d.x), r(d.y)] for d in self.dots],
        }

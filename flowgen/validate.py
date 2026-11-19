"""Проверка готовой раскладки.

Ищет то, что на схеме было бы ошибкой:
  * фигуры или подписи налезают друг на друга;
  * линия проходит через фигуру или подпись;
  * линии пересекаются или идут одна по другой;
  * в месте слияния трёх и более линий нет точки;
  * у линии висит конец (не упирается ни в фигуру, ни в другую линию);
  * текст не помещается в блок;
  * что-то вылезает за край рисунка.
Возвращает список понятных сообщений; пустой список — всё в порядке.
"""
from __future__ import annotations

import math
from typing import List, Optional

from .geometry import Box, Diagram, Point, Shape

T = 0.004        # допуск, дюймы


def _shrink(b: Box, d: float) -> Box:
    return (b[0] + d, b[1] + d, b[2] - d, b[3] - d)


def _overlap(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _segments(D: Diagram):
    out = []
    for li, l in enumerate(D.lines):
        pts = l.pts
        for si in range(len(pts) - 1):
            out.append((li, si, pts[si], pts[si + 1]))
    return out


def _seg_box(a: Point, b: Point) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def _name(s: Shape) -> str:
    t = " ".join(s.lines)[:30]
    return f"{s.kind} «{t}»"


def _on_outline(s: Shape, p: Point, tol: float = 0.01) -> bool:
    if s.kind in ("connector", "dot"):
        return abs(math.hypot(p[0] - s.x, p[1] - s.y) - s.w / 2) < tol
    x0, y0, x1, y1 = s.bbox()
    if s.kind == "term":
        r = min(s.w, s.h) / 2
        on_h = (abs(p[1] - y0) < tol or abs(p[1] - y1) < tol) and x0 + r - tol <= p[0] <= x1 - r + tol
        on_l = p[0] <= x0 + r and abs(math.hypot(p[0] - (x0 + r), p[1] - s.y) - r) < tol
        on_r = p[0] >= x1 - r and abs(math.hypot(p[0] - (x1 - r), p[1] - s.y) - r) < tol
        return on_h or on_l or on_r
    pts = s.outline()
    for a, b in zip(pts, pts[1:] + pts[:1]):
        if _dist_seg(p, a, b) < tol:
            return True
    return False


def _inside(s: Shape, p: Point, margin: float = 0.01) -> bool:
    """Точка строго внутри фигуры (отступив margin от контура)."""
    if s.kind in ("connector", "dot"):
        return math.hypot(p[0] - s.x, p[1] - s.y) < s.w / 2 - margin
    x0, y0, x1, y1 = s.bbox()
    if not (x0 + margin < p[0] < x1 - margin and y0 + margin < p[1] < y1 - margin):
        return False
    if s.kind in ("process", "call", "term"):
        return True
    poly = s.outline()
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))
    sign = 1.0 if area > 0 else -1.0
    for a, b in zip(poly, poly[1:] + poly[:1]):
        ex, ey = b[0] - a[0], b[1] - a[1]
        L = math.hypot(ex, ey) or 1.0
        cross = (ex * (p[1] - a[1]) - ey * (p[0] - a[0])) / L * sign
        if cross <= margin:
            return False
    return True


def _segment_enters(s: Shape, a: Point, b: Point) -> bool:
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L < 1e-9:
        return False
    steps = max(2, int(L / 0.01))
    for k in range(1, steps):
        t = k / steps
        p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        if _inside(s, p):
            return True
    return False


def _dist_seg(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    if L < 1e-12:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def check_diagram(D: Diagram, style: Optional[dict] = None) -> List[str]:
    probs: List[str] = []
    shapes = D.shapes
    segs = _segments(D)

    # 0. размеры
    for s in shapes:
        b = s.bbox()
        if b[0] < -T or b[1] < -T or b[2] > D.width + T or b[3] > D.height + T:
            probs.append(f"{_name(s)} выходит за край рисунка")
    for _, _, a, b in segs:
        for p in (a, b):
            if not (-T <= p[0] <= D.width + T and -T <= p[1] <= D.height + T):
                probs.append(f"линия выходит за край рисунка в точке ({p[0]:.2f}, {p[1]:.2f})")

    # 1. фигуры не налезают друг на друга
    for i in range(len(shapes)):
        bi = _shrink(shapes[i].bbox(), T)
        for j in range(i + 1, len(shapes)):
            if _overlap(bi, _shrink(shapes[j].bbox(), T)):
                probs.append(f"налезают {_name(shapes[i])} и {_name(shapes[j])}")

    # 2. подписи
    labs = D.labels
    for i, l in enumerate(labs):
        lb = _shrink(l.bbox(), T)
        for s in shapes:
            if _overlap(lb, s.bbox()):
                probs.append(f"подпись «{l.text}» налезает на {_name(s)}")
        for j in range(i + 1, len(labs)):
            if _overlap(lb, _shrink(labs[j].bbox(), T)):
                probs.append(f"подписи «{l.text}» и «{labs[j].text}» налезают друг на друга")
        for _, _, a, b in segs:
            sb = _seg_box(a, b)
            sb = (sb[0] - 0.006, sb[1] - 0.006, sb[2] + 0.006, sb[3] + 0.006)
            if _overlap(lb, sb):
                probs.append(f"линия проходит через подпись «{l.text}»")

    # 3. отрезки: только вертикальные и горизонтальные, не нулевые
    for li, si, a, b in segs:
        if abs(a[0] - b[0]) > 1e-6 and abs(a[1] - b[1]) > 1e-6:
            probs.append(f"косой отрезок линии ({a[0]:.2f}, {a[1]:.2f}) – ({b[0]:.2f}, {b[1]:.2f})")
        if abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6:
            probs.append(f"нулевой отрезок линии в ({a[0]:.2f}, {a[1]:.2f})")

    # 4. линия не проходит сквозь фигуру
    for li, si, a, b in segs:
        sb = _seg_box(a, b)
        for s in shapes:
            if not _overlap((sb[0] - T, sb[1] - T, sb[2] + T, sb[3] + T), s.bbox()):
                continue
            if _segment_enters(s, a, b):
                probs.append(f"линия проходит через {_name(s)}: отрезок ({a[0]:.2f}, {a[1]:.2f}) – ({b[0]:.2f}, {b[1]:.2f})")

    # 5. пересечения и наложения линий; 6. точки в местах слияния
    degree = {}

    def add_deg(p: Point, k: int) -> None:
        key = (round(p[0], 3), round(p[1], 3))
        degree[key] = degree.get(key, 0) + k

    for l in D.lines:
        pts = l.pts
        add_deg(pts[0], 1)
        add_deg(pts[-1], 1)
        for p in pts[1:-1]:
            add_deg(p, 2)
    n = len(segs)
    for i in range(n):
        li, si, a, b = segs[i]
        for j in range(i + 1, n):
            lj, sj, c, d = segs[j]
            if li == lj and abs(si - sj) == 1:
                continue
            ha = abs(a[1] - b[1]) < 1e-6
            hc = abs(c[1] - d[1]) < 1e-6
            if ha == hc:
                # параллельные: на одной прямой и перекрываются?
                if ha and abs(a[1] - c[1]) < T:
                    lo = max(min(a[0], b[0]), min(c[0], d[0]))
                    hi = min(max(a[0], b[0]), max(c[0], d[0]))
                    if hi - lo > T:
                        probs.append(f"линии идут одна по другой на y = {a[1]:.2f}")
                elif not ha and abs(a[0] - c[0]) < T:
                    lo = max(min(a[1], b[1]), min(c[1], d[1]))
                    hi = min(max(a[1], b[1]), max(c[1], d[1]))
                    if hi - lo > T:
                        probs.append(f"линии идут одна по другой на x = {a[0]:.2f}")
                continue
            h0, h1, v0, v1 = (a, b, c, d) if ha else (c, d, a, b)
            x, y = v0[0], h0[1]
            if not (min(h0[0], h1[0]) - T <= x <= max(h0[0], h1[0]) + T and
                    min(v0[1], v1[1]) - T <= y <= max(v0[1], v1[1]) + T):
                continue
            in_h = min(h0[0], h1[0]) + T < x < max(h0[0], h1[0]) - T
            in_v = min(v0[1], v1[1]) + T < y < max(v0[1], v1[1]) - T
            if in_h and in_v:
                probs.append(f"линии пересекаются в ({x:.2f}, {y:.2f})")
            elif in_h or in_v:
                add_deg((x, y), 2)       # Т-образное примыкание
    dots = {(round(d.x, 3), round(d.y, 3)) for d in D.dots}
    for key, deg in degree.items():
        if deg >= 3 and not any(abs(key[0] - dx) < 0.01 and abs(key[1] - dy) < 0.01 for dx, dy in dots):
            probs.append(f"слияние линий без точки в ({key[0]:.2f}, {key[1]:.2f})")
    for dx, dy in dots:
        k = max((deg for key, deg in degree.items() if abs(key[0] - dx) < 0.01 and abs(key[1] - dy) < 0.01), default=0)
        if k < 3:
            probs.append(f"лишняя точка в ({dx:.2f}, {dy:.2f})")

    # 7. висящие концы
    for l in D.lines:
        for p in (l.pts[0], l.pts[-1]):
            key = (round(p[0], 3), round(p[1], 3))
            if degree.get(key, 0) >= 2:
                continue
            if any(_on_outline(s, p) for s in shapes):
                continue
            probs.append(f"конец линии ни к чему не примыкает: ({p[0]:.2f}, {p[1]:.2f})")

    # 8. стрелка помещается на последнем отрезке
    alen = 0.1
    if style:
        alen = float(style.get("arrow", {}).get("length", 0.1))
    for l in D.lines:
        if l.arrow and len(l.pts) >= 2:
            a, b = l.pts[-2], l.pts[-1]
            if math.hypot(b[0] - a[0], b[1] - a[1]) < alen + 0.02:
                probs.append(f"слишком короткий отрезок под стрелку у ({b[0]:.2f}, {b[1]:.2f})")

    # 9. текст помещается в фигуру
    if style:
        probs += _check_text(D, style)
    return probs


def _check_text(D: Diagram, style: dict) -> List[str]:
    from .layout import text_loss
    from .textmetrics import Metrics
    f = style["font"]
    m = Metrics(f["family"], f["size"], style.get("line_spacing", 1.2))
    out = []
    for s in D.shapes:
        if s.kind in ("connector", "dot"):
            continue
        n = len(s.lines)
        if s.kind == "cond":
            for k, line in enumerate(s.lines):
                wk = m.width(line)
                off = abs((k - (n - 1) / 2) * m.line_h) + m.glyph_h / 2
                if (wk / 2) / (s.w / 2) + off / (s.h / 2) > 1.0 + 1e-6:
                    out.append(f"текст «{line}» не помещается в ромб")
            continue
        avail = s.w - text_loss(style, s.kind, s.h) + 0.03
        if s.kind == "term":
            avail = max(avail, s.w - s.h - 2 * style["block"]["pad_x"] + 0.03)
        for line in s.lines:
            if m.width(line) > avail:
                out.append(f"текст «{line}» шире блока {s.kind}")
        if n * m.line_h > s.h + 1e-6:
            out.append(f"текст «{' '.join(s.lines)[:30]}» выше блока {s.kind}")
    return out

"""Часть 4а. Рисование готовой раскладки через matplotlib: PNG, SVG, PDF.

Координаты Diagram — дюймы, ось y вниз. Рисунок делается ровно такого же
размера в дюймах, поэтому 1 единица = 1 дюйм и пропорции не искажаются.
"""
from __future__ import annotations

import math
import os
from typing import List, Optional, Tuple

import matplotlib
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, FancyBboxPatch, Polygon

from .geometry import Diagram, Line, Point, Shape

FORMATS = ("png", "svg", "pdf")

BASELINE_SHIFT = 0.30      # базовая линия ниже центра строки на 0.30 кегля — текст выглядит по центру


def _fp(style: dict, size: float) -> FontProperties:
    return FontProperties(family=list(style["font"]["family"]), size=size)


def _needs_arrow(line: Line, style: dict) -> bool:
    a = style.get("arrow", {})
    if not line.arrow or not a.get("enabled", True):
        return False
    if not a.get("only_when_needed", False):
        return True
    # ГОСТ 19.701: стрелку можно не ставить, если линия идёт без изломов вниз или вправо
    segs = list(line.segments())
    if len(segs) != 1:
        return True
    (x0, y0), (x1, y1) = segs[0]
    return x1 < x0 - 1e-6 or y1 < y0 - 1e-6


def _arrow_head(p0: Point, p1: Point, length: float, width: float) -> Tuple[List[Point], Point]:
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    d = math.hypot(dx, dy) or 1.0
    ux, uy = dx / d, dy / d
    length = min(length, d)
    bx, by = p1[0] - ux * length, p1[1] - uy * length
    nx, ny = -uy * width / 2, ux * width / 2
    return [p1, (bx + nx, by + ny), (bx - nx, by - ny)], (bx, by)


def _shape_patches(s: Shape, lw: float, fc: str) -> list:
    kw = dict(facecolor=fc, edgecolor="black", linewidth=lw, joinstyle="miter")
    x0, y0, x1, y1 = s.bbox()
    if s.kind == "term":
        r = min(s.h, s.w) / 2
        return [FancyBboxPatch((x0, y0), s.w, s.h, boxstyle=f"round,pad=0,rounding_size={r}", **kw)]
    if s.kind == "connector":
        return [Circle((s.x, s.y), s.w / 2, **kw)]
    if s.kind == "dot":
        return [Circle((s.x, s.y), s.w / 2, facecolor="black", edgecolor="black", linewidth=0)]
    out = [Polygon(s.outline(), closed=True, **kw)]
    if s.kind == "call":
        for x in (x0 + s.inset, x1 - s.inset):
            out.append(Line2D([x, x], [y0, y1], color="black", linewidth=lw, solid_capstyle="butt"))
    return out


def _text_lines(ax, s: Shape, fp: FontProperties, line_h: float) -> None:
    n = len(s.lines)
    shift = BASELINE_SHIFT * fp.get_size_in_points() / 72.0
    for k, text in enumerate(s.lines):
        if not text:
            continue
        cy = s.y + (k - (n - 1) / 2) * line_h
        ax.text(s.x, cy + shift, text, ha="center", va="baseline", fontproperties=fp, color="black")


def draw(D: Diagram, style: dict, path: str, fmt: Optional[str] = None) -> str:
    """Рисует схему в файл path. fmt — png | svg | pdf (по умолчанию — по расширению)."""
    fmt = (fmt or os.path.splitext(path)[1].lstrip(".")).lower()
    if fmt not in FORMATS:
        raise ValueError(f"matplotlib не рисует формат {fmt!r}; доступны: {', '.join(FORMATS)}")
    out = style.get("output", {})
    f = style["font"]
    lw = float(style.get("line_width", 1.0))
    a = style.get("arrow", {})
    alen, awid = float(a.get("length", 0.1)), float(a.get("width", 0.07))
    bg = out.get("background", "white")
    fc = "white" if bg in (None, "none", "transparent") else bg
    line_h = f["size"] * style.get("line_spacing", 1.2) / 72.0
    fp_main = _fp(style, f["size"])
    fp_lab = _fp(style, f["label_size"])
    fp_title = _fp(style, f["title_size"])

    rc = {
        "svg.fonttype": "path" if out.get("svg_text_as_paths", True) else "none",
        "pdf.fonttype": 42,
        "svg.hashsalt": "flowgen",
        "path.simplify": False,
    }
    with matplotlib.rc_context(rc):
        fig = Figure(figsize=(D.width, D.height))
        FigureCanvasAgg(fig)
        ax = fig.add_axes((0, 0, 1, 1))
        ax.set_xlim(0, D.width)
        ax.set_ylim(D.height, 0)
        ax.set_axis_off()
        if bg in (None, "none", "transparent"):
            fig.patch.set_alpha(0)
            ax.patch.set_alpha(0)

        # линии — под фигурами, чтобы концы не залезали на контуры
        for line in D.lines:
            pts = list(line.pts)
            segs = list(line.segments())
            if not segs:
                continue
            if _needs_arrow(line, style):
                p0, p1 = segs[-1]
                head, base = _arrow_head(p0, p1, alen, awid)
                pts = pts[:-1] + [base]
                ax.add_patch(Polygon(head, closed=True, facecolor="black", edgecolor="black",
                                     linewidth=lw * 0.5, joinstyle="miter", zorder=3))
            xs, ys = zip(*pts)
            ax.add_line(Line2D(xs, ys, color="black", linewidth=lw, solid_capstyle="butt",
                               solid_joinstyle="miter", zorder=1))

        for s in D.shapes:
            for p in _shape_patches(s, lw, fc):
                p.set_zorder(2)
                if isinstance(p, Line2D):
                    p.set_zorder(2.1)
                    ax.add_line(p)
                else:
                    ax.add_patch(p)
            if s.kind == "connector":
                fpc = _fp(style, f.get("connector_size", f["size"]))
                _text_lines(ax, s, fpc, line_h)
            else:
                _text_lines(ax, s, fp_main, line_h)

        r = float(style.get("dot_radius", 0.032))
        for d in D.dots:
            ax.add_patch(Circle((d.x, d.y), r, facecolor="black", edgecolor="none", zorder=4))

        for l in D.labels:
            fp = fp_title if l.kind == "title" else fp_lab
            va = {"bl": "bottom", "tl": "top", "c": "center"}[l.anchor]
            ha = "center" if l.anchor == "c" else "left"
            ax.text(l.x, l.y, l.text, ha=ha, va=va, fontproperties=fp, color="black", zorder=5)

        meta = {"svg": {"Date": None}, "pdf": {"CreationDate": None, "ModDate": None}}.get(fmt)
        fig.savefig(path, format=fmt, dpi=int(out.get("dpi", 250)),
                    facecolor=fig.get_facecolor() if bg not in (None, "none", "transparent") else "none",
                    metadata=meta)
    return path

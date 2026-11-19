"""Часть 4б. Сохранение раскладки в формат draw.io (diagrams.net).

Файл .drawio открывается в draw.io / diagrams.net (сайт, программа, расширение
для VS Code). Каждый блок — отдельная фигура с редактируемым текстом,
линии привязаны к блокам, поэтому блоки можно двигать.

Чтобы draw.io не сдвигал концы линий (он округляет точки привязки до целых
пикселей), все размеры приводятся к сетке в 1 px, а ширина и высота фигур —
к чётным числам: тогда середины сторон тоже попадают в целые пиксели.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from .geometry import Diagram, Line, Shape

Point = Tuple[float, float]


def _even(v: float) -> int:
    return max(2, 2 * int(round(v / 2.0)))


def _fmt(v: float) -> str:
    v = round(v, 2)
    return str(int(v)) if v == int(v) else f"{v:g}"


def _style(d: Dict[str, object]) -> str:
    return "".join(f"{k};" if v is None else f"{k}={v};" for k, v in d.items())


class _Snapped:
    """Фигура в пикселях: целые координаты, чётные размеры."""

    def __init__(self, s: Shape, ppi: float, cid: str):
        self.s = s
        self.id = cid
        self.w = _even(s.w * ppi)
        self.h = _even(s.h * ppi)
        self.cx = int(round(s.x * ppi))
        self.cy = int(round(s.y * ppi))
        self.x0 = self.cx - self.w // 2
        self.y0 = self.cy - self.h // 2
        self.skew = _even(s.skew * ppi) if s.kind == "io" else 0
        self.inset = int(round(s.inset * ppi))
        self.cut = int(round(s.cut * ppi))
        self.flip_v = s.kind == "loop_end"

    def ports(self, ppi: float):
        """(точка в дюймах до привязки, точка в px, относительные x, y для draw.io)."""
        s = self.s
        out = []
        for side in ("top", "bottom", "left", "right"):
            px, py = s.port(side)
            if side == "top":
                q, rel = (self.cx, self.y0), (0.5, 0.0)
            elif side == "bottom":
                q, rel = (self.cx, self.y0 + self.h), (0.5, 1.0)
            elif side == "left":
                off = self.skew // 2
                q, rel = (self.x0 + off, self.cy), (off / self.w, 0.5)
            else:
                off = self.skew // 2
                q, rel = (self.x0 + self.w - off, self.cy), (1 - off / self.w, 0.5)
            if self.flip_v:            # draw.io зеркалит точки привязки вместе с фигурой
                rel = (rel[0], 1.0 - rel[1])
            out.append(((px, py), q, rel))
        return out


def to_drawio(D: Diagram, style: dict, page_name: Optional[str] = None) -> str:
    """Возвращает текст файла .drawio для одной схемы."""
    return to_drawio_pages([(page_name or D.name or "схема", D)], style)


def to_drawio_pages(pages: List[Tuple[str, Diagram]], style: dict) -> str:
    root = ET.Element("mxfile", {"host": "flowgen", "agent": "flowgen", "type": "device"})
    for i, (name, D) in enumerate(pages):
        root.append(_diagram_element(D, style, name, f"page{i + 1}"))
    ET.indent(root, space="  ") if hasattr(ET, "indent") else None
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _diagram_element(D: Diagram, style: dict, name: str, did: str) -> ET.Element:
    out = style.get("output", {})
    ppi = float(out.get("drawio_px_per_inch", 96))
    f = style["font"]
    family = f["family"][0] if isinstance(f["family"], (list, tuple)) else str(f["family"])
    fs = _fmt(f["size"] * ppi / 72.0)
    lfs = _fmt(f["label_size"] * ppi / 72.0)
    tfs = _fmt(f["title_size"] * ppi / 72.0)
    cfs = _fmt(f.get("connector_size", f["size"]) * ppi / 72.0)
    sw = _fmt(float(style.get("line_width", 1.0)) * ppi / 72.0)
    a = style.get("arrow", {})
    arrow_on = a.get("enabled", True)
    only_needed = a.get("only_when_needed", False)
    end_size = max(3, int(round(float(a.get("length", 0.1)) * ppi - float(sw))))

    W = int(round(D.width * ppi))
    H = int(round(D.height * ppi))
    diagram = ET.Element("diagram", {"id": did, "name": name})
    model = ET.SubElement(diagram, "mxGraphModel", {
        "dx": "0", "dy": "0", "grid": "0", "gridSize": "10", "guides": "1", "tooltips": "1",
        "connect": "1", "arrows": "1", "fold": "1", "page": "1", "pageScale": "1",
        "pageWidth": str(W), "pageHeight": str(H), "math": "0", "shadow": "0"})
    cells = ET.SubElement(model, "root")
    ET.SubElement(cells, "mxCell", {"id": "0"})
    ET.SubElement(cells, "mxCell", {"id": "1", "parent": "0"})

    base = {"html": "0", "fontFamily": family, "fontColor": "#000000", "strokeColor": "#000000",
            "fillColor": "#FFFFFF", "strokeWidth": sw, "align": "center", "verticalAlign": "middle",
            "spacing": "0"}
    snapped: List[_Snapped] = []
    for k, s in enumerate(D.shapes):
        sn = _Snapped(s, ppi, f"s{k}")
        snapped.append(sn)
        st: Dict[str, object] = {}
        if s.kind == "term":
            st.update({"rounded": "1", "arcSize": "50"})
        elif s.kind == "io":
            st.update({"shape": "parallelogram", "perimeter": "parallelogramPerimeter", "fixedSize": "1",
                       "size": str(sn.skew)})
        elif s.kind == "call":
            st.update({"shape": "process", "fixedSize": "1", "size": str(sn.inset), "backgroundOutline": "1"})
        elif s.kind == "cond":
            st.update({"rhombus": None, "perimeter": "rhombusPerimeter"})
        elif s.kind in ("loop_start", "loop_end"):
            st.update({"shape": "loopLimit", "size": str(sn.cut)})
            if s.kind == "loop_end":
                st["flipV"] = "1"
        elif s.kind == "connector":
            st.update({"ellipse": None, "aspect": "fixed", "perimeter": "ellipsePerimeter"})
        else:
            st.update({"rounded": "0"})
        st.update(base)
        st["fontSize"] = cfs if s.kind == "connector" else fs
        cell = ET.SubElement(cells, "mxCell", {"id": sn.id, "value": "\n".join(s.lines),
                                               "style": _style(st), "vertex": "1", "parent": "1"})
        ET.SubElement(cell, "mxGeometry", {"x": str(sn.x0), "y": str(sn.y0), "width": str(sn.w),
                                           "height": str(sn.h), "as": "geometry"})

    # точки слияния линий
    dr = float(style.get("dot_radius", 0.032)) * ppi
    dsize = _even(2 * dr)
    dot_pts = []
    for k, d in enumerate(D.dots):
        cx, cy = int(round(d.x * ppi)), int(round(d.y * ppi))
        dot_pts.append((cx, cy))
        cell = ET.SubElement(cells, "mxCell", {
            "id": f"d{k}", "value": "", "vertex": "1", "parent": "1",
            "style": _style({"ellipse": None, "aspect": "fixed", "fillColor": "#000000",
                             "strokeColor": "none", "html": "0"})})
        ET.SubElement(cell, "mxGeometry", {"x": str(cx - dsize // 2), "y": str(cy - dsize // 2),
                                           "width": str(dsize), "height": str(dsize), "as": "geometry"})

    # подписи «да»/«нет» и заголовок
    for k, l in enumerate(D.labels):
        b = l.bbox()
        x0, y0 = int(round(b[0] * ppi)), int(round(b[1] * ppi))
        w = int(round((b[2] - b[0]) * ppi)) + 4
        h = int(round((b[3] - b[1]) * ppi))
        # текст по центру своего прямоугольника: так draw.io ставит его ровно туда,
        # где он стоит в PNG (при выравнивании по верху/низу draw.io сдвигает строку)
        st = {"text": None, "html": "0", "fontFamily": family, "fontColor": "#000000",
              "fontSize": tfs if l.kind == "title" else lfs, "align": "center" if l.anchor == "c" else "left",
              "verticalAlign": "middle", "spacing": "0", "strokeColor": "none", "fillColor": "none"}
        cell = ET.SubElement(cells, "mxCell", {"id": f"t{k}", "value": l.text, "style": _style(st),
                                               "vertex": "1", "parent": "1"})
        ET.SubElement(cell, "mxGeometry", {"x": str(x0), "y": str(y0), "width": str(w), "height": str(h),
                                           "as": "geometry"})

    # линии
    port_index = []
    for sn in snapped:
        for (pin, q, rel) in sn.ports(ppi):
            port_index.append((pin, q, rel, sn))

    def find_port(p: Point):
        for pin, q, rel, sn in port_index:
            if abs(pin[0] - p[0]) < 1e-4 and abs(pin[1] - p[1]) < 1e-4:
                return q, rel, sn
        return None

    def snap(p: Point) -> Tuple[int, int]:
        return (int(round(p[0] * ppi)), int(round(p[1] * ppi)))

    for k, line in enumerate(D.lines):
        pts = [snap(p) for p in line.pts]
        src = find_port(line.pts[0])
        dst = find_port(line.pts[-1])
        if src is not None:
            pts[0] = src[0]
        if dst is not None:
            pts[-1] = dst[0]
        st: Dict[str, object] = {"edgeStyle": "none", "rounded": "0", "html": "0", "strokeColor": "#000000",
                                 "strokeWidth": sw, "startArrow": "none"}
        if arrow_on and line.arrow and (not only_needed or _needs_arrow(line)):
            st.update({"endArrow": "block", "endFill": "1", "endSize": str(end_size)})
        else:
            st["endArrow"] = "none"
        attrs = {"id": f"e{k}", "edge": "1", "parent": "1"}
        if src is not None:
            st.update({"exitX": _fmt(src[1][0]), "exitY": _fmt(src[1][1]), "exitDx": "0", "exitDy": "0",
                       "exitPerimeter": "0"})
            attrs["source"] = src[2].id
        if dst is not None:
            st.update({"entryX": _fmt(dst[1][0]), "entryY": _fmt(dst[1][1]), "entryDx": "0", "entryDy": "0",
                       "entryPerimeter": "0"})
            attrs["target"] = dst[2].id
        attrs["style"] = _style(st)
        cell = ET.SubElement(cells, "mxCell", attrs)
        geo = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        ET.SubElement(geo, "mxPoint", {"x": str(pts[0][0]), "y": str(pts[0][1]), "as": "sourcePoint"})
        ET.SubElement(geo, "mxPoint", {"x": str(pts[-1][0]), "y": str(pts[-1][1]), "as": "targetPoint"})
        if len(pts) > 2:
            arr = ET.SubElement(geo, "Array", {"as": "points"})
            for x, y in pts[1:-1]:
                ET.SubElement(arr, "mxPoint", {"x": str(x), "y": str(y)})
    return diagram


def _needs_arrow(line: Line) -> bool:
    segs = list(line.segments())
    if len(segs) != 1:
        return True
    (x0, y0), (x1, y1) = segs[0]
    return x1 < x0 - 1e-6 or y1 < y0 - 1e-6


def draw(D: Diagram, style: dict, path: str) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(to_drawio(D, style))
    return path

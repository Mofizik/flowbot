"""Часть 3. Расположение элементов блок-схемы.

Получает JSON одной функции (формат ir.py) и стиль, возвращает Diagram —
фигуры, линии, подписи и точки с готовыми координатами (дюймы, ось y вниз).

Как раскладывается схема:
  * основной поток идёт сверху вниз по оси x = 0;
  * у ромба одна ветка уходит вправо, другая идёт вниз; ветки сливаются
    под ромбом (цепочка else-if рисуется лесенкой ромбов вниз);
  * цикл — две границы «Ц1: …» и «Ц1», тело между ними;
  * break / continue идут по вертикальной линии справа, а если она
    что-то пересекла бы — через соединители-кружки с одинаковыми цифрами;
  * return рисуется терминатором «Выход» / «Конец» прямо на месте;
  * длинная схема режется на столбцы с соединителями А, Б, …
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .geometry import (EPS, Box, Diagram, Dot, Label, Line, Point, Shape, box_contains, boxes_overlap,
                       seg_box, union)
from .textmetrics import Metrics

BIG = 1000.0
GY = 0.12          # минимальный зазор по вертикали между соседними частями
GX = 0.18          # минимальный зазор по горизонтали
CM = 0.04          # запас вокруг кружка-соединителя


# ====================================================================== фрагменты

@dataclass
class Jump:
    """Ещё не проведённый break / continue."""
    kind: str                       # break | continue
    target: int                     # номер цикла / switch, куда переход
    src: Optional[Point] = None     # откуда выходит линия
    dir: Optional[str] = None       # right — вправо от фигуры; down — вниз, потом вправо
    drop: float = 0.2               # спуск перед поворотом (для dir = down)
    room: float = 0.85              # место справа под кружок-соединитель
    rr: float = 0.2                 # половина высоты этого места

    def move(self, dx: float, dy: float) -> None:
        if self.src is not None:
            self.src = (self.src[0] + dx, self.src[1] + dy)

    def turn(self) -> Point:
        """Точка, после которой линия идёт вправо."""
        sx, sy = self.src
        return (sx, sy + self.drop) if self.dir == "down" else (sx, sy)

    def path(self) -> List[Point]:
        return [self.src, self.turn()] if self.dir == "down" else [self.src]

    def reservations(self) -> List[Box]:
        """Место, которое нельзя занимать, пока переход не проведён."""
        if self.src is None:
            return []
        sx, sy = self.src
        tx, ty = self.turn()
        out = [(tx, ty - self.rr, tx + self.room, ty + self.rr)]
        if self.dir == "down":
            out.append((sx - 0.06, sy, sx + 0.06, ty + 0.06))
        return out


@dataclass
class LaneRec:
    """Линии одного проведённого перехода — чтобы при разрезании на столбцы
    заменить их соединителями."""
    lines: List[Line]
    dots: List[Dot]
    sources: List[Tuple[List[Point], float]]
    target: Point

    def move(self, dx: float, dy: float) -> None:
        self.sources = [([(x + dx, y + dy) for x, y in pts], hy + dy) for pts, hy in self.sources]
        self.target = (self.target[0] + dx, self.target[1] + dy)


class Frag:
    """Кусок схемы в своих координатах: ось x = 0, сверху вниз."""

    def __init__(self) -> None:
        self.shapes: List[Shape] = []
        self.lines: List[Line] = []
        self.labels: List[Label] = []
        self.dots: List[Dot] = []
        self.jumps: List[Jump] = []
        self.lanes: List[LaneRec] = []
        self.cuts: List[Tuple[float, float]] = []     # места, где можно разрезать на столбцы
        self.reserved: List[Box] = []                 # дополнительные запретные зоны
        self.entry: Optional[Point] = None
        self.first: Optional[Shape] = None
        self.exit: Optional[Point] = None
        self.last: Optional[Shape] = None
        self.pure_jump = False

    def add(self, o: "Frag", with_jumps: bool = True) -> None:
        self.shapes += o.shapes
        self.lines += o.lines
        self.labels += o.labels
        self.dots += o.dots
        self.cuts += o.cuts
        self.reserved += o.reserved
        self.lanes += o.lanes
        if with_jumps:
            self.jumps += o.jumps

    def move(self, dx: float, dy: float) -> None:
        if abs(dx) < EPS and abs(dy) < EPS:
            return
        for s in self.shapes:
            s.move(dx, dy)
        for l in self.lines:
            l.move(dx, dy)
        for l in self.labels:
            l.move(dx, dy)
        for d in self.dots:
            d.move(dx, dy)
        for j in self.jumps:
            j.move(dx, dy)
        for ln in self.lanes:
            ln.move(dx, dy)
        self.cuts = [(a + dy, b + dy) for a, b in self.cuts]
        self.reserved = [(b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy) for b in self.reserved]
        if self.entry is not None:
            self.entry = (self.entry[0] + dx, self.entry[1] + dy)
        if self.exit is not None:
            self.exit = (self.exit[0] + dx, self.exit[1] + dy)

    def boxes(self, dot_r: float = 0.035) -> List[Box]:
        """Всё нарисованное: фигуры, подписи, точки, отрезки линий."""
        bs = [s.bbox() for s in self.shapes]
        bs += [l.bbox() for l in self.labels]
        bs += [(d.x - dot_r, d.y - dot_r, d.x + dot_r, d.y + dot_r) for d in self.dots]
        for l in self.lines:
            for a, b in l.segments():
                bs.append(seg_box(a, b))
        return bs

    def res_boxes(self) -> List[Box]:
        out = list(self.reserved)
        for j in self.jumps:
            out += j.reservations()
        return out

    def bounds(self) -> Box:
        u = union(self.boxes() + self.res_boxes())
        return u if u is not None else (0.0, 0.0, 0.0, 0.0)

    def top(self) -> float:
        return self.bounds()[1]

    def bottom(self) -> float:
        return self.bounds()[3]

    def is_empty(self) -> bool:
        return not self.shapes and not self.pure_jump


# ====================================================================== контекст

class Ctx:
    def __init__(self, style: dict, is_main: bool):
        self.st = style
        f = style["font"]
        self.m = Metrics(f["family"], f["size"], style.get("line_spacing", 1.2))
        self.ml = Metrics(f["family"], f["label_size"], 1.0)
        self.mt = Metrics(f["family"], f["title_size"], 1.2)
        self.sp = style["spacing"]
        self.r = float(style["connector_radius"])
        self.label_h = self.ml.size * 1.05 / 72.0
        # спуск перед поворотом вправо: ниже подписи «нет» под вершиной ромба
        self.drop = max(self.sp["stub"], 0.03 + self.label_h + 0.045)
        # от вершины ромба до правой ветки: стрелка и подпись «да»/«нет» над ней
        labs = style.get("labels", {})
        lab_w = max([self.ml.width(str(t)) for t in (labs.get("yes", ""), labs.get("no", ""))] + [0.0])
        self.side_gap = max(self.sp["arrow_min"], lab_w + 0.05 + 0.12)
        self.room = self.side_gap + 2 * self.r + GX
        self.is_main = is_main
        self.loop_no = 0
        self.digit = 0
        self.targets: List[Tuple[int, str]] = []
        self._tid = 0
        self.warnings: List[str] = []

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def push_target(self, kind: str) -> int:
        self._tid += 1
        self.targets.append((self._tid, kind))
        return self._tid

    def pop_target(self) -> None:
        self.targets.pop()

    def target_for(self, kind: str) -> Optional[int]:
        if kind == "break":
            return self.targets[-1][0] if self.targets else None
        for tid, k in reversed(self.targets):
            if k == "loop":
                return tid
        return None

    def next_digit(self) -> str:
        self.digit += 1
        return str(self.digit)

    def set_jump(self, j: Jump, src: Point, d: str) -> Jump:
        j.src, j.dir = src, d
        j.drop, j.room, j.rr = self.drop, self.room, self.r + CM
        return j


# ====================================================================== фигуры

def make_box(ctx: Ctx, kind: str, text: str) -> Shape:
    st = ctx.st
    b = st["block"]
    m = ctx.m
    W = b["width"]
    padx, pady = b["pad_x"], b["pad_y"]
    skew = st["io_skew"] if kind == "io" else 0.0
    inset = st["call_inset"] if kind == "call" else 0.0
    cut = st["loop_cut"] if kind in ("loop_start", "loop_end") else 0.0
    w = W
    loss = text_loss(st, kind, b["min_height"])
    longest = m.longest_word(text)
    if longest > W - loss:
        w = min(b["max_width"], longest + loss + 0.02)
    lines = m.wrap(text, w - loss)
    h = max(b["min_height"], len(lines) * m.line_h + 2 * pady)
    if kind == "term" and len(lines) > 1:
        # у терминатора скругления съедают ширину — расширяем при нескольких строках
        w = min(b["max_width"], max(w, max(m.width(l) for l in lines) + h + 2 * padx))
    return Shape(kind, 0.0, h / 2, w, h, lines, skew=skew, inset=inset, cut=cut)


def text_loss(st: dict, kind: str, h: float) -> float:
    """Сколько ширины блока не занимает текст (поля, скосы, полосы)."""
    loss = 2 * st["block"]["pad_x"]
    if kind == "io":
        loss += 1.5 * st["io_skew"]
    elif kind == "call":
        loss += 2 * st["call_inset"]
    elif kind == "term":
        loss += 0.25
    return loss


def make_cond(ctx: Ctx, text: str) -> Shape:
    st = ctx.st
    c = st["cond"]
    m = ctx.m
    W = st["block"]["width"]
    min_h, max_w, margin = c["min_height"], c["max_width"], c["margin"]
    longest = min(m.longest_word(text), 0.6 * max_w)
    best = None
    for f in (0.45, 0.55, 0.65, 0.78, 0.9, 1.05, 1.2, 1.4):
        ww = max(W * f, longest)
        lines = m.wrap(text, ww)
        widths = [m.width(l) for l in lines]
        n = len(lines)
        w = W
        while w <= max_w + 1e-9:
            h = min_h
            ok = True
            for k, wk in enumerate(widths):
                off = abs((k - (n - 1) / 2) * m.line_h) + m.glyph_h / 2
                denom = 1 - margin - (wk + 0.1) / w
                if denom <= 0.04:
                    ok = False
                    break
                h = max(h, 2 * off / denom)
            if ok:
                key = w * h * (1 + 0.15 * max(0.0, w - W))
                if best is None or key < best[0] - 1e-9:
                    best = (key, w, h, lines)
            w += 0.1
    if best is None:   # очень длинный текст: самый широкий ромб и столько строк, сколько нужно
        w = max_w
        lines = m.wrap(text, w * 0.55)
        h = max(min_h, len(lines) * m.line_h / 0.3)
        best = (0, w, h, lines)
    _, w, h, lines = best
    return Shape("cond", 0.0, h / 2, w, h, lines)


def make_label(ctx: Ctx, text: str, x: float, y: float, anchor: str) -> Label:
    m = ctx.ml
    return Label(x, y, text, anchor, m.width(text), ctx.label_h, m.size)


def side_label(ctx: Ctx, cshape: Shape, text: str) -> Label:
    vx, vy = cshape.port("right")
    return make_label(ctx, text, vx + 0.05, vy - 0.035, "bl")


def down_label(ctx: Ctx, cshape: Shape, text: str) -> Label:
    bx, by = cshape.port("bottom")
    return make_label(ctx, text, bx + 0.06, by + 0.03, "tl")


def simple(ctx: Ctx, kind: str, text: str) -> Frag:
    s = make_box(ctx, kind, text)
    F = Frag()
    F.shapes.append(s)
    F.entry = s.port("top")
    F.first = s
    if kind == "term":
        F.exit = None
        F.last = None
    else:
        F.exit = s.port("bottom")
        F.last = s
    return F


# ====================================================================== вспомогательное

def weight(stmts: Sequence[dict]) -> int:
    w = 0
    for s in stmts:
        t = s.get("type")
        if t in ("process", "io", "call", "return", "end"):
            w += 1
        elif t == "if":
            w += 1 + weight(s.get("yes", [])) + weight(s.get("no", []))
        elif t == "loop":
            w += 2 + weight(s.get("body", []))
        elif t == "switch":
            w += len(s.get("cases", [])) + sum(weight(c.get("body", [])) for c in s.get("cases", [])) \
                + weight(s.get("default") or [])
    return w


def has_loop(stmts: Sequence[dict]) -> bool:
    for s in stmts:
        t = s.get("type")
        if t in ("loop", "switch"):
            return True
        if t == "if" and (has_loop(s.get("yes", [])) or has_loop(s.get("no", []))):
            return True
    return False


def is_pure_jump(stmts: Sequence[dict]) -> bool:
    return len(stmts) == 1 and stmts[0].get("type") in ("break", "continue")


def _shift(b: Box, dx: float, dy: float) -> Box:
    return (b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy)


def _push_down(moving: List[Box], fixed: List[Box], gx: float = GX, gy: float = GY) -> float:
    """На сколько сдвинуть вниз moving, чтобы не задеть fixed."""
    total = 0.0
    for _ in range(1000):
        need = 0.0
        for b in moving:
            bb = _shift(b, 0, total)
            for a in fixed:
                if boxes_overlap(a, bb, gx, gy):
                    need = max(need, a[3] + gy - bb[1])
        if need <= 1e-6:
            return total
        total += need
    return total


def _push_right(moving: List[Box], fixed: List[Box], gx: float = GX, gy: float = GY) -> float:
    total = 0.0
    for _ in range(1000):
        need = 0.0
        for b in moving:
            bb = _shift(b, total, 0)
            for a in fixed:
                if boxes_overlap(a, bb, gx, gy):
                    need = max(need, a[2] + gx - bb[0])
        if need <= 1e-6:
            return total
        total += need
    return total


def _blocked(seg: Box, boxes: List[Box], ignore_pts: Sequence[Point]) -> bool:
    for b in boxes:
        if any(box_contains(b, p, 0.03) for p in ignore_pts):
            continue
        if boxes_overlap(seg, b):
            return True
    return False


def _hseg(x0: float, x1: float, y: float, trim: float = 0.03) -> Box:
    lo, hi = min(x0, x1), max(x0, x1)
    return (lo + trim, y - 0.01, hi - trim, y + 0.01)


def _vseg(x: float, y0: float, y1: float, trim: float = 0.03) -> Box:
    lo, hi = min(y0, y1), max(y0, y1)
    return (x - 0.01, lo + trim, x + 0.01, hi - trim)


def _return_corridor(ctx: Ctx, p: Point) -> List[Box]:
    """Путь возврата ветки: короткий спуск и коридор вправо до линии слияния."""
    x, y = p
    d = ctx.drop
    return [(x - 0.06, y, x + 0.06, y + d + 0.06), (x, y + d - 0.07, x + BIG, y + d + 0.07)]


def _right_corridor(p: Point) -> Box:
    return (p[0], p[1] - 0.12, p[0] + BIG, p[1] + 0.12)


def _jump_room(ctx: Ctx, jumps: Sequence[Jump], y0: float, y1: float) -> float:
    """Самая левая x вертикальной линии, при которой правее точек поворота
    ожидающих переходов (в полосе y0..y1) ещё помещается соединитель."""
    need = -BIG
    for j in jumps:
        if j.src is None:
            continue
        tx, ty = j.turn()
        if y0 - 0.25 <= ty <= y1 + 0.25:
            need = max(need, tx + ctx.room + 0.1)
    return need


def _extend_exit(ctx: Ctx, F: Frag) -> None:
    """Опускает выход фрагмента ниже всего, что правее него: от выхода
    линия пойдёт вниз и вправо и не должна задеть правые ветки."""
    if F.exit is None:
        return
    ex, ey = F.exit
    below = [b for b in F.boxes() + F.res_boxes() if b[3] > ey + 1e-6 and b[2] > ex + 0.02]
    if not below:
        return
    y_ext = max(b[3] for b in below) + ctx.sp["vertical"]
    F.lines.append(Line([F.exit, (ex, y_ext)], False))
    F.exit = (ex, y_ext)


def _right_is_free(ctx: Ctx, F: Frag, shape: Shape) -> bool:
    """Справа от блока на его уровне пусто — переход можно вести прямо вправо."""
    rx, ry = shape.port("right")
    h = ctx.r + CM + GY
    row = (rx + 0.01, ry - h, BIG, ry + h)
    own = shape.bbox()
    for b in F.boxes() + F.res_boxes():
        if b == own:
            continue
        if boxes_overlap(row, b):
            return False
    return True


# ====================================================================== последовательность

def build_seq(ctx: Ctx, stmts: Sequence[dict], allow_cuts: bool) -> Frag:
    sp = ctx.sp
    F = Frag()
    bottom: Optional[float] = None
    stmts = list(stmts)
    for i, s in enumerate(stmts):
        t = s.get("type")
        if t in ("break", "continue"):
            target = ctx.target_for(t)
            if target is None:
                ctx.warn(f"{t} вне цикла{' или switch' if t == 'break' else ''} пропущен")
                continue
            j = Jump(t, target)
            if bottom is None:
                F.pure_jump = True
                F.jumps.append(j)
            elif F.exit is not None:
                if F.last is not None and F.exit == F.last.port("bottom") and _right_is_free(ctx, F, F.last):
                    ctx.set_jump(j, F.last.port("right"), "right")
                else:
                    _extend_exit(ctx, F)
                    ctx.set_jump(j, F.exit, "down")
                F.jumps.append(j)
            F.exit = None
            F.last = None
            if i + 1 < len(stmts):
                ctx.warn(f"операторы после {t} не рисуются (они не выполняются)")
            break
        f = build_stmt(ctx, s, allow_cuts)
        if bottom is None:
            f.move(0, -f.top())
            F.entry, F.first = f.entry, f.first
        else:
            # сразу под выходом предыдущего оператора, если там свободно
            # (например, рядом с веткой, которая закончилась return)
            start = F.exit[1] if F.exit is not None else bottom
            f.move(0, start + sp["vertical"] - f.top())
            f.move(0, _push_down(f.boxes() + f.res_boxes(), F.boxes() + F.res_boxes()))
            if F.exit is not None and f.entry is not None:
                F.lines.append(Line([F.exit, f.entry], True))
                if allow_cuts and abs(F.exit[0]) < EPS and abs(f.entry[0]) < EPS and f.top() > bottom + 0.05:
                    F.cuts.append((bottom, f.top()))
        F.add(f)
        F.exit, F.last = f.exit, f.last
        bottom = F.bottom()
        if F.exit is None and i + 1 < len(stmts):
            if not (f.jumps and not f.shapes):
                ctx.warn("операторы после выхода из функции не рисуются (они не выполняются)")
            break
    return F


def build_stmt(ctx: Ctx, s: dict, allow_cuts: bool) -> Frag:
    t = s.get("type")
    if t in ("process", "io", "call"):
        return simple(ctx, t, s.get("text", ""))
    if t == "return":
        term = ctx.st["terminals"]
        val = s.get("value")
        if ctx.is_main:
            text = term["end_main"]
        elif val:
            text = term["return_value"].format(value=val)
        else:
            text = term["end"]
        return simple(ctx, "term", text)
    if t == "end":
        return simple(ctx, "term", s.get("text") or ctx.st["terminals"]["end_main"])
    if t == "if":
        return build_if(ctx, s)
    if t == "loop":
        return build_loop(ctx, s, allow_cuts)
    if t == "switch":
        return build_switch(ctx, s)
    ctx.warn(f"неизвестный тип оператора {t!r} нарисован как действие")
    return simple(ctx, "process", str(s.get("text", t)))


# ====================================================================== ветвления

def build_if(ctx: Ctx, s: dict) -> Frag:
    lab = ctx.st["labels"]
    yes, no = list(s.get("yes", [])), list(s.get("no", []))
    heavy = ctx.st.get("layout", {}).get("heavy_branch", 5)

    # цепочка else if: ромбы друг под другом, ветки «да» справа
    if len(no) == 1 and no[0].get("type") == "if":
        levels = []
        node = s
        while True:
            levels.append((node["cond"], list(node.get("yes", [])), lab["yes"], lab["no"]))
            nxt = node.get("no", [])
            if len(nxt) == 1 and nxt[0].get("type") == "if":
                node = nxt[0]
                continue
            return build_chain(ctx, levels, list(nxt), lab["no"])

    def is_heavy(b):
        return weight(b) > heavy or has_loop(b)

    if not no and not yes:
        side, down, side_is_yes = [], [], True
    elif not no:
        side_is_yes = not (is_heavy(yes) and not is_pure_jump(yes))
        side, down = (yes, []) if side_is_yes else ([], yes)
    elif not yes:
        side_is_yes = is_heavy(no) and not is_pure_jump(no)
        side, down = ([], no) if side_is_yes else (no, [])
    else:
        if is_pure_jump(no) and not is_pure_jump(yes):
            side_is_yes = False
        elif is_pure_jump(yes):
            side_is_yes = True
        else:
            side_is_yes = weight(yes) <= weight(no)
        side, down = (yes, no) if side_is_yes else (no, yes)
    sl, dl = (lab["yes"], lab["no"]) if side_is_yes else (lab["no"], lab["yes"])
    return build_chain(ctx, [(s["cond"], side, sl, dl)], down, dl)


def build_chain(ctx: Ctx, levels: List[Tuple[str, List[dict], str, str]], down_stmts: List[dict],
                last_down_label: str) -> Frag:
    """Ромбы один под другим; у каждого своя ветка справа; снизу — общая ветка down."""
    sp = ctx.sp
    levels = list(levels)
    down_stmts = list(down_stmts)
    # break/continue в нижней ветке последнего ромба выводим вправо, а ветку — вниз
    if is_pure_jump(down_stmts) and levels[-1][1] and not is_pure_jump(levels[-1][1]):
        c, side, sl, _ = levels[-1]
        levels[-1] = (c, down_stmts, last_down_label, sl)
        down_stmts, last_down_label = side, sl

    F = Frag()
    occ: List[Box] = []          # занятые места (для раздвигания)
    real: List[Box] = []         # настоящие элементы и места под переходы
    returns: List[Tuple[Point, str]] = []
    records = []
    prevC: Optional[Shape] = None
    prev_dl = None

    for idx, (cond_text, side_stmts, sl, dl) in enumerate(levels):
        last = idx == len(levels) - 1
        C = make_cond(ctx, cond_text)
        S = build_seq(ctx, side_stmts, False) if side_stmts else None
        if S is not None and S.is_empty():
            S = None
        if S is not None and S.exit is not None:
            _extend_exit(ctx, S)          # возврат ветки пойдёт от выхода вниз и вправо
        cy = C.h / 2 if prevC is None else prevC.bottom + sp["after_cond"] + C.h / 2

        def cbox(y: float, C=C) -> Box:
            return (-C.w / 2, y - C.h / 2, C.w / 2, y + C.h / 2)

        if S is not None and S.shapes:
            fb = S.first.bbox()
            dy0 = cy - S.first.y
            x_S = C.w / 2 + ctx.side_gap - fb[0]
            sb = S.boxes() + S.res_boxes()
            if S.exit is not None:
                sb += _return_corridor(ctx, S.exit)
            # ветка не должна заходить под свой ромб
            for b in S.boxes() + S.res_boxes():
                yb0, yb1 = b[1] + dy0, b[3] + dy0
                if yb0 < cy + C.h / 2 + GY and yb1 > cy - C.h / 2 - GY:
                    x_S = max(x_S, C.w / 2 + GX - b[0])
            moving = [_shift(b, x_S, dy0) for b in sb] + [cbox(cy)]
            dy = _push_down(moving, occ)
            cy += dy
            S.move(x_S, dy0 + dy)
            kind = "S"
        else:
            corridor = (C.w / 2, cy - 0.12, BIG, cy + 0.12)
            dy = _push_down([corridor, cbox(cy)], occ)
            cy += dy
            kind = "jump" if S is not None else "empty"
        C.y = cy
        F.shapes.append(C)
        occ.append(C.bbox())
        real.append(C.bbox())
        if prevC is not None:
            F.lines.append(Line([prevC.port("bottom"), C.port("top")], True))
            F.labels.append(down_label(ctx, prevC, prev_dl))
        records.append((C, S, sl, kind))
        if not last:
            _commit_side(ctx, F, C, S, sl, kind, occ, real, returns)
        prevC, prev_dl = C, dl

    lastC = prevC
    C, S, sl, kind = records[-1]

    # нижняя ветка
    E = build_seq(ctx, down_stmts, False) if down_stmts else None
    if E is not None and E.is_empty():
        E = None
    down_exit: Optional[Point] = None
    if E is None:
        down_exit = lastC.port("bottom")
        F.labels.append(down_label(ctx, lastC, last_down_label))
    elif E.pure_jump:
        for j in E.jumps:
            ctx.set_jump(j, lastC.port("bottom"), "down")
            F.jumps.append(j)
            zone = j.reservations()
            occ += zone
            real += zone
        F.labels.append(down_label(ctx, lastC, last_down_label))
        E = None
    else:
        E.move(0, lastC.bottom + sp["after_cond"] - E.top())
        E.move(0, _push_down(E.boxes() + E.res_boxes(), occ))
        F.add(E)
        F.lines.append(Line([lastC.port("bottom"), E.entry], True))
        F.labels.append(down_label(ctx, lastC, last_down_label))
        down_exit = E.exit
        eb = E.boxes() + E.res_boxes()
        occ += eb
        real += eb

    # последняя правая ветка: не залезать на нижнюю
    if kind == "S" and E is not None:
        sb = S.boxes() + S.res_boxes() + (_return_corridor(ctx, S.exit) if S.exit is not None else [])
        dx = _push_right(sb, E.boxes() + E.res_boxes())
        if dx > 0:
            S.move(dx, 0)
    _commit_side(ctx, F, C, S, sl, kind, occ, real, returns)

    # слияние веток
    if returns:
        _join_returns(ctx, F, returns, real, lastC, down_exit, E is None)
    else:
        F.exit = down_exit

    first = records[0][0]
    F.entry, F.first, F.last = first.port("top"), first, None
    F.move(0, -F.top())
    return F


def _commit_side(ctx, F, C, S, sl, kind, occ, real, returns) -> None:
    F.labels.append(side_label(ctx, C, sl))
    if kind == "S":
        F.lines.append(Line([C.port("right"), S.first.port("left")], True))
        F.add(S)
        sb = S.boxes() + S.res_boxes()
        occ += sb
        real += sb
        if S.exit is not None:
            returns.append((S.exit, "down"))
            occ += _return_corridor(ctx, S.exit)
    elif kind == "jump":
        for j in S.jumps:
            ctx.set_jump(j, C.port("right"), "right")
            F.jumps.append(j)
            real += j.reservations()
        occ.append(_right_corridor(C.port("right")))
    else:
        returns.append((C.port("right"), "right"))
        occ.append(_right_corridor(C.port("right")))


def _join_returns(ctx: Ctx, F: Frag, returns, real: List[Box], lastC: Shape, down_exit: Optional[Point],
                  down_empty: bool) -> None:
    """Сводит выходы правых веток на ось под цепочкой ромбов."""
    sp = ctx.sp
    join_y = max(b[3] for b in real) + sp["vertical"]
    if down_exit is not None and down_empty:
        join_y = max(join_y, lastC.bottom + sp["after_cond"] + 0.05)
    items = []
    for (sx, sy), d in returns:
        hy = sy + ctx.drop if d == "down" else sy
        items.append(((sx, sy), d, hy))
    all_boxes = real + F.boxes()

    # самая нижняя ветка может спуститься к линии слияния прямо
    straight = None
    downs = [it for it in items if it[1] == "down"]
    if downs:
        cand = max(downs, key=lambda it: it[0][1])
        others = [it for it in items if it is not cand]
        jy = join_y
        if others:
            jy = max(jy, max(it[2] for it in others) + GY)
        (sx, sy) = cand[0]
        ok = not _blocked(_vseg(sx, sy, jy), all_boxes, [cand[0]])
        for it in others:
            if sy - 0.02 <= it[2] <= jy + 0.02 and it[0][0] < sx + 0.02:
                ok = False
        for j in F.jumps:
            if j.src is None:
                continue
            tx, ty = j.turn()
            if sy - 0.25 <= ty <= jy + 0.25 and tx < sx:
                ok = False
        if ok:
            straight = cand
            join_y = jy
    others = [it for it in items if it is not straight]
    if others:
        join_y = max(join_y, max(it[2] for it in others) + GY)
        y_top = min(it[2] for it in others)
        band = [b for b in real if b[3] > y_top - 0.05 and b[1] < join_y + 0.05]
        xs = [b[2] for b in band] + [it[0][0] + sp["arrow_min"] for it in others]
        if straight is not None:
            xs.append(straight[0][0] + sp["arrow_min"])
        lane_x = max(xs) + sp["lane"]
        lane_x = max(lane_x, _jump_room(ctx, F.jumps, y_top, join_y))
        others.sort(key=lambda it: it[2])
        for k, ((sx, sy), d, hy) in enumerate(others):
            route = [(sx, sy), (sx, hy), (lane_x, hy)] if d == "down" else [(sx, sy), (lane_x, hy)]
            F.lines.append(Line(route, False))
            if k > 0:
                F.dots.append(Dot(lane_x, hy))
        F.lines.append(Line([(lane_x, y_top), (lane_x, join_y), (0.0, join_y)], True))
        if straight is not None:
            (sx, sy) = straight[0]
            F.lines.append(Line([(sx, sy), (sx, join_y)], False))
            F.dots.append(Dot(sx, join_y))
    else:
        (sx, sy) = straight[0]
        F.lines.append(Line([(sx, sy), (sx, join_y), (0.0, join_y)], True))
    if down_exit is not None:
        F.lines.append(Line([down_exit, (0.0, join_y)], False))
        F.dots.append(Dot(0.0, join_y))
    F.exit = (0.0, join_y)


# ====================================================================== циклы

def build_loop(ctx: Ctx, s: dict, allow_cuts: bool) -> Frag:
    sp = ctx.sp
    ctx.loop_no += 1
    n = ctx.loop_no
    post = s.get("test", "pre") == "post"
    infinite = bool(s.get("infinite", False))
    header = s.get("header", "") or ""
    lt = ctx.st["loop"]

    def fmt(t: str) -> str:
        out = t.format(n=n, header=header)
        return out.rstrip().rstrip(":").rstrip() if not header else out

    top_text = fmt(lt["post_start"] if post else lt["start"])
    end_text = fmt(lt["post_end"] if post else lt["end"])

    tid = ctx.push_target("loop")
    body = build_seq(ctx, s.get("body", []), allow_cuts)
    ctx.pop_target()

    F = Frag()
    T = make_box(ctx, "loop_start", top_text)
    F.shapes.append(T)
    F.entry, F.first = T.port("top"), T
    cont: List[Jump] = []
    brk: List[Jump] = []

    def take(j: Jump) -> None:
        if j.target == tid:
            (cont if j.kind == "continue" else brk).append(j)
        else:
            F.jumps.append(j)

    if body.shapes:
        body.move(0, T.bottom + sp["vertical"] - body.top())
        F.lines.append(Line([T.port("bottom"), body.entry], True))
        F.add(body, with_jumps=False)
        for j in body.jumps:
            take(j)
        body_exit = body.exit
        y_after = max(F.bottom(), body.bottom())
    elif body.pure_jump:
        for j in body.jumps:
            ctx.set_jump(j, T.port("bottom"), "down")
            take(j)
        body_exit = None
        y_after = max(b[3] for b in [T.bbox()] + [bx for j in cont + brk for bx in j.reservations()])
    else:
        y_after = T.bottom
        body_exit = T.port("bottom")
    for j in cont + brk:      # места под ещё не проведённые переходы этого цикла
        for b in j.reservations():
            y_after = max(y_after, b[3])

    y_c = None
    if cont:
        y_c = y_after + sp["vertical"]
        if body_exit is not None:
            F.lines.append(Line([body_exit, (0.0, y_c)], False))
        end_top = y_c + sp["vertical"]
    else:
        end_top = y_after + sp["vertical"]
    E = make_box(ctx, "loop_end", end_text)
    E.y = end_top + E.h / 2
    if cont:
        F.lines.append(Line([(0.0, y_c), E.port("top")], True))
    elif body_exit is not None:
        F.lines.append(Line([body_exit, E.port("top")], True))
    F.shapes.append(E)

    y_b = None
    if brk:
        y_b = E.bottom + sp["vertical"]
        if not infinite:
            F.lines.append(Line([E.port("bottom"), (0.0, y_b)], False))
        F.exit = (0.0, y_b)
    else:
        F.exit = None if infinite else E.port("bottom")

    route_jumps(ctx, F, [(cont, (0.0, y_c) if y_c is not None else None),
                         (brk, (0.0, y_b) if y_b is not None else None)])
    if cont and body_exit is not None:
        F.dots.append(Dot(0.0, y_c))
    if brk and not infinite:
        F.dots.append(Dot(0.0, y_b))
    F.last = None
    F.move(0, -F.top())
    return F


def build_switch(ctx: Ctx, s: dict) -> Frag:
    lab = ctx.st["labels"]
    sp = ctx.sp
    tid = ctx.push_target("switch")
    levels = [(c["cond"], list(c.get("body", [])), lab["yes"], lab["no"]) for c in s.get("cases", [])]
    default = list(s.get("default") or [])
    if levels:
        F = build_chain(ctx, levels, default, lab["no"])
    else:                                   # switch только с default — это просто его действия
        F = build_seq(ctx, default, False) if default else Frag()
    ctx.pop_target()
    brk = [j for j in F.jumps if j.target == tid]
    F.jumps = [j for j in F.jumps if j.target != tid]
    if not F.shapes:
        ctx.warn("switch без case и без действий нарисован одним блоком")
        return simple(ctx, "process", f"switch ({s.get('value', '')})")
    if brk:
        y_after = F.bottom()
        for j in brk:
            for b in j.reservations():
                y_after = max(y_after, b[3])
        y_b = y_after + sp["vertical"]
        if F.exit is not None:
            F.lines.append(Line([F.exit, (0.0, y_b)], False))
            F.dots.append(Dot(0.0, y_b))
        F.exit = (0.0, y_b)
        route_jumps(ctx, F, [(brk, (0.0, y_b))])
    return F


# ====================================================================== переходы break / continue

def route_jumps(ctx: Ctx, F: Frag, groups) -> None:
    """Проводит линии от break/continue к точкам цикла: справа по вертикали,
    а если так нельзя — через соединители с одинаковыми номерами.
    Сначала continue (внутренняя линия), потом break (внешняя)."""
    groups = [(js, t) for js, t in groups if js and t is not None]
    for gi, (js, t) in enumerate(groups):
        pending = list(F.jumps) + [j for js2, _ in groups[gi + 1:] for j in js2]
        if not _try_lane(ctx, F, js, t, pending):
            _connectors(ctx, F, js, t)


def _try_lane(ctx: Ctx, F: Frag, jumps: List[Jump], target: Point, pending: Sequence[Jump] = ()) -> bool:
    sp = ctx.sp
    tx, ty = target
    boxes = F.boxes()
    srcs = [(j.path(), j.turn()[1], j) for j in jumps]

    # прямой спуск самого нижнего перехода «вниз»
    straight = None
    downs = [s for s in srcs if s[2].dir == "down" and s[0][0][1] < ty - 0.1]
    if downs:
        cand = max(downs, key=lambda s: s[0][0][1])
        (sx, sy) = cand[0][0]
        ok = not _blocked(_vseg(sx, sy, ty), boxes, [(sx, sy)]) and \
            not _blocked(_hseg(sx, tx, ty), boxes, [(tx, ty)])
        for s in srcs:
            if s is not cand and sy - 0.02 <= s[1] <= ty + 0.02 and s[0][-1][0] < sx + 0.02:
                ok = False
        for j in pending:
            if j.src is None:
                continue
            px, py = j.turn()
            if sy - 0.25 <= py <= ty + 0.25 and px < sx:
                ok = False
        if ok:
            straight = cand
    others = [s for s in srcs if s is not straight]

    lines: List[Line] = []
    dots: List[Dot] = []
    if others:
        y_top = min(s[1] for s in others)
        band = [b for b in boxes if b[3] > y_top - 0.05 and b[1] < ty + 0.05]
        xs = [b[2] for b in band] + [s[0][-1][0] + sp["arrow_min"] for s in others]
        if straight is not None:
            xs.append(straight[0][0][0] + sp["arrow_min"])
        lane_x = max(xs) + sp["lane"]
        lane_x = max(lane_x, _jump_room(ctx, pending, y_top, ty))
        for pts, hy, j in others:
            ign = [pts[0]]
            if len(pts) == 2 and _blocked(_vseg(pts[0][0], pts[0][1], pts[1][1]), boxes, ign):
                return False
            if _blocked(_hseg(pts[-1][0], lane_x, hy), boxes, ign):
                return False
        if _blocked(_vseg(lane_x, y_top, ty), boxes, []):
            return False
        if _blocked(_hseg(lane_x, tx, ty), boxes, [(tx, ty)]):
            return False
        others.sort(key=lambda s: s[1])
        for k, (pts, hy, j) in enumerate(others):
            lines.append(Line(pts + [(lane_x, hy)], False))
            if k > 0:
                dots.append(Dot(lane_x, hy))
        lines.append(Line([(lane_x, y_top), (lane_x, ty), (tx, ty)], True))
        if straight is not None:
            (sx, sy) = straight[0][0]
            lines.append(Line([(sx, sy), (sx, ty)], False))
            dots.append(Dot(sx, ty))
    else:
        (sx, sy) = straight[0][0]
        lines.append(Line([(sx, sy), (sx, ty), (tx, ty)], True))
    F.lines += lines
    F.dots += dots
    F.lanes.append(LaneRec(lines, dots, [(list(s[0]), s[1]) for s in srcs], target))
    return True


def _connectors(ctx: Ctx, F: Frag, jumps: List[Jump], target: Point) -> None:
    label = ctx.next_digit()
    for j in jumps:
        _src_connector(ctx, F, j.path(), j.turn()[1], label)
    _dst_connector(ctx, F, target, label)


def _src_connector(ctx: Ctx, F: Frag, pts: List[Point], hy: float, label: str) -> None:
    """Короткая стрелка от перехода вправо в кружок с номером."""
    sp, r = ctx.sp, ctx.r
    sx = pts[-1][0]
    boxes = F.boxes()
    ign = [pts[0]]
    cx0 = sx + sp["arrow_min"] + r
    cx = cx0
    for _ in range(300):
        cb = (cx - r - CM, hy - r - CM, cx + r + CM, hy + r + CM)
        if not _blocked(cb, boxes, ign) and not _blocked(_hseg(sx, cx - r, hy), boxes, ign):
            break
        cx += 0.1
    else:
        cx = cx0
        ctx.warn("не удалось без наложений поставить соединитель перехода — проверьте схему")
    F.shapes.append(Shape("connector", cx, hy, 2 * r, 2 * r, [label]))
    F.lines.append(Line(list(pts) + [(cx - r, hy)], True))


def _dst_connector(ctx: Ctx, F: Frag, target: Point, label: str) -> None:
    """Кружок с тем же номером у точки, куда ведёт переход."""
    sp, r = ctx.sp, ctx.r
    tx, ty = target
    boxes = F.boxes()
    ign = [(tx, ty)]
    near = [b for b in boxes if b[3] > ty - r - 0.08 and b[1] < ty + r + 0.08 and not box_contains(b, (tx, ty), 0.03)]
    preferred = max([b[2] for b in near] + [tx + 0.3]) + sp["arrow_min"] + r
    options = [preferred] + [tx + sp["arrow_min"] + r + 0.1 * k for k in range(300)]
    chosen = None
    for cx in options:
        cb = (cx - r - CM, ty - r - CM, cx + r + CM, ty + r + CM)
        if not _blocked(cb, boxes, ign) and not _blocked(_hseg(tx, cx - r, ty), boxes, ign):
            chosen = cx
            break
    if chosen is None:
        chosen = preferred
        ctx.warn("не удалось без наложений поставить соединитель перехода — проверьте схему")
    F.shapes.append(Shape("connector", chosen, ty, 2 * r, 2 * r, [label]))
    F.lines.append(Line([(chosen - r, ty), (tx, ty)], True))


# ====================================================================== функция целиком

def build_function(ctx: Ctx, ir: dict) -> Frag:
    sp = ctx.sp
    term = ctx.st["terminals"]
    start = ir.get("start") or (term["start_main"] if ctx.is_main else term["start"])
    S0 = make_box(ctx, "term", start)
    F = Frag()
    F.shapes.append(S0)
    F.entry, F.first = S0.port("top"), S0
    body = build_seq(ctx, ir.get("body", []), True)
    for j in body.jumps:
        ctx.warn(f"{j.kind} вне цикла пропущен")
    body.jumps = []
    exit_pt: Optional[Point] = S0.port("bottom")
    if body.shapes:
        body.move(0, S0.bottom + sp["vertical"] - body.top())
        F.lines.append(Line([exit_pt, body.entry], True))
        F.cuts.append((S0.bottom, body.top()))
        F.add(body)
        exit_pt = body.exit
    if exit_pt is not None:
        end = ir.get("end") or (term["end_main"] if ctx.is_main else term["end"])
        bottom = F.bottom()
        E = make_box(ctx, "term", end)
        E.y = exit_pt[1] + sp["vertical"] + E.h / 2
        E.y += _push_down([E.bbox()], F.boxes())
        F.lines.append(Line([exit_pt, E.port("top")], True))
        if body.shapes and abs(exit_pt[0]) < EPS and E.top > bottom + 0.05:
            F.cuts.append((bottom, E.top))
        F.shapes.append(E)
    return F


# ====================================================================== столбцы

def _lane_line_ids(F: Frag) -> set:
    return {id(l) for ln in F.lanes for l in ln.lines}


def _cut_is_valid(F: Frag, y: float, lane_ids: set) -> bool:
    axis_x = F.first.x if F.first is not None else 0.0
    for s in F.shapes:
        if s.top - 0.01 < y < s.bottom + 0.01:
            return False
    for l in F.labels:
        b = l.bbox()
        if b[1] - 0.01 < y < b[3] + 0.01:
            return False
    for d in F.dots:
        if abs(d.y - y) < 0.06:
            return False
    for l in F.lines:
        for a, b in l.segments():
            if abs(a[1] - b[1]) < EPS:
                if abs(a[1] - y) < 0.03:
                    return False
                continue
            if min(a[1], b[1]) < y < max(a[1], b[1]):
                if id(l) in lane_ids:
                    continue
                if abs(a[0] - axis_x) > 0.01 or b[1] < a[1]:
                    return False
    return True


def _convert_lanes(ctx: Ctx, F: Frag, ycs: List[float]) -> None:
    """Переходы, чья линия пересекла бы разрез, заменяет парами соединителей."""
    for lane in list(F.lanes):
        ys = [p[1] for l in lane.lines for p in l.pts]
        lo, hi = min(ys), max(ys)
        if not any(lo + 0.01 < yc < hi - 0.01 for yc in ycs):
            continue
        ids = {id(l) for l in lane.lines}
        F.lines = [l for l in F.lines if id(l) not in ids]
        dids = {id(d) for d in lane.dots}
        F.dots = [d for d in F.dots if id(d) not in dids]
        F.lanes.remove(lane)
        label = ctx.next_digit()
        for pts, hy in lane.sources:
            _src_connector(ctx, F, list(pts), hy, label)
        _dst_connector(ctx, F, lane.target, label)


def _split(ctx: Ctx, F: Frag, cuts: List[Tuple[float, float]]) -> List[Frag]:
    """Режет фрагмент по выбранным местам на столбцы (без сдвига по x)."""
    if not cuts:
        return [F]
    r = ctx.r
    g = ctx.sp["vertical"]
    letters = ctx.st.get("connector_letters") or "АБВГДЕЖИКЛМНПРСТУФХЦЧШЭЮЯ"
    cuts = sorted(cuts)
    ycs = [(a + b) / 2 for a, b in cuts]
    _convert_lanes(ctx, F, ycs)
    cols = [Frag() for _ in range(len(cuts) + 1)]

    def col_of(y: float) -> int:
        k = 0
        while k < len(ycs) and y > ycs[k]:
            k += 1
        return k

    for s in F.shapes:
        cols[col_of(s.y)].shapes.append(s)
    for l in F.labels:
        b = l.bbox()
        cols[col_of((b[1] + b[3]) / 2)].labels.append(l)
    for d in F.dots:
        cols[col_of(d.y)].dots.append(d)

    for line in F.lines:
        pieces = [[line.pts[0]]]
        crossings: List[Tuple[int, float]] = []
        for a, b in zip(line.pts, line.pts[1:]):
            lo, hi = min(a[1], b[1]), max(a[1], b[1])
            for k, yc in enumerate(ycs):
                if lo < yc < hi:          # такие отрезки — только вертикальные вниз по оси
                    p = (a[0], yc)
                    pieces[-1].append(p)
                    crossings.append((k, a[0]))
                    pieces.append([p])
            pieces[-1].append(b)
        if not crossings:
            ys = [p[1] for p in line.pts]
            cols[col_of((min(ys) + max(ys)) / 2)].lines.append(line)
            continue
        for idx, pc in enumerate(pieces):
            arrow = line.arrow if idx == len(pieces) - 1 else True
            col = crossings[0][0] if idx == 0 else crossings[idx - 1][0] + 1
            if idx > 0:
                k, x = crossings[idx - 1]
                pc[0] = (x, cuts[k][1] - g)
            if idx < len(pieces) - 1:
                k, x = crossings[idx]
                pc[-1] = (x, cuts[k][0] + g)
            cols[col].lines.append(Line(pc, arrow))
        for k, x in crossings:
            label = letters[k % len(letters)]
            cols[k].shapes.append(Shape("connector", x, cuts[k][0] + g + r, 2 * r, 2 * r, [label]))
            cols[k + 1].shapes.append(Shape("connector", x, cuts[k][1] - g - r, 2 * r, 2 * r, [label]))
    for k in range(1, len(cols)):
        top = cuts[k - 1][1] - g - 2 * r
        cols[k].move(0, -top)
    return cols


def _choose_cuts(F: Frag, k: int) -> Optional[List[Tuple[float, float]]]:
    """Выбирает k−1 разрезов так, чтобы самый высокий столбец был как можно ниже."""
    if k <= 1:
        return []
    lane_ids = _lane_line_ids(F)
    cands = sorted(set(c for c in F.cuts if _cut_is_valid(F, (c[0] + c[1]) / 2, lane_ids)))
    if len(cands) < k - 1:
        return None
    top, bottom = F.top(), F.bottom()
    n = len(cands)
    INF = float("inf")
    best = [[INF] * n for _ in range(k)]
    prev = [[-1] * n for _ in range(k)]
    for i in range(n):
        best[1][i] = cands[i][0] - top
    for m in range(2, k):
        for i in range(n):
            for p in range(i):
                h = cands[i][0] - cands[p][1]
                if h <= 0 or best[m - 1][p] == INF:
                    continue
                v = max(best[m - 1][p], h)
                if v < best[m][i]:
                    best[m][i], prev[m][i] = v, p
    res, last = INF, -1
    for i in range(n):
        if best[k - 1][i] < INF:
            v = max(best[k - 1][i], bottom - cands[i][1])
            if v < res:
                res, last = v, i
    if last < 0:
        return None
    chosen = []
    m, i = k - 1, last
    while m >= 1 and i >= 0:
        chosen.append(cands[i])
        i = prev[m][i]
        m -= 1
    return sorted(chosen)


def _arrange(ctx: Ctx, cols: List[Frag]) -> List[Frag]:
    gap = ctx.st["columns"]["gap"]
    x = None
    for c in cols:
        b = c.bounds()
        if x is None:
            x = b[2]
            continue
        c.move(x + gap - b[0], 0)
        x = c.bounds()[2]
    return cols


def _measure(cols: List[Frag]) -> Tuple[float, float]:
    u = union([c.bounds() for c in cols])
    return (u[2] - u[0], u[3] - u[1])


# ====================================================================== склейка линий

def _key(p: Point) -> Tuple[float, float]:
    return (round(p[0], 3), round(p[1], 3))


def _simplify(pts: List[Point]) -> List[Point]:
    out: List[Point] = []
    for p in pts:
        if out and abs(out[-1][0] - p[0]) < 1e-6 and abs(out[-1][1] - p[1]) < 1e-6:
            continue
        out.append(p)
    i = 1
    while i < len(out) - 1:
        a, b, c = out[i - 1], out[i], out[i + 1]
        same_x = abs(a[0] - b[0]) < 1e-6 and abs(b[0] - c[0]) < 1e-6
        same_y = abs(a[1] - b[1]) < 1e-6 and abs(b[1] - c[1]) < 1e-6
        between = (min(a[0], c[0]) - 1e-6 <= b[0] <= max(a[0], c[0]) + 1e-6 and
                   min(a[1], c[1]) - 1e-6 <= b[1] <= max(a[1], c[1]) + 1e-6)
        if (same_x or same_y) and between:
            del out[i]
            continue
        i += 1
    return out


def _on_segment_interior(p: Point, a: Point, b: Point, tol: float = 1e-3) -> bool:
    if abs(a[0] - b[0]) < 1e-6:
        return abs(p[0] - a[0]) < tol and min(a[1], b[1]) + tol < p[1] < max(a[1], b[1]) - tol
    if abs(a[1] - b[1]) < 1e-6:
        return abs(p[1] - a[1]) < tol and min(a[0], b[0]) + tol < p[0] < max(a[0], b[0]) - tol
    return False


def merge_lines(lines: List[Line], dots: List[Dot]) -> List[Line]:
    """Склеивает линии A→P и P→B в одну, если в точке P больше ничего не сходится:
    тогда стрелка остаётся только в конце, а не на изломе."""
    lines = [Line(_simplify(l.pts), l.arrow) for l in lines]
    dotset = {_key((d.x, d.y)) for d in dots}
    changed = True
    while changed:
        changed = False
        ends = {}
        for i, l in enumerate(lines):
            ends.setdefault(_key(l.pts[0]), []).append((i, 0))
            ends.setdefault(_key(l.pts[-1]), []).append((i, 1))
        for k, lst in ends.items():
            if len(lst) != 2 or k in dotset:
                continue
            (i, a), (j, b) = lst
            if i == j:
                continue
            if a == 1 and b == 0:
                A, B = i, j
            elif a == 0 and b == 1:
                A, B = j, i
            else:
                continue
            P = lines[A].pts[-1]
            busy = False
            for idx, l in enumerate(lines):
                if idx in (A, B):
                    continue
                if any(_on_segment_interior(P, s0, s1) for s0, s1 in zip(l.pts, l.pts[1:])):
                    busy = True
                    break
            if busy:
                continue
            merged = Line(_simplify(lines[A].pts + lines[B].pts[1:]), lines[B].arrow)
            lines = [l for idx, l in enumerate(lines) if idx not in (A, B)] + [merged]
            changed = True
            break
    return lines


# ====================================================================== итог

def layout_function(ir: dict, style: dict) -> Diagram:
    ctx = Ctx(style, bool(ir.get("is_main", False)))
    F = build_function(ctx, ir)
    F.jumps = []
    F.reserved = []
    margin = style["margin"]

    # заголовок
    title_lab = None
    tcfg = style.get("title", {})
    if ir.get("title") or tcfg.get("show", True):
        text = ir.get("title") or tcfg.get("template", "{name}({params})").format(
            name=ir.get("name", ""), params=", ".join(ir.get("params", [])))
        if text.strip():
            title_lab = Label(0, 0, text, "tl", ctx.mt.width(text), ctx.mt.size * 1.25 / 72.0,
                              ctx.mt.size, "title")
    title_h = (title_lab.h + 0.18) if title_lab else 0.0

    # число столбцов: так, чтобы схема на странице выглядела крупнее
    ccfg = style.get("columns", {})
    mode = ir.get("columns", ccfg.get("mode", "auto"))
    max_k = max(1, int(ccfg.get("max", 4)))
    page_w, page_h = style["page"]["width"], style["page"]["height"]
    if isinstance(mode, str) and mode.strip().isdigit():
        mode = int(mode)
    if isinstance(mode, int) and not isinstance(mode, bool):
        candidates = list(range(max(1, mode), 0, -1))
        forced = True
    else:
        candidates = list(range(1, max_k + 1))
        forced = False
    best = None
    for k in candidates:
        cuts = _choose_cuts(F, k)
        if cuts is None:
            continue
        saved = (ctx.digit, list(ctx.warnings))
        cols = _arrange(ctx, _split(ctx, copy.deepcopy(F), cuts))
        w, h = _measure(cols)
        w = max(w, title_lab.w if title_lab else 0.0) + 2 * margin
        h = h + 2 * margin + title_h
        scale = min(page_w / w, page_h / h, 1.0)
        ctx.digit, ctx.warnings = saved
        if forced:
            best = (scale, k, cuts)
            break
        if best is None or scale > best[0] * 1.03:
            best = (scale, k, cuts)
    if best is None:
        best = (0, 1, [])
    _, k, cuts = best
    if forced and k != candidates[0]:
        ctx.warn(f"на {candidates[0]} столбца схему не разрезать, сделано столбцов: {k}")
    cols = _arrange(ctx, _split(ctx, F, cuts))

    D = Diagram(name=ir.get("name", ""), width=0, height=0, columns=k)
    for c in cols:
        D.shapes += c.shapes
        D.lines += c.lines
        D.labels += c.labels
        D.dots += c.dots
    u = union([c.bounds() for c in cols])
    dx = margin - u[0]
    dy = margin + title_h - u[1]
    for s in D.shapes:
        s.move(dx, dy)
    for l in D.lines:
        l.move(dx, dy)
    for l in D.labels:
        l.move(dx, dy)
    for d in D.dots:
        d.move(dx, dy)
    D.lines = merge_lines(D.lines, D.dots)
    D.width = max(u[2] - u[0], title_lab.w if title_lab else 0.0) + 2 * margin
    D.height = (u[3] - u[1]) + 2 * margin + title_h
    if title_lab:
        title_lab.x, title_lab.y = margin, margin
        D.labels.append(title_lab)
    D.warnings = list(ctx.warnings)
    return D

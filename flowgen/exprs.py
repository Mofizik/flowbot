"""Часть 2 (вспомогательная). Печать выражений C++ в текст для блоков.

Текст строится по синтаксическому дереву, а не заменой подстрок, поэтому
упрощения (словарь rules.json) никогда не задевают строки в кавычках.
Если какой-то узел не удалось красиво напечатать, берётся его исходный текст.
"""
from __future__ import annotations

import re
from typing import List, Optional, Set, Tuple

from tree_sitter import Node

from .cpp_parser import named_children

# приоритет бинарных операций (меньше — связывает сильнее)
PRECEDENCE = {
    "*": 3, "/": 3, "%": 3, "+": 4, "-": 4, "<<": 5, ">>": 5,
    "<": 6, "<=": 6, ">": 6, ">=": 6, "==": 7, "!=": 7,
    "&": 8, "^": 9, "|": 10, "&&": 11, "||": 12,
    "and": 11, "or": 12, "bitand": 8, "bitor": 10, "xor": 9, "not_eq": 7,
}

NEWLINE = object()   # маркер «перевод строки» в списке выводимых элементов

# лексемы для печати исходного текста с заменой операторов (запасной путь)
_TOKEN = re.compile(r"""(\s+|R"\(.*?\)"|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|"""
                    r"""\d+(?:\.\d*)?(?:[eE][-+]?\d+)?\w*|\.\d+(?:[eE][-+]?\d+)?\w*|[A-Za-z_]\w*|"""
                    r"""::|->\*?|\+\+|--|<<=|>>=|<=>|&&|\|\||[!=<>]=|<<|>>|[-+*/%&|^]=|.)""", re.S)
_WORD_OPS = ("&&", "||", "!", "and", "or", "not")


class ExprPrinter:
    def __init__(self, source: bytes, rules: dict, streams: Set[str], user_funcs: Set[str], warn=None):
        self.src = source
        self.warn = warn or (lambda msg: None)
        self.rules = rules
        self.streams = set(streams)
        self.user_funcs = set(user_funcs)
        self.ops = dict(rules.get("operators", {}))
        self.cond_ops = dict(rules.get("condition_operators", {}))
        self.drop_ns = list(rules.get("drop_namespaces", []))

    # ------------------------------------------------------------ базовое
    def raw(self, n: Optional[Node]) -> str:
        if n is None:
            return ""
        return self.src[n.start_byte:n.end_byte].decode("utf-8", "replace")

    def norm_name(self, n: Node) -> str:
        """Имя без std:: и лишних пробелов (std::cout -> cout)."""
        t = "".join(self.raw(n).split())
        for ns in self.drop_ns:
            pref = ns + "::"
            while t.startswith(pref):
                t = t[len(pref):]
        return t

    def op(self, token: str, cond: bool) -> str:
        if cond and token in self.cond_ops:
            return self.cond_ops[token]
        return self.ops.get(token, token)

    def p(self, n: Optional[Node], cond: bool = False) -> str:
        """Текст выражения с упрощениями."""
        if n is None:
            return ""
        if n.has_error:
            raw = " ".join(self.raw(n).split())
            self.warn(f"строка {n.start_point[0] + 1}: tree-sitter не смог разобрать «{raw[:50]}», "
                      f"в блок взят исходный текст")
            return raw
        if self._false_template(n):
            return self.raw_expr(n, cond)
        handler = getattr(self, "_p_" + n.type, None)
        if handler is not None:
            try:
                return handler(n, cond)
            except Exception:
                pass
        return self._default(n, cond)

    @staticmethod
    def _false_template(n: Node) -> bool:
        """«n < N && in >> x» tree-sitter читает как шаблон n<N && in> и «> x».
        Признак: в аргументах шаблона стоит выражение с операцией."""
        if n.type in ("template_argument_list", "template_function", "template_method", "template_type",
                      "condition_clause"):
            return False
        stack = [n]
        while stack:
            x = stack.pop()
            if x.type == "template_argument_list":
                if any(c.type == "binary_expression" for c in x.named_children):
                    return True
                continue
            if x.type in ("lambda_expression", "compound_statement"):
                continue
            stack.extend(x.named_children)
        return False

    def raw_expr(self, n: Node, cond: bool = False) -> str:
        """Исходный текст выражения с заменой операторов по rules.json."""
        out: List[str] = []
        for tok in _TOKEN.findall(self.raw(n)):
            if tok.isspace():
                out.append(" ")
                continue
            rep = self.op(tok, cond) if tok in self.ops or tok in self.cond_ops else tok
            if tok in _WORD_OPS and rep != tok:
                rep = f" {rep.strip()} " if tok not in ("!", "not") else f" {rep.strip()} "
            out.append(rep)
        text = re.sub(r"\s+", " ", "".join(out)).strip()
        text = re.sub(r"\(\s+", "(", text)
        text = re.sub(r"\s+\)", ")", text)
        for ns in self.drop_ns:
            text = re.sub(rf"\b{re.escape(ns)}::", "", text)
        return text

    def _default(self, n: Node, cond: bool) -> str:
        if n.child_count == 0:
            return " ".join(self.raw(n).split()) if n.type not in ("string_content",) else self.raw(n)
        out: List[str] = []
        prev_end = None
        need_space = False
        for c in n.children:
            if c.type == "comment":
                need_space = True
                prev_end = c.end_byte
                continue
            s = self.p(c, cond)
            if prev_end is not None:
                gap = self.src[prev_end:c.start_byte]
                if need_space or (gap and not gap.strip()):
                    out.append(" ")
            need_space = False
            out.append(s)
            prev_end = c.end_byte
        return "".join(out).strip()

    # ------------------------------------------------------------ выражения
    def _p_binary_expression(self, n, cond):
        l, r = n.child_by_field_name("left"), n.child_by_field_name("right")
        o = n.child_by_field_name("operator")
        token = self.raw(o) if o is not None else self.raw(n.children[1])
        return f"{self.p(l, cond)} {self.op(token, cond)} {self.p(r, cond)}"

    def _p_unary_expression(self, n, cond):
        o = n.child_by_field_name("operator")
        a = n.child_by_field_name("argument")
        token = self.raw(o)
        if token == "-":
            sym = self.rules.get("unary_minus", "-")
        else:
            sym = self.op(token, cond)
        return f"{sym}{self.p(a, cond)}"

    def _p_update_expression(self, n, cond):
        o = n.child_by_field_name("operator")
        a = n.child_by_field_name("argument")
        if o is not None and a is not None and o.start_byte < a.start_byte:
            return f"{self.raw(o)}{self.p(a, cond)}"
        return f"{self.p(a, cond)}{self.raw(o)}"

    def _p_assignment_expression(self, n, cond):
        l, r = n.child_by_field_name("left"), n.child_by_field_name("right")
        o = n.child_by_field_name("operator")
        return f"{self.p(l, cond)} {self.raw(o)} {self.p(r, cond)}"

    def _p_parenthesized_expression(self, n, cond):
        inner = named_children(n)
        if len(inner) == 1:
            return f"({self.p(inner[0], cond)})"
        return self._default(n, cond)

    def _p_condition_clause(self, n, cond):
        v = n.child_by_field_name("value")
        if v is not None and n.child_by_field_name("initializer") is None:
            return self.p(v, True)
        return self._default(n, True)

    def _p_conditional_expression(self, n, cond):
        c = n.child_by_field_name("condition")
        a = n.child_by_field_name("consequence")
        b = n.child_by_field_name("alternative")
        return f"{self.p(c, True)} ? {self.p(a, cond)} : {self.p(b, cond)}"

    def _p_comma_expression(self, n, cond):
        l, r = n.child_by_field_name("left"), n.child_by_field_name("right")
        return f"{self.p(l, cond)}, {self.p(r, cond)}"

    def _p_argument_list(self, n, cond):
        return "(" + ", ".join(self.p(a, cond) for a in named_children(n)) + ")"

    def _p_initializer_list(self, n, cond):
        items = named_children(n)
        k = int(self.rules.get("max_init_items", 3))
        shown = [self.p(a, cond) for a in items[:k]]
        if len(items) > k:
            shown.append("…")
        return "{" + ", ".join(shown) + "}"

    def _p_qualified_identifier(self, n, cond):
        return self.norm_name(n)

    def _p_call_expression(self, n, cond):
        fn = n.child_by_field_name("function")
        args = n.child_by_field_name("arguments")
        tmpl = self.stream_method_template(n)
        if tmpl is not None:
            return tmpl
        return f"{self.p(fn, cond)}{self.p(args, cond)}"

    def _p_number_literal(self, n, cond):
        t = self.raw(n).strip()
        if t.startswith("-"):
            return self.rules.get("unary_minus", "-") + t[1:]
        return t

    def _p_string_literal(self, n, cond):
        return self.raw(n)

    def _p_char_literal(self, n, cond):
        return self.raw(n)

    def _p_raw_string_literal(self, n, cond):
        return self.raw(n)

    def _p_concatenated_string(self, n, cond):
        return " ".join(self.raw(c) for c in named_children(n))

    def _p_lambda_expression(self, n, cond):
        t = " ".join(self.raw(n).split())
        return t if len(t) <= 40 else t[:39] + "…"

    # ------------------------------------------------------------ потоки
    def is_stream(self, n: Optional[Node], direction: str) -> bool:
        """direction: 'out' или 'in'."""
        if n is None or n.type not in ("identifier", "qualified_identifier", "field_expression"):
            return False
        name = self.norm_name(n)
        if direction == "out" and name in self.rules.get("console_output", []):
            return True
        if direction == "in" and name in self.rules.get("console_input", []):
            return True
        return name in self.streams

    def stream_chain(self, n: Optional[Node], token: str) -> Optional[Tuple[Node, List[Node]]]:
        """a << b << c -> (a, [b, c]), если a — поток."""
        items: List[Node] = []
        cur = n
        while cur is not None and cur.type == "binary_expression":
            o = cur.child_by_field_name("operator")
            if o is None or self.raw(o) != token:
                break
            items.insert(0, cur.child_by_field_name("right"))
            cur = cur.child_by_field_name("left")
        if not items:
            return None
        if self.is_stream(cur, "out" if token == "<<" else "in"):
            return cur, items
        return None

    def stream_method_template(self, call: Node) -> Optional[str]:
        fn = call.child_by_field_name("function")
        if fn is None or fn.type != "field_expression":
            return None
        obj = fn.child_by_field_name("argument")
        method = fn.child_by_field_name("field")
        if obj is None or method is None:
            return None
        if not (self.is_stream(obj, "in") or self.is_stream(obj, "out")):
            return None
        templates = self.rules.get("stream_methods", {})
        tmpl = templates.get(self.raw(method))
        if tmpl is None:
            return None
        args = [self.p(a) for a in named_children(call.child_by_field_name("arguments"))]
        try:
            return tmpl.format(*args, obj=self.norm_name(obj), args=", ".join(args))
        except (IndexError, KeyError):
            return None

    def string_value(self, n: Node) -> Optional[str]:
        """Содержимое строкового литерала без кавычек и управляющих символов."""
        if n.type == "concatenated_string":
            parts = [self.string_value(c) for c in named_children(n)]
            return " ".join(p for p in parts if p)
        if n.type == "raw_string_literal":
            t = self.raw(n)
            m = re.match(r'^[A-Za-z0-9]*R"([^(]*)\((.*)\)\1"$', t, re.S)
            return m.group(2) if m else t
        if n.type != "string_literal":
            return None
        t = self.raw(n)
        q = t.find('"')
        body = t[q + 1:-1] if q >= 0 else t
        body = re.sub(r"\\[ntrvfab0]", " ", body)
        body = body.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
        return body

    def output_items(self, items: List[Node], dropped: Optional[list] = None) -> list:
        """Элементы вывода: строки «…», выражения, NEWLINE; манипуляторы выбрасываются
        (их текст складывается в dropped, если список передан)."""
        q1, q2 = self.rules.get("quotes", ["«", "»"])
        max_chars = int(self.rules.get("max_string_chars", 40))
        drop = set(self.rules.get("drop_manipulators", []))
        drop_calls = set(self.rules.get("drop_manipulator_calls", []))
        newline_words = set(self.rules.get("newline_manipulators", ["endl"]))
        out: list = []
        for it in items:
            if it is None:
                continue
            if it.type in ("string_literal", "concatenated_string", "raw_string_literal"):
                raw = self.raw(it)
                s = self.string_value(it) or ""
                s = " ".join(s.split())
                if not s:
                    if "\\n" in raw:
                        out.append(NEWLINE)
                    continue
                if len(s) > max_chars:
                    s = s[: max_chars - 1].rstrip() + "…"
                out.append(q1 + s + q2)
                continue
            if it.type == "char_literal":
                t = self.raw(it)
                if t in ("'\\n'",):
                    out.append(NEWLINE)
                    continue
                if t in ("' '", "'\\t'"):
                    continue
                out.append(t)
                continue
            if it.type in ("identifier", "qualified_identifier"):
                name = self.norm_name(it)
                if name in newline_words:
                    out.append(NEWLINE)
                    continue
                if name in drop:
                    if dropped is not None:
                        dropped.append(name)
                    continue
            if it.type == "call_expression":
                fn = it.child_by_field_name("function")
                if fn is not None and self.norm_name(fn) in drop_calls:
                    if dropped is not None:
                        dropped.append(self.p(it))
                    continue
            out.append(self.p(it))
        return out

    def input_items(self, items: List[Node]) -> list:
        drop = set(self.rules.get("drop_manipulators", []))
        res = []
        for it in items:
            if it is None:
                continue
            if it.type in ("identifier", "qualified_identifier") and self.norm_name(it) in drop:
                continue
            if it.type == "call_expression":
                fn = it.child_by_field_name("function")
                if fn is not None and self.norm_name(fn) in set(self.rules.get("drop_manipulator_calls", [])):
                    continue
            res.append(self.p(it))
        return res

    # ------------------------------------------------------------ операторы-утверждения
    def statement(self, n: Node) -> str:
        """Текст выражения, стоящего отдельным оператором (i++ -> i = i + 1)."""
        rw = self.rules.get("statement_rewrites", {})
        if n.type == "update_expression":
            o = self.raw(n.child_by_field_name("operator"))
            x = self.p(n.child_by_field_name("argument"))
            key = "increment" if o == "++" else "decrement"
            if key in rw:
                return rw[key].format(x=x)
        if n.type == "assignment_expression":
            o = self.raw(n.child_by_field_name("operator"))
            if o in rw and o != "=":
                x = self.p(n.child_by_field_name("left"))
                yn = n.child_by_field_name("right")
                y = self.p(yn)
                if self._needs_parens(o[:-1], yn):
                    y = f"({y})"
                return rw[o].format(x=x, y=y)
        if n.type == "comma_expression":
            l, r = n.child_by_field_name("left"), n.child_by_field_name("right")
            return f"{self.statement(l)}; {self.statement(r)}"
        return self.p(n)

    def _needs_parens(self, base_op: str, y: Optional[Node]) -> bool:
        if y is None:
            return False
        if y.type in ("conditional_expression", "assignment_expression", "comma_expression"):
            return True
        if y.type != "binary_expression":
            return False
        o = y.child_by_field_name("operator")
        yp = PRECEDENCE.get(self.raw(o), 13)
        bp = PRECEDENCE.get(base_op, 13)
        if base_op in ("-", "/", "%"):
            return yp >= bp
        return yp > bp

    # ------------------------------------------------------------ поиск вызовов
    def calls_user_function(self, n: Optional[Node]) -> bool:
        if n is None:
            return False
        stack = [n]
        while stack:
            x = stack.pop()
            if x.type == "call_expression":
                fn = x.child_by_field_name("function")
                if fn is not None:
                    name = self.norm_name(fn)
                    if fn.type == "field_expression":
                        f2 = fn.child_by_field_name("field")
                        name = self.raw(f2) if f2 is not None else name
                    if name.split("::")[-1] in self.user_funcs:
                        return True
            if x.type == "lambda_expression":
                continue
            stack.extend(x.children)
        return False

    def call_name(self, n: Optional[Node]) -> str:
        if n is None or n.type != "call_expression":
            return ""
        fn = n.child_by_field_name("function")
        return self.norm_name(fn) if fn is not None else ""

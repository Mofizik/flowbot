"""Часть 2. Фильтр: из синтаксического дерева оставляет только то, что нужно
для блок-схемы, и записывает это в простой JSON — отдельно для каждой функции.

Формат JSON (он же формат для ручного описания схем) описан в ir.py и README.
"""
from __future__ import annotations

import copy
import re
from typing import Dict, List, Optional, Set

from tree_sitter import Node

from .cpp_parser import FunctionInfo, ParsedSource, declarator_name, named_children, walk
from .exprs import NEWLINE, ExprPrinter
from .ir import IR_VERSION

JUMPS = ("break", "continue", "return", "end")
TRUE_WORDS = ("true", "1", "TRUE")

SKIP_TYPES = {
    "comment", "using_declaration", "alias_declaration", "type_definition",
    "namespace_alias_definition", "static_assert_declaration", "preproc_def",
    "preproc_function_def", "preproc_include", "preproc_call", "function_definition",
    "class_specifier", "struct_specifier", "enum_specifier", "union_specifier",
    "template_declaration", "attributed_statement_placeholder",
}
PREPROC_BLOCKS = {"preproc_if", "preproc_ifdef", "preproc_else", "preproc_elif", "preproc_elifdef"}


class Extractor:
    def __init__(self, parsed: ParsedSource, rules: dict):
        self.ps = parsed
        self.rules = rules
        self.text = parsed.text
        self.global_streams = self._collect_streams(parsed.root, top_level_only=True)

    # ============================================================ общее
    def extract_all(self, only: Optional[List[str]] = None) -> List[dict]:
        result = []
        used: Dict[str, int] = {}
        for fi in self.ps.functions:
            if only and fi.name not in only and fi.short_name not in only:
                continue
            ir = self.extract_function(fi)
            k = used.get(fi.name, 0) + 1
            used[fi.name] = k
            ir["id"] = fi.name if k == 1 else f"{fi.name}#{k}"
            result.append(ir)
        return result

    def extract_function(self, fi: FunctionInfo) -> dict:
        self.warnings: List[str] = []
        streams = set(self.global_streams)
        streams |= self._param_streams(fi)
        streams |= self._collect_streams(fi.body, top_level_only=False)
        self.pr = ExprPrinter(self.ps.source, self.rules, streams, self.ps.declared, warn=self.warn)
        body = self.block(fi.body)
        ir = {
            "flowgen": IR_VERSION,
            "source": self.ps.path,
            "name": fi.name,
            "params": fi.params,
            "signature": fi.signature,
            "is_main": fi.short_name == "main",
            "line": fi.line,
            "body": body,
        }
        self._finalize(ir["body"])
        if self.warnings:
            ir["warnings"] = self.warnings
        return ir

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def line(self, n: Node) -> int:
        return n.start_point[0] + 1

    # ============================================================ потоки
    def _type_is_stream(self, type_text: str) -> bool:
        t = type_text.replace("std::", "")
        return any(re.search(r"\b" + re.escape(s) + r"\b", t) for s in self.rules.get("stream_types", []))

    def _collect_streams(self, root: Node, top_level_only: bool) -> Set[str]:
        names: Set[str] = set()
        nodes = named_children(root) if top_level_only else list(walk(root))
        for n in nodes:
            if n.type != "declaration":
                continue
            tnode = n.child_by_field_name("type")
            if tnode is None or not self._type_is_stream(self.text(tnode)):
                continue
            for f, c in self._declarators(n):
                nm = declarator_name(c, self.text)
                if nm:
                    names.add(nm)
        return names

    def _param_streams(self, fi: FunctionInfo) -> Set[str]:
        names: Set[str] = set()
        from .cpp_parser import find_function_declarator
        fdecl = find_function_declarator(fi.node.child_by_field_name("declarator"))
        params = fdecl.child_by_field_name("parameters") if fdecl is not None else None
        if params is None:
            return names
        for p in named_children(params):
            t = p.child_by_field_name("type")
            if t is not None and self._type_is_stream(self.text(t)):
                nm = declarator_name(p.child_by_field_name("declarator"), self.text)
                if nm:
                    names.add(nm)
        return names

    @staticmethod
    def _declarators(decl: Node):
        for i, c in enumerate(decl.children):
            try:
                f = decl.field_name_for_child(i)
            except AttributeError:
                f = None
            if f == "declarator":
                yield f, c

    # ============================================================ блоки
    def block(self, node: Optional[Node]) -> List[dict]:
        if node is None:
            return []
        if node.type == "compound_statement":
            kids = [c for c in node.children if c.is_named]
        else:
            kids = [node]
        return self.block_list(kids)

    def block_list(self, kids: List[Node]) -> List[dict]:
        out: List[dict] = []
        for i, k in enumerate(kids):
            if k.type in SKIP_TYPES:
                continue
            out.extend(self.stmt(k))
            if out and out[-1]["type"] in JUMPS:
                rest = [x for x in kids[i + 1:] if x.type not in SKIP_TYPES]
                if rest:
                    self.warn(f"строка {self.line(rest[0])}: код после {self._jump_word(out[-1])} "
                              f"никогда не выполняется, на схеме его нет")
                break
        return self.merge(out)

    @staticmethod
    def _jump_word(s: dict) -> str:
        return {"break": "break", "continue": "continue", "return": "return"}.get(s["type"], "выхода")

    # ============================================================ операторы
    def stmt(self, n: Node) -> List[dict]:
        t = n.type
        ln = self.line(n)
        if t == "compound_statement":
            return self.block(n)
        if t in ("expression_statement", "declaration", "return_statement", "throw_statement") and n.has_error:
            return self.broken_statement(n, ln)
        if t == "expression_statement":
            kids = named_children(n)
            if not kids:
                return []
            return self.expression_statement(kids[0], ln)
        if t == "declaration":
            return self.declaration(n, ln)
        if t == "if_statement":
            return self.if_statement(n)
        if t in ("for_statement", "for_range_loop", "while_statement", "do_statement"):
            return [self.loop(n)]
        if t == "switch_statement":
            return [self.switch(n)]
        if t == "break_statement":
            return [{"type": "break", "line": ln}]
        if t == "continue_statement":
            return [{"type": "continue", "line": ln}]
        if t == "return_statement":
            kids = named_children(n)
            val = self.pr.p(kids[0], self._is_boolean(kids[0])) if kids else None
            return [{"type": "return", "value": val, "line": ln}]
        if t == "throw_statement":
            kids = named_children(n)
            val = self.pr.p(kids[0]) if kids else ""
            return [{"type": "end", "text": self.tmpl("throw").format(value=val).strip(), "line": ln}]
        if t == "try_statement":
            return self.try_statement(n)
        if t == "labeled_statement":
            lab = n.child_by_field_name("label")
            self.warn(f"строка {ln}: метка {self.text(lab)} на схеме не показана")
            inner = [c for c in named_children(n)
                     if not (lab is not None and c.start_byte == lab.start_byte and c.type == lab.type)]
            return self.block_list(inner)
        if t == "goto_statement":
            self.warn(f"строка {ln}: goto нарисован как обычный блок")
            return [self.process(" ".join(self.text(n).rstrip(";").split()), ln)]
        if t in PREPROC_BLOCKS:
            self.warn(f"строка {ln}: директива препроцессора — взяты все ветки подряд")
            inner = [c for c in n.children if c.is_named and c.type not in ("identifier", "preproc_arg")
                     and c.type != "preproc_defined" and c.type != "binary_expression"]
            return self.block_list(inner)
        if t == "ERROR":
            self.warn(f"строка {ln}: не удалось разобрать код, взят исходный текст")
            return [self.process(" ".join(self.text(n).split()), ln)]
        if t in SKIP_TYPES:
            return []
        self.warn(f"строка {ln}: неизвестный оператор {t}, взят исходный текст")
        return [self.process(" ".join(self.text(n).rstrip(";").split()), ln)]

    def broken_statement(self, n: Node, ln: int) -> List[dict]:
        """Оператор, который tree-sitter разобрал с ошибкой: берём исходный текст."""
        start = n.start_byte
        name = ""
        if n.type == "declaration":
            decls = [d for _, d in self._declarators(n)]
            if decls:
                start = decls[0].start_byte
                name = declarator_name(decls[0], self.text)
        raw = self.ps.source[start:n.end_byte].decode("utf-8", "replace").strip()
        raw = " ".join(raw.rstrip(";").split())
        if name and "=" in raw:
            raw = name + " = " + raw.split("=", 1)[1].strip()
        self.warn(f"строка {ln}: tree-sitter не смог разобрать «{raw[:50]}», в блок взят исходный текст")
        if n.type == "return_statement":
            val = raw[len("return"):].strip()
            return [{"type": "return", "value": val or None, "line": ln}]
        if n.type == "throw_statement":
            return [{"type": "end", "text": raw, "line": ln}]
        if n.type == "declaration" and not any(d.type == "init_declarator" or d.type == "ERROR"
                                               for _, d in self._declarators(n)) and "=" not in raw:
            return []
        kind = "call" if self.pr.calls_user_function(n) else "process"
        return [self.process(raw, ln, kind)]

    def _is_boolean(self, n: Node) -> bool:
        n = self._strip_parens(n)
        if n is None:
            return False
        if n.type == "binary_expression":
            return self.pr.raw(n.child_by_field_name("operator")) in (
                "==", "!=", "<", "<=", ">", ">=", "&&", "||", "and", "or")
        return n.type == "unary_expression" and self.pr.raw(n.child_by_field_name("operator")) in ("!", "not")

    def process(self, text: str, ln: int, kind: str = "process") -> dict:
        return {"type": kind, "text": text, "line": ln}

    def tmpl(self, key: str) -> str:
        return self.rules.get("templates", {}).get(key, "{value}")

    # ------------------------------------------------------------ выражение-оператор
    def expression_statement(self, e: Node, ln: int) -> List[dict]:
        pr = self.pr
        if e.type == "comma_expression":
            parts: List[Node] = []
            stack = [e]
            while stack:
                x = stack.pop(0)
                if x.type == "comma_expression":
                    stack.insert(0, x.child_by_field_name("right"))
                    stack.insert(0, x.child_by_field_name("left"))
                else:
                    parts.append(x)
            res: List[dict] = []
            for p in parts:
                res.extend(self.expression_statement(p, ln))
            return res
        io = self.io_statement(e, ln)
        if io is not None:
            return [io]
        name = pr.call_name(e)
        if name and name.split("::")[-1] in self.rules.get("ignore_calls", []):
            return []
        if name in ("exit", "abort", "quick_exit", "_Exit"):
            return [{"type": "end", "text": self.tmpl("exit"), "line": ln}]
        text = pr.statement(e)
        kind = "call" if pr.calls_user_function(e) else "process"
        return [self.process(text, ln, kind)]

    def io_statement(self, e: Node, ln: int) -> Optional[dict]:
        pr = self.pr
        ch = pr.stream_chain(e, "<<")
        if ch is not None:
            head, items = ch
            dropped: list = []
            shown = pr.output_items(items, dropped)
            if not shown and dropped:
                stream = pr.norm_name(head)
                key = "output_format" if stream in self.rules.get("console_output", []) else "output_format_to"
                return self.process(self.tmpl(key).format(stream=stream, items=", ".join(dropped)), ln)
            return self.make_io("out", pr.norm_name(head), shown, ln)
        ch = pr.stream_chain(e, ">>")
        if ch is not None:
            head, items = ch
            return self.make_io("in", pr.norm_name(head), pr.input_items(items), ln)
        if e.type == "call_expression":
            name = pr.call_name(e)
            args = named_children(e.child_by_field_name("arguments"))
            if name == "getline" and args:
                stream = pr.norm_name(args[0])
                return self.make_io("in", stream, [pr.p(a) for a in args[1:2]], ln)
            if name == "printf" and args:
                return self.make_io("out", "cout", self.printf_items(args[0], args[1:]), ln)
            if name in ("puts", "putchar"):
                return self.make_io("out", "cout", pr.output_items(args), ln)
            if name == "fprintf" and len(args) >= 2:
                return self.make_io("out", pr.p(args[0]), self.printf_items(args[1], args[2:]), ln)
            if name == "fputs" and args:
                return self.make_io("out", pr.p(args[-1]), pr.output_items(args[:-1]), ln)
            if name == "scanf":
                return self.make_io("in", "cin", [pr.p(a).lstrip("&") for a in args[1:]], ln)
            if name == "fscanf" and args:
                return self.make_io("in", pr.p(args[0]), [pr.p(a).lstrip("&") for a in args[2:]], ln)
        return None

    _FORMAT_SPEC = re.compile(r"%[-+ #0]*(\*|\d+)?(?:\.(\*|\d+))?(?:hh|h|ll|l|L|z|j|t)?[diouxXeEfFgGaAcspn]")

    def printf_items(self, fmt: Node, args: List[Node]) -> list:
        """printf("Сумма: %d\\n", s) -> [«Сумма:», s]: аргументы встают на место %d, %f…"""
        pr = self.pr
        if fmt.type not in ("string_literal", "concatenated_string"):
            return pr.output_items([fmt] + list(args))
        q1, q2 = self.rules.get("quotes", ["«", "»"])
        max_chars = int(self.rules.get("max_string_chars", 40))
        value = (pr.string_value(fmt) or "").replace("%%", "\x00")
        items: list = []
        rest = list(args)

        def literal(t: str) -> None:
            t = " ".join(t.replace("\x00", "%").split())
            if t:
                if len(t) > max_chars:
                    t = t[: max_chars - 1].rstrip() + "…"
                items.append(q1 + t + q2)

        pos = 0
        for m in self._FORMAT_SPEC.finditer(value):
            literal(value[pos:m.start()])
            pos = m.end()
            for star in (m.group(1), m.group(2)):
                if star == "*" and rest:
                    rest.pop(0)              # ширина/точность из аргумента — на схеме не нужна
            if rest:
                items.append(pr.p(rest.pop(0)))
        literal(value[pos:])
        items += [pr.p(a) for a in rest]
        if not items and "\\n" in pr.raw(fmt):
            items.append(NEWLINE)
        return items

    def make_io(self, direction: str, stream: str, items: list, ln: int) -> dict:
        d = {"type": "io", "text": "", "line": ln,
             "_io": {"dir": direction, "stream": stream, "items": list(items)}}
        d["text"] = self.io_text(d["_io"])
        return d

    def io_text(self, io: dict) -> str:
        items = io["items"]
        real = [x for x in items if x is not NEWLINE]
        console = io["stream"] in (self.rules.get("console_output", []) if io["dir"] == "out"
                                   else self.rules.get("console_input", []))
        key = ("output" if io["dir"] == "out" else "input") + ("" if console else "_to" if io["dir"] == "out" else "_from")

        def fmt(shown: str) -> str:
            return self.tmpl(key).format(items=shown, stream=io["stream"]).strip()

        if not real:
            return fmt(self.rules.get("newline_item", "перевод строки") if any(x is NEWLINE for x in items) else "")
        text = fmt(", ".join(real))
        limit = int(self.rules.get("max_io_chars", 0) or 0)
        if limit and len(text) > limit and len(real) > 1:
            k = len(real) - 1
            while k > 1 and len(fmt(", ".join(real[:k]) + ", …")) > limit:
                k -= 1
            text = fmt(", ".join(real[:k]) + ", …")
        return text

    # ------------------------------------------------------------ объявления
    def declaration(self, n: Node, ln: int) -> List[dict]:
        pr = self.pr
        tnode = n.child_by_field_name("type")
        type_text = pr.norm_name(tnode) if tnode is not None else ""
        if tnode is not None and tnode.type in ("struct_specifier", "class_specifier", "enum_specifier", "union_specifier") \
                and tnode.child_by_field_name("body") is not None:
            return []
        res: List[dict] = []
        for _, d in self._declarators(n):
            if d.type == "init_declarator":
                name = declarator_name(d.child_by_field_name("declarator"), self.text)
                val = d.child_by_field_name("value")
                if val is None:
                    continue
                if val.type == "argument_list":          # T x(args);
                    res.extend(self.ctor(type_text, name, named_children(val), d, ln))
                    continue
                if val.type == "initializer_list" and self._type_is_stream(type_text):
                    res.extend(self.ctor(type_text, name, named_children(val), d, ln))
                    continue
                text = f"{name} = {pr.p(val)}"
                kind = "call" if pr.calls_user_function(val) else "process"
                res.append(self.process(text, ln, kind))
            elif d.type == "function_declarator":
                # «самый досадный разбор»: ifstream in(name);  -> конструктор
                name = declarator_name(d.child_by_field_name("declarator"), self.text)
                params = d.child_by_field_name("parameters")
                args = named_children(params) if params is not None else []
                if not args or not self.rules.get("constructor_declarations", True):
                    continue
                if any(a.child_by_field_name("declarator") is not None and not self._vexing_call(a) for a in args) \
                        and not self._type_is_stream(type_text):
                    continue      # это настоящий прототип: int f(int x);
                res.extend(self.ctor(type_text, name, args, d, ln))
            # объявления без инициализации не нужны на схеме
        return res

    def ctor(self, type_text: str, name: str, args: List[Node], node: Node, ln: int) -> List[dict]:
        pr = self.pr
        arg_texts = [pr.p(a) for a in args]
        base = re.sub(r"<.*>", "", type_text).split("::")[-1]
        ctors = self.rules.get("stream_constructors", {})
        if base in ctors:
            tmpl = ctors[base]
            try:
                text = tmpl.format(*arg_texts, name=name, args=", ".join(arg_texts), type=type_text)
            except (IndexError, KeyError):
                text = f"{name} = {type_text}({', '.join(arg_texts)})"
        else:
            text = f"{name} = {type_text}({', '.join(arg_texts)})"
        kind = "call" if any(pr.calls_user_function(a) or self._vexing_call(a) for a in args) else "process"
        return [self.process(text, ln, kind)]

    def _vexing_call(self, a: Node) -> bool:
        """istringstream s(f(x)); — tree-sitter видит «параметр f (x)», а это вызов функции f."""
        if a.type != "parameter_declaration":
            return False
        t = a.child_by_field_name("type")
        d = a.child_by_field_name("declarator")
        if t is None or d is None or d.type not in ("parenthesized_declarator", "abstract_function_declarator"):
            return False
        return self.pr.norm_name(t).split("::")[-1] in self.ps.declared

    # ------------------------------------------------------------ if
    def if_statement(self, n: Node) -> List[dict]:
        pr = self.pr
        ln = self.line(n)
        cond_clause = n.child_by_field_name("condition")
        value = cond_clause.child_by_field_name("value") if cond_clause is not None else None
        has_init = cond_clause is not None and cond_clause.child_by_field_name("initializer") is not None
        then_n = n.child_by_field_name("consequence")
        else_n = n.child_by_field_name("alternative")
        if else_n is not None and else_n.type == "else_clause":
            kids = named_children(else_n)
            else_n = kids[0] if kids else None
        yes = self.block(then_n)
        no = self.block(else_n) if else_n is not None else []
        is_elseif = else_n is not None and else_n.type == "if_statement"

        cond = value
        if cond is None or has_init:
            return [{"type": "if", "cond": pr.p(cond_clause, True), "yes": yes, "no": no, "line": ln}]
        cond = self._strip_parens(cond)
        if (self.rules.get("strip_negation", True) and not is_elseif and cond.type == "unary_expression"
                and pr.raw(cond.child_by_field_name("operator")) in ("!", "not")):
            cond = self._strip_parens(cond.child_by_field_name("argument"))
            yes, no = no, yes
        pre: List[dict] = []
        if not is_elseif:
            io = self.io_statement(cond, ln)
            if io is not None and io["_io"]["dir"] == "in":
                pre = [io]
                text = self.tmpl("input_ok")
                return pre + [{"type": "if", "cond": text, "yes": yes, "no": no, "line": ln}]
        return [{"type": "if", "cond": pr.p(cond, True), "yes": yes, "no": no, "line": ln}]

    def _strip_parens(self, n: Node) -> Node:
        while n is not None and n.type == "parenthesized_expression":
            kids = named_children(n)
            if len(kids) != 1:
                break
            n = kids[0]
        return n

    # ------------------------------------------------------------ циклы
    def loop(self, n: Node) -> dict:
        pr = self.pr
        ln = self.line(n)
        t = n.type
        body = self.block(n.child_by_field_name("body"))
        if t == "for_range_loop":
            var = declarator_name(n.child_by_field_name("declarator"), self.text) or \
                pr.p(n.child_by_field_name("declarator"))
            rng = pr.p(n.child_by_field_name("right"))
            return {"type": "loop", "test": "pre", "header": self.tmpl("for_each").format(var=var, range=rng),
                    "body": body, "line": ln}
        if t == "while_statement":
            c = n.child_by_field_name("condition")
            v = c.child_by_field_name("value") if c is not None else None
            if v is not None and pr.raw(v).strip() in TRUE_WORDS:
                return {"type": "loop", "test": "pre", "header": self.tmpl("infinite"), "infinite": True,
                        "body": body, "line": ln}
            return {"type": "loop", "test": "pre", "header": self.tmpl("while").format(cond=pr.p(c, True)),
                    "body": body, "line": ln}
        if t == "do_statement":
            c = n.child_by_field_name("condition")
            inner = self._strip_parens(c)
            if inner is not None and pr.raw(inner).strip() in TRUE_WORDS:
                return {"type": "loop", "test": "post", "header": self.tmpl("infinite"), "infinite": True,
                        "body": body, "line": ln}
            return {"type": "loop", "test": "post", "header": self.tmpl("do_while").format(cond=pr.p(inner, True)),
                    "body": body, "line": ln}
        # for (init; cond; update)
        init = n.child_by_field_name("initializer")
        cond = n.child_by_field_name("condition")
        upd = n.child_by_field_name("update")
        if cond is None:
            return {"type": "loop", "test": "pre", "header": self._for_generic(init, None, upd, infinite=True),
                    "infinite": True, "body": body, "line": ln}
        header = self._for_canonical(init, cond, upd)
        if header is None:
            header = self._for_generic(init, cond, upd)
        return {"type": "loop", "test": "pre", "header": header, "body": body, "line": ln}

    def _init_text(self, init: Optional[Node]) -> str:
        if init is None:
            return ""
        if init.type == "declaration":
            parts = [x["text"] for x in self.declaration(init, self.line(init))]
            return "; ".join(parts)
        return self.pr.statement(init)

    def _for_generic(self, init, cond, upd, infinite: bool = False) -> str:
        if infinite and not init and not upd:
            return self.tmpl("infinite")
        parts = []
        it = self._init_text(init)
        if it:
            parts.append(it)
        if cond is not None:
            parts.append(self.tmpl("while").format(cond=self.pr.p(cond, True)))
        elif infinite:
            parts.append(self.tmpl("infinite"))
        if upd is not None:
            parts.append(self.pr.statement(upd))
        return "; ".join(parts)

    def _for_canonical(self, init, cond, upd) -> Optional[str]:
        """for (i = a; i < b; i++) -> «i = a, …, b − 1»."""
        pr = self.pr
        if init is None or upd is None or cond is None:
            return None
        # ---- переменная и начало
        if init.type == "declaration":
            decls = [d for _, d in self._declarators(init)]
            if len(decls) != 1 or decls[0].type != "init_declarator":
                return None
            d = decls[0]
            dn = d.child_by_field_name("declarator")
            if dn is None or dn.type != "identifier":
                return None
            var = self.text(dn)
            start = d.child_by_field_name("value")
            if start is None or start.type in ("argument_list", "initializer_list"):
                return None
        elif init.type == "assignment_expression" and pr.raw(init.child_by_field_name("operator")) == "=":
            left = init.child_by_field_name("left")
            if left is None or left.type != "identifier":
                return None
            var = self.text(left)
            start = init.child_by_field_name("right")
        else:
            return None
        # ---- шаг
        step = self._step(upd, var)
        if step is None:
            return None
        # ---- условие
        if cond.type != "binary_expression":
            return None
        op = pr.raw(cond.child_by_field_name("operator"))
        l, r = cond.child_by_field_name("left"), cond.child_by_field_name("right")
        flip = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "!=": "!="}
        if l is not None and l.type == "identifier" and self.text(l) == var:
            bound = r
        elif r is not None and r.type == "identifier" and self.text(r) == var and op in flip:
            bound, op = l, flip[op]
        else:
            return None
        if bound is None or op not in flip:
            return None
        if step > 0 and op in ("<", "!="):
            end = self._shift(bound, -1)
        elif step > 0 and op == "<=":
            end = pr.p(bound)
        elif step < 0 and op in (">", "!="):
            end = self._shift(bound, +1)
        elif step < 0 and op == ">=":
            end = pr.p(bound)
        else:
            return None
        return self.tmpl("for_range").format(var=var, start=pr.p(start), end=end)

    def _step(self, upd: Node, var: str) -> Optional[int]:
        pr = self.pr
        if upd.type == "update_expression":
            a = upd.child_by_field_name("argument")
            if a is None or self.text(a) != var:
                return None
            return 1 if pr.raw(upd.child_by_field_name("operator")) == "++" else -1
        if upd.type == "assignment_expression":
            left = upd.child_by_field_name("left")
            if left is None or self.text(left) != var:
                return None
            o = pr.raw(upd.child_by_field_name("operator"))
            right = upd.child_by_field_name("right")
            if o in ("+=", "-=") and right is not None and pr.raw(right).strip() == "1":
                return 1 if o == "+=" else -1
            if o == "=" and right is not None and right.type == "binary_expression":
                rl, rr = right.child_by_field_name("left"), right.child_by_field_name("right")
                ro = pr.raw(right.child_by_field_name("operator"))
                if rl is not None and self.text(rl) == var and rr is not None and pr.raw(rr).strip() == "1" and ro in "+-":
                    return 1 if ro == "+" else -1
        return None

    def _shift(self, bound: Node, delta: int) -> str:
        pr = self.pr
        raw = pr.raw(bound).strip()
        if re.fullmatch(r"\d+", raw):
            return str(int(raw) + delta)
        text = pr.p(bound)
        plus = " + 1"
        minus_sym = self.rules.get("operators", {}).get("-", "-")
        minus = f" {minus_sym} 1"
        if delta < 0 and text.endswith(plus):
            return text[: -len(plus)]
        if delta > 0 and text.endswith(minus):
            return text[: -len(minus)]
        if bound.type in ("conditional_expression", "assignment_expression", "comma_expression") or (
                bound.type == "binary_expression" and
                self.pr.raw(bound.child_by_field_name("operator")) not in ("+", "-", "*", "/", "%")):
            text = f"({text})"
        return f"{text} {minus_sym} 1" if delta < 0 else f"{text} + 1"

    # ------------------------------------------------------------ switch
    def switch(self, n: Node) -> dict:
        pr = self.pr
        ln = self.line(n)
        value = pr.p(n.child_by_field_name("condition"), True)
        body = n.child_by_field_name("body")
        groups: List[dict] = []          # {"labels": [...], "stmts": [nodes], "default": bool}
        pending: List[str] = []
        pending_default = False
        for c in (named_children(body) if body is not None else []):
            if c.type != "case_statement":
                self.warn(f"строка {self.line(c)}: оператор в switch до первого case пропущен")
                continue
            val = c.child_by_field_name("value")
            is_default = val is None
            stmts = [x for x in c.children if x.is_named and x.type != "comment"
                     and not (val is not None and x.start_byte == val.start_byte and x.end_byte == val.end_byte)]
            if is_default:
                pending_default = True
            else:
                pending.append(pr.p(val, True))
            if not stmts:
                continue
            groups.append({"labels": pending, "default": pending_default, "nodes": stmts, "line": self.line(c)})
            pending, pending_default = [], False
        if pending or pending_default:
            groups.append({"labels": pending, "default": pending_default, "nodes": [], "line": ln})

        bodies = [self.block_list(g["nodes"]) for g in groups]
        cases, default = [], None
        for i, g in enumerate(groups):
            b = bodies[i]
            if b and b[-1]["type"] == "break":
                b = b[:-1]
            elif not (b and b[-1]["type"] in JUMPS) and i + 1 < len(groups):
                # проваливание в следующую ветку: дописываем её операторы
                if g["nodes"]:
                    self.warn(f"строка {g['line']}: ветка case без break — следующая ветка продублирована")
                j = i + 1
                extra: List[dict] = []
                while j < len(groups):
                    nb = copy.deepcopy(bodies[j])
                    if nb and nb[-1]["type"] == "break":
                        extra.extend(nb[:-1])
                        break
                    extra.extend(nb)
                    if nb and nb[-1]["type"] in JUMPS:
                        break
                    j += 1
                b = b + extra
            if g["default"] and not g["labels"]:
                default = b
            elif g["default"]:
                # case X: default: ...  — считаем веткой default
                default = b
            else:
                labels = ", ".join(g["labels"])
                cases.append({"cond": self.tmpl("switch_case").format(value=value, labels=labels), "body": b})
        res = {"type": "switch", "value": value, "cases": cases, "line": ln}
        if default is not None:
            res["default"] = default
        return res

    # ------------------------------------------------------------ try
    def try_statement(self, n: Node) -> List[dict]:
        body = self.block(n.child_by_field_name("body"))
        handlers = []
        for c in named_children(n):
            if c.type == "catch_clause":
                params = c.child_by_field_name("parameters")
                ptxt = self.text(params).strip()[1:-1].strip() if params is not None else "..."
                handlers.append((ptxt, self.block(c.child_by_field_name("body")), self.line(c)))
        if not handlers:
            return body
        self.warn(f"строка {self.line(n)}: try/catch показан как проверка «исключение?» после блока try")
        chain: List[dict] = []
        for ptxt, hb, ln in reversed(handlers):
            chain = [{"type": "if", "cond": self.tmpl("try_catch").format(param=ptxt), "yes": hb, "no": chain,
                      "line": ln}]
        return body + chain

    # ============================================================ слияние
    def merge(self, stmts: List[dict]) -> List[dict]:
        m = self.rules.get("merge", {})
        max_chars = int(m.get("max_chars", 48))
        max_items = int(m.get("max_items", 3))
        out: List[dict] = []
        for s in stmts:
            if out:
                a = out[-1]
                if (m.get("process", True) and a["type"] == "process" and s["type"] == "process"
                        and a.get("_n", 1) < max_items and len(a["text"]) + 2 + len(s["text"]) <= max_chars):
                    a["text"] = a["text"] + "; " + s["text"]
                    a["_n"] = a.get("_n", 1) + 1
                    continue
                if (m.get("io", True) and a["type"] == "io" and s["type"] == "io" and "_io" in a and "_io" in s
                        and a["_io"]["dir"] == s["_io"]["dir"] and a["_io"]["stream"] == s["_io"]["stream"]
                        and a.get("_n", 1) < max_items):
                    trial = dict(a["_io"])
                    trial["items"] = a["_io"]["items"] + s["_io"]["items"]
                    text = self.io_text(trial)
                    if len(text) <= max_chars:
                        a["_io"] = trial
                        a["text"] = text
                        a["_n"] = a.get("_n", 1) + 1
                        continue
            out.append(s)
        return out

    def _finalize(self, stmts: List[dict]) -> None:
        """Убирает служебные поля и применяет регулярные замены из rules.json."""
        regex = [(re.compile(p), r) for p, r in self.rules.get("regex", [])]

        def fix(t):
            if not isinstance(t, str):
                return t
            for p, r in regex:
                t = p.sub(r, t)
            return t

        for s in stmts:
            for k in [k for k in s if k.startswith("_")]:
                del s[k]
            for key in ("text", "cond", "header", "value"):
                if key in s:
                    s[key] = fix(s[key])
            for key in ("yes", "no", "body", "default"):
                if isinstance(s.get(key), list):
                    self._finalize(s[key])
            for c in s.get("cases", []) or []:
                c["cond"] = fix(c["cond"])
                self._finalize(c["body"])


def extract(parsed: ParsedSource, rules: dict, only: Optional[List[str]] = None) -> List[dict]:
    return Extractor(parsed, rules).extract_all(only)

"""Часть 1. Разбор кода C++ с помощью tree-sitter-cpp.

Здесь нет ничего про блок-схемы: модуль только строит синтаксическое дерево
и находит в нём функции. Всё, что нужно дальше, лежит в ParsedSource.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional, Set, Tuple

import tree_sitter_cpp
from tree_sitter import Language, Node, Parser

_LANGUAGE: Optional[Language] = None


def _language() -> Language:
    global _LANGUAGE
    if _LANGUAGE is None:
        _LANGUAGE = Language(tree_sitter_cpp.language())
    return _LANGUAGE


def make_parser() -> Parser:
    """Парсер C++ (поддерживаются py-tree-sitter 0.22 и новее)."""
    lang = _language()
    try:
        return Parser(lang)
    except TypeError:  # очень старый API
        parser = Parser()
        parser.set_language(lang)
        return parser


@dataclass
class FunctionInfo:
    """Одна функция с телом."""
    name: str               # полное имя: main, Point::norm, operator+
    params: List[str]       # имена параметров
    signature: str          # заголовок функции без тела, в одну строку
    node: Node              # узел function_definition
    body: Node              # узел compound_statement
    line: int               # номер строки (с 1)

    @property
    def short_name(self) -> str:
        return self.name.split("::")[-1]


@dataclass
class ParsedSource:
    path: str
    source: bytes
    tree: object
    functions: List[FunctionInfo] = field(default_factory=list)
    declared: Set[str] = field(default_factory=set)   # имена всех функций файла
    errors: List[Tuple[int, str]] = field(default_factory=list)

    @property
    def root(self) -> Node:
        return self.tree.root_node

    def text(self, node: Optional[Node]) -> str:
        if node is None:
            return ""
        return self.source[node.start_byte:node.end_byte].decode("utf-8", "replace")


# ---------------------------------------------------------------- обход дерева

def children_with_fields(node: Node) -> Iterator[Tuple[Optional[str], Node]]:
    """Дети узла вместе с ролью (полем): condition, body и т. п."""
    for i, child in enumerate(node.children):
        try:
            name = node.field_name_for_child(i)
        except AttributeError:  # старые версии py-tree-sitter
            name = None
        yield name, child


def named_children(node: Node) -> List[Node]:
    return [c for c in node.children if c.is_named and c.type != "comment"]


def walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def declarator_name(node: Optional[Node], text) -> str:
    """Имя из декларатора: *p -> p, &a -> a, A[M][N] -> A, f(int) -> f."""
    while node is not None:
        if node.type in ("identifier", "field_identifier", "qualified_identifier",
                         "destructor_name", "operator_name", "template_function",
                         "type_identifier"):
            return text(node)
        inner = node.child_by_field_name("declarator")
        if inner is None:
            # reference_declarator хранит имя без поля
            ids = [c for c in node.children if c.is_named]
            inner = ids[-1] if ids else None
        node = inner
    return ""


def find_function_declarator(node: Optional[Node]) -> Optional[Node]:
    while node is not None:
        if node.type == "function_declarator":
            return node
        inner = node.child_by_field_name("declarator")
        if inner is None:
            ids = [c for c in node.children if c.is_named]
            inner = ids[-1] if ids else None
        node = inner
    return None


def parameter_names(fdecl: Node, text) -> List[str]:
    params = fdecl.child_by_field_name("parameters")
    names: List[str] = []
    if params is None:
        return names
    for p in named_children(params):
        if p.type == "variadic_parameter_declaration" or text(p) == "...":
            names.append("...")
            continue
        decl = p.child_by_field_name("declarator")
        name = declarator_name(decl, text) if decl is not None else ""
        if not name:
            name = text(p.child_by_field_name("type") or p)
        names.append(" ".join(name.split()))
    return names


def _enclosing_class_names(node: Node, text) -> List[str]:
    names = []
    p = node.parent
    while p is not None:
        if p.type in ("class_specifier", "struct_specifier", "union_specifier"):
            n = p.child_by_field_name("name")
            if n is not None:
                names.append(text(n))
        p = p.parent
    return list(reversed(names))


# ---------------------------------------------------------------- главное

def parse_code(code, path: str = "<string>") -> ParsedSource:
    source = code.encode("utf-8") if isinstance(code, str) else bytes(code)
    tree = make_parser().parse(source)
    ps = ParsedSource(path=path, source=source, tree=tree)
    text = ps.text

    for node in walk(tree.root_node):
        if node.type == "ERROR" or node.is_missing:
            ps.errors.append((node.start_point[0] + 1, " ".join(text(node).split())[:60]))
        if node.type == "function_definition":
            fdecl = find_function_declarator(node.child_by_field_name("declarator"))
            body = node.child_by_field_name("body")
            if fdecl is None or body is None:
                continue
            name = " ".join(text(fdecl.child_by_field_name("declarator")).split())
            classes = _enclosing_class_names(node, text)
            if classes and "::" not in name:
                name = "::".join(classes + [name])
            header = text(node)[: body.start_byte - node.start_byte]
            ps.functions.append(FunctionInfo(
                name=name,
                params=parameter_names(fdecl, text),
                signature=" ".join(header.split()),
                node=node,
                body=body,
                line=node.start_point[0] + 1,
            ))
            ps.declared.add(name.split("::")[-1])
        elif node.type in ("declaration", "field_declaration"):
            # прототипы: int f(int a);  (но не внутри тел функций)
            if _inside_function_body(node):
                continue
            fdecl = find_function_declarator(node.child_by_field_name("declarator"))
            if fdecl is not None:
                inner = fdecl.child_by_field_name("declarator")
                ps.declared.add(text(inner).split("::")[-1].strip())
    ps.functions.sort(key=lambda f: f.node.start_byte)
    return ps


def _inside_function_body(node: Node) -> bool:
    p = node.parent
    while p is not None:
        if p.type in ("compound_statement",):
            return True
        p = p.parent
    return False


def read_source(path) -> str:
    """Текст файла: UTF-8 (с BOM или без), UTF-16 с BOM или cp1251 (Windows)."""
    return decode_source(Path(path).read_bytes())


def decode_source(data: bytes) -> str:
    """Байты файла с кодом -> текст (UTF-8, UTF-8 с BOM, UTF-16 с BOM или cp1251)."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1251")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def parse_file(path) -> ParsedSource:
    path = Path(path)
    return parse_code(read_source(path), str(path))


# ---------------------------------------------------------------- печать дерева

def dump_tree(ps: ParsedSource, node: Optional[Node] = None, named_only: bool = True,
              max_text: int = 50) -> str:
    """Дерево в виде текста с отступами (для отладки: flowgen --ast)."""
    lines: List[str] = []

    def rec(n: Node, depth: int, fld: Optional[str]) -> None:
        if named_only and not n.is_named:
            return
        info = "  " * depth + (f"{fld}: " if fld else "") + n.type + f"  [стр. {n.start_point[0] + 1}]"
        if n.child_count == 0 or n.type in ("string_literal", "char_literal"):
            t = " ".join(ps.text(n).split())
            info += "  -> " + (t if len(t) <= max_text else t[: max_text - 1] + "…")
        lines.append(info)
        if n.type in ("string_literal", "char_literal"):
            return
        for f, c in children_with_fields(n):
            rec(c, depth + 1, f)

    rec(node or ps.root, 0, None)
    return "\n".join(lines)


def tree_to_dict(ps: ParsedSource, node: Optional[Node] = None) -> dict:
    """Дерево в виде словаря (можно сохранить в JSON)."""
    def rec(n: Node, fld: Optional[str]) -> dict:
        d = {"type": n.type, "line": n.start_point[0] + 1}
        if fld:
            d["field"] = fld
        kids = [rec(c, f) for f, c in children_with_fields(n) if c.is_named]
        if kids:
            d["children"] = kids
        else:
            d["text"] = ps.text(n)
        return d
    return rec(node or ps.root, None)

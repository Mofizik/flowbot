"""flowgen — блок-схемы по ГОСТ 19.701 по коду на C++.

Части проекта:
  1. cpp_parser   — разбор кода tree-sitter-cpp, поиск функций;
  2. extractor    — отбор нужного для схемы, упрощение текста (rules.json),
                    отдельный JSON для каждой функции (формат описан в ir.py);
  3. layout       — расположение блоков, линий и соединителей;
  4. render_mpl   — рисование PNG / SVG / PDF, render_drawio — файл draw.io;
  5. style.json   — настройки рисования; схему можно нарисовать и по JSON,
                    написанному вручную.

Быстрый старт:
    from flowgen import generate
    generate("code.cpp", formats="png,svg")
"""
__version__ = "1.0.0"

from .cpp_parser import dump_tree, parse_code, parse_file          # noqa: E402  часть 1
from .extractor import extract                                     # noqa: E402  часть 2
from .layout import layout_function                                # noqa: E402  часть 3
from .pipeline import (Result, generate, generate_from_code,       # noqa: E402  всё вместе
                       generate_from_ir, layout, load_functions, render)
from .style import load_rules, load_style                          # noqa: E402  часть 5
from .validate import check_diagram                                # noqa: E402

__all__ = [
    "__version__", "parse_file", "parse_code", "dump_tree", "extract", "layout_function", "layout",
    "render", "generate", "generate_from_code", "generate_from_ir", "load_functions", "Result",
    "load_style", "load_rules", "check_diagram",
]

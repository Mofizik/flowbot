"""Командная строка: python -m flowgen code.cpp -f png,svg"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .pipeline import FORMATS, generate, load_functions, parse_formats
from .style import default_rules_path, default_style_path, load_rules

EPILOG = """примеры:
  python -m flowgen code.cpp                      PNG всех функций в папку code_схемы
  python -m flowgen code.cpp -f png,svg,drawio    сразу несколько форматов
  python -m flowgen code.cpp -F main -F inp       только функции main и inp
  python -m flowgen code.cpp -o схемы --columns 2 своя папка, два столбца
  python -m flowgen code.cpp --json-only          только JSON функций (части 1–2)
  python -m flowgen main.json -f pdf              нарисовать JSON, написанный вручную
  python -m flowgen --init-config настройки       копии style.json и rules.json для правки
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flowgen",
        description="Блок-схемы (ГОСТ 19.701) по коду на C++: отдельный рисунок для каждой функции.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="*", metavar="файл", help="файлы .cpp (.cc, .h, …) или .json")
    p.add_argument("-o", "--out", metavar="ПАПКА",
                   help="куда сохранить (по умолчанию <имя файла>_схемы рядом с файлом)")
    p.add_argument("-f", "--format", default="png", metavar="ФОРМАТЫ",
                   help=f"через запятую: {', '.join(FORMATS)} или all (по умолчанию png)")
    p.add_argument("-F", "--function", action="append", metavar="ИМЯ",
                   help="нарисовать только эту функцию (можно несколько раз или через запятую)")
    p.add_argument("--columns", metavar="N", help="число столбцов: 1, 2, … или auto (по умолчанию auto)")
    p.add_argument("--style", metavar="ФАЙЛ", help="свой style.json (шрифт, размеры, подписи)")
    p.add_argument("--rules", metavar="ФАЙЛ", help="свой rules.json (словарь упрощений текста)")
    p.add_argument("--dpi", type=int, metavar="N", help="разрешение PNG (по умолчанию из style.json)")
    p.add_argument("--title", action="store_true", help="подписать над схемой «имя(параметры)»")
    p.add_argument("--no-json", action="store_true", help="не сохранять JSON функций")
    p.add_argument("--json-only", action="store_true", help="только JSON функций, без рисунков")
    p.add_argument("--no-check", action="store_true", help="не проверять раскладку на наложения")
    p.add_argument("--list", action="store_true", help="показать найденные функции и выйти")
    p.add_argument("--ast", action="store_true", help="напечатать дерево tree-sitter (часть 1) и выйти")
    p.add_argument("--init-config", metavar="ПАПКА", help="скопировать стандартные style.json и rules.json")
    p.add_argument("-q", "--quiet", action="store_true", help="печатать только ошибки")
    p.add_argument("--version", action="version", version=f"flowgen {__version__}")
    return p


def _say(quiet: bool, *a) -> None:
    if not quiet:
        print(*a)


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):        # кириллица в консоли Windows
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = build_parser()
    a = p.parse_args(argv)

    if a.init_config:
        d = Path(a.init_config)
        d.mkdir(parents=True, exist_ok=True)
        for src in (default_style_path(), default_rules_path()):
            dst = d / src.name
            if dst.exists():
                print(f"уже есть, не трогаю: {dst}")
            else:
                shutil.copyfile(src, dst)
                print(f"создан {dst}")
        if not a.files:
            return 0
    if not a.files:
        p.print_usage()
        print("flowgen: укажите файл .cpp или .json", file=sys.stderr)
        return 2

    funcs = None
    if a.function:
        funcs = [x.strip() for item in a.function for x in item.split(",") if x.strip()]
    try:
        parse_formats(a.format)
    except ValueError as e:
        print(f"flowgen: {e}", file=sys.stderr)
        return 2

    code = 0
    for name in a.files:
        src = Path(name)
        if not src.exists():
            print(f"flowgen: файл не найден: {src}", file=sys.stderr)
            code = 1
            continue
        if a.ast:
            from .cpp_parser import dump_tree, parse_file
            print(dump_tree(parse_file(src)))
            continue
        if a.list:
            try:
                irs, _ = load_functions(src, load_rules(a.rules))
            except Exception as e:
                print(f"flowgen: {src}: {e}", file=sys.stderr)
                code = 1
                continue
            print(f"{src}:")
            for ir in irs:
                sig = ir.get("signature") or f"{ir.get('name')}({', '.join(ir.get('params', []))})"
                line = f" (строка {ir['line']})" if ir.get("line") else ""
                print(f"  {ir.get('id', ir.get('name'))}{line}: {sig}")
            continue

        out = a.out
        if out is not None and len(a.files) > 1:
            out = str(Path(out) / f"{src.stem}_схемы")
        try:
            results = generate(src, out_dir=out, formats=a.format, style=a.style, rules=a.rules,
                               functions=funcs, save_json=not a.no_json, json_only=a.json_only,
                               columns=a.columns, check=not a.no_check, dpi=a.dpi,
                               title=True if a.title else None)
        except Exception as e:
            print(f"flowgen: {src}: {e}", file=sys.stderr)
            code = 1
            continue
        folder = (results[0].json_path or (results[0].files[0] if results[0].files else None)).parent \
            if results and (results[0].json_path or results[0].files) else Path(out or ".")
        _say(a.quiet, f"{src} → {folder}{'/' if not str(folder).endswith('/') else ''}")
        if not results:
            _say(a.quiet, "  функций не найдено")
        for r in results:
            if r.error:
                print(f"  {r.function}: ОШИБКА {r.error}", file=sys.stderr)
                code = 1
                continue
            files = [f.name for f in r.files] + ([r.json_path.name] if r.json_path else [])
            if a.json_only:
                _say(a.quiet, f"  {r.function}: {', '.join(files)}")
            else:
                cols = "столбец" if r.columns == 1 else "столбца" if r.columns < 5 else "столбцов"
                _say(a.quiet, f"  {r.function}: {r.columns} {cols}, {r.width:.1f}×{r.height:.1f} дюйма — "
                              f"{', '.join(files)}")
            for w in r.warnings:
                _say(a.quiet, f"      · {w}")
            for pr in r.problems:
                print(f"      ! проверка раскладки: {pr}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())

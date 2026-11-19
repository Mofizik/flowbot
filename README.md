# flowgen — блок-схемы по коду на C++

Строит блок-схемы по ГОСТ 19.701 для каждой функции программы на C++:
PNG, SVG, PDF или файл draw.io, который можно править.

```
python3 -m flowgen examples/demo.cpp -f png,drawio
examples/demo.cpp → examples/demo_схемы/
  read_numbers: 1 столбец, 3.7×5.0 дюйма — read_numbers.png, read_numbers.drawio, read_numbers.json
  …
  main: 1 столбец, 6.6×12.8 дюйма — main.png, main.drawio, main.json
```

## Установка

Нужен Python 3.9 или новее. На Mac:

```
cd flowgen
python3 -m pip install -r requirements.txt
```

Если хочется вызывать просто `flowgen …` из любой папки, вместо этого выполните `python3 -m pip install -e .`.

## Как пользоваться

```
python3 -m flowgen code.cpp                     PNG всех функций в папку code_схемы
python3 -m flowgen code.cpp -f png,svg,pdf      несколько форматов сразу (all — все рисунки)
python3 -m flowgen code.cpp -F main -F inp      только функции main и inp
python3 -m flowgen code.cpp -o схемы            своя папка для результатов
python3 -m flowgen code.cpp --columns 2         принудительно два столбца
python3 -m flowgen code.cpp --list              какие функции найдены
python3 -m flowgen code.cpp --ast               дерево tree-sitter (часть 1)
python3 -m flowgen code.cpp --json-only         только JSON функций (части 1–2)
python3 -m flowgen main.json -f pdf             нарисовать JSON (свой или исправленный)
python3 -m flowgen --init-config настройки      копии style.json и rules.json для правки
python3 -m flowgen code.cpp --style настройки/style.json --rules настройки/rules.json
```

Остальные ключи: `--dpi N` (разрешение PNG, по умолчанию 250), `--title` (подпись «имя(параметры)» над схемой),
`--no-json` (не сохранять JSON), `--no-check` (не проверять раскладку), `-q` (тихо).

Из Python:

```python
from flowgen import generate, generate_from_code

results = generate("code.cpp", formats="png,svg", functions=["main"])
for r in results:
    print(r.function, r.files, r.warnings)

generate_from_code("int sq(int x) { return x * x; }", "out", formats=["pdf"])
```

## Части проекта

| Часть | Файлы | Что делает |
|---|---|---|
| 1. Разбор кода | `cpp_parser.py` | tree-sitter-cpp строит дерево; находятся функции (и методы классов), их параметры и прототипы, места с синтаксическими ошибками |
| 2. Отбор и JSON | `extractor.py`, `exprs.py`, `data/rules.json`, `ir.py` | из дерева берётся только то, что нужно схеме; текст упрощается по словарю `rules.json`; **каждая функция — отдельный JSON** и отдельный рисунок |
| 3. Расположение | `layout.py`, `geometry.py`, `textmetrics.py` | размеры блоков по тексту, ветки, циклы, переходы, разбиение на столбцы |
| 4. Рисование | `render_mpl.py` (PNG, SVG, PDF), `render_drawio.py` (.drawio) | готовые координаты → файл |
| 5. Свои настройки | `data/style.json`, свой JSON схемы | шрифт, размеры, подписи; схему можно нарисовать по JSON, написанному или исправленному вручную |

Вместе части собирает `pipeline.py`, командную строку — `cli.py`.
`validate.py` проверяет каждую готовую схему: блоки и подписи не налезают друг на друга,
линии не пересекаются и не проходят сквозь блоки, в каждом слиянии трёх линий стоит точка,
текст помещается в блок. Если что-то нашлось, это печатается со знаком «!».

## Что рисуется

* начало и конец — терминаторы «Начало»/«Конец» у `main`, «Вход»/«Выход» у других функций;
  `return x` — терминатор «Выход: x» на месте; `exit()` — «Конец»;
* действия — прямоугольник; несколько коротких подряд объединяются: `a = 0; b = 1`;
* ввод/вывод (`cin`, `cout`, файлы, `getline`, `printf`…) — параллелограмм: «Ввод x, y», «Вывод в out: «Сумма:», s»;
* вызов своей функции — блок «предопределённый процесс» (с двумя полосами);
* `if` — ромб «да/нет», одна ветка вправо, другая вниз; цепочка `else if` — ромбы друг под другом;
  отрицание снимается: `if (!f.is_open())` → «f открыт» с переставленными ветками;
* `switch` — цепочка ромбов «x = 1», «x = 2, 3»;
* циклы — границы цикла «Ц1: i = 0, …, n − 1» … «Ц1»; у `do … while` условие в нижней границе;
* `break` / `continue` — линия к концу цикла справа, а если она что-то пересекла бы — пара
  соединителей-кружков с одинаковой цифрой;
* длинная схема делится на столбцы (соединители А, Б, …); число столбцов выбирается так,
  чтобы на странице 6.5×9.2 дюйма схема была как можно крупнее.

## Формат JSON (выход части 2)

```json
{
  "name": "read_numbers",
  "params": ["in", "a"],
  "is_main": false,
  "body": [
    {"type": "process", "text": "n = 0"},
    {"type": "loop", "header": "пока n < N", "body": [
      {"type": "io", "text": "Ввод из in: x"},
      {"type": "if", "cond": "x < 0", "yes": [{"type": "break"}], "no": []},
      {"type": "process", "text": "a[n] = x; n = n + 1"}
    ]},
    {"type": "return", "value": "n"}
  ]
}
```

Типы операторов: `process`, `io`, `call`, `if` (`cond`, `yes`, `no`), `loop` (`header`, `body`,
`test`: `pre`/`post`, `infinite`), `switch` (`cases`: [{`cond`, `body`}], `default`), `break`, `continue`,
`return` (`value`), `end` (`text`). Необязательные поля функции: `title`, `start`, `end` (свой текст
терминаторов), `columns`. Полное описание — в начале `flowgen/ir.py`.

Удобный порядок для отчёта: `--json-only`, поправить тексты в JSON, затем `python3 -m flowgen функция.json`.

## Настройки

Свои `style.json` и `rules.json` могут содержать только изменённые ключи — остальное берётся из стандартных
(`flowgen/data/`). `--init-config ПАПКА` кладёт полные копии.

### style.json — как рисовать

| Ключ | Что задаёт |
|---|---|
| `font.family`, `size`, `label_size`, `title_size` | шрифт (первый найденный из списка) и кегли в пунктах: текст блоков, «да/нет», заголовок |
| `line_width`, `line_spacing` | толщина линий (пт) и межстрочный интервал |
| `block.width`, `min_height`, `max_width`, `pad_x`, `pad_y` | размеры блоков в дюймах: обычная ширина, наименьшая высота, предел расширения, поля |
| `cond.min_height`, `max_width`, `margin` | ромбы: подбираются по тексту в этих пределах |
| `io_skew`, `call_inset`, `loop_cut` | скос параллелограмма, полосы вызова, срез углов границы цикла |
| `spacing.vertical`, `after_cond`, `arrow_min`, `lane`, `stub` | расстояния между блоками, после ромба, до правой ветки, между линиями переходов |
| `connector_radius`, `dot_radius` | кружок-соединитель и точка слияния |
| `arrow.enabled`, `only_when_needed`, `length`, `width` | стрелки; `only_when_needed: true` — только где этого требует ГОСТ (линия вверх, влево или с изломом) |
| `page.width`, `height`, `columns.mode`, `max`, `gap` | страница для подбора столбцов; `mode`: `"auto"` или число |
| `layout.heavy_branch` | ветка тяжелее стольких блоков (или с циклом) идёт вниз, а не вправо |
| `labels.yes`, `labels.no` | подписи выходов ромба |
| `terminals.*`, `loop.*`, `title.*` | тексты терминаторов, границ цикла (`{n}` — номер, `{header}` — условие), заголовка |
| `connector_letters` | буквы соединителей между столбцами |
| `output.dpi`, `svg_text_as_paths`, `background`, `drawio_px_per_inch` | параметры файлов |

### rules.json — словарь упрощений текста

| Ключ | Что задаёт |
|---|---|
| `operators`, `condition_operators` | замены операций: `!=` → `≠`, `&&` → `и`, …; в условиях дополнительно `==` → `=` |
| `statement_rewrites` | `i++` → `i = i + 1`, `s += x` → `s = s + x` и т. п. |
| `console_output`, `console_input`, `stream_types` | что считать выводом на экран, вводом с клавиатуры и файловыми потоками |
| `drop_manipulators`, `drop_manipulator_calls`, `newline_manipulators` | манипуляторы, которые не показываются (`setw`, `fixed`…) и `endl` |
| `quotes`, `max_string_chars`, `max_io_chars`, `max_init_items` | кавычки строк и пределы длины: строка, весь блок ввода/вывода, элементы `{…}` |
| `templates` | тексты блоков: «Вывод {items}», «Ввод из {stream}: {items}», заголовки циклов, `case` … |
| `stream_methods`, `stream_constructors` | `f.open(name)` → «Открыть name (f)», `f.eof()` → «конец файла f», `ifstream f(name)` |
| `ignore_calls` | вызовы, которые не рисуются (по умолчанию `setlocale`, `SetConsoleOutputCP`, `SetConsoleCP`) |
| `merge` | объединение подряд идущих действий и вывода: вкл./выкл., не длиннее `max_chars`, не больше `max_items` |
| `regex` | последние замены в готовых текстах: список пар `["шаблон", "замена"]` |

## Ограничения

* tree-sitter не знает типов, поэтому редкие места C++ разбираются по исходному тексту (например,
  `while (n < N && in >> x)` он видит как шаблон) — такие места flowgen печатает как в коде и пишет предупреждение;
* `goto` рисуется обычным блоком «goto метка», сами метки не показываются, у `#ifdef` внутри функции
  берутся все ветки подряд — обо всём этом выводится предупреждение;
* код после `return`/`break`, до которого выполнение не доходит, не рисуется;
* упрощения механические: для отчёта иногда лучше поправить пару текстов в JSON и перерисовать;
* очень длинная функция получается мелкой — попробуйте `--columns`, а лучше разбейте её на функции.

## Тесты

```
python3 -m pip install pytest
python3 -m pytest
```

Около 200 тестов: разбор, упрощения, раскладка (в том числе 160 случайных программ — каждая схема
проходит проверку `validate.py`), все форматы файлов и командная строка.

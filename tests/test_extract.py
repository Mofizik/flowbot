"""Части 1 и 2: разбор кода и отбор/упрощение текста блоков."""
from flowgen import extract, load_rules, parse_code


def body_of(code, name=None, rules=None):
    irs = extract(parse_code(code), rules or load_rules())
    if name is None:
        assert len(irs) == 1
        return irs[0]["body"]
    return next(ir for ir in irs if ir["name"] == name)["body"]


def test_functions_and_params():
    ps = parse_code("""
        int f(int a, double& b);
        struct S { void m(int k) { k++; } };
        int f(int a, double& b) { return a; }
        void S2::g() {}
        int main() { return 0; }
    """)
    names = [fi.name for fi in ps.functions]
    assert names == ["S::m", "f", "S2::g", "main"]
    assert ps.functions[1].params == ["a", "b"]
    assert "f" in ps.declared


def test_syntax_error_is_reported():
    ps = parse_code("int main() { int x = ; return 0; }")
    assert ps.errors


def test_operators_and_conditions():
    b = body_of("""void f() {
        if (a != b && c <= d || !e) x = 1;
        while (i >= 0) i--;
    }""")
    assert b[0]["type"] == "if"
    assert b[0]["cond"] == "a ≠ b и c ≤ d или не e"
    assert b[1] == {"type": "loop", "header": "пока i ≥ 0", "test": "pre",
                    "body": [{"type": "process", "text": "i = i − 1", "line": 3}], "line": 3}


def test_equality_in_condition_only():
    b = body_of("void f() { if (a == b) ok = (x == y); }")
    assert b[0]["cond"] == "a = b"
    assert b[0]["yes"][0]["text"] == "ok = (x == y)"


def test_for_loops():
    b = body_of("""void f(int n) {
        for (int i = 0; i < n; i++) s += i;
        for (int j = n - 1; j >= 1; --j) s -= j;
        for (int k = 1; k <= n; k += 2) s *= k;
        for (auto x : v) s += x;
    }""")
    assert [s["header"] for s in b] == ["i = 0, …, n − 1", "j = n − 1, …, 1", "k = 1; пока k ≤ n; k = k + 2",
                                         "для каждого x из v"]
    assert b[0]["body"][0]["text"] == "s = s + i"


def test_infinite_and_post_loops():
    b = body_of("void f() { for (;;) { if (a) break; } do { x++; } while (x < 5); }")
    assert b[0]["infinite"] is True and b[0]["header"] == "бесконечный цикл"
    assert b[1]["test"] == "post" and b[1]["header"] == "пока x < 5"


def test_stream_io():
    b = body_of("""void f(ifstream& in, ofstream& out) {
        cout << "Сумма: " << s << endl;
        out << setw(5) << a[i];
        cin >> x >> y;
        getline(in, line);
    }""")
    assert b[0]["type"] == "io" and b[0]["text"] == "Вывод «Сумма:», s"
    assert b[1]["text"] == "Вывод в out: a[i]"
    assert b[2]["text"] == "Ввод x, y"
    assert b[3]["text"] == "Ввод из in: line"


def test_negation_is_stripped_and_branches_swapped():
    b = body_of("""void f() {
        ifstream in("a.txt");
        if (!in.is_open()) { cout << "нет"; return; }
    }""")
    assert b[0]["text"] == 'Открыть "a.txt" (in)'
    assert b[1]["cond"] == "in открыт"
    assert b[1]["yes"] == [] and b[1]["no"][0]["type"] == "io"


def test_input_in_condition_becomes_io_block():
    b = body_of("void f() { if (!(cin >> n)) return; }")
    assert b[0] == {"type": "io", "text": "Ввод n", "line": 1}
    assert b[1]["cond"] == "Ввод успешен"


def test_user_calls_and_vexing_parse():
    b = body_of("""
        string clean(const string& s);
        int count(int x);
        void f(string line) {
            istringstream stream(clean(line));
            n = count(5);
            int g(int x);
        }
        string clean(const string& s) { return s; }
        int count(int x) { return x; }
    """, "f")
    assert b[0] == {"type": "call", "text": "stream ← clean(line)", "line": 5}
    assert b[1]["type"] == "call" and b[1]["text"] == "n = count(5)"
    assert len(b) == 2           # локальный прототип int g(int x) не рисуется


def test_false_template_is_printed_from_source():
    b = body_of("void f() { while (n < N && in >> x) n++; }")
    assert b[0]["header"] == "пока n < N и in >> x"


def test_switch_with_fallthrough_labels():
    b = body_of("""void f() {
        switch (c) {
        case 1: a(); break;
        case 2: case 3: b(); break;
        default: d();
        }
    }""")
    sw = b[0]
    assert sw["type"] == "switch"
    assert [c["cond"] for c in sw["cases"]] == ["c = 1", "c = 2, 3"]
    assert sw["default"][0]["text"] == "d()"


def test_merge_and_limits():
    rules = load_rules()
    b = body_of('void f() { a = 1; b = 2; c = 3; d = 4; cout << "x"; cout << "y"; }', rules=rules)
    assert b[0]["text"] == "a = 1; b = 2; c = 3"
    assert b[1]["text"] == "d = 4"
    assert b[2]["text"] == "Вывод «x», «y»"
    long_out = " << ".join(f'"строка номер {k}"' for k in range(10))
    b = body_of(f"void f() {{ cout << {long_out}; }}")
    assert len(b[0]["text"]) <= rules["max_io_chars"] and b[0]["text"].endswith("…")


def test_ignore_calls_and_custom_rules():
    b = body_of('int main() { setlocale(LC_ALL, ""); x = 1; return 0; }')
    assert b == [{"type": "process", "text": "x = 1", "line": 1}, {"type": "return", "value": "0", "line": 1}]
    rules = load_rules({"ignore_calls": [], "operators": {"&&": "AND"}})
    b = body_of('int main() { setlocale(LC_ALL, ""); if (a && b) x = 1; }', rules=rules)
    assert b[0]["text"] == 'setlocale(LC_ALL, "")'
    assert b[1]["cond"] == "a AND b"


def test_unreachable_code_is_dropped_with_warning():
    irs = extract(parse_code("void f() { while (1) { break; x = 2; } }"), load_rules())
    loop = irs[0]["body"][0]
    assert loop["infinite"] and loop["body"] == [{"type": "break", "line": 1}]
    assert irs[0].get("warnings")


def test_printf_arguments_replace_format_specifiers():
    b = body_of(r"""void f(FILE* fp) {
        printf("Сумма: %d\n", s);
        x = 1;
        printf("%*d|%5.1f%%\n", w, k, p);
        y = 2;
        fprintf(fp, "x = %d\n", x);
        scanf("%d %lf", &n, &v);
    }""")
    assert [st["text"] for st in b] == ["Вывод «Сумма:», s", "x = 1", "Вывод k, «|», p, «%»", "y = 2",
                                        "Вывод в fp: «x =», x", "Ввод n, v"]


def test_windows_encodings(tmp_path):
    from flowgen import parse_file
    code = 'int main() {\n    cout << "Привет" << endl;\n    return 0;\n}\n'
    for enc, data in (("cp1251", code.encode("cp1251")), ("bom", b"\xef\xbb\xbf" + code.encode("utf-8")),
                      ("utf16", code.encode("utf-16"))):
        f = tmp_path / f"{enc}.cpp"
        f.write_bytes(data)
        ps = parse_file(f)
        assert not ps.errors
        assert extract(ps, load_rules())[0]["body"][0]["text"] == "Вывод «Привет»"

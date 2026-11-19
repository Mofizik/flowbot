"""Генератор случайных программ во внутреннем формате (для проверки раскладки)."""
import random

WORDS = ["a", "b", "x", "i", "count", "sum", "result", "matrix[i][j]", "value", "tmp_string",
         "n", "k", "max_area", "line", "file_number", "min_row_len", "current_row"]


def text(r: random.Random, kind: str) -> str:
    n = r.choice([1, 1, 1, 2, 3, 6])
    parts = []
    for _ in range(n):
        w = r.choice(WORDS)
        if kind == "io":
            parts.append(r.choice([w, "«Сообщение для пользователя»", "«Очень длинное сообщение об ошибке ввода»"]))
        else:
            parts.append(f"{w} = {r.choice(WORDS)} + {r.randint(0, 99)}")
    if kind == "io":
        return ("Вывод " if r.random() < 0.6 else "Ввод из f: ") + ", ".join(parts)
    if kind == "call":
        return f"{r.choice(['process', 'read_matrix', 'out1', 'get_max_rectangle'])}({', '.join(r.sample(WORDS, r.randint(1, 4)))})"
    return "; ".join(parts)


def cond(r: random.Random) -> str:
    n = r.choice([1, 1, 2, 3, 5])
    return " или ".join(f"{r.choice(WORDS)} > {r.randint(0, 9)}" for _ in range(n))


def block(r: random.Random, depth: int, in_loop: bool, in_switch: bool, size: int = None) -> list:
    out = []
    n = size if size is not None else r.randint(0, 4)
    for i in range(n):
        p = r.random()
        if depth > 0 and p < 0.22:
            out.append({"type": "if", "cond": cond(r),
                        "yes": block(r, depth - 1, in_loop, in_switch),
                        "no": block(r, depth - 1, in_loop, in_switch) if r.random() < 0.6 else []})
        elif depth > 0 and p < 0.27:
            levels = r.randint(2, 4)
            node = {"type": "if", "cond": cond(r), "yes": block(r, depth - 1, in_loop, in_switch), "no": []}
            cur = node
            for _ in range(levels - 1):
                nxt = {"type": "if", "cond": cond(r), "yes": block(r, depth - 1, in_loop, in_switch), "no": []}
                cur["no"] = [nxt]
                cur = nxt
            if r.random() < 0.5:
                cur["no"] = block(r, depth - 1, in_loop, in_switch, r.randint(1, 2))
            out.append(node)
        elif depth > 0 and p < 0.42:
            test = r.choice(["pre", "pre", "post"])
            lp = {"type": "loop", "header": r.choice(["i = 0, …, n − 1", "пока " + cond(r), ""]),
                  "test": test, "body": block(r, depth - 1, True, False, r.randint(1, 4))}
            if r.random() < 0.15:
                lp["infinite"] = True
                lp["header"] = "бесконечный цикл"
                lp["test"] = "pre"
            out.append(lp)
        elif depth > 0 and p < 0.47:
            cases = [{"cond": f"state = {k}", "body": block(r, depth - 1, in_loop, True)} for k in range(r.randint(1, 4))]
            out.append({"type": "switch", "value": "state", "cases": cases,
                        "default": block(r, depth - 1, in_loop, True) if r.random() < 0.5 else None})
        elif (in_loop or in_switch) and p < 0.58 and r.random() < 0.6:
            kinds = []
            if in_loop or in_switch:
                kinds.append("break")
            if in_loop:
                kinds.append("continue")
            out.append({"type": r.choice(kinds)})
            if r.random() < 0.7:
                break
        elif p < 0.62 and r.random() < 0.4:
            out.append({"type": "return", "value": r.choice(["", "false", "a", "count + 1"])})
            if r.random() < 0.8:
                break
        else:
            kind = r.choice(["process", "process", "io", "call"])
            out.append({"type": kind, "text": text(r, kind)})
    return out


def program(seed: int) -> dict:
    r = random.Random(seed)
    return {"name": f"f{seed}", "params": ["a", "b"], "is_main": r.random() < 0.3,
            "body": block(r, r.randint(1, 4), False, False, r.randint(1, 7))}

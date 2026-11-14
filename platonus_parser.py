"""Разбор таблицы журнала Platonus, скопированной со страницы."""
import re

STREAM_RE = re.compile(r"-(L|P|LAB|SRSP)$", re.IGNORECASE)
NUMBER_RE = re.compile(r"-?\d+(\.\d+)?")
LETTER_RE = re.compile(r"[A-F][+-]?")

TYPE_NAMES = {
    "L": "Лекция",
    "P": "Практика",
    "LAB": "Лабораторная",
    "SRSP": "СРСП",
}

NOT_PROVIDED = {"н.п.", "н.п", "нп"}

# «НЯ» (не явка). В журнале выглядит как «н». По регламенту (п. 6) равно оценке 0.
ABSENT = {"н", "н.", "н.я", "н.я.", "ня"}


def is_stream(cell):
    return bool(STREAM_RE.search(cell))


def to_number(text):
    text = text.replace(",", ".")
    if NUMBER_RE.fullmatch(text):
        return float(text)
    return None


def tidy(number):
    if number is not None and float(number).is_integer():
        return int(number)
    return number


def smart_value(text):
    if text == "":
        return None
    number = to_number(text)
    if number is not None:
        return tidy(number)
    return text


def analyze_weeks(cells):
    """Разбирает ячейки недель.

    Возвращает словарь: оценки (НЯ считается нулём), что показать на экране,
    сколько 'н.п.', пустых, НЯ и непонятных значений.
    """
    info = {"grades": [], "shown": [], "np": 0, "empty": 0, "absent": 0, "unknown": []}
    for cell in cells:
        low = cell.lower()
        if cell == "":
            info["empty"] += 1
        elif low in NOT_PROVIDED:
            info["np"] += 1
        elif low in ABSENT:
            info["grades"].append(0.0)
            info["shown"].append("н")
            info["absent"] += 1
        else:
            number = to_number(cell)
            if number is None:
                info["unknown"].append(cell)
            else:
                info["grades"].append(number)
                info["shown"].append(tidy(number))
    return info


def check_tk(label, value, grades, empty_count, warnings):
    slots = len(grades) + empty_count
    if slots == 0:
        return
    if value is None:
        warnings.append(f"{label}: не удалось прочитать значение")
        return
    expected = sum(grades) / slots
    if abs(expected - value) > 0.02:
        warnings.append(
            f"{label}: в Platonus {value}, а по оценкам получается "
            f"{round(expected, 2)}. Проверьте, что строка скопирована целиком."
        )


def parse_component(stream, teacher, weeks1, tk1_raw, weeks2, tk2_raw):
    w1 = analyze_weeks(weeks1)
    w2 = analyze_weeks(weeks2)
    grades = w1["grades"] + w2["grades"]

    type_code = stream.rsplit("-", 1)[-1].upper()
    type_name = TYPE_NAMES.get(type_code, type_code)
    label = f"{type_name} ({stream})"

    warnings = []
    for bad in w1["unknown"] + w2["unknown"]:
        warnings.append(f"{label}: непонятное значение «{bad}»")

    tk1 = to_number(tk1_raw)
    tk2 = to_number(tk2_raw)
    check_tk(f"{label}, ТК1", tk1, w1["grades"], w1["empty"], warnings)
    check_tk(f"{label}, ТК2", tk2, w2["grades"], w2["empty"], warnings)

    component = {
        "type": type_code,
        "type_name": type_name,
        "stream": stream,
        "teacher": teacher,
        "grades": w1["shown"] + w2["shown"],
        "np_count": w1["np"] + w2["np"],
        "absent_count": w1["absent"] + w2["absent"],
        "zero_count": sum(1 for g in grades if g == 0),  # включает НЯ
        "w1": list(weeks1),  # ячейки недель 1-9 как в журнале
        "w2": list(weeks2),  # ячейки недель 10-15
        "tk1": tidy(tk1),
        "tk2": tidy(tk2),
    }
    return component, warnings


def new_subject(name):
    return {
        "name": name,
        "components": [],
        "tk1_total": None,
        "tk2_total": None,
        "rk1": None,
        "r1": None,
        "rk2": None,
        "r2": None,
        "admission": None,
        "exam": None,
        "final_percent": None,
        "letter": None,
        "warnings": [],
    }


# Раскладки столбцов. Семестр: 9 + 6 недель. Триместр: 5 + 5 недель.
# first = индексы в первой строке предмета, next = в остальных строках.
LAYOUTS = {
    "semester": {
        "title": "Семестр (15 недель)",
        "first": dict(w1=(0, 9), tk1=9, tk1t=10, rk1=11, r1=12, w2=(13, 19), tk2=19,
                      tk2t=20, rk2=21, r2=22, rd=26, exam=27, pct=28, letter=29, size=30),
        "next": dict(w1=(0, 9), tk1=9, w2=(10, 16), tk2=16, size=17),
    },
    # ДОПУЩЕНИЕ: раскладка триместра взята из листа «Пример на триместр» Excel,
    # реальный вид копии из Platonus нужно подтвердить на примере.
    "trimester": {
        "title": "Триместр (10 недель)",
        "first": dict(w1=(0, 5), tk1=5, tk1t=6, rk1=7, r1=8, w2=(9, 14), tk2=14,
                      tk2t=15, rk2=16, r2=17, rd=21, exam=22, pct=23, letter=24, size=25),
        "next": dict(w1=(0, 5), tk1=5, w2=(6, 11), tk2=11, size=12),
    },
}


def parse_platonus(raw_text, mode="auto"):
    """mode: 'semester', 'trimester' или 'auto' (берём раскладку с меньшим числом замечаний)."""
    if mode in LAYOUTS:
        return parse_layout(raw_text, mode)
    best = None
    for name in ("semester", "trimester"):
        res = parse_layout(raw_text, name)
        bad = len(res["warnings"]) + sum(len(x["warnings"]) for x in res["subjects"])
        if best is None or bad < best[0]:
            best = (bad, res)
    return best[1]


def parse_layout(raw_text, mode):
    lay = LAYOUTS[mode]
    subjects = []
    warnings = []
    current = None

    for line in raw_text.splitlines():
        cells = [c.strip() for c in line.split("\t")]
        if len(cells) < 3:
            continue

        if is_stream(cells[0]):
            if current is None:
                msg = "Найдены строки без названия дисциплины. Копируйте таблицу с самого начала."
                if msg not in warnings:
                    warnings.append(msg)
                continue
            first_row = False
            stream, teacher, rest = cells[0], cells[1], cells[2:]
        elif is_stream(cells[1]):
            current = new_subject(cells[0])
            subjects.append(current)
            first_row = True
            stream, teacher, rest = cells[1], cells[2], cells[3:]
        else:
            continue

        if first_row:
            L = lay["first"]
            p = rest + [""] * max(0, L["size"] - len(rest))
            weeks1, tk1_raw = p[L["w1"][0]:L["w1"][1]], p[L["tk1"]]
            weeks2, tk2_raw = p[L["w2"][0]:L["w2"][1]], p[L["tk2"]]

            current["tk1_total"] = smart_value(p[L["tk1t"]])
            current["rk1"] = smart_value(p[L["rk1"]])
            current["r1"] = smart_value(p[L["r1"]])
            current["tk2_total"] = smart_value(p[L["tk2t"]])
            current["rk2"] = smart_value(p[L["rk2"]])
            current["r2"] = smart_value(p[L["r2"]])
            current["admission"] = smart_value(p[L["rd"]])
            current["exam"] = smart_value(p[L["exam"]])
            current["final_percent"] = smart_value(p[L["pct"]])
            current["letter"] = p[L["letter"]] or None

            if p[L["letter"]] and not LETTER_RE.fullmatch(p[L["letter"]]):
                current["warnings"].append(
                    f"Буквенная оценка «{p[L['letter']]}» не распознана"
                )
        else:
            L = lay["next"]
            p = rest + [""] * max(0, L["size"] - len(rest))
            weeks1, tk1_raw = p[L["w1"][0]:L["w1"][1]], p[L["tk1"]]
            weeks2, tk2_raw = p[L["w2"][0]:L["w2"][1]], p[L["tk2"]]

        component, comp_warnings = parse_component(
            stream, teacher, weeks1, tk1_raw, weeks2, tk2_raw
        )
        current["components"].append(component)
        current["warnings"].extend(comp_warnings)

    ok = not warnings and all(not s["warnings"] for s in subjects)
    return {"subjects": subjects, "warnings": warnings, "ok": ok,
            "mode": mode, "mode_title": lay["title"]}

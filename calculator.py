"""Расчёты по Регламенту рубежных контролей (АУЭС им. Г. Даукеева, 2026).

Все числа и формулы ниже взяты из регламента. Места, где регламент молчит
и мы приняли допущение, помечены словом «ДОПУЩЕНИЕ».
"""
import math

# --- Таблица 1 регламента: веса видов занятий ------------------------------
# Ключ: какие виды занятий есть у предмета. L, P, LAB, SRSP.
WEIGHTS = {
    frozenset({"L", "P", "LAB", "SRSP"}): {"L": 0.2, "P": 0.3, "LAB": 0.3, "SRSP": 0.2},  # К1
    frozenset({"L", "P", "SRSP"}):        {"L": 0.3, "P": 0.5, "SRSP": 0.2},               # К2 (нет лаб.)
    frozenset({"L", "LAB", "SRSP"}):      {"L": 0.3, "LAB": 0.5, "SRSP": 0.2},             # К3 (нет практики)
    frozenset({"P", "SRSP"}):             {"P": 0.9, "SRSP": 0.1},                         # К4
    frozenset({"L", "SRSP"}):             {"L": 0.5, "SRSP": 0.5},                         # К5
}
FULL_WEIGHTS = WEIGHTS[frozenset({"L", "P", "LAB", "SRSP"})]

# --- Шкала МОН РК № 595 ------------------------------------------------------
# (минимальный процент, буква, балл GPA)
SCALE = [
    (95, "A", 4.0), (90, "A-", 3.67), (85, "B+", 3.33), (80, "B", 3.0),
    (75, "B-", 2.67), (70, "C+", 2.33), (65, "C", 2.0), (60, "C-", 1.67),
    (55, "D+", 1.33), (50, "D", 1.0), (25, "FX", 0.5), (0, "F", 0.0),
]


def round_half_up(x):
    """Обычное школьное округление (41.5 -> 42).

    По вашим данным Platonus округляет рейтинг допуска именно так
    (Физкультура: 83 / 2 = 41.5, в Platonus 42).
    """
    return int(math.floor(x + 0.5))


def letter_for(percent):
    """Буква и балл GPA по проценту. ДОПУЩЕНИЕ: процент сначала округляется."""
    p = round_half_up(percent)
    for low, letter, gpa in SCALE:
        if p >= low:
            return letter, gpa
    return "F", 0.0


def weights_for(types):
    """Веса для набора видов занятий. Возвращает (веса, примечание)."""
    key = frozenset(types)
    if key in WEIGHTS:
        return WEIGHTS[key], None
    # ДОПУЩЕНИЕ: регламент не описывает такую комбинацию (например, одна
    # практика без СРСП, как у физкультуры). Берём веса К1 и пересчитываем
    # пропорционально, чтобы сумма была 100%. Для физкультуры это даёт
    # ТК общ = ТК практики, и это совпало с Platonus.
    raw = {t: FULL_WEIGHTS[t] for t in key if t in FULL_WEIGHTS}
    total = sum(raw.values())
    if total == 0:
        return {}, "Не удалось определить веса"
    return (
        {t: w / total for t, w in raw.items()},
        "Такой набор занятий не описан в регламенте: веса пересчитаны пропорционально.",
    )


def tk_total(subject, which):
    """ТК общ. по оценкам занятий. which = 'tk1' или 'tk2'. None, если нечего считать."""
    comps = subject["components"]
    weights, note = weights_for({c["type"] for c in comps})
    if not weights:
        return None, note
    total = 0.0
    for c in comps:
        value = c.get(which)
        total += weights.get(c["type"], 0) * (value if value is not None else 0)
    return total, note


def rating(tk, rk):
    """Р = 0,7 · ТК общ. + 0,3 · РК. Если РК не выставлен, берём 0, как делает Platonus."""
    return 0.7 * tk + 0.3 * (rk or 0)


def exam_needed(admission, target_percent):
    """Сколько баллов нужно на экзамене. ИО = 0,6 · РД + 0,4 · ИК."""
    return (target_percent - 0.6 * admission) / 0.4


# Пороги итоговой оценки, как в таблице вуза (70 = стипендия)
THRESHOLDS = [70, 75, 80, 85, 90, 95]


def exam_matrix(rd_rounded):
    """Сколько баллов нужно на экзамене для каждого порога. None = недостижимо."""
    row = []
    for target in THRESHOLDS:
        need = round(exam_needed(rd_rounded, target), 6)
        if need > 100:
            row.append({"target": target, "need": None})
        else:
            row.append({"target": target, "need": max(0, math.ceil(need))})
    return row


def final_grade(admission, exam):
    return 0.6 * admission + 0.4 * exam


def admission_check(rk1, rk2, rd):
    """Условия допуска из п. 8 регламента. Возвращает список проблем (пусто = допуск есть)."""
    problems = []
    if rk1 == 0 or rk2 == 0:
        problems.append("РК равен 0: допуска к экзамену нет, как бы ни сложился второй РК.")
    if rk1 is not None and rk2 is not None and (rk1 + rk2) / 2 < 50:
        problems.append("Средний балл РК1 и РК2 меньше 50.")
    if rd is not None and rd < 50:
        problems.append("Рейтинг допуска меньше 50.")
    return problems


def analyze_subject(subject, assumed_r2=None):
    """Считает всё по одному предмету. Ничего не меняет в исходных данных."""
    out = {"notes": [], "checks": []}

    # 1. Пересчёт ТК общ. и Р1 по регламенту, сверка с Platonus
    tk1, note = tk_total(subject, "tk1")
    if note:
        out["notes"].append(note)
    out["tk1_calc"] = tk1
    if tk1 is not None and isinstance(subject["tk1_total"], (int, float)):
        ok = abs(tk1 - subject["tk1_total"]) <= 0.02
        out["checks"].append(("ТК1 общ.", subject["tk1_total"], round(tk1, 2), ok))

    r1 = None
    if tk1 is not None and isinstance(subject["rk1"], (int, float)):
        r1 = rating(tk1, subject["rk1"])
        if isinstance(subject["r1"], (int, float)):
            out["checks"].append(("Р1", subject["r1"], round(r1, 2), abs(r1 - subject["r1"]) <= 0.02))
    out["r1_calc"] = r1

    # 2. Прогноз рейтинга допуска
    #    Р2 пока нет (РК2 не выставлен), поэтому берём предположение.
    base_r1 = r1 if r1 is not None else (subject["r1"] if isinstance(subject["r1"], (int, float)) else None)
    out["r1_used"] = base_r1
    r2 = None
    r2_is_assumed = False
    if isinstance(subject["r2"], (int, float)) and subject["r2"] > 0:
        r2 = subject["r2"]
    elif base_r1 is not None:
        r2 = assumed_r2 if assumed_r2 is not None else base_r1
        r2_is_assumed = True
    out["r2_used"] = r2
    out["r2_assumed"] = r2_is_assumed

    rd = None
    if base_r1 is not None and r2 is not None:
        rd = (base_r1 + r2) / 2
    out["rd_forecast"] = rd

    # 3. Проверка допуска (по прогнозу)
    rk2 = subject["rk2"] if isinstance(subject["rk2"], (int, float)) else None
    out["admission_problems"] = admission_check(
        subject["rk1"] if isinstance(subject["rk1"], (int, float)) else None,
        rk2, rd,
    ) if rd is not None else []

    # 4. Что нужно на экзамене для каждой оценки
    needs = []
    if rd is not None:
        rd_r = rd  # без округления, как в Excel вуза (РД 53,439)
        for low, letter, gpa in SCALE:
            if letter in ("FX", "F"):
                continue
            need = exam_needed(rd_r, low)
            if need > 100:
                status = "недостижимо"
            elif need <= 0:
                status = "уже обеспечено"
            else:
                status = f"{math.ceil(round(need, 6))}"
            needs.append({"letter": letter, "gpa": gpa, "target": low, "need": need, "status": status})
    out["exam_needs"] = needs
    out["rd_rounded"] = round(rd, 1) if rd is not None else None
    out["matrix"] = exam_matrix(rd) if rd is not None else []
    out["admission_req"] = admission_requirements(subject)
    out["automat"] = automat_status(subject)
    return out


def _num(value):
    return value if isinstance(value, (int, float)) else None


def admission_requirements(subject):
    """Допуск к экзамену (п. 8): РК1, РК2 не 0; (РК1+РК2)/2 >= 50; РД >= 50.

    Что нужно получить за вторую половину семестра, чтобы допуск был.
    ДОПУЩЕНИЕ: РД не округляется (так в Excel-примере вуза: РД 53,439).
    """
    rk1, r1 = _num(subject["rk1"]), _num(subject["r1"])
    rk2, r2 = _num(subject["rk2"]), _num(subject["r2"])
    res = {"status": "unknown", "text": "Недостаточно данных", "need_rk2": None,
           "need_r2": None, "need_tk2": None}
    if rk1 is None or r1 is None:
        return res

    # РК2 уже выставлен: проверяем по факту
    if rk2 is not None:
        rd = (r1 + (r2 or 0)) / 2
        problems = admission_check(rk1, rk2, rd)
        if problems:
            res.update(status="no", text="Допуска нет: " + " ".join(problems))
        else:
            res.update(status="yes", text="Допуск есть")
        return res

    if rk1 == 0:
        res.update(status="no", text="Допуска нет: РК1 равен 0")
        return res

    need_rk2 = max(1, 100 - rk1)           # и не ноль, и сумма РК не меньше 100
    need_r2 = max(0.0, 100 - r1)           # (Р1 + Р2) / 2 >= 50
    need_tk2 = max(0.0, (need_r2 - 0.3 * need_rk2) / 0.7)
    if need_rk2 > 100 or need_r2 > 100:
        res.update(status="no", text="Допуск уже недостижим")
        return res
    res.update(
        status="possible",
        text="Допуск возможен",
        need_rk2=math.ceil(round(need_rk2, 6)),
        need_r2=round(need_r2, 1),
        need_tk2=math.ceil(round(need_tk2, 6)),
    )
    if need_r2 <= 0:
        res["text"] = "Допуск уже обеспечен по РД, нужен только РК2"
    return res


def automat_status(subject):
    """Условия автомата (пп. 8 и 9.2). Нужны: нет 0 и НЯ, РК1 и РК2 не меньше 50.

    Посещаемость мы видим только по отметкам «н» в журнале.
    Решение всё равно за преподавателем: он вправе, но не обязан.
    """
    bad = []
    for comp in subject["components"]:
        if comp.get("zero_count"):
            bad.append(f'{comp["type_name"]}: {comp["zero_count"]}')
    if bad:
        return {"status": "no", "text": "Автомата нет: есть 0 или «н» (" + "; ".join(bad) + ")"}
    rk1 = _num(subject["rk1"])
    if rk1 is not None and rk1 < 50:
        return {"status": "no", "text": "Автомата нет: РК1 меньше 50"}
    return {"status": "possible",
            "text": "Автомат возможен: нулей и «н» нет. Нужно сохранить так до конца и набрать не меньше 50 за РК2"}

"""Чтение скриншотов журнала из мобильного приложения Platonus.

Скриншот -> Tesseract (слова с координатами) -> карточки «L / P / Lab / SRSP» ->
текст в том же формате, что и копия таблицы с компьютера. Дальше работает обычный
разбор (platonus_parser) и обычная интерактивная таблица.

Колонки недель определяем по координатам заголовка «Неделя 1 2 3 ... ТК»: так пустая
ячейка остаётся пустой, а не «съезжает» влево.
"""
import csv
import io
import os
import re
import statistics
import subprocess
import tempfile

from PIL import Image, ImageOps

from platonus_parser import layout_for

try:  # скриншоты и фото с iPhone иногда в формате HEIC
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

TARGET_WIDTH = 900   # меньше пикселей = быстрее на слабом сервере; цифры читаются так же

# Кириллические буквы, похожие на латинские (в кодах предметов Platonus латиница)
CYR2LAT = {ord(a): b for a, b in zip("АВСЕНКМОРТХавсекмортх", "ABCEHKMOPTXabcekmoptx")}

CODE_RE = re.compile(r"([A-Z]{2,8})\s*(\d{3,6}(?:-\d+)+)-(LAB|SRSP|L|P)\b", re.IGNORECASE)
TYPE_LABEL = {"L": "L", "P": "P", "LAB": "Lab", "SRSP": "SRSP"}

NP_RE = re.compile(r"^[нНhH][.,]?\s*[пПnN][.,]?$")
NOISE_WORDS = (
    "журнал", "оценки", "оценка", "главная", "сервисы", "чаты", "каналы",
    "текущие", "итоговые", "преподаватель", "неделя",
)


class OcrError(Exception):
    pass


def _run_tesseract(image):
    """Возвращает список слов: dict(text, cx, cy, h, conf, line)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "shot.png")
        image.save(path)
        env = dict(os.environ, OMP_THREAD_LIMIT="1")
        try:
            proc = subprocess.run(
                ["tesseract", path, "stdout", "--psm", "6", "-l", "rus+eng", "tsv"],
                capture_output=True, text=True, timeout=150, env=env,
            )
        except FileNotFoundError:
            raise OcrError("На сервере не установлен Tesseract.")
        except subprocess.TimeoutExpired:
            raise OcrError("Скриншот читался слишком долго. Попробуйте обрезать его.")
    words = []
    reader = csv.DictReader(io.StringIO(proc.stdout), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text:
            continue
        try:
            left, top = int(row["left"]), int(row["top"])
            width, height = int(row["width"]), int(row["height"])
        except (KeyError, ValueError):
            continue
        words.append({
            "text": text,
            "cx": left + width / 2,
            "cy": top + height / 2,
            "h": height,
            "line": (row["block_num"], row["par_num"], row["line_num"]),
        })
    return words


def _lines(words):
    groups = {}
    for w in words:
        groups.setdefault(w["line"], []).append(w)
    lines = []
    for ws in groups.values():
        ws.sort(key=lambda w: w["cx"])
        lines.append({"words": ws, "cy": statistics.mean(w["cy"] for w in ws)})
    lines.sort(key=lambda l: l["cy"])
    return lines


def _text(line):
    return " ".join(w["text"] for w in line["words"])


def _clean_number(text):
    t = text.replace(",", ".")
    if re.fullmatch(r"[0-9OoІIl]+(\.[0-9OoІIl]+)?", t) and re.search(r"[0-9]", t):
        t = t.translate({ord("O"): "0", ord("o"): "0", ord("І"): "1", ord("I"): "1", ord("l"): "1"})
    return t


def _cell(text):
    """Приводит текст ячейки к виду, как в Platonus."""
    t = text.strip()
    if not t:
        return ""
    if NP_RE.match(t.replace(" ", "")):
        return "н.п."
    if t in ("н", "Н", "н.", "н,"):
        return "н"
    t = _clean_number(t)
    if re.fullmatch(r"-?\d+(\.\d+)?", t):
        return t
    return t  # непонятное значение пусть увидит проверка разбора


def _columns(tokens):
    return [w["cx"] for w in tokens]


def _assign(values, columns):
    """Раскладывает слова строки «Оценка» по колонкам заголовка."""
    cells = [[] for _ in columns]
    if len(columns) >= 2:
        spacing = statistics.median([b - a for a, b in zip(columns, columns[1:])])
    else:
        spacing = 80
    stray = []
    for w in values:
        idx = min(range(len(columns)), key=lambda i: abs(columns[i] - w["cx"]))
        if abs(columns[idx] - w["cx"]) > spacing * 0.75:
            if w["cx"] < columns[0]:      # подпись «Оценка» слева, читается как угодно
                continue
            stray.append(w["text"])
            continue
        cells[idx].append(w)
    out = []
    for group in cells:
        group.sort(key=lambda w: w["cx"])
        out.append(_cell("".join(w["text"] for w in group)))
    return out, stray


def read_image(file_obj, cache):
    """Читает один скриншот. Возвращает список событий: ('title', текст) и ('card', словарь)."""
    try:
        image = Image.open(file_obj)
        image = ImageOps.exif_transpose(image).convert("RGB")
    except Exception:
        raise OcrError("Не удалось открыть файл как картинку.")
    scale = TARGET_WIDTH / image.width
    image = image.resize((TARGET_WIDTH, max(1, int(image.height * scale))), Image.LANCZOS)

    lines = _lines(_run_tesseract(image))
    if any(_text(l).lower().startswith("рубеж") for l in lines):
        return [("final", b) for b in parse_final_screen(lines, TARGET_WIDTH)], []
    events = []
    card = None
    state = None          # что ждём внутри карточки: 'h1', 'v1', 'h2', 'v2'
    cols = None
    pending_title = None
    warnings = []

    for line in lines:
        text = _text(line)
        norm = text.translate(CYR2LAT)
        m = CODE_RE.search(norm)

        if m:
            if pending_title:
                events.append(("title", pending_title))
                pending_title = None
            prefix, number, kind = m.group(1).upper(), m.group(2), m.group(3).upper()
            card = {
                "prefix": prefix,
                "type": TYPE_LABEL[kind],
                "stream": f"{prefix} {number}-{TYPE_LABEL[kind]}",
                "teacher": "", "w1": None, "tk1": "", "w2": None, "tk2": "",
                "n1": None, "n2": None, "warnings": [],
            }
            events.append(("card", card))
            state = "h1"
            continue

        low = text.lower()
        if card is not None and low.startswith("препод"):
            card["teacher"] = text.split(":", 1)[1].strip() if ":" in text else text.split(" ", 1)[-1]
            continue

        if card is not None and state in ("h1", "h2") and low.startswith("недел"):
            tokens = line["words"][1:]
            kind = "first" if state == "h1" else "second"
            if len(tokens) >= 5:
                cols = _columns(tokens)
                cache[kind] = cols
            else:
                cols = cache.get(kind)
            if cols is None:
                card["warnings"].append("не удалось найти колонки недель")
                state = None
                continue
            state = "v1" if state == "h1" else "v2"
            continue

        if card is not None and state in ("v1", "v2"):
            values = line["words"]
            if values and re.match(r"^(оцен|oцен|оценк)", values[0]["text"].lower()):
                values = values[1:]
            cells, stray = _assign(values, cols)
            if stray:
                card["warnings"].append("лишний текст в строке оценок: " + " ".join(stray))
            weeks, tk = cells[:-1], cells[-1]
            if state == "v1":
                card["w1"], card["tk1"], card["n1"] = weeks, tk, len(weeks)
                state = "h2"
            else:
                card["w2"], card["tk2"], card["n2"] = weeks, tk, len(weeks)
                state = None
            continue

        # Ничего из служебного: возможно, это название предмета
        if _looks_like_title(text):
            pending_title = re.sub(r"\s+\S{1,2}$", "", text).strip()

    return events, warnings


FINAL_KEYS = ("rk1", "r1", "tk1t", "rk2", "r2", "tk2t", "rd", "course", "exam", "total")


def _final_label(text):
    """Возвращает ключ строки на экране «Итоговые оценки».

    Для строк с номером (РК, рейтинг, ТК) возвращает (основа, цифра или None):
    цифра может «приклеиться» к слову и не прочитаться, тогда её определит порядок строк.
    """
    t = text.lower().strip()
    if t.startswith("рубеж"):
        base = "rk"
    elif t.startswith("рейтинг") and "допуск" in t:
        return "rd"
    elif t.startswith("рейтинг"):
        base = "r"
    elif re.match(r"^[тt][кk]", t):
        base = "tk"
    elif "курсов" in t:
        return "course"
    elif t.startswith("экзам"):
        return "exam"
    elif t.startswith("итогов"):
        return "total"
    else:
        return None
    m = re.search(r"(?<![0-9])([12lI])(?![0-9])", text)
    digit = None if not m else ("1" if m.group(1) in "lI1" else "2")
    return (base, digit)


def _final_key(label, block):
    if isinstance(label, str) or label is None:
        return label
    base, digit = label
    suffix = "t" if base == "tk" else ""
    if digit is None:
        first = (block or {}).get(base + "1" + suffix, "")
        digit = "1" if (block is None or first == "") else "2"
    return base + digit + suffix


def _final_value(words):
    text = "".join(w["text"] for w in words).strip()
    if not text:
        return ""
    low = text.lower()
    if low.startswith("недоп"):
        return "недоп."
    return _clean_number(text)


def parse_final_screen(lines, width):
    """Список блоков {name, rk1, r1, ...}: на одном экране может быть один или несколько предметов."""
    blocks, block, pending = [], None, None
    for line in lines:
        text = _text(line)
        left = [w for w in line["words"] if w["cx"] < width * 0.5]
        right = [w for w in line["words"] if w["cx"] >= width * 0.5]
        key = _final_label(" ".join(w["text"] for w in left)) if left else None
        key = _final_key(key, block)
        if key:
            if key == "rk1":
                block = {"name": pending or "", "titled": bool(pending)}
                block.update({k: "" for k in FINAL_KEYS})
                blocks.append(block)
                pending = None
            if block is not None:
                block[key] = _final_value(right)
            continue
        if _looks_like_title(text):
            pending = re.sub(r"\s+\S{1,2}$", "", text).strip()
    return blocks


def _looks_like_title(text):
    low = text.lower()
    if len(text) < 4:
        return False
    if any(word in low for word in NOISE_WORDS):
        return False
    letters = sum(ch.isalpha() for ch in text)
    digits = sum(ch.isdigit() for ch in text)
    return letters >= 4 and letters > 2 * digits


def _slots(cells):
    """Как в разборе: «н.п.» не считается, пустая ячейка и 0 считаются."""
    grades, empty = [], 0
    for c in cells:
        if c == "":
            empty += 1
        elif c == "н.п.":
            continue
        elif c == "н":
            grades.append(0.0)
        else:
            try:
                grades.append(float(c))
            except ValueError:
                pass
    return grades, empty


def _verify(card, warnings):
    """Сверяет ТК из скриншота со средним по оценкам. Расхождение значит, что цифра прочитана неверно."""
    fixed = {}
    for key, weeks, label in (("tk1", card["w1"], "ТК1"), ("tk2", card["w2"], "ТК2")):
        grades, empty = _slots(weeks or [])
        slots = len(grades) + empty
        shown = card[key]
        if slots == 0:
            fixed[key] = ""
            continue
        expected = sum(grades) / slots
        try:
            value = float(shown)
        except ValueError:
            value = None
        # В приложении ТК округлён до целого, поэтому допуск 1 балл
        if value is None and shown == "" and expected == 0:
            fixed[key] = "0"   # в приложении пусто, в разборе нужен ноль
            continue
        if value is None or abs(value - expected) > 1.01:
            warnings.append(
                f"{card['stream']}, {label}: на скриншоте {shown or 'пусто'}, "
                f"а по оценкам {round(expected, 2)}. Проверьте оценки этого предмета."
            )
        fixed[key] = f"{expected:.2f}".rstrip("0").rstrip(".")
    return fixed


def build_text(subjects):
    """Собирает текст в формате копии таблицы с компьютера (с заголовком, чтобы разбор знал деление недель).

    Возвращает (текст, режим).
    """
    first_card = next(c for s in subjects for c in s["cards"] if c["w1"] is not None)
    n1, n2 = len(first_card["w1"]), len(first_card["w2"] or [])
    n2 = n2 or max(1, 15 - n1)
    name, lay = layout_for(n1, n2)
    F, N = lay["first"], lay["next"]

    header = ["Дисциплина", "Учебный поток", "Преподаватель"]
    header += [str(i) for i in range(1, n1 + 1)] + ["ТК1", "ТК1 ОБЩ.", "РК1", "Р1"]
    header += [str(i) for i in range(n1 + 1, n1 + n2 + 1)] + ["ТК2", "ТК2 ОБЩ.", "РК2", "Р2"]
    lines = ["\t".join(header)]

    for subject in subjects:
        for i, card in enumerate(subject["cards"]):
            w1 = ((card["w1"] or []) + [""] * n1)[:n1]
            w2 = ((card["w2"] or []) + [""] * n2)[:n2]
            L = F if i == 0 else N
            p = [""] * L["size"]
            p[L["w1"][0]:L["w1"][1]] = w1
            p[L["tk1"]] = card["tk1_fixed"]
            p[L["w2"][0]:L["w2"][1]] = w2
            p[L["tk2"]] = card["tk2_fixed"]
            if i == 0:
                fin = subject.get("final")
                if fin:
                    for src, dst in (("tk1t", "tk1t"), ("rk1", "rk1"), ("r1", "r1"), ("tk2t", "tk2t"),
                                     ("rk2", "rk2"), ("r2", "r2"), ("rd", "rd"), ("exam", "exam"),
                                     ("total", "pct")):
                        p[L[dst]] = fin.get(src, "")
                row = [subject["name"], card["stream"], card["teacher"]] + p
            else:
                row = [card["stream"], card["teacher"]] + p
            lines.append("\t".join(row))
    return "\n".join(lines), name


def _norm_name(name):
    return re.sub(r"[^0-9a-zа-я]+", "", name.lower())


def _attach_finals(subjects, finals, warnings):
    """Привязывает «Итоговые оценки» к предметам: по названию, затем по порядку для безымянных."""
    import difflib
    unmatched = []
    used = set()
    for fin in finals:
        best, score = None, 0.0
        if fin["titled"]:
            for sub in subjects:
                if "final" in sub or not sub["titled"]:
                    continue
                ratio = difflib.SequenceMatcher(None, _norm_name(sub["name"]), _norm_name(fin["name"])).ratio()
                if ratio > score:
                    best, score = sub, ratio
        if best is not None and score >= 0.72:
            best["final"] = fin
        else:
            unmatched.append(fin)

    nameless = [s for s in subjects if "final" not in s and not s["titled"]]
    if unmatched and len(unmatched) == len(nameless):
        for sub, fin in zip(nameless, unmatched):
            sub["final"] = fin
            if fin["titled"]:
                sub["name"] = fin["name"]
        unmatched = []
    elif unmatched:
        for fin in unmatched:
            warnings.append(
                f"Итоговые оценки предмета «{fin['name'] or '?'}» не удалось привязать к предмету из текущих оценок. "
                "Добавьте скриншот «Текущие оценки» этого предмета."
            )
    if finals:
        for sub in subjects:
            if "final" not in sub:
                warnings.append(f"Для предмета «{sub['name']}» нет скриншота «Итоговые оценки», РК1 и РК2 не заполнены.")
    else:
        warnings.append("Нет скриншота «Итоговые оценки»: РК1 и РК2 не заполнены, рейтинг допуска не посчитать.")


def read_screenshots(files):
    """files: список файловых объектов. Возвращает dict(text, mode, warnings, subjects)."""
    cache = {}
    warnings = []
    subjects = []
    seen = {}
    current = None
    finals = []

    for f in files:
        events, _ = read_image(f, cache)
        title = None
        for kind, payload in events:
            if kind == "final":
                finals.append(payload)
                continue
            if kind == "title":
                title = payload
                continue
            card = payload
            warnings.extend(f"{card['stream']}: {w}" for w in card["warnings"])
            if card["w1"] is None:
                continue

            new_subject = False
            if title:
                new_subject = True
            elif current is None:
                new_subject = True
            elif current["prefix"] != card["prefix"]:
                new_subject = True
            elif any(c["type"] == card["type"] for c in current["cards"]):
                new_subject = True

            if card["stream"] in seen:          # тот же блок попал на два скриншота
                title = None
                continue
            seen[card["stream"]] = True

            if new_subject:
                current = {"name": title or card["prefix"], "prefix": card["prefix"], "cards": [], "titled": bool(title)}
                subjects.append(current)
            title = None
            fixed = _verify(card, warnings)
            card["tk1_fixed"], card["tk2_fixed"] = fixed["tk1"], fixed["tk2"]
            current["cards"].append(card)

    _attach_finals(subjects, finals, warnings)
    for sub in subjects:
        if not sub["titled"] and sub["name"] == sub["prefix"]:
            warnings.append(
                f"У предмета {sub['prefix']} нет названия: на скриншоте «Текущие оценки» его не видно. "
                "Добавьте скриншот «Итоговые оценки» этого предмета, название возьмётся оттуда."
            )

    if not subjects:
        raise OcrError("Не нашли в скриншотах журнал. Откройте «Журнал» → «Текущие оценки» и сделайте скриншот.")
    text, mode = build_text(subjects)
    return {"text": text, "mode": mode, "warnings": warnings, "subjects": subjects}

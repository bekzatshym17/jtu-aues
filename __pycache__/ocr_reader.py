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

TARGET_WIDTH = 1400

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
                capture_output=True, text=True, timeout=45, env=env,
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
    """Собирает текст в формате копии таблицы с компьютера. Возвращает (текст, режим)."""
    lines = []
    mode = "semester"
    for subject in subjects:
        for i, card in enumerate(subject["cards"]):
            w1, w2 = card["w1"] or [], card["w2"] or []
            if len(w1) <= 6:
                mode = "trimester"
            if mode == "semester":
                first_size, next_size = 30, 17
                p1_w1, p1_tk1, p1_w2, p1_tk2 = 0, 9, 13, 19
                p2_w1, p2_tk1, p2_w2, p2_tk2 = 0, 9, 10, 16
                n1, n2 = 9, 6
            else:
                first_size, next_size = 25, 12
                p1_w1, p1_tk1, p1_w2, p1_tk2 = 0, 5, 9, 14
                p2_w1, p2_tk1, p2_w2, p2_tk2 = 0, 5, 6, 11
                n1, n2 = 5, 5
            w1 = (w1 + [""] * n1)[:n1]
            w2 = (w2 + [""] * n2)[:n2]
            if i == 0:
                p = [""] * first_size
                p[p1_w1:p1_w1 + n1] = w1
                p[p1_tk1] = card["tk1_fixed"]
                p[p1_w2:p1_w2 + n2] = w2
                p[p1_tk2] = card["tk2_fixed"]
                row = [subject["name"], card["stream"], card["teacher"]] + p
            else:
                p = [""] * next_size
                p[p2_w1:p2_w1 + n1] = w1
                p[p2_tk1] = card["tk1_fixed"]
                p[p2_w2:p2_w2 + n2] = w2
                p[p2_tk2] = card["tk2_fixed"]
                row = [card["stream"], card["teacher"]] + p
            lines.append("\t".join(row))
    return "\n".join(lines), mode


def read_screenshots(files):
    """files: список файловых объектов. Возвращает dict(text, mode, warnings, subjects)."""
    cache = {}
    warnings = []
    subjects = []
    seen = {}
    current = None

    for f in files:
        events, _ = read_image(f, cache)
        title = None
        for kind, payload in events:
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
                current = {"name": title or card["prefix"], "prefix": card["prefix"], "cards": []}
                subjects.append(current)
                if not title:
                    warnings.append(
                        f"У предмета {card['prefix']} на скриншоте нет названия. "
                        "Прокрутите так, чтобы название было видно, или оставьте как есть."
                    )
            title = None
            fixed = _verify(card, warnings)
            card["tk1_fixed"], card["tk2_fixed"] = fixed["tk1"], fixed["tk2"]
            current["cards"].append(card)

    if not subjects:
        raise OcrError("Не нашли в скриншотах журнал. Откройте «Журнал» → «Текущие оценки» и сделайте скриншот.")
    text, mode = build_text(subjects)
    return {"text": text, "mode": mode, "warnings": warnings, "subjects": subjects}

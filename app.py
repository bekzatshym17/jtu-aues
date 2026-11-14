from flask import Flask, g, jsonify, render_template, request

from i18n import DEFAULT_LANG, LANGS, js_strings, translate

from calculator import THRESHOLDS, analyze_subject, exam_matrix
from platonus_parser import parse_platonus

app = Flask(__name__)


@app.before_request
def pick_language():
    """Язык: ?lang=kk|ru (запоминаем в cookie), иначе cookie, иначе русский."""
    lang = request.args.get("lang")
    g.set_cookie = lang in LANGS
    if lang not in LANGS:
        lang = request.cookies.get("lang")
    g.lang = lang if lang in LANGS else DEFAULT_LANG


@app.after_request
def remember_language(response):
    if getattr(g, "set_cookie", False):
        response.set_cookie("lang", g.lang, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return response


@app.context_processor
def inject_i18n():
    lang = g.get("lang", DEFAULT_LANG)
    return {"lang": lang, "t": lambda key, **kw: translate(lang, key, **kw)}


def build_payload(result):
    """Данные для интерактивной таблицы (их читает static/js/journal.js)."""
    def number_or_none(value):
        return value if isinstance(value, (int, float)) else None

    payload = []
    for s in result["subjects"]:
        payload.append({
            "name": s["name"],
            "rk1": number_or_none(s["rk1"]),
            "rk2": number_or_none(s["rk2"]),
            "exam": number_or_none(s["exam"]),
            "platonus": {
                "tk1_total": number_or_none(s["tk1_total"]),
                "r1": number_or_none(s["r1"]),
            },
            "components": [
                {k: c[k] for k in ("type", "type_name", "stream", "teacher", "w1", "w2")}
                for c in s["components"]
            ],
        })
    return payload


@app.route("/api/status", methods=["POST"])
def api_status():
    """Короткая сводка по каждому предмету для главной страницы. Ничего не сохраняем."""
    data = request.get_json(silent=True) or {}
    raw = str(data.get("raw", ""))[:200000]
    mode = data.get("mode", "auto")
    if mode not in ("auto", "semester", "trimester"):
        mode = "auto"
    result = parse_platonus(raw, mode)
    cards = []
    for subject in result["subjects"]:
        calc = analyze_subject(subject)
        need = {m["target"]: m["need"] for m in calc["matrix"]}
        cards.append({
            "name": subject["name"],
            "rd": calc["rd_rounded"],
            "forecast": bool(calc["r2_assumed"]),
            "adm": calc["admission_req"]["status"],
            "auto": calc["automat"]["status"],
            "n70": need.get(70),
            "n90": need.get(90),
            "has_need": 70 in need,
        })
    return jsonify({"cards": cards, "mode": result["mode"]})


@app.route("/")
def index():
    return render_template("index.html", js_i18n=js_strings(g.lang))


@app.route("/analyze", methods=["GET", "POST"])
def analyze():
    raw_text = ""
    result = None
    assumed_raw = ""
    mode = request.form.get("mode", "auto")
    if request.method == "POST":
        raw_text = request.form.get("raw_text", "")
        assumed_raw = request.form.get("assumed_r2", "").strip()

        assumed = None
        if assumed_raw:
            try:
                assumed = float(assumed_raw.replace(",", "."))
                assumed = max(0.0, min(100.0, assumed))
            except ValueError:
                assumed = None

        result = parse_platonus(raw_text, mode)
        for subject in result["subjects"]:
            subject["calc"] = analyze_subject(subject, assumed_r2=assumed)

    payload = build_payload(result) if result else []
    return render_template(
        "analyze.html", raw_text=raw_text, result=result,
        assumed_raw=assumed_raw, payload=payload, mode=mode,
        js_i18n=js_strings(g.lang),
    )


@app.route("/table")
def table():
    rows = [{"rd": rd, "cells": exam_matrix(rd)} for rd in range(50, 101)]
    return render_template("table.html", rows=rows, thresholds=THRESHOLDS)


@app.route("/gpa")
def gpa():
    return render_template("gpa.html")


@app.route("/help")
def help_page():
    return render_template("help.html")


if __name__ == "__main__":
    app.run(debug=True)

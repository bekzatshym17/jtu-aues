// Интерактивная таблица журнала. Формулы взяты из регламента вуза (см. calculator.py).
(function () {
  "use strict";

  var dataEl = document.getElementById("payload");
  var root = document.getElementById("journal");
  if (!dataEl || !root) return;
  var subjects = JSON.parse(dataEl.textContent);
  var i18nEl = document.getElementById("i18n");
  var T = i18nEl ? JSON.parse(i18nEl.textContent) : {};
  // Подставляет {имя} из объекта vars в строку перевода
  function tr(key, vars) {
    var s = T[key] !== undefined ? T[key] : key;
    if (vars) Object.keys(vars).forEach(function (k) { s = s.split("{" + k + "}").join(vars[k]); });
    return s;
  }

  var NP = ["н.п.", "н.п", "нп"];
  var ABS = ["н", "н.", "н.я", "н.я.", "ня"]; // НЯ = 0
  var FULL = { L: 0.2, P: 0.3, LAB: 0.3, SRSP: 0.2 };
  var TABLE = {
    "L,LAB,P,SRSP": { L: 0.2, P: 0.3, LAB: 0.3, SRSP: 0.2 },
    "L,P,SRSP": { L: 0.3, P: 0.5, SRSP: 0.2 },
    "L,LAB,SRSP": { L: 0.3, LAB: 0.5, SRSP: 0.2 },
    "P,SRSP": { P: 0.9, SRSP: 0.1 },
    "L,SRSP": { L: 0.5, SRSP: 0.5 }
  };
  var SCALE = [[95, "A"], [90, "A-"], [85, "B+"], [80, "B"], [75, "B-"], [70, "C+"],
    [65, "C"], [60, "C-"], [55, "D+"], [50, "D"], [25, "FX"], [0, "F"]];
  var THRESHOLDS = [70, 75, 80, 85, 90, 95];
  var targetRd = 70;   // желаемый рейтинг допуска
  var updaters = [];   // функции пересчёта всех предметов

  function weightsFor(types) {
    var key = types.slice().sort().join(",");
    if (TABLE[key]) return TABLE[key];
    // Набор не описан в регламенте: пропорционально весам К1
    var total = 0, w = {};
    types.forEach(function (t) { total += FULL[t] || 0; });
    types.forEach(function (t) { w[t] = total ? (FULL[t] || 0) / total : 0; });
    return w;
  }

  function toNum(v) {
    if (v === null || v === undefined) return null;
    var s = String(v).trim().replace(",", ".");
    if (!/^-?\d+(\.\d+)?$/.test(s)) return null;
    return parseFloat(s);
  }

  function kindOf(v) {
    var s = String(v == null ? "" : v).trim(), l = s.toLowerCase();
    if (s === "") return "empty";
    if (NP.indexOf(l) >= 0) return "np";
    if (ABS.indexOf(l) >= 0) return "abs";
    return toNum(s) === null ? "bad" : "num";
  }

  // ТК = сумма оценок / число ячеек, где не стоит «н.п.». Пустая и «н» считаются нулём.
  function avg(cells) {
    var sum = 0, slots = 0;
    cells.forEach(function (v) {
      var k = kindOf(v);
      if (k === "np") return;
      slots++;
      if (k === "num") sum += toNum(v);
    });
    return slots ? sum / slots : 0;
  }

  function hasZero(cells) {
    return cells.some(function (v) {
      var k = kindOf(v);
      return k === "abs" || (k === "num" && toNum(v) === 0);
    });
  }

  function fmt(x) {
    if (x === null || x === undefined || isNaN(x)) return "—";
    return String(Math.round(x * 100) / 100);
  }

  function letterFor(pct) {
    var p = Math.floor(pct + 0.5);
    for (var i = 0; i < SCALE.length; i++) if (p >= SCALE[i][0]) return SCALE[i][1];
    return "F";
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function inputCell(value, onChange) {
    var td = el("td", "edit");
    var input = el("input");
    input.type = "text";
    input.inputMode = "decimal";
    input.value = value;
    input.addEventListener("input", function () {
      input.classList.toggle("bad", input.value.trim() !== "" && kindOf(input.value) === "bad");
      onChange(input.value);
    });
    td.appendChild(input);
    return td;
  }

  function weekCell(orig, current, onChange) {
    var k = kindOf(orig);
    if (k === "np") return el("td", "gray", tr("np"));
    if (k === "abs") return el("td", "abs", "н");
    if (k === "empty") return inputCell(current, onChange);
    return el("td", "", String(orig));
  }

  function build(sub) {
    var st = {
      comps: sub.components.map(function (c) {
        return { type: c.type, w1: c.w1.slice(), w2: c.w2.slice() };
      }),
      rk1: sub.rk1 === null ? "" : String(sub.rk1),
      rk2: sub.rk2 === null ? "" : String(sub.rk2),
      exam: sub.exam === null ? "" : String(sub.exam)
    };
    var n = sub.components.length;
    var refs = { tk1: [], tk2: [] };

    var table = el("table", "journal");
    var head = el("tr");
    var titles = [tr("subject"), tr("stream"), tr("teacher")];
    var len1 = sub.components[0].w1.length, len2 = sub.components[0].w2.length;
    for (var i = 1; i <= len1; i++) titles.push(String(i));
    titles.push(tr("tk1"), tr("tk1t"), tr("rk1"), tr("r1"));
    for (i = len1 + 1; i <= len1 + len2; i++) titles.push(String(i));
    titles.push(tr("tk2"), tr("tk2t"), tr("rk2"), tr("r2"), tr("rd"), tr("exam"), tr("io"), tr("letter"));
    titles.forEach(function (t) { head.appendChild(el("th", "", t)); });
    table.appendChild(head);

    function span(td) { td.rowSpan = n; return td; }

    sub.components.forEach(function (c, idx) {
      var tr = el("tr");
      var comp = st.comps[idx];
      if (idx === 0) tr.appendChild(span(el("td", "name", sub.name)));
      tr.appendChild(el("td", "", c.stream + " (" + c.type_name + ")"));
      tr.appendChild(el("td", "", c.teacher));
      c.w1.forEach(function (orig, j) {
        tr.appendChild(weekCell(orig, comp.w1[j], function (v) { comp.w1[j] = v; update(); }));
      });
      var tk1 = el("td", "tk c-tk1"); refs.tk1.push(tk1); tr.appendChild(tk1);
      if (idx === 0) {
        refs.tk1tot = span(el("td", "tk c-tk1tot"));
        tr.appendChild(refs.tk1tot);
        if (sub.rk1 === null) {
          var td1 = inputCell("", function (v) { st.rk1 = v; update(); });
          tr.appendChild(span(td1));
        } else {
          tr.appendChild(span(el("td", "", String(sub.rk1))));
        }
        refs.r1 = span(el("td", "rating c-r1"));
        tr.appendChild(refs.r1);
      }
      c.w2.forEach(function (orig, j) {
        tr.appendChild(weekCell(orig, comp.w2[j], function (v) { comp.w2[j] = v; update(); }));
      });
      var tk2 = el("td", "tk c-tk2"); refs.tk2.push(tk2); tr.appendChild(tk2);
      if (idx === 0) {
        refs.tk2tot = span(el("td", "tk c-tk2tot"));
        tr.appendChild(refs.tk2tot);
        if (sub.rk2 === null) {
          tr.appendChild(span(inputCell("", function (v) { st.rk2 = v; update(); })));
        } else {
          tr.appendChild(span(el("td", "", String(sub.rk2))));
        }
        refs.r2 = span(el("td", "rating c-r2"));
        tr.appendChild(refs.r2);
        refs.rd = span(el("td", "rating c-rd"));
        tr.appendChild(refs.rd);
        tr.appendChild(span(inputCell(sub.exam === null ? "" : String(sub.exam),
          function (v) { st.exam = v; update(); })));
        refs.io = span(el("td", "c-io"));
        tr.appendChild(refs.io);
        refs.letter = span(el("td", "c-letter"));
        tr.appendChild(refs.letter);
      }
      table.appendChild(tr);
    });

    var wrap = el("div", "journal-block");
    var scroll = el("div", "journal-scroll");
    scroll.appendChild(table);
    wrap.appendChild(scroll);

    var summary = el("div", "journal-summary");
    var admLine = el("div", "j-line c-adm");
    var autoLine = el("div", "j-line c-auto");
    var needs = el("div", "j-line c-needs");
    var weeksLine = el("div", "j-line c-weeks");
    summary.appendChild(weeksLine);
    summary.appendChild(admLine);
    summary.appendChild(autoLine);
    summary.appendChild(needs);
    wrap.appendChild(summary);

    function update() {
      var w = weightsFor(st.comps.map(function (c) { return c.type; }));
      var t1 = 0, t2 = 0, anyZero = false;
      st.comps.forEach(function (c, i) {
        var a = avg(c.w1), b = avg(c.w2);
        t1 += (w[c.type] || 0) * a;
        t2 += (w[c.type] || 0) * b;
        refs.tk1[i].textContent = fmt(a);
        refs.tk2[i].textContent = fmt(b);
        if (hasZero(c.w1) || hasZero(c.w2)) anyZero = true;
      });
      var rk1 = toNum(st.rk1), rk2 = toNum(st.rk2), ik = toNum(st.exam);
      // Пока РК не выставлен, Р = 0 (так считает Platonus)
      var r1 = rk1 === null ? null : 0.7 * t1 + 0.3 * rk1;
      var r2 = rk2 === null ? null : 0.7 * t2 + 0.3 * rk2;
      var rd = ((r1 || 0) + (r2 || 0)) / 2;

      refs.tk1tot.textContent = fmt(t1);
      refs.tk2tot.textContent = fmt(t2);
      refs.r1.textContent = fmt(r1);
      refs.r2.textContent = fmt(r2);
      refs.rd.textContent = fmt(rd);


      // Сколько нужно в среднем за оставшиеся (пустые) клетки второй половины
      var base = 0, perA = 0, free = 0;
      st.comps.forEach(function (c) {
        var n = 0, S = 0, k = 0;
        c.w2.forEach(function (v) {
          var kd = kindOf(v);
          if (kd === "np") return;
          n++;
          if (kd === "num") S += toNum(v);
          else if (kd === "empty") k++;
        });
        if (n > 0) {
          base += (w[c.type] || 0) * S / n;
          perA += (w[c.type] || 0) * k / n;
          free += k;
        }
      });
      var rk2Used = rk2 !== null ? rk2 : rk1;
      if (rk1 === null || rk2Used === null) {
        weeksLine.textContent = tr("weeks.enter");
      } else if (free === 0) {
        weeksLine.textContent = tr("weeks.none");
      } else {
        var needR2 = 2 * targetRd - r1;
        var needT2 = (needR2 - 0.3 * rk2Used) / 0.7;
        var a = (needT2 - base) / perA;
        var assumed = rk2 === null ? tr("weeks.assumed", { rk1: rk1 }) : "";
        if (a <= 0) {
          weeksLine.textContent = tr("weeks.ok", { t: targetRd, a: assumed });
        } else if (a > 100) {
          weeksLine.textContent = tr("weeks.no", { t: targetRd, a: assumed });
        } else {
          weeksLine.textContent = tr("weeks.need", { t: targetRd, x: Math.ceil(a - 1e-9), n: free, a: assumed });
        }
      }

      // Допуск к экзамену (п. 8 регламента)
      var admitted = null;
      if (rk1 !== null && rk2 !== null) {
        var problems = [];
        if (rk1 === 0 || rk2 === 0) problems.push(tr("adm.zero"));
        if ((rk1 + rk2) / 2 < 50) problems.push(tr("adm.avg"));
        if (rd < 50) problems.push(tr("adm.rd"));
        admitted = problems.length === 0;
        admLine.textContent = admitted ? tr("adm.yes") : tr("adm.no", { p: problems.join(", ") });
      } else if (rk1 !== null) {
        var needRk2 = Math.max(1, 100 - rk1);
        var needR2 = Math.max(0, 100 - r1);
        admLine.textContent = tr("adm.pend", { a: Math.ceil(needRk2 - 1e-9), b: Math.ceil(needR2 * 10 - 1e-9) / 10 });
      } else {
        admLine.textContent = tr("adm.enter");
      }

      // Автомат (пп. 8 и 9.2): нет 0 и «н», РК не меньше 50
      var autoBad = anyZero || (rk1 !== null && rk1 < 50) || (rk2 !== null && rk2 < 50);
      autoLine.textContent = autoBad
        ? tr("auto.no") : tr("auto.yes");

      // Итог
      if (ik !== null && admitted === true) {
        var io = 0.6 * rd + 0.4 * ik;
        refs.io.textContent = fmt(io);
        refs.letter.textContent = letterFor(io);
      } else {
        refs.io.textContent = ik !== null && admitted === false ? tr("noadm") : "—";
        refs.letter.textContent = "—";
      }

      // Сколько нужно на экзамене
      if (rk2 !== null) {
        var parts = THRESHOLDS.map(function (t) {
          var need = Math.ceil(Math.round(((t - 0.6 * rd) / 0.4) * 1e6) / 1e6);
          var label = t === 70 ? tr("stip") : t + "+";
          return label + ": " + (need > 100 ? "—" : Math.max(0, need));
        });
        needs.textContent = tr("needs", { p: parts.join(" · ") });
      } else {
        needs.textContent = tr("needs.enter");
      }
    }

    updaters.push(update);
    update();
    return wrap;
  }

  // Выбор цели: какой рейтинг допуска нужен
  var control = el("div", "journal-control");
  var label = el("label", "", tr("goal"));
  var select = el("select");
  [50, 60, 70, 75, 80, 85, 90, 95].forEach(function (v) {
    var o = el("option", "", String(v));
    o.value = String(v);
    if (v === targetRd) o.selected = true;
    select.appendChild(o);
  });
  select.addEventListener("change", function () {
    targetRd = parseInt(select.value, 10);
    updaters.forEach(function (fn) { fn(); });
  });
  label.appendChild(select);
  control.appendChild(label);
  root.appendChild(control);

  subjects.forEach(function (sub) { root.appendChild(build(sub)); });
})();
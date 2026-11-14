// Главная: показывает сохранённый журнал сразу при открытии сайта.
// Журнал лежит в localStorage этого устройства, сервер ничего не запоминает.
(function () {
    var box = document.getElementById("status");
    if (!box) return;
    var T = {};
    try { T = JSON.parse(document.getElementById("i18n").textContent); } catch (e) {}
    function tr(key, vars) {
        var s = T[key] || key;
        for (var k in (vars || {})) s = s.replace("{" + k + "}", vars[k]);
        return s;
    }
    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined) n.textContent = text;
        return n;
    }

    var saved = null;
    try { saved = JSON.parse(localStorage.getItem("jtu.saved") || "null"); } catch (e) {}
    box.hidden = false;

    if (!saved || !saved.raw) {
        var empty = el("div", "st-empty");
        empty.appendChild(el("h2", "", tr("st.empty.h")));
        empty.appendChild(el("p", "", tr("st.empty.p")));
        var go = el("a", "btn", tr("st.empty.b"));
        go.href = "/analyze";
        empty.appendChild(go);
        box.appendChild(empty);
        return;
    }

    box.appendChild(el("p", "st-load", tr("st.load")));

    fetch("/api/status", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({raw: saved.raw, mode: saved.mode})
    }).then(function (r) { return r.json(); }).then(function (data) {
        box.textContent = "";
        render(data.cards || []);
    }).catch(function () {
        box.textContent = "";
        box.appendChild(el("p", "st-err", tr("st.err")));
    });

    function needText(n, has) {
        if (!has) return null;
        if (n === null || n === undefined) return tr("st.none");
        if (n <= 0) return tr("st.done");
        return tr("st.points", {n: n});
    }

    function render(cards) {
        var head = el("div", "st-head");
        head.appendChild(el("h2", "", tr("st.title")));
        var d = new Date(saved.ts || Date.now());
        head.appendChild(el("span", "st-date",
            tr("st.updated", {date: d.toLocaleDateString() + " " + d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})})));
        box.appendChild(head);

        var grid = el("div", "st-grid");
        cards.forEach(function (c) {
            var state = "neutral";
            if (c.adm === "no") state = "bad";
            else if ((c.adm === "yes" || c.adm === "possible") && c.auto === "possible") state = "good";
            else if (c.adm === "yes" || c.adm === "possible") state = "warn";

            var card = el("div", "st-card st-" + state);
            card.appendChild(el("div", "st-name", c.name));

            var rd = el("div", "st-rd");
            rd.appendChild(el("span", "st-rd-n", c.rd === null || c.rd === undefined ? "–" : String(c.rd)));
            var lbl = tr("st.rd") + (c.forecast ? " (" + tr("st.forecast") + ")" : "");
            rd.appendChild(el("span", "st-rd-l", lbl));
            card.appendChild(rd);

            var chips = el("div", "st-chips");
            chips.appendChild(el("span", "st-chip st-adm-" + c.adm, tr("st.adm." + c.adm)));
            chips.appendChild(el("span", "st-chip st-auto-" + c.auto, tr("st.auto." + (c.auto === "possible" ? "possible" : "no"))));
            card.appendChild(chips);

            var n70 = needText(c.n70, c.has_need), n90 = needText(c.n90, c.has_need);
            if (n70) {
                var ex = el("div", "st-exam");
                ex.appendChild(el("div", "", tr("st.exam", {t: "70", n: n70})));
                ex.appendChild(el("div", "", tr("st.exam", {t: "90", n: n90})));
                card.appendChild(ex);
            }
            grid.appendChild(card);
        });
        box.appendChild(grid);

        var foot = el("div", "st-foot");
        var upd = el("a", "btn", tr("st.update"));
        upd.href = "/analyze";
        foot.appendChild(upd);
        var forget = el("button", "st-forget", tr("st.forget"));
        forget.type = "button";
        forget.addEventListener("click", function () {
            if (!window.confirm(tr("st.confirm"))) return;
            try { localStorage.removeItem("jtu.saved"); } catch (e) {}
            location.reload();
        });
        foot.appendChild(forget);
        box.appendChild(foot);
        box.appendChild(el("p", "st-note", tr("st.local")));
    }
})();

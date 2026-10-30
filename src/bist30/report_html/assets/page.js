(function () {
  "use strict";
  var P = JSON.parse(document.getElementById("payload").textContent);
  var M = P.metrics, S = P.series, META = P.meta || {}, TH = M.thresholds, C = P.content;
  var SVGNS = "http://www.w3.org/2000/svg";
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------------------ helpers
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "text") e.textContent = attrs[k];
      else if (k === "cls") e.className = attrs[k];
      else e.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) { if (c != null) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function sv(tag, attrs) {
    var e = document.createElementNS(SVGNS, tag);
    if (attrs) for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  function svText(x, y, s, attrs) { var t = sv("text", Object.assign({ x: x, y: y }, attrs || {})); t.textContent = s; return t; }
  function fin(x) { return x !== null && x !== undefined && isFinite(x); }
  var nfCache = {};
  function nf(d) { return nfCache[d] || (nfCache[d] = new Intl.NumberFormat("tr-TR", { minimumFractionDigits: d, maximumFractionDigits: d })); }
  function num(x, d, sign) {
    if (!fin(x)) return "–";
    var s = nf(d === undefined ? 2 : d).format(Math.abs(x));
    if (/^[0,]+$/.test(s)) x = 0;          // no "−0,00"
    if (sign) return (x > 0 ? "+" : x < 0 ? "−" : "±") + s;
    return (x < 0 ? "−" : "") + s;
  }
  function pct(x, d, sign) {
    if (!fin(x)) return "–";
    var s = "%" + nf(d === undefined ? 3 : d).format(Math.abs(x));
    if (sign) return (x > 0 ? "+" : x < 0 ? "−" : "±") + s;
    return (x < 0 ? "−" : "") + s;
  }
  function int(x) { return fin(x) ? new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 }).format(x) : "–"; }
  function hhmm(iso) { return iso ? iso.slice(11, 16) : "--:--"; }
  function dmy(iso) { return iso ? iso.slice(8, 10) + "." + iso.slice(5, 7) + "." + iso.slice(0, 4) : "–"; }
  function dm(iso) { return iso ? iso.slice(8, 10) + "." + iso.slice(5, 7) : "–"; }
  var WD = ["Paz", "Pzt", "Sal", "Çar", "Per", "Cum", "Cmt"];
  function weekday(isoDate) { var p = isoDate.split("-"); return WD[new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])).getUTCDay()]; }
  function dur(s) { if (!fin(s)) return "–"; if (s < 90) return num(s, 0) + " sn"; return num(s / 60, 1) + " dk"; }

  function niceStep(span, count) {
    var raw = span / Math.max(1, count), mag = Math.pow(10, Math.floor(Math.log10(raw))), r = raw / mag;
    return (r < 1.5 ? 1 : r < 3 ? 2 : r < 7 ? 5 : 10) * mag;
  }
  function ticks(lo, hi, count) {
    var st = niceStep(hi - lo, count), out = [];
    for (var v = Math.ceil(lo / st) * st; v <= hi + st * 1e-9; v += st) out.push(+v.toFixed(10));
    return { values: out, step: st };
  }
  function decimalsFor(step) { return Math.max(0, Math.min(4, -Math.floor(Math.log10(step) + 1e-9))); }

  var T = S.t.map(function (s) { return Date.parse(s); });

  function rowStatus(i) {
    if (!S.valid[i]) return { cls: "invalid", ico: "muted", label: reasonText(S.reason[i]) };
    if (!fin(S.off[i]) || !fin(S.dpct[i])) return { cls: "invalid", ico: "muted", label: C.reason_labels.no_official || "resmî değer yok" };
    var a = Math.abs(S.dpct[i]);
    if (a > TH.crit_pct) return { cls: "crit", ico: "crit", label: "sapma > " + pct(TH.crit_pct, 2) };
    if (a > TH.warn_pct) return { cls: "warn", ico: "warn", label: "dikkat > " + pct(TH.warn_pct, 2) };
    return { cls: "good", ico: "good", label: "uyumlu" };
  }
  function reasonKey(part) {
    var head = part.split(":")[0];
    for (var k in C.reason_labels) if (head.indexOf(k) === 0) return k;
    return head;
  }
  function reasonText(r) {
    if (!r) return "geçersiz";
    return r.split("|").filter(Boolean).map(function (part) {
      var k = reasonKey(part), lab = C.reason_labels[k] || k, syms = part.indexOf(":") >= 0 ? part.split(":")[1].split(",") : [];
      if (!syms.length || !syms[0]) return lab;
      var shown = syms.slice(0, 3).join(", ") + (syms.length > 3 ? " +" + (syms.length - 3) : "");
      return lab + ": " + shown;
    }).join(" · ");
  }
  function icon(kind) {
    var glyph = { good: "✓", warn: "!", crit: "×", muted: "–" }[kind] || "";
    return el("span", { cls: "ico ico--" + kind, "aria-hidden": "true", text: glyph });
  }
  function levelOfMean(p) {
    if (!fin(p)) return "muted";
    var a = Math.abs(p);
    return a > TH.crit_pct ? "crit" : a > TH.warn_pct ? "warn" : a >= TH.bias_econ_pct ? "warn" : "good";
  }

  // ------------------------------------------------------------------ hero
  function renderHero() {
    var st = META.status || "provisional";
    var banner = $("status-banner");
    if (st !== "final") {
      banner.hidden = false;
      banner.appendChild(icon("warn"));
      banner.appendChild(el("div", null, [el("strong", { text: st === "provisional" ? "GEÇİCİ (provisional weights)" : "ÖN SÜRÜM" }),
        " " + (META.status_note || "Ağırlıklar resmî kaynaklardan doğrulanmadı; sayılar nihai değildir.")]));
    }
    var days = P.days;
    $("hero-eyebrow").textContent = "Geriye dönük test · " + dmy(days[0].date) + " – " + dmy(days[days.length - 1].date) +
      " · " + days.length + " işlem günü · " + int(M.n_compared) + " dakika karşılaştırıldı";
    $("hero-headline").textContent = M.verdict.headline;
    $("hero-lede").textContent = "BIST 30'u Borsa İstanbul kural setindeki formülle her dakika kendimiz hesapladık ve resmî XU030 ile " +
      "aynı dakikada karşılaştırdık. Bölen yalnız " + dmy(P.divisor.reference_ts) + " resmî kapanışında bir kez kalibre edildi; " +
      "sonraki her dakika örneklem dışıdır.";

    var sm = M.summary, b = M.bias;
    var tiles = [
      ["Ortalama mutlak fark", num(sm.mae_pts, 2) + " puan", pct(sm.mean_abs_pct, 3) + " · RMSE " + num(sm.rmse_pts, 2)],
      ["Ortalama fark (bias)", num(sm.bias_pts, 2, true) + " puan", pct(b.mean_pct, 3, true) + " · hesaplanan − gerçekleşen"],
      ["En büyük mutlak fark", pct(sm.max_abs_pct, 3), num(sm.max_abs_pts, 2) + " puan"],
      ["±" + pct(TH.warn_pct, 2) + " içinde", "%" + num(M.within_warn_pct, 1), "±" + pct(TH.crit_pct, 2) + " içinde: %" + num(M.within_crit_pct, 1)],
      ["Geçerli dakika", "%" + num(sm.pct_valid, 1), int(M.n_compared) + " / " + int(M.n_minutes_oos) + " karşılaştırıldı"]
    ];
    var k = $("kpis");
    tiles.forEach(function (t) {
      k.appendChild(el("div", { cls: "kpi" }, [el("span", { cls: "kpi__label", text: t[0] }), el("span", { cls: "kpi__value", text: t[1] }), el("span", { cls: "kpi__note", text: t[2] })]));
    });

    var v = M.verdict, ans = $("verdict-answer");
    ans.appendChild(icon(v.level)); ans.appendChild(el("span", { cls: "t-" + v.level, text: v.answer }));
    var cd = M.cross_day_drift, id = M.intraday_drift;
    var checks = [
      { label: "Ortalama fark anlamlı mı?", value: "t = " + num(b.t_days, 1), ok: !(Math.abs(b.t_days) >= TH.bias_t),
        detail: "Gün ortalamalarının ortalaması " + pct(b.mean_of_daily_pct, 3, true) + ", standart hata " + pct(b.se_days, 3) + " (" + b.n_days + " gün). Eşik |t| ≥ " + num(TH.bias_t, 0) +
          ". Dakika bazında AR(1) düzeltmeli t = " + num(b.t_ar1, 1) + " (ρ = " + num(b.ar1_rho, 2) + ", etkin n ≈ " + int(b.n_eff) + ")." },
      { label: "Fark ekonomik olarak büyük mü?", value: pct(b.mean_pct, 3, true), ok: !(Math.abs(b.mean_pct) >= TH.bias_econ_pct),
        detail: "Eşik ±" + pct(TH.bias_econ_pct, 2) + " (yaklaşık " + num(TH.bias_econ_pct / 100 * (P.divisor.reference_official_value || 0), 0) + " puan)." },
      { label: "Günler arasında kayma var mı?", value: num(cd.slope_bp_per_day, 2, true) + " bp/gün", ok: !(Math.abs(cd.t) >= TH.drift_t),
        detail: "Günlük ortalama farkın işlem günü sırasına göre eğimi, t = " + num(cd.t, 1) + ". İlk " + b.third_len_days + " gün " + pct(b.early_mean_pct, 3, true) +
          ", son " + b.third_len_days + " gün " + pct(b.late_mean_pct, 3, true) + "." },
      { label: "Gün içinde kayma var mı?", value: num(id.mean_slope_bp_per_h, 2, true) + " bp/saat", ok: !(Math.abs(id.t) >= TH.drift_t),
        detail: "Her gün için farkın saate göre eğimi, " + id.n_days + " günün ortalaması, t = " + num(id.t, 1) + "." },
      { label: "Dakikaların çoğu bant içinde mi?", value: "%" + num(M.within_warn_pct, 1), ok: M.within_warn_pct >= 95,
        detail: "±" + pct(TH.warn_pct, 2) + " bandında olan karşılaştırılmış dakika payı (beklenti ≥ %95). ±" + pct(TH.crit_pct, 2) + " bandında: %" + num(M.within_crit_pct, 1) + "." },
      { label: "Zaman kayması var mı?", value: "en iyi gecikme " + (M.lag.best_lag === null ? "–" : M.lag.best_lag + " dk"), ok: M.lag.best_lag === 0,
        detail: "1 dakikalık getirilerin çapraz korelasyonu; gecikme 0'da r = " + num(M.lag.corr0, 3) + ". Yalnız teşhis amaçlı, seri kaydırılmadı." }
    ];
    var ul = $("checks");
    checks.forEach(function (c) {
      var kind = c.ok ? "good" : "warn";
      ul.appendChild(el("li", { cls: "check" }, [icon(kind), el("span", { cls: "check__label", text: c.label }),
        el("span", { cls: "check__value t-" + kind, text: c.value }), el("span", { cls: "check__detail", text: c.detail })]));
    });
    $("verdict-method").textContent = "Yalnız geçerli ve aynı dakikada resmî değeri olan dakikalar sayılır. ✓ = eşik aşılmadı, ! = eşik aşıldı. " +
      "Ardışık dakikaların farkları birbirine çok bağlı olduğundan anlamlılık testi için gün ortalamaları kullanılır.";

    var facts = [
      ["Dönem", dmy(days[0].date) + " – " + dmy(days[days.length - 1].date) + (days.some(function (d) { return d.partial; }) ? " (son gün kısmi)" : "")],
      ["Veri kaynağı", META.data_source || "–"],
      ["Gecikme", META.latency || "–"],
      ["Kalibrasyon", dmy(P.divisor.reference_ts) + " " + hhmm(P.divisor.reference_ts) + " · resmî kapanış " + num(P.divisor.reference_official_value, 2)],
      ["Bölen", num(P.divisor.value, 8) + (META.final_divisor && META.final_divisor !== P.divisor.value ? " → " + num(META.final_divisor, 8) : "")],
      ["Bileşen verisi", META.metadata_label || "–"]
    ];
    var dl = $("facts");
    facts.forEach(function (f) { dl.appendChild(el("div", null, [el("dt", { text: f[0] }), el("dd", { text: f[1] })])); });
  }

  // ------------------------------------------------------------------ daily chart
  function renderDaily() {
    var host = $("daily-chart"); host.innerHTML = "";
    var days = P.days, W = Math.max(280, host.clientWidth || 600), H = Math.round(Math.min(260, Math.max(190, W * 0.4)));
    var m = { l: 52, r: 12, t: 14, b: 26 }, iw = W - m.l - m.r, ih = H - m.t - m.b;
    var vals = days.map(function (d) { return d.stats ? d.stats.mean_pct : null; });
    var lim = Math.max(TH.warn_pct * 1.25, Math.max.apply(null, vals.filter(fin).map(Math.abs)) * 1.15);
    var y = function (v) { return m.t + ih / 2 - v / lim * ih / 2; };
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Gün gün ortalama fark çubuk grafiği" });
    var tk = ticks(-lim, lim, 4), dec = decimalsFor(tk.step);
    tk.values.forEach(function (v) {
      svg.appendChild(sv("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), "class": v === 0 ? "zero" : "gridline" }));
      svg.appendChild(svText(m.l - 6, y(v) + 4, pct(v, dec, v !== 0), { "text-anchor": "end" }));
    });
    [TH.warn_pct, -TH.warn_pct].forEach(function (v) {
      svg.appendChild(sv("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), "class": "band-warn-line", "stroke-dasharray": "4 3" }));
    });
    svg.appendChild(svText(W - m.r, y(TH.warn_pct) - 4, "±" + pct(TH.warn_pct, 2) + " eşik", { "text-anchor": "end" }));
    var bw = iw / days.length, gap = Math.min(4, bw * 0.3);
    var tip = ensureTip(host);
    days.forEach(function (d, i) {
      var v = vals[i], x = m.l + i * bw;
      var g = sv("g", { "class": "bar" + (i === state.day ? " is-active" : ""), tabindex: "0", role: "button",
        "aria-label": dm(d.date) + " ortalama fark " + pct(v, 3, true) });
      g.appendChild(sv("rect", { "class": "hit", x: x, y: m.t, width: bw, height: ih, fill: "transparent", rx: 3 }));
      if (fin(v)) {
        var y0 = y(0), y1 = y(v), h = Math.max(1, Math.abs(y1 - y0)), r = Math.min(4, (bw - gap) / 2, h);
        var bx = x + gap / 2, w = bw - gap, top = Math.min(y0, y1);
        // rounded at the data end, square at the baseline
        var path = v >= 0
          ? "M" + bx + "," + y0 + "V" + (top + r) + "Q" + bx + "," + top + " " + (bx + r) + "," + top + "H" + (bx + w - r) + "Q" + (bx + w) + "," + top + " " + (bx + w) + "," + (top + r) + "V" + y0 + "Z"
          : "M" + bx + "," + y0 + "V" + (y1 - r) + "Q" + bx + "," + y1 + " " + (bx + r) + "," + y1 + "H" + (bx + w - r) + "Q" + (bx + w) + "," + y1 + " " + (bx + w) + "," + (y1 - r) + "V" + y0 + "Z";
        var lvl = levelOfMean(v);
        g.appendChild(sv("path", { d: path, "class": "mark", fill: lvl === "good" ? "var(--ink-3)" : lvl === "warn" ? "var(--warn)" : "var(--crit)" }));
      }
      var every = Math.ceil(days.length / Math.max(2, Math.floor(iw / 46)));
      if (i % every === 0 || (i === days.length - 1 && (days.length - 1) % every >= Math.ceil(every / 2) + 1)) svg.appendChild(svText(x + bw / 2, H - 8, dm(d.date), { "text-anchor": "middle" }));
      g.addEventListener("click", function () { selectDay(i, true); });
      g.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectDay(i, true); } });
      function show() {
        var s = d.stats || {};
        tip.replaceChildren(el("div", { cls: "tooltip__t", text: dmy(d.date) + " " + weekday(d.date) + (d.half_day ? " · yarım gün" : "") + (d.partial ? " · kısmi" : "") }),
          tipRow(null, "Ortalama fark", pct(s.mean_pct, 3, true) + " · " + num(s.mean_pts, 2, true) + " p"),
          tipRow(null, "Ort. mutlak", pct(s.mae_pct, 3)), tipRow(null, "En büyük", pct(s.max_abs_pct, 3)),
          tipRow(null, "Dakika", int(s.n_compared) + " / " + int(s.n_rows)));
        tip.hidden = false;
        var bx2 = (x + bw / 2) / W * host.clientWidth, left = Math.min(Math.max(0, bx2 - 100), host.clientWidth - 210);
        tip.style.left = left + "px"; tip.style.top = (H / W * host.clientWidth + 6) + "px";
      }
      g.addEventListener("pointerenter", show); g.addEventListener("focus", show);
      g.addEventListener("pointerleave", function () { tip.hidden = true; }); g.addEventListener("blur", function () { tip.hidden = true; });
      svg.appendChild(g);
    });
    host.appendChild(svg);
    $("daily-sub").textContent = "Hesaplanan − gerçekleşen, % · gri: |ortalama| < " + pct(TH.bias_econ_pct, 2) + ", sarı: ≥ " + pct(TH.bias_econ_pct, 2) + " (ekonomik eşik) · çubuğa tıklayınca o gün açılır";
  }
  function ensureTip(host) {
    var t = host.querySelector(".tooltip");
    if (!t) { t = el("div", { cls: "tooltip", role: "status" }); t.hidden = true; host.appendChild(t); }
    return t;
  }
  function tipRow(color, label, value, dashed) {
    var key = el("span", { cls: "tooltip__key" });
    if (color) { key.style.borderTopColor = color; if (dashed) key.style.borderTopStyle = "dashed"; } else key.style.borderTopColor = "transparent";
    return el("div", { cls: "tooltip__row" }, [key, el("span", { text: label }), el("b", { text: value })]);
  }

  // ------------------------------------------------------------------ animation state
  var state = { day: 0, n: 0, playing: false, speed: 15, unit: "pct", tSim: 0, last: 0, tapeN: 0 };
  function dayRange() { var d = P.days[state.day]; return [d.start, d.end]; }
  function dayLen() { var r = dayRange(); return r[1] - r[0]; }

  function selectDay(i, scroll) {
    pause();
    state.day = i;
    state.n = dayLen();       // complete at rest: the whole day drawn
    state.tSim = T[dayRange()[1] - 1];
    $("scrubber").max = dayLen(); $("scrubber").value = state.n;
    document.querySelectorAll("#day-chips .chip").forEach(function (c, k) { c.setAttribute("aria-selected", k === i ? "true" : "false"); c.tabIndex = k === i ? 0 : -1; });
    document.querySelectorAll("#daily-chart .bar").forEach(function (g, k) { g.classList.toggle("is-active", k === i); });
    var chip = document.querySelectorAll("#day-chips .chip")[i]; if (chip && chip.scrollIntoView) chip.scrollIntoView({ block: "nearest", inline: "nearest" });
    dayCache = null;
    renderTape(true); drawCharts(); updateClock(); dayNote();
    if (scroll) $("animasyon").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  }

  function renderDayChips() {
    var host = $("day-chips");
    P.days.forEach(function (d, i) {
      var s = d.stats || {}, lvl = levelOfMean(s.mean_pct);
      var b = el("button", { type: "button", cls: "chip", role: "option", "aria-selected": "false",
        "aria-label": dmy(d.date) + ", ortalama fark " + pct(s.mean_pct, 3, true) + (d.half_day ? ", yarım gün" : "") + (d.partial ? ", kısmi gün" : "") });
      var dot = el("span", { cls: "chip__dot" }); dot.style.background = lvl === "good" ? "var(--good)" : lvl === "warn" ? "var(--warn)" : "var(--crit)";
      b.appendChild(dot); b.appendChild(document.createTextNode(dm(d.date) + " " + weekday(d.date)));
      if (d.half_day) b.appendChild(el("span", { cls: "chip__tag", text: "yarım" }));
      if (d.partial) b.appendChild(el("span", { cls: "chip__tag", text: "kısmi" }));
      b.addEventListener("click", function () { selectDay(i, false); });
      b.addEventListener("keydown", function (e) {
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); var j = Math.max(0, Math.min(P.days.length - 1, i + (e.key === "ArrowRight" ? 1 : -1))); selectDay(j, false); host.children[j].focus(); }
      });
      host.appendChild(b);
    });
  }

  function play() {
    if (state.n >= dayLen()) { state.n = 0; state.tSim = T[dayRange()[0]] - 60000; renderTape(true); }
    state.playing = true; state.last = 0;
    var b = $("btn-play"); b.setAttribute("aria-pressed", "true"); b.querySelector(".btn__icon").textContent = "❚❚"; b.querySelector(".btn__label").textContent = "Duraklat";
    requestAnimationFrame(tick);
  }
  function pause() {
    state.playing = false;
    var b = $("btn-play"); b.setAttribute("aria-pressed", "false"); b.querySelector(".btn__icon").textContent = "▶"; b.querySelector(".btn__label").textContent = "Oynat";
  }
  function tick(ts) {
    if (!state.playing) return;
    if (!state.last) state.last = ts;
    var dt = Math.min(250, ts - state.last); state.last = ts;
    state.tSim += dt * state.speed * 60;           // speed = simulated minutes per real second
    var r = dayRange(), n = state.n;
    while (r[0] + n < r[1] && T[r[0] + n] <= state.tSim) n++;
    if (n !== state.n) { state.n = n; $("scrubber").value = n; renderTape(false); drawCharts(); updateClock(); }
    if (state.n >= dayLen()) { pause(); return; }
    requestAnimationFrame(tick);
  }

  function updateClock() {
    var r = dayRange(), i = r[0] + state.n - 1;
    $("clock-date").textContent = dmy(P.days[state.day].date) + " " + weekday(P.days[state.day].date);
    $("clock-time").textContent = state.n > 0 ? hhmm(S.t[i]) : hhmm(P.days[state.day].domain_start);
  }
  function dayNote() {
    var d = P.days[state.day], s = d.stats || {}, notes = [];
    notes.push(dmy(d.date) + ": " + int(s.n_compared) + " dakika karşılaştırıldı, ortalama fark " + pct(s.mean_pct, 3, true) + " (" + num(s.mean_pts, 2, true) + " puan), en büyük mutlak fark " + pct(s.max_abs_pct, 3) + ".");
    if (d.half_day) notes.push("Yarım gün (arife): sürekli işlem 12:30'da biter.");
    if (d.partial) notes.push("Kısmi gün: veri " + hhmm(d.stats && d.stats.last_ts) + "'e kadar çekildi.");
    (META.divisor_events || []).forEach(function (e) { if (e.date === d.date && e.changed) notes.push("Bu sabah bölen düzeltildi (kural 5.1): " + e.text + "."); });
    $("day-note").textContent = notes.join(" ");
  }

  // ------------------------------------------------------------------ charts
  var dayCache = null;
  function cacheDay() {
    if (dayCache && dayCache.day === state.day) return dayCache;
    var r = dayRange(), d = P.days[state.day], lo = Infinity, hi = -Infinity, emax = 0, pmax = 0, offmax = 0;
    for (var i = r[0]; i < r[1]; i++) {
      [S.calc[i], S.unchecked[i], S.off[i]].forEach(function (v) { if (fin(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
      if (S.valid[i] && fin(S.dpct[i])) { emax = Math.max(emax, Math.abs(S.dpct[i])); pmax = Math.max(pmax, Math.abs(S.dpts[i])); }
      if (fin(S.off[i])) offmax = Math.max(offmax, S.off[i]);
    }
    var pad = Math.max(5, (hi - lo) * 0.08);
    dayCache = { day: state.day, r: r, x0: Date.parse(d.domain_start), x1: Date.parse(d.domain_end), lo: lo - pad, hi: hi + pad,
      pctLim: Math.max(emax, TH.crit_pct) * 1.15, ptsLim: Math.max(pmax, TH.crit_pct / 100 * offmax) * 1.15 };
    return dayCache;
  }
  var geom = null;
  function drawCharts() {
    var dc = cacheDay(), host = $("main-chart"), eh = $("err-chart");
    var W = Math.max(300, host.clientWidth || 700), narrow = W < 560;
    var m = { l: 58, r: narrow ? 10 : 118, t: 10, b: 24 };
    var H = Math.round(Math.min(380, Math.max(230, W * 0.42))), EH = Math.round(Math.min(170, Math.max(120, W * 0.18)));
    var iw = W - m.l - m.r, ih = H - m.t - m.b, eih = EH - m.t - m.b;
    var x = function (t) { return m.l + (t - dc.x0) / (dc.x1 - dc.x0) * iw; };
    var y = function (v) { return m.t + (dc.hi - v) / (dc.hi - dc.lo) * ih; };
    var lim = state.unit === "pct" ? dc.pctLim : dc.ptsLim;
    var ey = function (v) { return m.t + eih / 2 - v / lim * eih / 2; };
    geom = { x: x, m: m, W: W, H: H, EH: EH, iw: iw };
    var r = dc.r, n = state.n, end = r[0] + n;

    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Hesaplanan ve gerçekleşen XU030" });
    var esv = sv("svg", { viewBox: "0 0 " + W + " " + EH, role: "img", "aria-label": "Fark" });

    // x ticks (hourly, half-hourly on short days)
    var span = dc.x1 - dc.x0, stepMin = span < 4 * 3600e3 ? 30 : (narrow ? 120 : 60);
    var first = Math.ceil(dc.x0 / (stepMin * 60e3)) * stepMin * 60e3;
    for (var tt = first; tt <= dc.x1 + 1; tt += stepMin * 60e3) {
      var lab = new Date(tt + 3 * 3600e3).toISOString().slice(11, 16);   // Istanbul = UTC+3 (no DST)
      [[svg, H, ih], [esv, EH, eih]].forEach(function (s) {
        s[0].appendChild(sv("line", { x1: x(tt), x2: x(tt), y1: m.t, y2: m.t + s[2], "class": "gridline" }));
      });
      svg.appendChild(svText(x(tt), H - 6, lab, { "text-anchor": "middle" }));
      esv.appendChild(svText(x(tt), EH - 6, lab, { "text-anchor": "middle" }));
    }
    // y ticks main
    var yt = ticks(dc.lo, dc.hi, 5);
    yt.values.forEach(function (v) {
      svg.appendChild(sv("line", { x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v), "class": "gridline" }));
      svg.appendChild(svText(m.l - 6, y(v) + 4, num(v, decimalsFor(yt.step)), { "text-anchor": "end" }));
    });

    // invalid stretches
    var runStart = -1;
    for (var i = r[0]; i <= end; i++) {
      var inv = i < end && !S.valid[i];
      if (inv && runStart < 0) runStart = i;
      if (!inv && runStart >= 0) {
        var xa = x(T[runStart] - 60e3), xb = x(T[i - 1]);
        svg.appendChild(sv("rect", { x: xa, y: m.t, width: Math.max(2, xb - xa), height: ih, "class": "invalid-band" }));
        esv.appendChild(sv("rect", { x: xa, y: m.t, width: Math.max(2, xb - xa), height: eih, "class": "invalid-band" }));
        runStart = -1;
      }
    }

    // lines
    var pc = "", po = "", pu = "", penC = false, penO = false;
    for (i = r[0]; i < end; i++) {
      var X = x(T[i]).toFixed(1);
      if (S.valid[i] && fin(S.calc[i])) { pc += (penC ? "L" : "M") + X + "," + y(S.calc[i]).toFixed(1); penC = true; } else penC = false;
      if (fin(S.off[i])) { po += (penO ? "L" : "M") + X + "," + y(S.off[i]).toFixed(1); penO = true; } else penO = false;
      if (!S.valid[i] && fin(S.unchecked[i])) {
        var j = i - 1, start = j >= r[0] && fin(S.unchecked[j]) ? j : i;
        pu += "M" + x(T[start]).toFixed(1) + "," + y(S.unchecked[start]).toFixed(1) + "L" + X + "," + y(S.unchecked[i]).toFixed(1);
        if (i + 1 < end && S.valid[i + 1] && fin(S.calc[i + 1])) pu += "L" + x(T[i + 1]).toFixed(1) + "," + y(S.calc[i + 1]).toFixed(1);
      }
    }
    if (pu) svg.appendChild(sv("path", { d: pu, "class": "line--unchecked" }));
    svg.appendChild(sv("path", { d: po, "class": "line series-off" }));
    svg.appendChild(sv("path", { d: pc, "class": "line series-calc" }));

    // current points + end labels
    if (n > 0) {
      var li = end - 1, cv = S.valid[li] ? S.calc[li] : S.unchecked[li], ov = S.off[li];
      if (fin(ov)) svg.appendChild(sv("circle", { cx: x(T[li]), cy: y(ov), r: 4.5, "class": "dot dot-off" }));
      if (fin(cv)) svg.appendChild(sv("circle", { cx: x(T[li]), cy: y(cv), r: 4.5, "class": "dot dot-calc", "fill-opacity": S.valid[li] ? 1 : 0.4 }));
      if (!narrow) {
        var labs = [{ v: cv, name: "Hesaplanan", cls: "calc" }, { v: ov, name: "Gerçekleşen", cls: "off" }].filter(function (a) { return fin(a.v); })
          .map(function (a) { a.y = y(a.v); return a; }).sort(function (a, b) { return a.y - b.y; });
        if (labs.length === 2 && labs[1].y - labs[0].y < 30) { var mid = (labs[0].y + labs[1].y) / 2; labs[0].y = mid - 15; labs[1].y = mid + 15; }
        labs.forEach(function (a) {
          var yy = Math.max(m.t + 10, Math.min(m.t + ih - 4, a.y)), lx = m.l + iw + 10;
          svg.appendChild(sv("line", { x1: lx - 6, x2: lx - 1, y1: yy - 4, y2: yy - 4, "class": "series-" + a.cls, "stroke-width": 2 }));
          svg.appendChild(svText(lx + 2, yy - 1, num(a.v, 2), { "class": "endlabel" }));
          svg.appendChild(svText(lx + 2, yy + 11, a.name + (a.cls === "calc" && !S.valid[li] ? " (geçersiz)" : ""), { "class": "endlabel-sub" }));
        });
      }
    }

    // error panel: bands, zero, line, flags
    var et = ticks(-lim, lim, 4), edec = decimalsFor(et.step);
    if (state.unit === "pct") {
      [[TH.warn_pct, TH.crit_pct], [-TH.crit_pct, -TH.warn_pct]].forEach(function (b) {
        var ya = ey(Math.min(b[1], lim)), yb = ey(Math.max(b[0], -lim));
        esv.appendChild(sv("rect", { x: m.l, y: ya, width: iw, height: Math.max(0, yb - ya), "class": "band-warn" }));
      });
      [TH.crit_pct, -TH.crit_pct].forEach(function (v) { if (Math.abs(v) <= lim) esv.appendChild(sv("line", { x1: m.l, x2: m.l + iw, y1: ey(v), y2: ey(v), "class": "band-crit-line" })); });
    } else {
      // threshold envelopes in points vary with the index level: ±warn% x official(t)
      var up = "", lastOff = null, pts = [];
      for (i = r[0]; i < r[1]; i++) { if (fin(S.off[i])) lastOff = S.off[i]; if (lastOff) pts.push([x(T[i]), lastOff]); }
      if (pts.length) {
        var poly = function (a, b) {
          var s = pts.map(function (p, k) { return (k ? "L" : "M") + p[0].toFixed(1) + "," + ey(a / 100 * p[1]).toFixed(1); }).join("");
          for (var k = pts.length - 1; k >= 0; k--) s += "L" + pts[k][0].toFixed(1) + "," + ey(b / 100 * pts[k][1]).toFixed(1);
          return s + "Z";
        };
        esv.appendChild(sv("path", { d: poly(TH.warn_pct, Math.min(TH.crit_pct, lim / (lastOff / 100))), "class": "band-warn" }));
        esv.appendChild(sv("path", { d: poly(-TH.warn_pct, -Math.min(TH.crit_pct, lim / (lastOff / 100))), "class": "band-warn" }));
        [TH.crit_pct, -TH.crit_pct].forEach(function (v) {
          up = pts.map(function (p, k) { return (k ? "L" : "M") + p[0].toFixed(1) + "," + ey(v / 100 * p[1]).toFixed(1); }).join("");
          if (Math.abs(v / 100 * pts[0][1]) <= lim) esv.appendChild(sv("path", { d: up, "class": "band-crit-line", fill: "none" }));
        });
      }
    }
    et.values.forEach(function (v) {
      esv.appendChild(svText(m.l - 6, ey(v) + 4, state.unit === "pct" ? pct(v, edec, v !== 0) : num(v, edec, v !== 0), { "text-anchor": "end" }));
    });
    esv.appendChild(sv("line", { x1: m.l, x2: m.l + iw, y1: ey(0), y2: ey(0), "class": "zero" }));
    var pe = "", pen = false, flags = [];
    for (i = r[0]; i < end; i++) {
      var v = state.unit === "pct" ? S.dpct[i] : S.dpts[i];
      if (S.valid[i] && fin(v)) {
        pe += (pen ? "L" : "M") + x(T[i]).toFixed(1) + "," + ey(Math.max(-lim, Math.min(lim, v))).toFixed(1); pen = true;
        var a = Math.abs(S.dpct[i]); if (a > TH.warn_pct) flags.push([i, a > TH.crit_pct ? "crit" : "warn", v]);
      } else pen = false;
    }
    esv.appendChild(sv("path", { d: pe, "class": "err-line" }));
    flags.forEach(function (f) { esv.appendChild(sv("circle", { cx: x(T[f[0]]), cy: ey(Math.max(-lim, Math.min(lim, f[2]))), r: 2.6, "class": "flag-" + f[1] })); });
    if (n > 0 && S.valid[end - 1]) {
      var lv = state.unit === "pct" ? S.dpct[end - 1] : S.dpts[end - 1];
      if (fin(lv)) {
        esv.appendChild(sv("circle", { cx: x(T[end - 1]), cy: ey(Math.max(-lim, Math.min(lim, lv))), r: 4, "class": "dot", fill: "var(--ink-2)" }));
        if (!narrow) esv.appendChild(svText(m.l + iw + 10, ey(Math.max(-lim, Math.min(lim, lv))) + 4, state.unit === "pct" ? pct(lv, 3, true) : num(lv, 2, true) + " p", { "class": "endlabel" }));
      }
    }

    // crosshair layers
    var ch1 = sv("line", { "class": "crosshair", y1: m.t, y2: m.t + ih, visibility: "hidden" });
    var ch2 = sv("line", { "class": "crosshair", y1: m.t, y2: m.t + eih, visibility: "hidden" });
    svg.appendChild(ch1); esv.appendChild(ch2);
    [svg, esv].forEach(function (s, k) {
      var hit = sv("rect", { x: m.l, y: 0, width: iw, height: k ? EH : H, fill: "transparent" });
      hit.addEventListener("pointermove", function (e) { hover(e, s, ch1, ch2); });
      hit.addEventListener("pointerleave", function () { ch1.setAttribute("visibility", "hidden"); ch2.setAttribute("visibility", "hidden"); $("tooltip").hidden = true; });
      s.appendChild(hit);
    });

    host.replaceChildren(svg); eh.replaceChildren(esv);
    $("err-key").textContent = state.unit === "pct"
      ? "Sarı bant: ±" + pct(TH.warn_pct, 2) + " – ±" + pct(TH.crit_pct, 2) + " (dikkat) · kırmızı kesik çizgi: ±" + pct(TH.crit_pct, 2) + " (sapma)"
      : "Bantlar puan cinsinden aynı eşikler: ±" + pct(TH.warn_pct, 2) + " ve ±" + pct(TH.crit_pct, 2) + " × resmî değer";
  }

  function hover(e, s, ch1, ch2) {
    var dc = cacheDay(), r = dc.r, end = r[0] + state.n; if (state.n === 0) return;
    var box = s.getBoundingClientRect(), scale = geom.W / box.width, px = (e.clientX - box.left) * scale;
    var t = dc.x0 + (px - geom.m.l) / geom.iw * (dc.x1 - dc.x0);
    var lo = r[0], hi = end - 1;
    while (lo < hi) { var mid = (lo + hi) >> 1; if (T[mid] < t) lo = mid + 1; else hi = mid; }
    var i = lo; if (i > r[0] && Math.abs(T[i - 1] - t) < Math.abs(T[i] - t)) i = i - 1;
    var X = geom.x(T[i]);
    [ch1, ch2].forEach(function (c) { c.setAttribute("x1", X); c.setAttribute("x2", X); c.setAttribute("visibility", "visible"); });
    var tip = $("tooltip"), st = rowStatus(i);
    tip.replaceChildren(el("div", { cls: "tooltip__t", text: dmy(S.t[i]) + " " + hhmm(S.t[i]) }),
      tipRow("var(--calc)", "Hesaplanan", S.valid[i] ? num(S.calc[i], 2) : "(" + num(S.unchecked[i], 2) + ")"),
      tipRow("var(--off)", "Gerçekleşen", num(S.off[i], 2)),
      tipRow(null, "Fark", S.valid[i] ? num(S.dpts[i], 2, true) + " p · " + pct(S.dpct[i], 3, true) : "–"),
      el("div", { cls: "tooltip__note" }, [icon(st.ico), " " + st.label]));
    tip.hidden = false;
    var wrap = tip.parentElement.getBoundingClientRect(), left = e.clientX - wrap.left + 14;
    if (left + 220 > wrap.width) left = e.clientX - wrap.left - 230;
    tip.style.left = Math.max(4, left) + "px";
    tip.style.top = (e.clientY - wrap.top + 12) + "px";
  }

  function renderLegend() {
    var L = $("legend");
    var items = [["var(--calc)", "Hesaplanan (bizim)"], ["var(--off)", "Gerçekleşen (resmî XU030)"]];
    items.forEach(function (it) { var sw = el("span", { cls: "legend__swatch" }); sw.style.borderTopColor = it[0]; L.appendChild(el("span", { cls: "legend__item" }, [sw, it[1]])); });
    var dsh = el("span", { cls: "legend__swatch legend__swatch--dash" }); dsh.style.borderTopColor = "var(--ink-3)";
    L.appendChild(el("span", { cls: "legend__item" }, [dsh, "geçersiz dakikada kontrolsüz değer"]));
    var bd = el("span", { cls: "legend__swatch legend__swatch--band" }); bd.style.background = "var(--invalid-band)";
    L.appendChild(el("span", { cls: "legend__item" }, [bd, "geçersiz dakika"]));
  }

  // ------------------------------------------------------------------ tape
  function tapeRow(i, fresh) {
    var st = rowStatus(i), valid = S.valid[i];
    var tr = el("tr", { cls: "r-" + st.cls + (fresh ? " is-new" : "") }, [
      el("td", { text: hhmm(S.t[i]) }),
      el("td", { cls: "num", text: valid ? num(S.calc[i], 2) : (fin(S.unchecked[i]) ? "(" + num(S.unchecked[i], 2) + ")" : "–") }),
      el("td", { cls: "num", text: num(S.off[i], 2) }),
      el("td", { cls: "num", text: valid ? num(S.dpts[i], 2, true) : "–" }),
      el("td", { cls: "num", text: valid ? pct(S.dpct[i], 3, true) : "–" }),
      el("td", { cls: "st" }, [icon(st.ico), st.label])
    ]);
    return tr;
  }
  function renderTape(full) {
    var body = $("tape-body"), r = dayRange(), n = state.n;
    if (full || n < state.tapeN) {
      var frag = document.createDocumentFragment();
      for (var i = r[0] + n - 1; i >= r[0]; i--) frag.appendChild(tapeRow(i, false));
      body.replaceChildren(frag);
    } else {
      for (var k = r[0] + state.tapeN; k < r[0] + n; k++) body.insertBefore(tapeRow(k, !reduceMotion), body.firstChild);
      var stale = body.querySelectorAll("tr.is-new");
      for (var q = 6; q < stale.length; q++) stale[q].classList.remove("is-new");
    }
    state.tapeN = n;
    var flagged = 0, invalid = 0;
    for (var j = r[0]; j < r[0] + n; j++) { var s = rowStatus(j).cls; if (s === "warn" || s === "crit") flagged++; if (s === "invalid") invalid++; }
    $("tape-count").textContent = n + " / " + dayLen() + " dakika · " + flagged + " eşik üstü · " + invalid + " geçersiz";
  }

  // ------------------------------------------------------------------ method
  function renderMethod() {
    $("method-sub").textContent = "Her adım, " + C.ruleset_title + " metnine bağlı. Sayfa numaraları PDF sayfasıdır (" + C.ruleset_file + "); basılı sayfa numarası bir eksiğidir.";
    var ol = $("steps");
    C.steps.forEach(function (s) {
      var refs = el("div", { cls: "refs" }, s.refs.map(function (r) {
        return el("span", { cls: "ref", title: "Basılı sayfa " + r.printed_page, text: "Madde " + r.section + " · PDF s." + r.pdf_page });
      }));
      var body = el("div", { cls: "step__body" }, [el("div", { cls: "step__top" }, [el("h3", { cls: "step__title", text: s.title }), refs])]);
      if (s.formula) body.appendChild(el("div", { cls: "formula", text: s.formula }));
      body.appendChild(el("p", { cls: "step__plain", text: s.plain }));
      s.quotes.forEach(function (q) {
        body.appendChild(el("blockquote", { cls: "quote" }, [el("q", { text: q.text }),
          el("cite", { text: "Kural Seti, madde " + q.ref + ", PDF s." + q.pdf_page + (q.verbatim ? "" : " · tablo satırı, düzenlenmiş") })]));
      });
      body.appendChild(el("p", { cls: "step__ours" }, [el("b", { text: "Biz: " }), s.ours]));
      ol.appendChild(el("li", { cls: "step" }, [body]));
    });
    var dh = $("diffs");
    C.differences.forEach(function (d) { dh.appendChild(el("div", { cls: "diff" }, [el("h4", { text: d.title }), el("p", { text: d.text })])); });
  }

  // ------------------------------------------------------------------ constituents
  function renderConstituents() {
    var D = P.divisor, rows = P.constituents;
    $("cons-sub").textContent = rows.length + " hisse, kalibrasyon anındaki (" + dmy(D.reference_ts) + " " + hhmm(D.reference_ts) + ") fiyat ve ağırlıklarıyla. Ağırlık = F·N·H·K ÷ Σ F·N·H·K. " + (META.metadata_label || "");
    var box = $("divisor-box");
    var items = [["Bölen (kalibrasyon)", num(D.value, 8)], ["Kalibrasyon anı", dmy(D.reference_ts) + " " + hhmm(D.reference_ts) + " · günlük kapanış barı"],
      ["Resmî XU030 (o an)", num(D.reference_official_value, 2)], ["Σ F·N·H·K (o an)", num(D.reference_raw_mcap, 0) + " TL"],
      ["Yöntem", D.method === "calibrated" ? "Bir kez kalibre edildi (B = Σ ÷ E_resmî)" : D.method]];
    items.forEach(function (it) { box.appendChild(el("div", null, [el("dt", { text: it[0] }), el("dd", { text: it[1] })])); });
    var ev = (META.divisor_events || []).filter(function (e) { return e.changed; });
    var evText = ev.length ? ev.map(function (e) { return dmy(e.date) + ": " + e.text + " → B = " + num(e.divisor, 8); }).join(" · ") : "Dönem içinde bölen düzeltmesi yok.";
    var info = (META.divisor_events || []).filter(function (e) { return !e.changed; }).map(function (e) { return dmy(e.date) + " " + e.text; });
    box.appendChild(el("div", { cls: "events" }, [el("dt", { text: "Kural 5.1 düzeltmeleri" }), el("dd", { text: evText + (info.length ? " Bölene etkisi olmayan olaylar: " + info.join("; ") + "." : "") })]));
    var tb = document.querySelector("#cons-table tbody"), maxW = Math.max.apply(null, rows.map(function (r) { return r.weight_pct || 0; }));
    rows.forEach(function (r, i) {
      var fill = el("span", { cls: "wbar__fill" }); fill.style.width = ((r.weight_pct || 0) / maxW * 100).toFixed(1) + "%";
      var hCell = el("td", { cls: "num", text: "%" + num(r.H * 100, r.H < 0.01 ? 2 : 0) });
      (r.changes || []).forEach(function (c) { hCell.appendChild(el("span", { cls: "note-chip", text: dm(c.from) + "'dan %" + num(c.H * 100, 0) })); });
      tb.appendChild(el("tr", null, [el("td", { cls: "num", text: String(i + 1) }), el("td", { cls: "code", text: r.symbol }), el("td", { cls: "name", text: r.name }),
        el("td", { cls: "num", text: int(r.N) }), hCell, el("td", { cls: "num", text: num(r.K, 0) }), el("td", { cls: "num", text: num(r.price_cal, 2) }),
        el("td", { cls: "num" }, [el("span", { cls: "wbar" }, [el("span", { cls: "wbar__track" }, [fill]), el("span", { text: pct(r.weight_pct, 2) })])])]));
    });
  }

  // ------------------------------------------------------------------ data quality
  function hbars(host, items, total) {
    var max = Math.max.apply(null, items.map(function (i) { return i[1]; }).concat([1]));
    items.forEach(function (it) {
      var f = el("span", { cls: "hbar__fill" }); f.style.width = (it[1] / max * 100).toFixed(1) + "%";
      host.appendChild(el("div", { cls: "hbar" }, [el("span", { cls: "hbar__label", title: it[0], text: it[0] }), el("span", { cls: "hbar__track" }, [f]),
        el("span", { cls: "hbar__v", text: int(it[1]) + (total ? " · %" + num(it[1] / total * 100, 1) : "") })]));
    });
    if (!items.length) host.appendChild(el("p", { cls: "fine", text: "Kayıt yok." }));
  }
  function renderQuality() {
    var I = M.invalid, n = M.n_minutes_oos;
    $("dq-sub").textContent = "Örneklem dışı " + int(n) + " dakikanın " + int(I.n_invalid) + " tanesi (%" + num(I.n_invalid / n * 100, 1) + ") geçersiz. " +
      int(I.n_no_official) + " geçerli dakikada aynı dakikaya ait resmî değer yok. Geçersiz dakikalardaki kontrolsüz değerin resmî değerden ortalama mutlak farkı " + pct(I.unchecked_mae_pct, 3) + " (metriklere girmez).";
    var reasons = Object.keys(I.by_reason).map(function (k) { return [C.reason_labels[k] || k, I.by_reason[k]]; });
    hbars($("dq-reasons"), reasons, n);
    hbars($("dq-symbols"), I.by_symbol.slice(0, 10).map(function (r) {
      var parts = Object.keys(r).filter(function (k) { return k !== "symbol" && k !== "total"; }).map(function (k) { return C.reason_labels[k] || k; });
      return [r.symbol + " · " + parts.join(", "), r.total];
    }), null);
    var st = M.staleness, dl = $("dq-stale");
    [["Medyan en eski fiyat", dur(st.median_max_staleness_s)], ["95. yüzdelik en eski fiyat", dur(st.p95_max_staleness_s)],
     ["Medyan ağırlıklı tazelik", dur(st.median_weighted_staleness_s)], ["95. yüzdelik ağırlıklı tazelik", dur(st.p95_weighted_staleness_s)],
     ["En az bir eski fiyat olan dakika", "%" + num(st.pct_rows_with_stale, 1)], ["Eskime eşiği", dur(TH.staleness_s)]]
      .forEach(function (p) { dl.appendChild(el("dt", { text: p[0] })); dl.appendChild(el("dd", { text: p[1] })); });

    var cc = M.close_check || [];
    var oc = cc.filter(function (r) { return !r.calibration_day && fin(r.diff_pct); });
    var mc = oc.length ? oc.reduce(function (a, r) { return a + r.diff_pct; }, 0) / oc.length : null;
    $("close-sub").textContent = "1 dakikalık veride kapanış müzayedesi yok. Bu yüzden her günün resmî kapanışını, aynı bölenle ve günlük kapanış fiyatlarıyla (müzayede dahil) ayrıca hesapladık. " +
      "Örneklem dışı günlerde ortalama kapanış farkı " + pct(mc, 3, true) + ". Bu fark dakika serisindeki farka yakınsa sapmanın kaynağı ağırlıklardır (N, H), zamanlama değil.";
    var tb = document.querySelector("#close-table tbody");
    cc.forEach(function (r) {
      tb.appendChild(el("tr", null, [el("td", { text: dmy(r.date) + (r.calibration_day ? " (kalibrasyon)" : "") }), el("td", { cls: "num", text: num(r.official_close, 2) }),
        el("td", { cls: "num", text: num(r.calc_close, 2) }), el("td", { cls: "num", text: num(r.diff_pts, 2, true) }), el("td", { cls: "num", text: pct(r.diff_pct, 3, true) }),
        el("td", { cls: "num", text: pct(r.last_1m_diff_pct, 3, true) })]));
    });
    var ul = $("limits");
    (META.limitations || []).forEach(function (l) { ul.appendChild(el("li", null, [el("b", { text: l.title + ". " }), l.text])); });
  }

  function renderFooter() {
    var f = $("footer");
    f.appendChild(el("p", { text: "Üretildi: " + (META.generated_at ? dmy(META.generated_at) + " " + hhmm(META.generated_at) : "–") + " (İstanbul) · " + (META.generator || "") }));
    f.appendChild(el("p", { text: "Bu sayfa bağımsız bir replikasyon testidir; Borsa İstanbul ile bağlantısı yoktur. Resmî endeks değerleri Yahoo Finance üzerinden alınmıştır. Yatırım tavsiyesi değildir." }));
  }

  // ------------------------------------------------------------------ wiring
  function init() {
    renderHero(); renderDayChips(); renderLegend(); renderMethod(); renderConstituents(); renderQuality(); renderFooter();
    renderDaily();
    $("btn-play").addEventListener("click", function () { state.playing ? pause() : play(); });
    $("btn-restart").addEventListener("click", function () { pause(); state.n = 0; state.tSim = T[dayRange()[0]] - 60000; $("scrubber").value = 0; renderTape(true); drawCharts(); updateClock(); });
    $("speed").addEventListener("change", function (e) { state.speed = +e.target.value; });
    $("scrubber").addEventListener("input", function (e) {
      state.n = +e.target.value; var r = dayRange(); state.tSim = state.n ? T[r[0] + state.n - 1] : T[r[0]] - 60000;
      renderTape(false); drawCharts(); updateClock();
    });
    ["unit-pct", "unit-pts"].forEach(function (id) {
      $(id).addEventListener("click", function (e) {
        state.unit = e.currentTarget.getAttribute("data-unit");
        document.querySelectorAll(".seg__b").forEach(function (b) { b.setAttribute("aria-checked", b === e.currentTarget ? "true" : "false"); });
        drawCharts();
      });
    });
    var start = 0;
    try { var h = (location.hash || "").replace("#", ""); var k = P.days.findIndex(function (d) { return d.date === h; }); if (k >= 0) start = k; } catch (err) { /* ignore */ }
    selectDay(start, false);
    var rt = null;
    window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(function () { renderDaily(); drawCharts(); }, 120); });
  }
  init();
})();

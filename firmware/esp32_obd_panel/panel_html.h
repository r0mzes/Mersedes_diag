// Сгенерировано tools/gen_panel_assets.py из vito_diag/data/panel.html — не править руками.
#pragma once
#ifndef PROGMEM
#define PROGMEM
#endif
static const char PANEL_HTML[] PROGMEM = R"PANEL(<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#111418">
<title>vito_diag — приборка</title>
<style>
  :root {
    --bg: #f4f5f7; --card: #ffffff; --text: #16191d; --muted: #6b7280; --line: #e3e6ea;
    --accent: #1565c0; --bad: #c62828; --ok: #2e7d32; --warn: #b26a00; --graph: #1565c0;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #111418; --card: #1a1f25; --text: #e8eaed; --muted: #9aa3ad; --line: #2a3139;
      --accent: #64a8ff; --bad: #ff6b6b; --ok: #5fd068; --warn: #f0b04a; --graph: #64a8ff;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 15px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  header { position: sticky; top: 0; z-index: 2; background: var(--card); border-bottom: 1px solid var(--line);
           padding: 10px 16px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--muted); flex: none; }
  .dot.online { background: var(--ok); } .dot.lost { background: var(--bad); }
  .dot.connecting { background: var(--warn); animation: blink 1s infinite; }
  @keyframes blink { 50% { opacity: .3; } }
  h1 { font-size: 16px; margin: 0; font-weight: 600; }
  #status { color: var(--muted); font-size: 13px; }
  header .sp { flex: 1; }
  button { font: inherit; background: none; color: var(--accent); border: 1px solid var(--line);
           border-radius: 8px; padding: 6px 12px; cursor: pointer; }
  main { padding: 12px 16px 32px; max-width: 1200px; margin: 0 auto; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(165px, 1fr)); gap: 10px; }
  .tile { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px 6px; }
  .tile .lbl { color: var(--muted); font-size: 13px; min-height: 2.6em; }
  .tile .val { font-size: 34px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.1; }
  .tile .unit { font-size: 14px; color: var(--muted); font-weight: 400; margin-left: 4px; }
  .tile.bad .val { color: var(--bad); }
  .tile.stale { opacity: .45; }
  .tile .age { font-size: 11px; color: var(--muted); height: 14px; }
  .tile canvas { width: 100%; height: 42px; display: block; }
  section.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
                 padding: 12px 14px; margin-top: 12px; }
  section.card h2 { font-size: 14px; margin: 0 0 6px; color: var(--muted); font-weight: 600; }
  ul { margin: 0; padding-left: 18px; }
  li { margin: 3px 0; }
  .code { font-weight: 700; font-variant-numeric: tabular-nums; }
  .muted { color: var(--muted); font-size: 13px; }
  #settings { display: none; }
  #settings.open { display: block; }
  #settings label { display: inline-flex; gap: 6px; align-items: center; margin: 4px 14px 4px 0; }
  .banner { display: none; background: var(--bad); color: #fff; padding: 8px 16px; font-size: 14px; }
  .banner.show { display: block; }
  @media (max-width: 480px) {
    .tiles { grid-template-columns: repeat(2, 1fr); }
    .tile .val { font-size: 28px; }
    header { padding: 8px 12px; } main { padding: 10px 12px 28px; }
  }
</style>
</head>
<body>
<header>
  <span id="dot" class="dot connecting"></span>
  <div>
    <h1 id="title">vito_diag</h1>
    <div id="status">подключение…</div>
  </div>
  <span class="sp"></span>
  <button id="gear" type="button">Плитки</button>
</header>
<div id="banner" class="banner"></div>
<main>
  <section id="settings" class="card">
    <h2>Какие плитки показывать</h2>
    <div id="choices"></div>
  </section>
  <div id="tiles" class="tiles"></div>
  <section class="card" id="hintsCard">
    <h2>Подсказки</h2>
    <ul id="hints"><li class="muted">появятся после первых замеров</li></ul>
  </section>
  <section class="card">
    <h2>Блок и ошибки</h2>
    <div id="ecu" class="muted">—</div>
    <ul id="dtcs"></ul>
  </section>
  <p class="muted" id="foot"></p>
</main>
<script>
"use strict";
const HISTORY = 120;
const series = {};         // имя -> массив последних значений
let state = null, chosen = null, built = "";

function store(key, value) {
  try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); }
  catch (e) { return null; }
}
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c])); }
function fmt(v) {
  const a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
}

function tilesWanted() {
  if (chosen) return chosen;
  const saved = store("tiles_" + state.profile);
  if (saved) { try { chosen = JSON.parse(saved); return chosen; } catch (e) {} }
  const all = Object.keys(state.values);
  chosen = state.default_tiles.filter(n => all.includes(n));
  if (!chosen.length) chosen = all.slice(0, 8);
  return chosen;
}

function buildTiles() {
  const names = tilesWanted().filter(n => state.values[n]);
  const key = names.join(",");
  if (key === built) return;
  built = key;
  document.getElementById("tiles").innerHTML = names.map(n =>
    `<div class="tile" id="t_${n}"><div class="lbl">${esc(state.values[n].label)}</div>
     <div class="val"><span>—</span><span class="unit">${esc(state.values[n].unit)}</span></div>
     <canvas></canvas><div class="age"></div></div>`).join("");
}

function buildChoices() {
  const names = Object.keys(state.values);
  const box = document.getElementById("choices");
  box.innerHTML = names.map(n => `<label><input type="checkbox" value="${n}" ${tilesWanted().includes(n) ? "checked" : ""}>
     ${esc(state.values[n].label)}</label>`).join("") || '<span class="muted">данных ещё нет</span>';
  box.querySelectorAll("input").forEach(cb => cb.onchange = () => {
    chosen = [...box.querySelectorAll("input:checked")].map(x => x.value);
    store("tiles_" + state.profile, JSON.stringify(chosen));
    built = ""; render();
  });
}

function draw(canvas, values) {
  const w = canvas.clientWidth, h = canvas.clientHeight, r = window.devicePixelRatio || 1;
  if (canvas.width !== w * r) { canvas.width = w * r; canvas.height = h * r; }
  const g = canvas.getContext("2d");
  g.setTransform(r, 0, 0, r, 0, 0);
  g.clearRect(0, 0, w, h);
  if (values.length < 2) return;
  let lo = Math.min(...values), hi = Math.max(...values);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const css = getComputedStyle(document.documentElement);
  g.strokeStyle = css.getPropertyValue("--graph").trim();
  g.lineWidth = 2;
  g.beginPath();
  values.forEach((v, i) => {
    const x = i * (w - 36) / (HISTORY - 1), y = h - 3 - (v - lo) / (hi - lo) * (h - 6);
    i ? g.lineTo(x, y) : g.moveTo(x, y);
  });
  g.stroke();
  g.fillStyle = css.getPropertyValue("--muted").trim();
  g.font = "10px system-ui"; g.textAlign = "right";
  g.fillText(fmt(hi), w - 2, 10); g.fillText(fmt(lo), w - 2, h - 2);
}

function render() {
  const s = state;
  document.getElementById("dot").className = "dot " + s.status;
  document.getElementById("title").textContent = s.info.title || "vito_diag";
  document.getElementById("status").textContent = s.message + (s.samples ? ` · замеров ${s.samples}` : "")
    + (s.reconnects ? ` · переподключений ${s.reconnects}` : "");
  const banner = document.getElementById("banner");
  banner.textContent = s.status === "lost" ? "Нет связи с блоком: " + s.message + ". Показаны последние значения." : "";
  banner.className = "banner" + (s.status === "lost" ? " show" : "");
  buildTiles();
  for (const n of tilesWanted()) {
    const v = s.values[n], el = document.getElementById("t_" + n);
    if (!v || !el) continue;
    el.className = "tile" + (v.bad ? " bad" : "") + (v.stale ? " stale" : "");
    el.querySelector(".val span").textContent = fmt(v.value);
    el.querySelector(".age").textContent = v.stale ? `устарело: ${Math.round(v.age)} с назад` : "";
    draw(el.querySelector("canvas"), series[n] || []);
  }
  const hints = s.hints.length ? s.hints : [];
  document.getElementById("hints").innerHTML = (s.info.fuel_system ? `<li class="muted">Топливная система: ${esc(s.info.fuel_system)}</li>` : "")
    + (hints.map(h => `<li>${esc(h)}</li>`).join("") || '<li class="muted">появятся после первых замеров</li>');
  const i = s.info;
  document.getElementById("ecu").innerHTML = [
    i.ecu && `номер блока <b>${esc(i.ecu)}</b>`, i.vin && `VIN ${esc(i.vin)}`,
    i.adapter && `адаптер ${esc(i.adapter)}`, i.voltage && `напряжение ${esc(i.voltage)}`,
  ].filter(Boolean).join(" · ") || "—";
  const d = i.dtcs || [];
  document.getElementById("dtcs").innerHTML = i.dtc_error ? `<li>ошибки не прочитаны: ${esc(i.dtc_error)}</li>`
    : d.length ? d.map(x => `<li><span class="code">${esc(x.code)}</span> — ${esc(x.description)}
       <span class="muted">${esc(x.status)}</span></li>`).join("")
    : (s.status === "online" || s.samples ? '<li>ошибок нет</li>' : "");
  document.getElementById("foot").textContent = (i.notes || []).join(" ")
    + (s.csv ? ` Запись: ${s.csv}` : "");
}

function push(snapshot) {
  const fresh = snapshot.samples !== (state && state.samples);
  state = snapshot;
  if (fresh) for (const [n, v] of Object.entries(snapshot.values)) {
    if (v.stale) continue;
    (series[n] = series[n] || []).push(v.value);
    if (series[n].length > HISTORY) series[n].shift();
  }
  render();
}

document.getElementById("gear").onclick = () => {
  const box = document.getElementById("settings");
  box.classList.toggle("open");
  if (box.classList.contains("open") && state) buildChoices();
};

fetch("/api/state").then(r => r.json()).then(s => {
  for (const h of (s.history || []).slice(-HISTORY))
    for (const [n, v] of Object.entries(h)) if (n !== "t") (series[n] = series[n] || []).push(v);
  state = s; render();
}).catch(() => {}).finally(() => {
  const es = new EventSource("/api/stream");
  es.onmessage = e => push(JSON.parse(e.data));
  es.onerror = () => {
    document.getElementById("dot").className = "dot lost";
    document.getElementById("status").textContent = "нет связи с программой на ноутбуке — переподключение…";
  };
});
window.addEventListener("resize", () => state && render());
</script>
</body>
</html>
)PANEL";

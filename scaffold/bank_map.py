"""Temporary exploration page: an animated map of US banks being absorbed, 1970 to today (James, 2026-11-16).

Reads the same FDIC BankFind files as bank_mergers.py, from <data root>/tmp/scaffold/banks/fdic/ (quick lane, nothing stored):
  institutions.csv   one row per institution ever insured, with coordinates
  events_200.csv     one row per institution disappearing
Each institution with coordinates is a dot from its founding date to its end date. When it is absorbed, the dot travels to
the acquirer and the acquirer's dot grows with the number of institutions inside it. Dots are coloured by the bank they end
up inside today, for the ten largest family trees. Charter level only. State outlines come from the us-atlas package.
Every figure is computed here. Writes bank-map.html.  Run: uv run python scaffold/bank_map.py"""
import json
from pathlib import Path

import duckdb
from common import esc, now_et, page

from pharos.paths import data_root

HERE = Path(__file__).parent
FDIC = data_root() / "tmp" / "scaffold" / "banks" / "fdic"
LIBS = ('<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>'
        '<script src="https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js"></script>')
ATLAS = "https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/states-10m.json"
T0, TOP = 1970, 10
COLORS = ["#6b7280", "#eab308", "#2563eb", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d", "#92400e", "#111827"]
con = duckdb.connect()


def q(sql):
    return con.execute(sql).fetchall()


for t in ("institutions", "events_200"):
    con.execute(f"CREATE TABLE {t} AS SELECT * FROM read_csv('{(FDIC / (t + '.csv')).as_posix()}', header=true, all_varchar=true)")
YF = "year({0}) + (dayofyear({0}) - 1) / 365.0"
con.execute("""CREATE TABLE ev AS SELECT UNINUM AS o, nullif(ACQ_UNINUM, '0') AS a, EFFDATE[:10]::DATE AS dt, CHANGECODE::INT AS code,
               coalesce(nullif(OUT_INSTNAME, ''), INSTNAME) AS name, upper(coalesce(nullif(OUT_PCITY, ''), PCITY)) AS city,
               coalesce(nullif(OUT_PSTALP, ''), PSTALP) AS st
               FROM events_200 QUALIFY row_number() OVER (PARTITION BY UNINUM ORDER BY (nullif(ACQ_UNINUM, '0') IS NOT NULL) DESC, EFFDATE DESC) = 1""")
con.execute("""CREATE TABLE raw AS SELECT UNINUM AS u, NAME, ACTIVE = '1' AS active, strptime(ESTYMD, '%m/%d/%Y')::DATE AS est,
               CASE WHEN ACTIVE = '1' THEN NULL ELSE strptime(ENDEFYMD, '%m/%d/%Y')::DATE END AS fin, upper(CITY) AS city, STALP AS st,
               nullif(try_cast(LONGITUDE AS DOUBLE), 0) AS lon, nullif(try_cast(LATITUDE AS DOUBLE), 0) AS lat FROM institutions""")
# a bank with no coordinates is placed at the average position of the other institutions in its city
con.execute("CREATE TABLE town AS SELECT city, st, avg(lon) AS lon, avg(lat) AS lat FROM raw WHERE lon IS NOT NULL AND lat IS NOT NULL GROUP BY ALL")
con.execute("""CREATE TABLE inst AS
               SELECT r.u, r.NAME, r.active, r.est, r.fin, coalesce(r.lon, t.lon, 0) AS lon, coalesce(r.lat, t.lat, 0) AS lat,
                      (r.lon IS NULL AND t.lon IS NOT NULL) AS placed, false AS event_only
               FROM raw r LEFT JOIN town t ON t.city = r.city AND t.st = r.st
               UNION ALL
               SELECT ev.o, ev.name, false, DATE '1900-01-01', ev.dt, coalesce(t.lon, 0), coalesce(t.lat, 0), t.lon IS NOT NULL, true
               FROM ev LEFT JOIN town t ON t.city = ev.city AND t.st = ev.st WHERE ev.o NOT IN (SELECT u FROM raw)""")
# the bank each one ends up inside
con.execute("CREATE TABLE e AS SELECT o, a, dt FROM ev WHERE a IS NOT NULL AND a <> o")
con.execute("""CREATE TABLE lin AS WITH RECURSIVE w(anc, cur, steps) AS (
                 SELECT o, a, 1 FROM e UNION ALL SELECT w.anc, e.a, w.steps + 1 FROM w JOIN e ON e.o = w.cur WHERE w.steps < 60)
               SELECT anc, arg_max(cur, steps) AS fin FROM w GROUP BY 1""")
top = q(f"""SELECT l.fin, any_value(i.NAME), count(*) FROM lin l JOIN inst i ON i.u = l.fin WHERE i.active GROUP BY 1 ORDER BY 3 DESC LIMIT {TOP}""")
grp = {u: k + 1 for k, (u, _, _) in enumerate(top)}
con.execute("CREATE TABLE g (u VARCHAR, k INT)")
con.executemany("INSERT INTO g VALUES (?, ?)", list(grp.items()))

n_inst, n_coord, n_placed, n_event_only = q("""SELECT count(*) FILTER (NOT event_only), count(*) FILTER (lon <> 0 AND NOT placed),
    count(*) FILTER (placed), count(*) FILTER (event_only) FROM inst""")[0]
banks = q(f"""SELECT i.u, round(i.lon, 3), round(i.lat, 3), round({YF.format('i.est')}, 2),
              round({YF.format('coalesce(ev.dt, i.fin)')}, 2), coalesce(gs.k, gl.k, 0), i.NAME
              FROM inst i LEFT JOIN ev ON ev.o = i.u LEFT JOIN lin l ON l.anc = i.u LEFT JOIN g gl ON gl.u = l.fin LEFT JOIN g gs ON gs.u = i.u
              WHERE i.lon <> 0 AND i.lat <> 0 AND coalesce(ev.dt, i.fin, DATE '9999-01-01') >= DATE '{T0}-01-01' ORDER BY i.u""")
idx = {b[0]: n for n, b in enumerate(banks)}
events_all = q(f"SELECT o, a, round({YF.format('dt')}, 3), code FROM ev WHERE dt >= DATE '{T0}-01-01' ORDER BY dt, o")
events, n_no_dot, n_no_acq_dot = [], 0, 0
for o, a, t, code in events_all:
    if o not in idx:
        n_no_dot += 1
        continue
    if a is not None and a not in idx:
        n_no_acq_dot += 1
    fail = 1 if code in (211, 213, 215, 216, 217, 230, 235) else 0
    events.append([t, idx[o], idx.get(a, -1) if a is not None else -1, fail])
t_end = max(e[0] for e in events)
t_last = -(-round(t_end * 1000) // 50) / 20  # the slider stops at the last event, rounded up to its 0.05 step
data = {"lon": [b[1] for b in banks], "lat": [b[2] for b in banks], "s": [b[3] for b in banks], "e": [b[4] for b in banks],
        "g": [b[5] for b in banks], "ev": events, "top": [[idx[u], name, n] for u, name, n in top], "t0": T0, "t1": t_last,
        "col": COLORS, "atlas": ATLAS}
alive0 = sum(1 for b in banks if b[3] <= T0 and (b[4] is None or b[4] > T0))
alive1 = sum(1 for b in banks if b[4] is None)
# marked inactive by the FDIC with an end date of 12/31/9999 and no event: they never leave the map
no_end = sum(1 for b in banks if b[4] is not None and b[4] > t_last)

script = "const D = " + json.dumps(data, separators=(",", ":")) + ";" + r"""
const cv = document.getElementById('map'), ctx = cv.getContext('2d'), W = cv.width, H = cv.height;
const proj = d3.geoAlbersUsa().scale(1500).translate([W / 2, H / 2 - 10]);
const N = D.lon.length, X = new Float32Array(N), Y = new Float32Array(N), OK = new Uint8Array(N), size = new Int32Array(N), fam = new Int32Array(D.col.length);
for (let i = 0; i < N; i++) { const p = proj([D.lon[i], D.lat[i]]); if (p) { X[i] = p[0]; Y[i] = p[1]; OK[i] = 1; } }
const END = D.e.map(v => v == null ? 1e9 : v), FLY = 0.8;
let T = D.t0, playing = false, speed = 2, last = null, ptr = 0, sizedTo = -1, states = null;
const bg = document.createElement('canvas'); bg.width = W; bg.height = H;
d3.json(D.atlas).then(us => {
  const b = bg.getContext('2d'), path = d3.geoPath(proj, b);
  b.beginPath(); path(topojson.feature(us, us.objects.nation)); b.fillStyle = '#f6f5f1'; b.fill();
  b.beginPath(); path(topojson.mesh(us, us.objects.states)); b.strokeStyle = '#cfccc2'; b.lineWidth = 0.8; b.stroke();
  states = true; draw();
}).catch(() => { document.getElementById('note').textContent = 'State outlines did not load (no network); dots are still drawn.'; draw(); });
function sizesAt(t) {
  if (t < sizedTo) { size.fill(0); fam.fill(0); ptr = 0; }
  while (ptr < D.ev.length && D.ev[ptr][0] <= t) { const e = D.ev[ptr++]; if (e[2] >= 0) { size[e[2]] += size[e[1]] + 1; fam[D.g[e[1]]]++; } }
  sizedTo = t;
}
const rad = n => n === 0 ? 1.3 : Math.min(1.6 + 0.85 * Math.sqrt(n), 34);
function draw() {
  sizesAt(T);
  ctx.clearRect(0, 0, W, H); ctx.drawImage(bg, 0, 0);
  let alive = 0; const big = [];
  ctx.globalAlpha = 0.6;
  for (let i = 0; i < N; i++) {
    if (D.s[i] > T || END[i] <= T) continue;
    alive++;
    if (!OK[i]) continue;
    if (size[i] >= 4) { big.push(i); continue; }
    ctx.fillStyle = D.col[D.g[i]]; const r = rad(size[i]); ctx.fillRect(X[i] - r, Y[i] - r, 2 * r, 2 * r);
  }
  big.sort((a, b) => size[b] - size[a]);
  for (const i of big) {
    ctx.globalAlpha = 0.55; ctx.fillStyle = D.col[D.g[i]]; ctx.beginPath(); ctx.arc(X[i], Y[i], rad(size[i]), 0, 6.2832); ctx.fill();
    ctx.globalAlpha = 0.9; ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 0.7; ctx.stroke();
  }
  // institutions in flight to their acquirer, and failures flashing out
  let lo = 0, hi = D.ev.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (D.ev[m][0] < T - FLY) lo = m + 1; else hi = m; }
  for (let k = lo; k < D.ev.length && D.ev[k][0] <= T; k++) {
    const [t, o, a, f] = D.ev[k], u = (T - t) / FLY;
    if (!OK[o]) continue;
    if (a >= 0 && OK[a]) {
      const w = u * u * (3 - 2 * u), x = X[o] + (X[a] - X[o]) * w, y = Y[o] + (Y[a] - Y[o]) * w;
      ctx.globalAlpha = 0.25; ctx.strokeStyle = D.col[D.g[o]]; ctx.lineWidth = 0.8; ctx.beginPath(); ctx.moveTo(X[o], Y[o]); ctx.lineTo(x, y); ctx.stroke();
      ctx.globalAlpha = 0.95; ctx.fillStyle = f ? '#dc2626' : D.col[D.g[o]]; ctx.beginPath(); ctx.arc(x, y, 2.2, 0, 6.2832); ctx.fill();
    } else if (f) {
      ctx.globalAlpha = 1 - u; ctx.strokeStyle = '#dc2626'; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(X[o], Y[o], 2 + 9 * u, 0, 6.2832); ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
  ctx.fillStyle = '#1d1d1b'; ctx.font = '600 54px system-ui, sans-serif'; ctx.fillText(Math.floor(T), 28, 70);
  ctx.font = '18px system-ui, sans-serif'; ctx.fillText(alive.toLocaleString() + ' institutions', 30, 98);
  document.getElementById('legend').innerHTML = D.top.map((r, k) =>
    `<tr><td><span style="display:inline-block;width:12px;height:12px;border-radius:6px;background:${D.col[k + 1]}"></span></td><td>${r[1]}</td>` +
    `<td class=num>${fam[k + 1].toLocaleString()}</td><td class=num>${r[2].toLocaleString()}</td></tr>`).join('');
  slider.value = T;
}
const slider = document.getElementById('year'), btn = document.getElementById('play');
slider.min = D.t0; slider.max = D.t1; slider.step = 0.05;
function tick(now) {
  if (!playing) return;
  if (last != null) T = Math.min(D.t1, T + (now - last) / 1000 * speed);
  last = now; draw();
  if (T >= D.t1) { playing = false; btn.textContent = 'Play again'; return; }
  requestAnimationFrame(tick);
}
btn.addEventListener('click', () => {
  if (!playing && T >= D.t1) T = D.t0;
  playing = !playing; btn.textContent = playing ? 'Pause' : 'Play'; last = null;
  if (playing) requestAnimationFrame(tick);
});
slider.addEventListener('input', () => { T = +slider.value; draw(); });
document.getElementById('speed').addEventListener('change', ev => { speed = +ev.target.value; });
const m = /t=([0-9.]+)/.exec(location.hash); if (m) T = +m[1];
draw();
"""

body = f"""<h1>Banks being absorbed, {T0} to today</h1>
<p class=sub>Temporary exploration page, built {now_et()}. Source: FDIC BankFind. Each dot is one insured institution at its head office.
When it is absorbed, the dot travels to the bank that absorbed it, and that bank's circle grows with the number of institutions inside it.
Red marks a failure. Colours show which of today's ten largest family trees a bank ends up in; grey is every other bank.</p>
<p><button id=play style="padding:6px 16px;font:inherit">Play</button>
<input id=year type=range style="width:55%;vertical-align:middle">
<select id=speed style="padding:6px;font:inherit"><option value=1>1 year a second</option><option value=2 selected>2 years a second</option>
<option value=4>4 years a second</option><option value=8>8 years a second</option></select></p>
<div style="display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap">
<canvas id=map width=1180 height=720 style="width:100%;max-width:1180px;border:1px solid #e3e1da;background:#ffffff"></canvas>
<div><table><tr><th></th><th>Ends up inside</th><th>Of its family tree, absorbed so far</th><th>By today</th></tr><tbody id=legend></tbody></table></div></div>
<p class=sub id=note></p>

<h2>What is drawn</h2>
<table><tr><th>Item</th><th>Count</th><th>Note</th></tr>
<tr><td>Institutions in the FDIC file</td><td class=num>{n_inst:,}</td><td>{n_coord:,} have coordinates</td></tr>
<tr><td>Placed by city</td><td class=num>{n_placed:,}</td><td>No coordinates in the file, so the dot sits at the average position of the other institutions in the same city.
This includes institutions that appear only in the events ({n_event_only:,} in all); those have no founding date and are drawn from the start.</td></tr>
<tr><td>Dots on the map</td><td class=num>{len(banks):,}</td><td>Institutions with a position that still existed on 1 January {T0} or opened later</td></tr>
<tr><td>Dots at the start</td><td class=num>{alive0:,}</td><td>Existing on 1 January {T0}</td></tr>
<tr><td>Dots at the end</td><td class=num>{alive1 + no_end:,}</td><td>{alive1:,} still active. The other {no_end:,} are marked inactive in the FDIC file with no end date and no event
(by name: trust companies, offices of foreign banks, finance companies), so their dots never leave the map.</td></tr>
<tr><td>Disappearances shown</td><td class=num>{len(events):,}</td><td>Of {len(events_all):,} since {T0}; {n_no_dot:,} are left out because the institution could not be placed</td></tr>
<tr><td>Absorbed with no flight drawn</td><td class=num>{n_no_acq_dot:,}</td><td>The acquirer could not be placed, so the dot just disappears</td></tr></table>
<p class=sub>Limits: this is the bank-charter level, so a bank owned by a holding company keeps its dot until its charter is merged.
A dot sits at the head office, not at branches. Circle size counts institutions absorbed, not dollars.
Institutions outside the fifty states and Washington, DC are counted but not drawn.</p>"""

out = HERE / "bank-map.html"
out.write_text(page("Banks being absorbed", "Ideas", body, crumb="Bank map", head=LIBS)
               .replace("</main></body>", f"</main><script>{script}</script></body>"), encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB): {len(banks):,} dots, {alive0:,} at start, {alive1:,} at end, {len(events):,} events "
      f"({n_no_dot:,} without a dot, {n_no_acq_dot:,} acquirer without a dot); top: {', '.join(n for _, n, _ in top[:4])}")

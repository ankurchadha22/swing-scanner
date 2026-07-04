#!/usr/bin/env python3
"""
render_site.py — output adapter: run the scan, publish docs/index.html + docs/data.json.

This file only RENDERS. The checklist logic lives in swing_scanner.py (verified,
do not modify) and the batch/caching/universe machinery in swing_scanner_scaled.py
(unchanged — the pieces are imported and orchestrated here exactly as run() does).

The published page deliberately EXCLUDES all position-sizing / account / risk
fields — sizing stays local via swing_scanner.size_position().

Run:  .venv/bin/python render_site.py       (or plain python in CI)
"""

import os, json, html, datetime as dt

from swing_scanner import add_indicators, evaluate
from swing_scanner_scaled import (
    load_universe, get_prices, MARKET_SYMBOL, SLOPE_LOOKBACK,
)

DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
INCOMPLETE_BELOW = 0.9      # same threshold as the console summary
STRETCH_NOTE_PCT = 5.0      # same "getting stretched" watch-for as the console


# ----------------------------------------------------------------------
# SCAN  (orchestrates the imported, already-QA'd pieces; mirrors run())
# ----------------------------------------------------------------------
def run_scan(use_cache=True) -> dict:
    universe = load_universe(verbose=False)

    regime_ok, mslope = None, None
    mkt = get_prices([MARKET_SYMBOL], use_cache=False, verbose=False).get(MARKET_SYMBOL)
    if mkt is not None:
        m = add_indicators(mkt); ml = m.iloc[-1]
        mslope = float((ml["sma200"] - m["sma200"].iloc[-1 - SLOPE_LOOKBACK])
                       / m["sma200"].iloc[-1 - SLOPE_LOOKBACK] * 100)
        regime_ok = bool((ml["close"] > ml["sma200"]) and (mslope > 0))

    prices = get_prices(universe, use_cache=use_cache, verbose=False)
    results = []
    for sym, df in prices.items():
        try:
            results.append(evaluate(sym, df))
        except Exception:
            continue
    return {"regime_ok": regime_ok, "mslope": mslope, "results": results,
            "universe": universe, "prices": prices}


# ----------------------------------------------------------------------
# PAYLOAD  (pure — QA'd with mocked Verdicts, no network)
# ----------------------------------------------------------------------
def build_payload(regime_ok, mslope, results, universe, prices) -> dict:
    as_of = None
    if prices:
        as_of = max(df.index[-1] for df in prices.values()).date().isoformat()

    rank = {"CANDIDATE": 0, "CANDIDATE (soft)": 1, "WATCH": 2, "FAIL": 3}
    ordered = sorted(results, key=lambda v: (rank.get(v.status, 9),
                     v.days_since_cross if v.days_since_cross is not None else 999))

    def base(v):
        row = {
            "symbol": v.symbol,
            "price": round(v.price, 2),
            "days_since_cross": v.days_since_cross,
            "rvol": round(v.rvol, 2),
            "extension_pct": round(v.extension_pct, 1),
        }
        return row

    candidates, softs = [], []
    for v in ordered:
        if v.status == "CANDIDATE":
            row = base(v)
            row["watch_for"] = (f"{v.extension_pct:+.1f}% above 21 EMA — getting stretched"
                                if v.extension_pct > STRETCH_NOTE_PCT else None)
            candidates.append(row)
        elif v.status == "CANDIDATE (soft)":
            row = base(v)
            flaws = []
            if not v.stacked: flaws.append("EMAs not stacked")
            if not v.vol_ok:  flaws.append(f"volume light {v.rvol:.2f}x")
            row["unchecked"] = ", ".join(flaws) or "marginal"
            softs.append(row)

    skipped = sorted(set(universe) - set(prices))
    return {
        "as_of": as_of,
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "regime": {
            "symbol": MARKET_SYMBOL,
            "ok": regime_ok,
            "slope_pct": round(mslope, 1) if mslope is not None else None,
        },
        "coverage": {
            "evaluated": len(results),
            "universe": len(universe),
            "skipped": skipped,
            "incomplete": len(results) < INCOMPLETE_BELOW * len(universe),
        },
        "counts": {
            "candidates": len(candidates),
            "soft": len(softs),
            "watch": sum(1 for v in results if v.status == "WATCH"),
            "fail": sum(1 for v in results if v.status == "FAIL"),
        },
        "candidates": candidates,
        "soft_candidates": softs,
    }


# ----------------------------------------------------------------------
# HTML  (pure — single static file, inline CSS, no JS, no CDNs)
# ----------------------------------------------------------------------
CSS = """
:root{
  --bg:#fbfbf9; --ink:#1c1c1a; --muted:#6d6d66; --hairline:#e4e3dc;
  --green-ink:#1e6b3f; --green-bg:#e9f3ec; --green-rule:#2c8a52;
  --red-ink:#8c2f2a; --red-bg:#f7ebe9; --red-rule:#b0433c;
  --amber-ink:#7a5410; --amber-bg:#f9f1de; --amber-rule:#c08a1e;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:680px;margin:0 auto;padding:28px 16px 56px}
.mono{font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  font-variant-numeric:tabular-nums}
header h1{font-size:22px;line-height:1.2;margin:0;letter-spacing:-.01em}
header .sub{margin:4px 0 0;font-size:12px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.09em}
.asof{margin:14px 0 0;font-size:15px;font-weight:600}
.asof span{color:var(--muted);font-weight:400}
.band{margin:20px 0 0;padding:12px 16px;border-radius:6px;border-left:4px solid}
.band .lede{font-weight:700}
.band .detail{font-size:14px;margin-top:2px}
.band.on{background:var(--green-bg);color:var(--green-ink);border-color:var(--green-rule)}
.band.off{background:var(--red-bg);color:var(--red-ink);border-color:var(--red-rule)}
.band.unknown{background:var(--amber-bg);color:var(--amber-ink);border-color:var(--amber-rule)}
.coverage{margin:14px 0 0;font-size:13.5px;color:var(--muted)}
.warn{margin:14px 0 0;padding:12px 16px;border-radius:6px;border-left:4px solid var(--red-rule);
  background:var(--red-bg);color:var(--red-ink);font-weight:600;font-size:14.5px}
h2{margin:34px 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.09em;
  color:var(--muted);font-weight:600}
h2 b{color:var(--ink)}
.note-under{font-size:13px;color:var(--muted);margin:0 0 10px}
table{width:100%;border-collapse:collapse}
th{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
  font-weight:600;text-align:right;padding:6px 8px;border-bottom:1px solid var(--ink)}
th.l{text-align:left}
td{padding:9px 8px;border-bottom:1px solid var(--hairline);text-align:right;
  font-size:15px;white-space:nowrap}
td.l{text-align:left}
td.sym{font-weight:700}
tr.has-note td{border-bottom:none;padding-bottom:2px}
tr.note td{padding:0 8px 9px;border-bottom:1px solid var(--hairline);text-align:left;
  font-size:13px;color:var(--amber-ink);white-space:normal}
.soft td{font-size:14px;color:#444}
.soft td.flaw{white-space:normal;color:var(--muted);font-size:13px}
.empty{margin:16px 0 0;padding:26px 16px;text-align:center;border:1px solid var(--hairline);
  border-radius:6px;color:var(--muted);font-size:15px}
footer{margin-top:44px;padding-top:14px;border-top:1px solid var(--hairline);
  font-size:12.5px;color:var(--muted);line-height:1.6}
footer a{color:var(--muted)}
@media (max-width:420px){
  .wrap{padding:22px 12px 48px}
  td,th{padding-left:5px;padding-right:5px}
  td{font-size:14px}
}
"""


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "unavailable"
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def render_html(p: dict) -> str:
    e = html.escape
    reg, cov, cnt = p["regime"], p["coverage"], p["counts"]

    if reg["ok"] is True:
        band = ('<div class="band on"><div class="lede">RISK-ON — new longs allowed</div>'
                f'<div class="detail">{e(reg["symbol"])} is above its rising 200-day SMA '
                f'(slope {reg["slope_pct"]:+.1f}%)</div></div>')
    elif reg["ok"] is False:
        band = ('<div class="band off"><div class="lede">RISK-OFF — stand down on new longs</div>'
                f'<div class="detail">{e(reg["symbol"])} is not above a rising 200-day SMA '
                f'(slope {reg["slope_pct"]:+.1f}%). Everything below is watch-only.</div></div>')
    else:
        band = ('<div class="band unknown"><div class="lede">REGIME UNKNOWN</div>'
                f'<div class="detail">{e(reg["symbol"])} data unavailable — treat every '
                'setup as watch-only.</div></div>')

    coverage = (f'<p class="coverage">{cov["evaluated"]} / {cov["universe"]} tickers evaluated'
                + (f' · skipped: {e(", ".join(cov["skipped"][:8]))}'
                   + (f' +{len(cov["skipped"])-8} more' if len(cov["skipped"]) > 8 else '')
                   if cov["skipped"] else '')
                + f' · {cnt["candidates"]} candidates · {cnt["soft"]} soft · '
                  f'{cnt["watch"]} watch · {cnt["fail"]} fail</p>')

    warn = ''
    if cov["incomplete"]:
        warn = (f'<div class="warn">INCOMPLETE SCAN — {cov["universe"] - cov["evaluated"]} of '
                f'{cov["universe"]} tickers are missing. The data source may be failing; '
                'do not treat this page as a full read of the market.</div>')

    def cand_rows(rows):
        out = []
        for r in rows:
            note = r.get("watch_for")
            xc = f'{r["days_since_cross"]}d' if r["days_since_cross"] is not None else "–"
            out.append(f'<tr{" class=\"has-note\"" if note else ""}>'
                       f'<td class="l sym mono">{e(r["symbol"])}</td>'
                       f'<td class="mono">{r["price"]:,.2f}</td>'
                       f'<td class="mono">{xc}</td>'
                       f'<td class="mono">{r["rvol"]:.2f}×</td></tr>')
            if note:
                out.append(f'<tr class="note"><td colspan="4">Watch: {e(note)}</td></tr>')
        return "".join(out)

    if p["candidates"]:
        cand_html = (f'<h2><b>{cnt["candidates"]}</b> candidate{"s" if cnt["candidates"] != 1 else ""}</h2>'
                     '<p class="note-under">Fresh 8/21 cross + stacked EMAs + volume confirmed. '
                     'QA each on the real chart before any entry.</p>'
                     '<table><thead><tr><th class="l">Ticker</th><th>Price</th>'
                     '<th>Cross</th><th>Vol</th></tr></thead>'
                     f'<tbody>{cand_rows(p["candidates"])}</tbody></table>')
    else:
        cand_html = ('<h2>Candidates</h2>'
                     '<div class="empty">No fresh setups today — nothing to do.<br>'
                     'Not trading is also a decision.</div>')

    soft_html = ''
    if p["soft_candidates"]:
        rows = "".join(
            f'<tr class="soft"><td class="l sym mono">{e(r["symbol"])}</td>'
            f'<td class="mono">{r["days_since_cross"]}d</td>'
            f'<td class="l flaw">{e(r["unchecked"])}</td></tr>'
            for r in p["soft_candidates"])
        soft_html = (f'<h2><b>{cnt["soft"]}</b> soft candidate{"s" if cnt["soft"] != 1 else ""}</h2>'
                     '<p class="note-under">Fresh cross, one box unchecked.</p>'
                     '<table><thead><tr><th class="l">Ticker</th><th>Cross</th>'
                     '<th class="l">Unchecked</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Swing Scanner — daily read</title>
<meta name="description" content="Daily trend-following scan of the S&amp;P 500 and Nasdaq-100.">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Swing Scanner</h1>
  <p class="sub">S&amp;P 500 + Nasdaq-100 · daily trend scan</p>
  <p class="asof">Data as of {e(_fmt_date(p["as_of"]))} <span>(last completed daily bar)</span></p>
</header>
{band}
{warn}
{coverage}
{cand_html}
{soft_html}
<footer>
  <p>Screening aid, not financial advice. A candidate is the <em>start</em> of your own
  chart analysis — entry, stop, and judgment are yours — not a buy signal or a
  recommendation to trade.</p>
  <p>Generated {e(p["generated_utc"])} · updates weekdays after the US close ·
  <a href="data.json">raw data (JSON)</a></p>
</footer>
</div>
</body>
</html>
"""


# ----------------------------------------------------------------------
# WRITE
# ----------------------------------------------------------------------
def write_site(payload: dict, docs_dir: str = DOCS_DIR) -> tuple[str, str]:
    os.makedirs(docs_dir, exist_ok=True)
    html_path = os.path.join(docs_dir, "index.html")
    json_path = os.path.join(docs_dir, "data.json")
    with open(html_path, "w") as f:
        f.write(render_html(payload))
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=1)
    return html_path, json_path


def main():
    print("Running scan...")
    scan = run_scan()
    payload = build_payload(**scan)
    html_path, json_path = write_site(payload)
    c = payload["counts"]
    print(f"as of {payload['as_of']} — {c['candidates']} candidates, {c['soft']} soft "
          f"({payload['coverage']['evaluated']}/{payload['coverage']['universe']} evaluated)")
    print(f"wrote {html_path}\nwrote {json_path}")


if __name__ == "__main__":
    main()

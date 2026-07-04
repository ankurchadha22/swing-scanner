#!/usr/bin/env python3
"""
render_site.py — output adapter: run the scan, publish the static site into docs/.

Pages:
  docs/index.html         today's read
  docs/how-it-works.html  plain-English explanation of every check and term
  docs/data.json          the same data, structured

This file only RENDERS. The checklist logic lives in swing_scanner.py (verified,
do not modify) and the batch/caching/universe machinery in swing_scanner_scaled.py
(unchanged — the pieces are imported and orchestrated here exactly as run() does).

The published pages deliberately EXCLUDE all position-sizing / account / risk
fields — sizing stays local via swing_scanner.size_position().

Design: editorial/newsprint (serif display, warm paper, hairline rules, no
tinted callout cards, no rounded boxes, no gradients). Single-file pages with
inline CSS, no JS, no CDNs.

Run:  .venv/bin/python render_site.py       (or plain python in CI)
"""

import os, json, html, datetime as dt

from swing_scanner import add_indicators, evaluate, VOL_LOOKBACK_S
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
def fmt_volume(n) -> str:
    """1_234_567 -> '1.2M' (shares/day)."""
    if n is None:
        return "–"
    n = float(n)
    if n >= 1e9: return f"{n/1e9:.1f}B"
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1e3: return f"{n/1e3:.0f}K"
    return str(int(n))


def build_payload(regime_ok, mslope, results, universe, prices) -> dict:
    as_of = None
    if prices:
        as_of = max(df.index[-1] for df in prices.values()).date().isoformat()

    rank = {"CANDIDATE": 0, "CANDIDATE (soft)": 1, "WATCH": 2, "FAIL": 3}
    ordered = sorted(results, key=lambda v: (rank.get(v.status, 9),
                     v.days_since_cross if v.days_since_cross is not None else 999))

    def base(v):
        avg_vol = None
        df = prices.get(v.symbol)
        if df is not None and "volume" in df:
            avg_vol = float(df["volume"].iloc[-VOL_LOOKBACK_S:].mean())
        return {
            "symbol": v.symbol,
            "price": round(v.price, 2),
            "days_since_cross": v.days_since_cross,
            "rvol": round(v.rvol, 2),
            "avg_volume": int(avg_vol) if avg_vol is not None else None,
            "avg_volume_fmt": fmt_volume(avg_vol),
            "extension_pct": round(v.extension_pct, 1),
        }

    candidates, softs = [], []
    for v in ordered:
        if v.status == "CANDIDATE":
            row = base(v)
            row["watch_for"] = (
                f"already {v.extension_pct:+.1f}% above its 21-day average — "
                "don’t chase; wait for a calmer entry"
                if v.extension_pct > STRETCH_NOTE_PCT else None)
            candidates.append(row)
        elif v.status == "CANDIDATE (soft)":
            row = base(v)
            flaws = []
            if not v.stacked:
                flaws.append("trend stack not aligned (8 > 21 > 50)")
            if not v.vol_ok:
                flaws.append(f"volume below normal ({v.rvol:.2f}×)")
            row["unchecked"] = "; ".join(flaws) or "marginal"
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
# SHARED PAGE SHELL  (editorial: paper, serif display, rules — no cards)
# ----------------------------------------------------------------------
CSS = """
:root{
  --paper:#f9f6ef; --ink:#1c1812; --body:#3d382e; --muted:#716b5d;
  --hair:#ddd6c6; --green:#1f6440; --red:#9c2b20; --amber:#8a5f14;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:660px;margin:0 auto;padding:32px 18px 64px}
.mono{font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  font-variant-numeric:tabular-nums}
a{color:inherit}
/* masthead */
.masthead h1{font-family:Georgia,"Times New Roman",serif;font-size:31px;margin:0;
  font-weight:700;letter-spacing:-.015em}
.tagline{margin:2px 0 12px;font-size:14.5px;color:var(--muted);
  font-family:Georgia,serif;font-style:italic}
.double{border-top:3px solid var(--ink);border-bottom:1px solid var(--ink);
  height:6px;margin-bottom:12px}
nav{display:flex;gap:24px;font-size:12px;text-transform:uppercase;letter-spacing:.14em}
nav a{color:var(--muted);text-decoration:none;padding-bottom:3px}
nav a.active{color:var(--ink);font-weight:700;border-bottom:2px solid var(--ink)}
nav a:hover{color:var(--ink)}
.dateline{margin:18px 0 0;font-size:11.5px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--muted)}
.dateline b{color:var(--ink);font-weight:700}
/* regime lede — typographic, not a card */
.regime{margin:30px 0 0}
.regime .bar{width:54px;height:4px;margin-bottom:12px}
.regime.on .bar{background:var(--green)}
.regime.off .bar{background:var(--red)}
.regime.unknown .bar{background:var(--amber)}
.regime h2{font-family:Georgia,serif;font-size:25px;line-height:1.25;margin:0;font-weight:700}
.regime.on h2{color:var(--green)}
.regime.off h2{color:var(--red)}
.regime.unknown h2{color:var(--amber)}
.regime .dek{margin:8px 0 0;font-size:15.5px;color:var(--body);max-width:56ch}
.regime .dek a{color:var(--muted)}
/* coverage */
.coverage{margin:20px 0 0;font-size:13px;color:var(--muted);line-height:1.55}
/* incomplete warning — newspaper correction box */
.warn{margin:22px 0 0;border-top:3px solid var(--red);border-bottom:1px solid var(--red);
  padding:10px 0;color:var(--red);font-weight:700;font-size:14.5px}
/* section kickers */
.kicker{display:flex;align-items:baseline;gap:12px;margin:40px 0 4px}
.kicker h3{margin:0;font-size:12px;letter-spacing:.14em;text-transform:uppercase;font-weight:700}
.kicker .n{color:var(--muted);font-weight:400}
.kicker::after{content:"";flex:1;border-top:1px solid var(--hair);transform:translateY(-3px)}
.intro{margin:6px 0 14px;font-size:14.5px;color:var(--body);
  font-family:Georgia,serif;font-style:italic;max-width:60ch}
/* tables */
table{width:100%;border-collapse:collapse}
th{font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);
  font-weight:700;text-align:right;padding:6px 8px;border-bottom:2px solid var(--ink)}
th.l{text-align:left}
td{padding:10px 8px;border-bottom:1px solid var(--hair);text-align:right;
  font-size:15px;white-space:nowrap;vertical-align:top}
td.l{text-align:left}
td.sym{font-weight:700}
td .raw{display:block;font-size:11.5px;color:var(--muted);margin-top:1px}
tr.has-note td{border-bottom:none;padding-bottom:2px}
tr.note td{padding:0 8px 10px;border-bottom:1px solid var(--hair);text-align:left;
  font-size:13.5px;color:var(--amber);white-space:normal;
  font-family:Georgia,serif;font-style:italic}
.soft td{font-size:14px;color:var(--body)}
.soft td.flaw{white-space:normal;color:var(--muted);font-size:13px}
/* column legend */
.legend{margin:12px 0 0;font-size:12.5px;color:var(--muted);line-height:1.6;max-width:62ch}
.legend b{color:var(--body);font-weight:700}
/* empty state */
.empty{margin:14px 0 0;padding:22px 0;text-align:center;color:var(--muted);
  font-family:Georgia,serif;font-style:italic;font-size:15.5px;
  border-top:1px solid var(--hair);border-bottom:1px solid var(--hair)}
/* explainer prose */
.prose{font-family:Georgia,"Times New Roman",serif;font-size:16.5px;color:var(--body)}
.prose h2{font-size:21px;color:var(--ink);margin:38px 0 8px;line-height:1.3}
.prose h2 .step{display:block;font-family:-apple-system,"Segoe UI",Roboto,sans-serif;
  font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);
  font-weight:700;margin-bottom:4px}
.prose p{margin:0 0 14px;max-width:64ch}
.prose .term{font-style:italic}
.prose table{font-family:-apple-system,"Segoe UI",Roboto,sans-serif}
.prose td{white-space:normal;text-align:left;font-size:14px}
.prose td.k{font-weight:700;white-space:nowrap;vertical-align:top}
footer{margin-top:48px;padding-top:14px;border-top:1px solid var(--hair);
  font-size:12.5px;color:var(--muted);line-height:1.6}
@media (max-width:420px){
  .wrap{padding:24px 13px 52px}
  td,th{padding-left:5px;padding-right:5px}
  td{font-size:14px}
  .regime h2{font-size:22px}
}
"""


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "unavailable"
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _page(title: str, active: str, dateline: str, body: str, generated: str) -> str:
    tabs = (f'<a href="index.html"{" class=\"active\"" if active == "today" else ""}>Today’s scan</a>'
            f'<a href="how-it-works.html"{" class=\"active\"" if active == "how" else ""}>How it works</a>')
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title}</title>
<meta name="description" content="A daily trend-following read of the S&amp;P 500 and Nasdaq-100.">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header class="masthead">
  <h1>Swing Scanner</h1>
  <p class="tagline">A daily trend-following read of the S&amp;P&nbsp;500 and Nasdaq-100</p>
  <div class="double"></div>
  <nav>{tabs}</nav>
  {dateline}
</header>
{body}
<footer>
  <p>Screening aid, not financial advice. A candidate is the <em>start</em> of your own
  chart analysis — entry, stop, and judgment are yours — not a buy signal or a
  recommendation to trade.</p>
  <p>Generated {html.escape(generated)} · updates weekdays after the US close ·
  <a href="data.json">raw data (JSON)</a></p>
</footer>
</div>
</body>
</html>
"""


# ----------------------------------------------------------------------
# TODAY'S SCAN PAGE
# ----------------------------------------------------------------------
def render_html(p: dict) -> str:
    e = html.escape
    reg, cov, cnt = p["regime"], p["coverage"], p["counts"]

    if reg["ok"] is True:
        regime = (f'<section class="regime on"><div class="bar"></div>'
                  f'<h2>Risk-on: the market’s light is green.</h2>'
                  f'<p class="dek">{e(reg["symbol"])} closed above its rising 200-day average '
                  f'(slope {reg["slope_pct"]:+.1f}%), so the broad tide is up and this checklist '
                  f'allows new buys. <a href="how-it-works.html#regime">Why this gate exists</a>.</p></section>')
    elif reg["ok"] is False:
        regime = (f'<section class="regime off"><div class="bar"></div>'
                  f'<h2>Risk-off: stand down on new buys.</h2>'
                  f'<p class="dek">{e(reg["symbol"])} is not above a rising 200-day average '
                  f'(slope {reg["slope_pct"]:+.1f}%) — the broad tide is against new positions. '
                  f'Everything below is watch-only until the market heals. '
                  f'<a href="how-it-works.html#regime">Why this gate exists</a>.</p></section>')
    else:
        regime = ('<section class="regime unknown"><div class="bar"></div>'
                  '<h2>Regime unknown.</h2>'
                  f'<p class="dek">{e(reg["symbol"])} data was unavailable for this run — '
                  'treat every setup below as watch-only.</p></section>')

    skipped_txt = ""
    if cov["skipped"]:
        shown = ", ".join(cov["skipped"][:8])
        more = f" +{len(cov['skipped'])-8} more" if len(cov["skipped"]) > 8 else ""
        skipped_txt = (f'<br>{len(cov["skipped"])} skipped — too new to compute a 200-day '
                       f'average: {e(shown)}{more}.')
    coverage = (f'<p class="coverage">{cov["evaluated"]} of {cov["universe"]} tickers evaluated '
                f'· {cnt["candidates"]} candidates · {cnt["soft"]} soft · '
                f'{cnt["watch"]} in healthy trends without a fresh trigger · '
                f'{cnt["fail"]} not in uptrends{skipped_txt}</p>')

    warn = ''
    if cov["incomplete"]:
        warn = (f'<div class="warn">INCOMPLETE SCAN — {cov["universe"] - cov["evaluated"]} of '
                f'{cov["universe"]} tickers are missing. The data source may be failing; '
                'do not treat this page as a full read of the market.</div>')

    def vol_cell(r):
        return (f'<td class="mono">{r["rvol"]:.2f}×'
                f'<span class="raw">{e(r["avg_volume_fmt"])}/day</span></td>')

    def cand_rows(rows):
        out = []
        for r in rows:
            note = r.get("watch_for")
            xc = f'{r["days_since_cross"]}d ago' if r["days_since_cross"] is not None else "–"
            out.append(f'<tr{" class=\"has-note\"" if note else ""}>'
                       f'<td class="l sym mono">{e(r["symbol"])}</td>'
                       f'<td class="mono">{r["price"]:,.2f}</td>'
                       f'<td class="mono">{xc}</td>'
                       f'{vol_cell(r)}</tr>')
            if note:
                out.append(f'<tr class="note"><td colspan="4">Watch — {e(note)}.</td></tr>')
        return "".join(out)

    if p["candidates"]:
        cand_html = (
            '<div class="kicker"><h3>Candidates <span class="n">· '
            f'{cnt["candidates"]}</span></h3></div>'
            '<p class="intro">Passed every check: a long-term uptrend, all three trend '
            'lenses aligned, a buy trigger within the last seven sessions, and above-normal '
            'volume behind it. Each one is a chart to go review — not a buy signal.</p>'
            '<table><thead><tr><th class="l">Ticker</th><th>Last close</th>'
            '<th>Trigger</th><th>Volume</th></tr></thead>'
            f'<tbody>{cand_rows(p["candidates"])}</tbody></table>'
            '<p class="legend"><b>Trigger</b> — trading days since the stock’s 8-day '
            'average closed above its 21-day average, the entry signal this system waits for '
            '(<a href="how-it-works.html#cross">explained here</a>). '
            '<b>Volume</b> — the last five days’ average trading volume vs the '
            'stock’s own 50-day norm; 1.50× means half again busier than usual, '
            'with the average shares traded per day underneath.</p>')
    else:
        cand_html = ('<div class="kicker"><h3>Candidates</h3></div>'
                     '<div class="empty">No fresh setups today — nothing to do. '
                     'Not trading is also a decision.</div>')

    soft_html = ''
    if p["soft_candidates"]:
        rows = "".join(
            f'<tr class="soft"><td class="l sym mono">{e(r["symbol"])}</td>'
            f'<td class="mono">{r["days_since_cross"]}d ago</td>'
            f'{vol_cell(r)}'
            f'<td class="l flaw">{e(r["unchecked"])}</td></tr>'
            for r in p["soft_candidates"])
        soft_html = (
            f'<div class="kicker"><h3>Soft candidates <span class="n">· {cnt["soft"]}</span></h3></div>'
            '<p class="intro">The trigger fired, but one box is unchecked — worth a look, '
            'with lower conviction.</p>'
            '<table><thead><tr><th class="l">Ticker</th><th>Trigger</th><th>Volume</th>'
            '<th class="l">What’s missing</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')

    dateline = (f'<p class="dateline">Data as of <b>{html.escape(_fmt_date(p["as_of"]))}</b>'
                ' — last completed daily bar</p>')
    body = f"{regime}\n{warn}\n{coverage}\n{cand_html}\n{soft_html}"
    return _page("Swing Scanner — today’s read", "today", dateline, body,
                 p["generated_utc"])


# ----------------------------------------------------------------------
# HOW-IT-WORKS PAGE  (static reference; regenerated with the site)
# ----------------------------------------------------------------------
def render_explainer(generated: str) -> str:
    body = """
<article class="prose">
<p style="margin-top:30px">This page explains every check and every term on the scan,
assuming no trading background. The scanner runs the same fixed checklist over roughly
500 large U.S. stocks (the S&amp;P&nbsp;500 plus the Nasdaq-100) each evening, and
surfaces the few names worth a human look. It predicts nothing — it filters.</p>

<h2 id="averages">First, a 30-second primer on moving averages</h2>
<p>A <span class="term">moving average</span> is just a stock’s average closing
price over some number of recent trading days, redrawn each day. It smooths out daily
noise so you can see the underlying direction. This system uses four of them as lenses
of different lengths:</p>
<table><tbody>
<tr><td class="k">8-day</td><td>about a week and a half of trading — the short-term pulse</td></tr>
<tr><td class="k">21-day</td><td>about one trading month — the medium-term trend</td></tr>
<tr><td class="k">50-day</td><td>about one quarter — the intermediate trend</td></tr>
<tr><td class="k">200-day</td><td>almost a full trading year — the long-term tide</td></tr>
</tbody></table>
<p style="margin-top:12px">The 8-, 21-, and 50-day lenses are <span class="term">exponential</span>
moving averages (EMAs), which weight recent days a bit more heavily; the 200-day is a
simple average. The distinction barely matters for reading the scan — think of them all
as trend lines of different speeds.</p>

<h2 id="regime"><span class="step">Step 1</span>Is the tide coming in? (market regime)</h2>
<p>Before looking at any single stock, the scanner checks the whole market: is SPY
(a fund tracking the S&amp;P&nbsp;500) trading <em>above</em> its 200-day average, and
is that average itself <em>rising</em>? Most stocks follow the market’s tide, so
buying individual uptrends while the broad market is falling fails far more often.
When this gate is red — <span class="term">risk-off</span> — the whole page becomes
watch-only, no matter how good an individual chart looks.</p>

<h2 id="stage"><span class="step">Step 2</span>Is the stock itself in a long-term uptrend?</h2>
<p>The same test applied to each stock: price above its own rising 200-day average.
A stock below that line, or with the line still falling, is in a downtrend or still
repairing one — it fails the scan outright, whatever else is happening. This is the
single filter that removes most of the ~500 names on a typical day.</p>

<h2 id="stack"><span class="step">Step 3</span>Do the three lenses agree? (the trend stack)</h2>
<p>In a healthy uptrend the short lens sits above the medium lens, which sits above
the intermediate one: 8-day above 21-day above 50-day. The scan calls that
<span class="term">stacked</span> — short-, medium-, and intermediate-term momentum all
pointing the same way. When they’re tangled, the trend is still sorting itself out.</p>

<h2 id="cross"><span class="step">Step 4</span>The trigger: a fresh 8/21 cross</h2>
<p>This is the signal the whole system waits for. When the 8-day average closes above
the 21-day average — an <span class="term">8/21 bullish cross</span> — it means the
last week-and-a-half of buying has overtaken the past month’s pace: short-term
momentum has just turned up inside a bigger uptrend. That moment, not the uptrend
itself, is the entry cue.</p>
<p>The cross must be <span class="term">fresh</span> — within the last 7 trading
days. Older than that and the move has usually already run; you’d be arriving
late to it. The <b>Trigger</b> column on the scan (“2d ago”) is simply how
many trading days have passed since that cross.</p>

<h2 id="volume"><span class="step">Step 5</span>Is there conviction behind it? (volume)</h2>
<p><span class="term">Volume</span> is how much of the stock actually traded. The scan
compares the last five days’ average volume with the stock’s own 50-day norm:
1.50× means trading has been half again busier than usual — real participation
behind the move. Below 1.00× the move happened on light traffic, which fizzles
more easily; that alone demotes a name to “soft.” The small number underneath
(“2.1M/day”) is the raw average traded per day, useful for judging how easily
a stock trades.</p>

<h2 id="extension"><span class="step">Step 6</span>Don’t chase (the extension guard)</h2>
<p>A stock stretched far above its 21-day average is like a stretched rubber band —
it tends to snap back before continuing. If price is more than 8% above the 21-day
line, the scan refuses to call it a candidate no matter how good everything else
looks; beyond 5% it stays a candidate but carries a “don’t chase” note.
The disciplined play on an extended name is to wait for it to come back to its
averages, not to buy it stretched.</p>

<h2 id="verdicts">What the verdicts mean</h2>
<table><tbody>
<tr><td class="k">Candidate</td><td>passed every check. Worth opening the real chart
to judge an entry — that judgment stays with the human.</td></tr>
<tr><td class="k">Soft candidate</td><td>the trigger fired, but one box is unchecked —
the stack isn’t aligned or volume is below normal. Lower conviction.</td></tr>
<tr><td class="k">Watch</td><td>healthy uptrend, but no fresh trigger (or too extended
to buy). Wait for the next pullback or cross. Not listed on the front page.</td></tr>
<tr><td class="k">Fail</td><td>not in a long-term uptrend (steps 1–2). Skipped
regardless of anything else. Not listed.</td></tr>
</tbody></table>

<h2 id="limits">What this is not</h2>
<p>This is a screening aid built on end-of-day data — not financial advice, not a
prediction, and never an automatic trade. It exists to replace scrolling and hype with
a fixed, repeatable checklist. A candidate here is an invitation to look at the actual
chart and decide for yourself; plenty of candidates deserve a pass. Stocks listed less
than 200 trading days (recent IPOs and spin-offs) are excluded because their long-term
trend can’t be measured yet.</p>
</article>
"""
    return _page("Swing Scanner — how it works", "how", "", body, generated)


# ----------------------------------------------------------------------
# WRITE
# ----------------------------------------------------------------------
def write_site(payload: dict, docs_dir: str = DOCS_DIR) -> tuple[str, str, str]:
    os.makedirs(docs_dir, exist_ok=True)
    # Serve these files verbatim: bypass GitHub Pages' default Jekyll build,
    # which otherwise processes /docs and can fail on hand-written HTML.
    # Written every run so it can never be dropped from the published folder.
    with open(os.path.join(docs_dir, ".nojekyll"), "w") as f:
        f.write("")
    html_path = os.path.join(docs_dir, "index.html")
    how_path = os.path.join(docs_dir, "how-it-works.html")
    json_path = os.path.join(docs_dir, "data.json")
    with open(html_path, "w") as f:
        f.write(render_html(payload))
    with open(how_path, "w") as f:
        f.write(render_explainer(payload["generated_utc"]))
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=1)
    return html_path, how_path, json_path


def main():
    print("Running scan...")
    scan = run_scan()
    payload = build_payload(**scan)
    paths = write_site(payload)
    c = payload["counts"]
    print(f"as of {payload['as_of']} — {c['candidates']} candidates, {c['soft']} soft "
          f"({payload['coverage']['evaluated']}/{payload['coverage']['universe']} evaluated)")
    for p in paths:
        print(f"wrote {p}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
QA for render_site.py — no network, mocked Verdicts.

Covers the payload builder and HTML renderers only. The scan machinery has its
own QA (qa_scanner_scaled.py) and the checklist logic in swing_scanner.py is
verified separately and never re-tested here.

Run:  .venv/bin/python qa_render_site.py
"""

import os, json, shutil, tempfile
from html.parser import HTMLParser
import pandas as pd

from swing_scanner import Verdict
from render_site import (
    build_payload, render_html, render_explainer, write_site, fmt_volume,
)

PASS, FAIL = 0, 0
def check(name, cond):
    global PASS, FAIL
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def V(sym, status, dsc=None, rvol=1.2, ext=2.0, stacked=True, vol_ok=True):
    """Verdict field order: symbol, price, ema8, ema21, ema50, sma200, slope,
    stacked, above_rising_200, days_since_cross, fresh_trigger, rvol, vol_ok,
    extension_pct, extended, status, reasons."""
    return Verdict(sym, 123.45, 120, 118, 115, 100, 1.0, stacked, True,
                   dsc, dsc is not None, rvol, vol_ok, ext, ext > 8.0, status, [])


def fake_prices(tickers, last_day="2026-07-02", volume=1_500_000):
    idx = pd.bdate_range(end=pd.Timestamp(last_day), periods=210)
    df = pd.DataFrame({"close": 100.0, "high": 101.0, "low": 99.0,
                       "volume": volume}, index=idx)
    return {t: df for t in tickers}


class TagBalanceChecker(HTMLParser):
    VOID = {"meta", "br", "hr", "img", "link", "input"}
    def __init__(self):
        super().__init__(); self.stack, self.errors = [], []
    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID: self.stack.append(tag)
    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(tag)


def balanced(doc: str) -> bool:
    p = TagBalanceChecker(); p.feed(doc)
    return not p.errors and not p.stack


def main():
    print("QA: render_site payload + HTML\n")

    results = [
        V("JCI", "CANDIDATE", dsc=2, rvol=1.70, ext=-1.4),
        V("ELV", "CANDIDATE", dsc=1, rvol=1.08, ext=5.3),           # stretched -> watch-for
        V("EBAY", "CANDIDATE (soft)", dsc=2, rvol=0.57, vol_ok=False),
        V("PWR", "CANDIDATE (soft)", dsc=2, rvol=1.68, stacked=False),
        V("MSFT", "WATCH"),
        V("CRM", "FAIL"),
    ]
    universe = ["CRM", "EBAY", "ELV", "JCI", "MSFT", "PWR"]
    prices = fake_prices(universe)

    # ---- volume formatting ---------------------------------------------------
    check("fmt_volume: K/M/B tiers",
          fmt_volume(950) == "950" and fmt_volume(85_400) == "85K"
          and fmt_volume(1_500_000) == "1.5M" and fmt_volume(2_300_000_000) == "2.3B"
          and fmt_volume(None) == "–")

    # ---- payload -------------------------------------------------------------
    p = build_payload(True, 0.7, results, universe, prices)
    check("payload: as_of = last bar date", p["as_of"] == "2026-07-02")
    check("payload: candidates sorted by trigger age",
          [c["symbol"] for c in p["candidates"]] == ["ELV", "JCI"])
    check("payload: raw average volume computed and formatted",
          p["candidates"][0]["avg_volume"] == 1_500_000
          and p["candidates"][0]["avg_volume_fmt"] == "1.5M")
    check("payload: stretched candidate carries a watch-for; calm one does not",
          p["candidates"][0]["watch_for"] is not None
          and p["candidates"][1]["watch_for"] is None)
    check("payload: soft flaws in plain English",
          p["soft_candidates"][0]["unchecked"].startswith("volume below normal")
          and "trend stack not aligned" in p["soft_candidates"][1]["unchecked"])
    check("payload: counts + full coverage",
          p["counts"] == {"candidates": 2, "soft": 2, "watch": 1, "fail": 1}
          and not p["coverage"]["incomplete"])
    check("payload: JSON-serializable", bool(json.dumps(p)))

    big_universe = universe + [f"X{i:03d}" for i in range(94)]      # 6/100 evaluated
    p_bad = build_payload(True, 0.7, results, big_universe, prices)
    check("payload: sparse coverage flips the incomplete flag",
          p_bad["coverage"]["incomplete"] and len(p_bad["coverage"]["skipped"]) == 94)

    # ---- today's page ----------------------------------------------------------
    h = render_html(p)
    check("html: tags balanced / parses cleanly", balanced(h))
    check("html: data-as-of dateline present", "Data as of <b>July 2, 2026</b>" in h)
    check("html: risk-on lede (typographic, not a card)",
          "Risk-on: the market’s light is green." in h
          and "border-radius" not in h and "gradient" not in h)
    check("html: nav tabs present with today active",
          'href="how-it-works.html"' in h and 'class="active">Today’s scan' in h)
    check("html: candidates with watch-for note rendered",
          "JCI" in h and "Watch — already +5.3% above its 21-day average" in h)
    check("html: raw volume shown next to the ratio", "1.5M/day" in h)
    check("html: column legend explains Trigger and Volume",
          "<b>Trigger</b> — trading days since" in h and "<b>Volume</b> — the last five days" in h)
    check("html: soft section says what's missing in plain English",
          "trend stack not aligned" in h)
    check("html: disclaimer present", "not financial advice" in h)
    check("html: phone viewport meta present", 'name="viewport"' in h)

    lower = h.lower() + json.dumps(p).lower()
    forbidden = ["account", "risk_dollars", "per_share_risk", "position_value",
                 "size_position", "8000", "8,000"]
    check("no sizing/account data in page or JSON",
          not any(s in lower for s in forbidden))

    h_off = render_html(build_payload(False, -0.3, results, universe, prices))
    check("html: risk-off lede when regime fails",
          "Risk-off: stand down on new buys." in h_off)
    h_bad = render_html(p_bad)
    check("html: incomplete-scan warning is loud", "INCOMPLETE SCAN" in h_bad)
    h_empty = render_html(build_payload(True, 0.7,
                          [V("MSFT", "WATCH")], ["MSFT"], fake_prices(["MSFT"])))
    check("html: calm empty state when nothing qualifies",
          "No fresh setups today" in h_empty)

    # ---- how-it-works page -----------------------------------------------------
    x = render_explainer("2026-07-04 22:00 UTC")
    check("explainer: tags balanced / parses cleanly", balanced(x))
    check("explainer: nav tabs present with how-it-works active",
          'href="index.html"' in x and 'class="active">How it works' in x)
    check("explainer: teaches the 8/21 cross in plain English",
          'id="cross"' in x and "8-day average closes above" in x
          and "21-day average" in x)
    check("explainer: covers every step + verdicts + limits",
          all(f'id="{a}"' in x for a in
              ("averages", "regime", "stage", "stack", "cross",
               "volume", "extension", "verdicts", "limits")))
    check("explainer: no sizing/account data",
          not any(s in x.lower() for s in forbidden))

    # ---- file output -------------------------------------------------------
    tmp = tempfile.mkdtemp()
    try:
        hp, xp, jp = write_site(p, docs_dir=tmp)
        with open(jp) as f:
            round_trip = json.load(f)
        check("write_site: emits index, how-it-works, and loadable data.json",
              os.path.basename(hp) == "index.html"
              and os.path.basename(xp) == "how-it-works.html"
              and os.path.getsize(xp) > 2000
              and round_trip["as_of"] == "2026-07-02")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS}/{PASS+FAIL} checks passed")
    raise SystemExit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()

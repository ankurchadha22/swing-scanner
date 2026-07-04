#!/usr/bin/env python3
"""
QA for render_site.py — no network, mocked Verdicts.

Covers the payload builder and HTML renderer only. The scan machinery has its
own QA (qa_scanner_scaled.py) and the checklist logic in swing_scanner.py is
verified separately and never re-tested here.

Run:  .venv/bin/python qa_render_site.py
"""

import os, json, shutil, tempfile
from html.parser import HTMLParser
import pandas as pd

from swing_scanner import Verdict
from render_site import build_payload, render_html, write_site

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


def fake_prices(tickers, last_day="2026-07-02"):
    idx = pd.bdate_range(end=pd.Timestamp(last_day), periods=210)
    df = pd.DataFrame({"close": 100.0, "high": 101.0, "low": 99.0,
                       "volume": 1_000_000}, index=idx)
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

    # ---- payload -----------------------------------------------------------
    p = build_payload(True, 0.7, results, universe, prices)
    check("payload: as_of = last bar date", p["as_of"] == "2026-07-02")
    check("payload: candidates sorted by cross age",
          [c["symbol"] for c in p["candidates"]] == ["ELV", "JCI"])
    check("payload: stretched candidate carries a watch-for; calm one does not",
          p["candidates"][0]["watch_for"] is not None
          and p["candidates"][1]["watch_for"] is None)
    check("payload: soft flaws name the unchecked box",
          p["soft_candidates"][0]["unchecked"].startswith("volume light")
          and p["soft_candidates"][1]["unchecked"] == "EMAs not stacked")
    check("payload: counts + full coverage",
          p["counts"] == {"candidates": 2, "soft": 2, "watch": 1, "fail": 1}
          and not p["coverage"]["incomplete"])
    check("payload: JSON-serializable", bool(json.dumps(p)))

    big_universe = universe + [f"X{i:03d}" for i in range(94)]      # 6/100 evaluated
    p_bad = build_payload(True, 0.7, results, big_universe, prices)
    check("payload: sparse coverage flips the incomplete flag",
          p_bad["coverage"]["incomplete"] and len(p_bad["coverage"]["skipped"]) == 94)

    # ---- html --------------------------------------------------------------
    h = render_html(p)
    parser = TagBalanceChecker(); parser.feed(h)
    check("html: tags balanced / parses cleanly", not parser.errors and not parser.stack)
    check("html: data-as-of header present", "Data as of July 2, 2026" in h)
    check("html: RISK-ON banner", "RISK-ON — new longs allowed" in h)
    check("html: candidates + watch-for rendered",
          "JCI" in h and "Watch: +5.3% above 21 EMA" in h)
    check("html: soft section names the unchecked box", "EMAs not stacked" in h)
    check("html: disclaimer present", "not financial advice" in h)
    check("html: phone viewport meta present", 'name="viewport"' in h)

    lower = h.lower() + json.dumps(p).lower()
    forbidden = ["account", "risk_dollars", "per_share_risk", "shares",
                 "position_value", "8000", "8,000", "size_position"]
    check("no sizing/account data in page or JSON",
          not any(s in lower for s in forbidden))

    h_off = render_html(build_payload(False, -0.3, results, universe, prices))
    check("html: RISK-OFF banner when regime fails",
          "RISK-OFF — stand down on new longs" in h_off)
    h_bad = render_html(p_bad)
    check("html: incomplete-scan warning is loud", "INCOMPLETE SCAN" in h_bad)
    h_empty = render_html(build_payload(True, 0.7,
                          [V("MSFT", "WATCH")], ["MSFT"], fake_prices(["MSFT"])))
    check("html: calm empty state when nothing qualifies",
          "No fresh setups today" in h_empty)

    # ---- file output -------------------------------------------------------
    tmp = tempfile.mkdtemp()
    try:
        hp, jp = write_site(p, docs_dir=tmp)
        with open(jp) as f:
            round_trip = json.load(f)
        check("write_site: emits index.html + loadable data.json",
              os.path.basename(hp) == "index.html" and round_trip["as_of"] == "2026-07-02")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS}/{PASS+FAIL} checks passed")
    raise SystemExit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
QA for swing_scanner_scaled.py — no network, mocked data source.

Covers the batch/caching/universe MACHINERY only. The checklist logic in
swing_scanner.py is verified separately against live data and is NOT
re-tested (and must not be modified) here.

Run:  .venv/bin/python qa_scanner_scaled.py
"""

import os, json, shutil, tempfile, datetime as dt
import numpy as np
import pandas as pd

import swing_scanner_scaled as sc

PASS, FAIL = 0, 0
def check(name, cond):
    global PASS, FAIL
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def fake_df(n=260, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.1, 1.0, n))
    idx = pd.bdate_range(end=pd.Timestamp.today(), periods=n)
    return pd.DataFrame({"close": close, "high": close + 1, "low": close - 1,
                         "volume": rng.integers(1e6, 5e6, n)}, index=idx)


def fake_panel(tickers, n=260):
    frames = {}
    for i, t in enumerate(tickers):
        df = fake_df(n, seed=i)
        df.columns = pd.MultiIndex.from_product([[t], ["Close", "High", "Low", "Volume"]])
        frames[t] = df
    return pd.concat(frames.values(), axis=1)


def main():
    print("QA: swing_scanner_scaled machinery\n")

    # ---- _split_panel ----------------------------------------------------
    tickers = ["AAA", "BBB", "CCC"]
    out = sc._split_panel(fake_panel(tickers), tickers)
    check("split_panel returns all tickers with >=200 bars", set(out) == set(tickers))
    check("split_panel lowercases columns",
          list(out["AAA"].columns) == ["close", "high", "low", "volume"])
    short = sc._split_panel(fake_panel(["DDD"], n=50), ["DDD"])
    check("split_panel drops <200-bar histories (new IPOs)", short == {})

    # ---- batch_fetch: chunking + retry + partial failure -------------------
    calls = {"n": 0}
    orig_download, orig_backoff = sc._download_chunk, sc.BACKOFF_SEC
    sc.BACKOFF_SEC = 0.0
    def mock_download(grp, period):
        calls["n"] += 1
        return fake_panel(grp)
    sc._download_chunk = mock_download
    names = [f"T{i:03d}" for i in range(250)]
    got = sc.batch_fetch(names, chunk=100, verbose=False)
    check("batch_fetch: 250 names -> 3 bulk calls", calls["n"] == 3)
    check("batch_fetch: all names returned", len(got) == 250)

    calls["n"] = 0
    def flaky_download(grp, period):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("simulated rate limit")
        return fake_panel(grp)
    sc._download_chunk = flaky_download
    got = sc.batch_fetch(["XXX"], chunk=100, verbose=False)
    check("batch_fetch: retry recovers a rate-limited chunk", len(got) == 1)

    calls["n"] = 0
    def dead_download(grp, period):
        calls["n"] += 1
        if grp[0] == "BAD0":
            raise ConnectionError("chunk permanently down")
        return fake_panel(grp)
    sc._download_chunk = dead_download
    got = sc.batch_fetch(["BAD0", "OK00"], chunk=1, verbose=False)
    check("batch_fetch: partial failure keeps other chunks", set(got) == {"OK00"})

    # ---- caching -----------------------------------------------------------
    sc._download_chunk = mock_download
    tmp = tempfile.mkdtemp()
    orig_cache = sc.CACHE_DIR
    sc.CACHE_DIR = tmp
    try:
        calls["n"] = 0
        a = sc.get_prices(["AAA", "BBB"], use_cache=True, verbose=False)
        b = sc.get_prices(["AAA", "BBB"], use_cache=True, verbose=False)
        check("cache: second same-day call makes 0 network calls",
              calls["n"] == 1 and set(b) == {"AAA", "BBB"})

        # regression for the SPY-pollution bug found live 2026-07-04:
        # use_cache=False must neither read NOR WRITE the daily cache file
        shutil.rmtree(tmp); os.makedirs(tmp)
        sc.get_prices(["SPY"], use_cache=False, verbose=False)
        check("cache: use_cache=False does not write the cache file",
              not os.listdir(tmp))
        full = sc.get_prices(["AAA", "BBB", "CCC"], use_cache=True, verbose=False)
        check("cache: universe fetch after regime check returns full set",
              set(full) == {"AAA", "BBB", "CCC"})
    finally:
        sc.CACHE_DIR = orig_cache
        sc._download_chunk = orig_download
        sc.BACKOFF_SEC = orig_backoff
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- universe: caching (weekly TTL) + normalization + fallback ladder ---
    orig_wiki = sc._read_wiki_tables
    orig_ucache, orig_cdir = sc.UNIVERSE_CACHE, sc.CACHE_DIR
    utmp = tempfile.mkdtemp()
    sc.CACHE_DIR = utmp
    sc.UNIVERSE_CACHE = os.path.join(utmp, "universe.json")

    def mock_wiki(url):
        if "S%26P" in url or "S&P" in url:
            return [pd.DataFrame({"Symbol": ["AAPL", "BRK.B", "MSFT"]})]
        return [pd.DataFrame({"Ticker": ["AAPL", "NVDA", "nan"]})]
    try:
        # live fetch: normalize + dedup, and it writes the cache
        sc._read_wiki_tables = mock_wiki
        uni = sc.load_universe(verbose=False)
        check("universe: BRK.B normalized to BRK-B", "BRK-B" in uni)
        check("universe: S&P + Nasdaq deduped, 'nan' dropped",
              uni == ["AAPL", "BRK-B", "MSFT", "NVDA"])
        check("universe: live fetch writes the cache", os.path.exists(sc.UNIVERSE_CACHE))

        # fresh cache: a second same-week call must NOT touch Wikipedia
        def wiki_forbidden(url):
            raise AssertionError("wiki fetched despite a fresh cache")
        sc._read_wiki_tables = wiki_forbidden
        uni2 = sc.load_universe(verbose=False)
        check("universe: fresh cache -> 0 wiki calls, same list", uni2 == uni)

        # stale cache (past TTL): re-fetch live and re-date the cache to today
        with open(sc.UNIVERSE_CACHE) as f: blob = json.load(f)
        blob["date"] = (dt.date.today()
                        - dt.timedelta(days=sc.UNIVERSE_TTL_DAYS + 1)).isoformat()
        with open(sc.UNIVERSE_CACHE, "w") as f: json.dump(blob, f)
        def mock_wiki_changed(url):
            if "S%26P" in url or "S&P" in url:
                return [pd.DataFrame({"Symbol": ["AAPL", "TSLA"]})]
            return [pd.DataFrame({"Ticker": ["nan"]})]
        sc._read_wiki_tables = mock_wiki_changed
        uni3 = sc.load_universe(verbose=False)
        _, age = sc._read_universe_cache()
        check("universe: stale cache re-fetches live and refreshes",
              "TSLA" in uni3 and age == 0)

        # live fail + stale cache present: serve the last-good list, don't drop it
        with open(sc.UNIVERSE_CACHE, "w") as f:
            json.dump({"date": (dt.date.today() - dt.timedelta(days=99)).isoformat(),
                       "tickers": ["AAA", "BBB"]}, f)
        def wiki_403(url):
            raise ConnectionError("HTTP Error 403: Forbidden")
        sc._read_wiki_tables = wiki_403
        check("universe: live fail + stale cache -> serves stale list",
              sc.load_universe(verbose=False) == ["AAA", "BBB"])

        # live fail + no cache at all: last resort is the bundled sp500.csv
        os.remove(sc.UNIVERSE_CACHE)
        check("universe: live fail + no cache -> bundled sp500.csv",
              len(sc.load_universe(verbose=False)) > 400)
    finally:
        sc._read_wiki_tables = orig_wiki
        sc.UNIVERSE_CACHE, sc.CACHE_DIR = orig_ucache, orig_cdir
        shutil.rmtree(utmp, ignore_errors=True)

    print(f"\n{PASS}/{PASS+FAIL} checks passed")
    raise SystemExit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()

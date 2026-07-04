#!/usr/bin/env python3
"""
Swing Scanner (scaled) — S&P 500 + Nasdaq-100 universe.

Reuses the VERIFIED checklist logic from swing_scanner.py (single source of
truth) and adds the machinery needed to scan ~600 names quickly:
  - dynamic universe (S&P 500 + Nasdaq-100), de-duplicated
  - BATCH downloading (a few bulk calls, not one-per-ticker)
  - local daily caching (re-runs same day are instant)
  - retry/backoff + graceful partial-failure handling

Requires swing_scanner.py in the same folder.
Data source: yfinance (free, no API key). Screening aid, NOT financial advice.
"""

import os, time, json, pickle, datetime as dt
import pandas as pd

# reuse the already-verified logic — do NOT reimplement it here
from swing_scanner import (
    add_indicators, evaluate, size_position, Verdict,
    MARKET_SYMBOL, SLOPE_LOOKBACK,
)

CHUNK_SIZE   = 100          # tickers per bulk download call
PERIOD       = "2y"         # history per ticker (need >200 bars for the 200-SMA)
MAX_RETRIES  = 3
BACKOFF_SEC  = 2.0
CACHE_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".scan_cache")
FALLBACK_SP500_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sp500.csv")
UNIVERSE_CACHE     = os.path.join(CACHE_DIR, "universe.json")
UNIVERSE_TTL_DAYS  = 7      # re-fetch the constituent lists at most this often
SP500_URL   = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"


# ----------------------------------------------------------------------
# UNIVERSE
# ----------------------------------------------------------------------
def _read_wiki_tables(url: str) -> list[pd.DataFrame]:
    """Wikipedia blocks pandas' default user-agent (403), so fetch with a
    browser UA and parse the HTML text."""
    import urllib.request
    from io import StringIO
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    return pd.read_html(StringIO(html))


def _normalize_tickers(sp: list[str], nq: list[str], verbose=True) -> list[str]:
    """Yahoo uses '-' not '.' for share classes (BRK.B -> BRK-B); dedup + drop 'nan'."""
    clean = []
    for t in sp + nq:
        t = t.strip().upper().replace(".", "-")
        if t and t not in ("NAN",):
            clean.append(t)
    universe = sorted(set(clean))
    if verbose:
        print(f"  universe: {len(sp)} S&P + {len(nq)} Nasdaq-100 "
              f"-> {len(universe)} unique tickers")
    return universe


def _fetch_universe_live(verbose=True) -> list[str]:
    """Live Wikipedia fetch of S&P 500 + Nasdaq-100. Raises if the S&P 500 list
    (the primary source) can't be obtained at all — the caller then falls back."""
    sp, nq, sp_ok = [], [], False
    try:
        sp_tbl = _read_wiki_tables(SP500_URL)[0]
        sp = sp_tbl["Symbol"].astype(str).tolist()
        sp_ok = True
    except Exception as e:
        if verbose: print(f"  S&P500 live fetch failed ({e})")
    try:
        tables = _read_wiki_tables(NASDAQ100_URL)
        # the constituents table is the one with a 'Ticker' or 'Symbol' column
        for t in tables:
            cols = [c.lower() for c in t.columns.astype(str)]
            if "ticker" in cols or "symbol" in cols:
                col = t.columns[[c.lower() in ("ticker", "symbol") for c in t.columns.astype(str)]][0]
                nq = t[col].astype(str).tolist()
                break
    except Exception as e:
        if verbose: print(f"  Nasdaq-100 live fetch failed ({e}); "
                           f"S&P500 already covers most large Nasdaq names")
    if not sp_ok:
        raise RuntimeError("live universe fetch failed: S&P 500 unavailable")
    return _normalize_tickers(sp, nq, verbose)


def _read_universe_cache():
    """Return (tickers, age_in_days) from the cached universe file, or (None, None)."""
    if not os.path.exists(UNIVERSE_CACHE):
        return None, None
    try:
        with open(UNIVERSE_CACHE) as f:
            blob = json.load(f)
        age = (dt.date.today() - dt.date.fromisoformat(blob["date"])).days
        return blob["tickers"], age
    except Exception:
        return None, None


def _write_universe_cache(tickers: list[str]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(UNIVERSE_CACHE, "w") as f:
        json.dump({"date": dt.date.today().isoformat(), "tickers": tickers}, f)


def load_universe(verbose=True) -> list[str]:
    """S&P 500 + Nasdaq-100, de-duplicated, cached locally for UNIVERSE_TTL_DAYS.

    Fallback ladder (most-fresh to most-reliable):
      1. fresh cache (< TTL)      -> zero network calls
      2. live Wikipedia fetch     -> refreshes and re-caches
      3. stale cache (last-good)  -> if the live fetch fails
      4. bundled sp500.csv        -> last resort (S&P 500 only, no Nasdaq-100)
    """
    cached, age = _read_universe_cache()

    if cached and age is not None and age < UNIVERSE_TTL_DAYS:
        if verbose:
            print(f"  universe: {len(cached)} tickers (cached {age}d ago; "
                  f"refreshes after {UNIVERSE_TTL_DAYS}d)")
        return cached

    try:
        universe = _fetch_universe_live(verbose)
        _write_universe_cache(universe)
        return universe
    except Exception as e:
        if verbose: print(f"  live universe fetch failed ({e})")

    if cached:
        if verbose:
            print(f"  universe: using stale cache ({len(cached)} tickers, {age}d old)")
        return cached

    if os.path.exists(FALLBACK_SP500_CSV):
        if verbose: print("  universe: falling back to bundled sp500.csv (no Nasdaq-100)")
        sp = pd.read_csv(FALLBACK_SP500_CSV)["Symbol"].astype(str).tolist()
        return _normalize_tickers(sp, [], verbose)

    return []


# ----------------------------------------------------------------------
# BATCH FETCH  (this is the speed fix)
# ----------------------------------------------------------------------
def _download_chunk(tickers: list[str], period: str) -> pd.DataFrame:
    """One bulk yfinance call for many tickers. Isolated so QA can mock it."""
    import yfinance as yf
    return yf.download(tickers, period=period, interval="1d", auto_adjust=True,
                       group_by="ticker", threads=True, progress=False)


def _split_panel(panel: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Turn a multi-ticker yfinance panel into {ticker: tidy df}."""
    out = {}
    for t in tickers:
        try:
            if isinstance(panel.columns, pd.MultiIndex):
                sub = panel[t] if t in panel.columns.get_level_values(0) else None
            else:
                sub = panel  # single-ticker chunk -> flat columns
            if sub is None or sub.empty:
                continue
            sub = sub.rename(columns=str.lower)[["close", "high", "low", "volume"]].dropna()
            if len(sub) >= 200:
                out[t] = sub
        except Exception:
            continue
    return out


def batch_fetch(tickers, period=PERIOD, chunk=CHUNK_SIZE, verbose=True) -> dict:
    """Fetch all tickers in bulk chunks with retry. Returns {ticker: df}."""
    data, chunks = {}, [tickers[i:i+chunk] for i in range(0, len(tickers), chunk)]
    for ci, grp in enumerate(chunks, 1):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                panel = _download_chunk(grp, period)
                got = _split_panel(panel, grp)
                data.update(got)
                if verbose:
                    print(f"  chunk {ci}/{len(chunks)}: {len(got)}/{len(grp)} ok")
                break
            except Exception as e:
                if attempt == MAX_RETRIES:
                    if verbose: print(f"  chunk {ci} failed after {MAX_RETRIES} tries: {e}")
                else:
                    time.sleep(BACKOFF_SEC * attempt)
    return data


# ----------------------------------------------------------------------
# CACHING
# ----------------------------------------------------------------------
def _cache_path(day: str) -> str:
    return os.path.join(CACHE_DIR, f"prices_{day}.pkl")


def get_prices(tickers, use_cache=True, verbose=True) -> dict:
    os.makedirs(CACHE_DIR, exist_ok=True)
    day = dt.date.today().isoformat()
    path = _cache_path(day)
    if use_cache and os.path.exists(path):
        if verbose: print(f"  cache hit: {path}")
        with open(path, "rb") as f:
            return pickle.load(f)
    data = batch_fetch(tickers, verbose=verbose)
    # only persist full-universe fetches: a use_cache=False call (e.g. the
    # regime check on SPY alone) must never overwrite the daily cache, or a
    # later cached read would return just that one ticker
    if use_cache:
        with open(path, "wb") as f:
            pickle.dump(data, f)
    return data


# ----------------------------------------------------------------------
# PLAIN-ENGLISH SUMMARY  (primary output — readable in seconds)
# ----------------------------------------------------------------------
def print_summary(regime_ok, mslope, results, universe, prices):
    missing = sorted(set(universe) - set(prices))
    cands = [v for v in results if v.status == "CANDIDATE"]
    softs = [v for v in results if v.status == "CANDIDATE (soft)"]

    print("=" * 64)
    print(f"TODAY'S READ — {dt.date.today().isoformat()}")
    print("=" * 64)
    if regime_ok is None:
        print(f"Market: UNKNOWN — {MARKET_SYMBOL} data unavailable; "
              "treat every setup as watch-only.")
    elif regime_ok:
        print(f"Market: RISK-ON — {MARKET_SYMBOL} is above its rising 200-day SMA "
              f"(slope {mslope:+.1f}%). New longs allowed.")
    else:
        print(f"Market: RISK-OFF — {MARKET_SYMBOL} is not above a rising 200-day SMA "
              f"(slope {mslope:+.1f}%). Stand down on new longs; "
              "everything below is watch-only.")

    cov = f"Coverage: {len(results)}/{len(universe)} tickers evaluated"
    if missing:
        cov += f" (skipped: {', '.join(missing[:10])}"
        cov += f" +{len(missing)-10} more)" if len(missing) > 10 else ")"
    print(cov)
    if len(results) < 0.9 * len(universe):
        print(f"*** WARNING: {len(universe) - len(results)} of {len(universe)} tickers "
              "missing — data source may be failing; treat this scan as INCOMPLETE ***")
    print()

    if not cands and not softs:
        print("No fresh setups today — nothing to do. Not trading is also a decision.")
    if cands:
        print(f"{len(cands)} CANDIDATE{'S' if len(cands) != 1 else ''} "
              "(fresh 8/21 cross + stacked EMAs + volume confirmed):")
        for v in cands:
            line = (f"  {v.symbol:<6} ${v.price:<8.2f} cross {v.days_since_cross}d ago, "
                    f"vol {v.rvol:.2f}x baseline")
            if v.extension_pct > 5:
                line += f" — watch: {v.extension_pct:+.1f}% above 21 EMA, getting stretched"
            print(line)
        print("  -> QA each on the real chart before any entry (entry, stop, size).")
    if softs:
        print(f"\n{len(softs)} soft candidate{'s' if len(softs) != 1 else ''} "
              "(fresh cross, one box unchecked):")
        for v in softs:
            flaws = []
            if not v.stacked: flaws.append("EMAs not stacked")
            if not v.vol_ok:  flaws.append(f"vol light {v.rvol:.2f}x")
            print(f"  {v.symbol:<6} cross {v.days_since_cross}d ago ({', '.join(flaws)})")
    print()


# ----------------------------------------------------------------------
# RUNNER
# ----------------------------------------------------------------------
def run(use_cache=True, top=None):
    t0 = time.time()
    print("Loading universe...")
    universe = load_universe()

    print("Checking market regime...")
    mkt = get_prices([MARKET_SYMBOL], use_cache=False, verbose=False).get(MARKET_SYMBOL)
    if mkt is not None:
        mkt = add_indicators(mkt); ml = mkt.iloc[-1]
        mslope = (ml["sma200"] - mkt["sma200"].iloc[-1-SLOPE_LOOKBACK]) \
                 / mkt["sma200"].iloc[-1-SLOPE_LOOKBACK] * 100
        regime_ok = (ml["close"] > ml["sma200"]) and (mslope > 0)
    else:
        regime_ok = None; mslope = float("nan")

    print(f"Fetching {len(universe)} tickers in bulk...")
    prices = get_prices(universe, use_cache=use_cache)

    results = []
    for sym, df in prices.items():
        try:
            results.append(evaluate(sym, df))
        except Exception:
            continue

    rank = {"CANDIDATE": 0, "CANDIDATE (soft)": 1, "WATCH": 2, "FAIL": 3}
    results.sort(key=lambda v: (rank.get(v.status, 9),
                                v.days_since_cross if v.days_since_cross is not None else 999))

    print(f"Scanned {len(results)} names in {time.time()-t0:.1f}s.\n")

    # primary output: the plain-English read
    print_summary(regime_ok, mslope, results, universe, prices)

    # secondary detail: ranked table of actionable names
    actionable = [v for v in results if v.status.startswith("CANDIDATE")]
    show = actionable if actionable else results[:20]
    hdr = f"{'SYM':<7}{'STATUS':<18}{'PRICE':>9}{'200slp':>8}{'xCross':>8}{'rvol':>6}{'ext%':>7}"
    print("DETAIL (ranked):")
    print(hdr); print("-"*len(hdr))
    for v in (show if top is None else show[:top]):
        xc = f"{v.days_since_cross}d" if v.days_since_cross is not None else "-"
        print(f"{v.symbol:<7}{v.status:<18}{v.price:>9.2f}{v.sma200_slope:>7.1f}%"
              f"{xc:>8}{v.rvol:>6.2f}{v.extension_pct:>6.1f}%")

    # optional record for digging into every ticker's numbers (not the main output)
    out_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           f"scan_{dt.date.today().isoformat()}.csv")
    pd.DataFrame([v.__dict__ for v in results]).to_csv(out_csv, index=False)
    print(f"\n(full per-ticker record, if you want to dig: {os.path.basename(out_csv)})")
    return results


if __name__ == "__main__":
    run()

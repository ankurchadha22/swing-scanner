# Swing Scanner — how to run it

A standalone Python version of the pre-trade checklist. It screens a watchlist
for trend-following long candidates using the 200-day SMA regime filter.

## What it checks (per ticker)
1. **Regime** — SPY above a rising 200-day SMA (printed once at top; if RISK-OFF, treat everything as watch-only)
2. **Stage** — the stock itself above its own rising 200-day SMA
3. **Context** — 8 > 21 > 50 EMA bullish stack
4. **Trigger** — a *fresh* 8/21 bullish cross within the last 7 trading days
5. **Volume** — recent volume vs its own baseline
6. **Extension guard** — flags names already stretched >8% above the 21 EMA ("don't chase")

## Status meanings
- **CANDIDATE** — passes everything; worth pulling up the chart to QA an entry
- **CANDIDATE (soft)** — fresh cross but one condition (stack or volume) is marginal
- **WATCH** — healthy trend but no fresh trigger (or extended) — wait for a pullback/cross
- **FAIL** — fails the regime/stage gate; skip regardless of anything else

## Setup (one time)
```bash
pip install yfinance pandas
```

## Run it
```bash
python3 swing_scanner.py
```

## Customize
Open the file and edit the CONFIG block near the top:
- `WATCHLIST` — the tickers to scan
- `ACCOUNT_SIZE` / `RISK_PCT` — for position sizing (default $8,000, 1%)
- `TRIGGER_WINDOW` — how many days counts as a "fresh" cross (default 7)
- `EXTENSION_FLAG` — % above 21 EMA that trips the chase-risk flag (default 8%)

## Website (GitHub Pages)
`render_site.py` runs the scan and writes `docs/index.html` + `docs/data.json` —
a phone-friendly "TODAY'S READ" page. `.github/workflows/scan.yml` regenerates
it weekdays after the US close (21:30 UTC) and commits `docs/`; GitHub Pages
serves the `/docs` folder. **The page contains no position-sizing or account
data** — sizing stays local. QA for the renderer: `.venv/bin/python
qa_render_site.py` (expects 29/29). See `PLAN_website.md` for the full spec.

## Position sizing helper
```python
from swing_scanner import size_position
size_position(entry=50.0, stop=46.0)   # -> shares to buy for 1% risk on $8k
```

## Important notes
- **Data source is yfinance** (free Yahoo data, no API key). It's unofficial and
  can occasionally hiccup or rate-limit; fine for a once-a-day scan of a modest list.
- This uses **end-of-day** data. Run it after the close so the latest daily bar is settled.
- The logic here was verified to reproduce the same verdicts we computed by hand
  from live Robinhood data during our session.
- **This is a screening aid, not financial advice or a recommendation to trade.**
  A CANDIDATE is the *start* of your own analysis (chart, volume, entry/stop), not a buy signal.

---

# Scaled version — swing_scanner_scaled.py (S&P 500 + Nasdaq-100)

Scans the full S&P 500 + Nasdaq-100 (~600 unique names) without the 10–40 min problem.

## What makes it fast
- **Batch downloading**: fetches ~100 tickers per bulk call, so it makes ~6 network
  calls instead of ~600 individual ones. This is the core speed fix.
- **Local caching**: results are cached per day in `.scan_cache/`. Re-running the same
  day reads the cache instantly (0 network calls).
- **Retry + backoff**: a transient rate-limit on a chunk retries instead of dropping names.
- **Graceful partial failure**: if one chunk can't be fetched, the rest still return.

## Requires
- **Python 3.10+** — the code uses `int | None` annotations, so the macOS system
  Python 3.9 cannot run it. A dedicated venv lives in this folder:
  ```bash
  # one-time setup (already done 2026-07-04):
  brew install python@3.13
  /opt/homebrew/bin/python3.13 -m venv .venv
  .venv/bin/pip install yfinance pandas lxml
  ```
- `swing_scanner.py` in the same folder (it reuses that verified logic — single source of truth)
- `sp500.csv` in the same folder (offline fallback if Wikipedia is unreachable)

## Run it
```bash
.venv/bin/python swing_scanner_scaled.py
```
Or, in Claude Code from this folder, just type **`/scan`** — it runs the scanner
and relays today's read in chat.

The **primary output is the plain-English "TODAY'S READ"** printed to the
console: market regime, coverage (with a loud warning if a meaningful number of
tickers failed to fetch), one line per candidate with why it qualified and any
watch-fors. The ranked table follows as detail, and `scan_YYYY-MM-DD.csv` is
written as an optional record for digging into every ticker's numbers.

## Universe source
The S&P 500 + Nasdaq-100 constituent list is **cached locally for 7 days**
(`.scan_cache/universe.json`), so it does NOT hit Wikipedia on every run — only
when the cache is older than a week. Fallback ladder, most-fresh to most-reliable:
1. **fresh cache** (under 7 days old) — zero network calls
2. **live Wikipedia fetch** — when the cache is stale/missing; refreshes and re-caches
3. **stale cache** (last-good) — if the live fetch fails, the week-old list still runs
4. **bundled `sp500.csv`** — last resort (S&P 500 only, loses the ~14 Nasdaq-100 names)

Change the refresh interval via `UNIVERSE_TTL_DAYS` near the top of the file.
Ticker symbols are normalized to Yahoo's format automatically (e.g. BRK.B -> BRK-B).

## How this version was QA'd (without live market access)
1. **Logic parity** — the scaled scanner imports `evaluate()` from the verified v1, and QA
   confirms it reproduces the same by-hand verdicts (CRM/PLTR/MSFT/S fail, BE/CRWD/PANW pass).
2. **Machinery** — batch chunking, retry, partial-failure, and caching were tested with a
   mock fetcher (no network): 250 names -> 3 bulk calls, retries recover, cache = 0 re-calls.
3. **Universe** — dedup + share-class normalization verified against the real S&P 500 list.
19/19 checks passed.

That machinery QA now lives in `qa_scanner_scaled.py` — run it after ANY change
to the scaled scanner:
```bash
.venv/bin/python qa_scanner_scaled.py   # expects 17/17 checks passed
```

## Fixes from the first live run (2026-07-04)
- **Wikipedia 403** — Wikipedia blocks pandas' default user-agent, so the
  universe fetch now uses a browser user-agent (`_read_wiki_tables`). The
  `sp500.csv` fallback still covers a full outage (but loses the Nasdaq-100
  names, so a fallback run scans ~503 instead of ~517).
- **Cache pollution** — the SPY regime check (`use_cache=False`) used to WRITE
  the daily cache, so the universe fetch would "cache hit" a file containing
  only SPY and scan 1 name. `get_prices` now only writes the cache when
  `use_cache=True`. Both have regression tests in the QA script.
- Tickers with under 200 trading days of history (recent IPOs/spinoffs) are
  excluded by design — the 200-day SMA can't be computed. They're listed in
  the coverage line so exclusions are always visible.

## Expected real-world runtime
First run of the day: roughly 1–3 minutes (dominated by ~6 bulk network calls).
Same-day re-runs: near-instant (cache).

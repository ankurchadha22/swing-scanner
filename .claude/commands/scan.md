---
description: Run the daily swing scan and give today's read in plain English
---

Run the daily swing scan and relay the results:

1. Run: `.venv/bin/python swing_scanner_scaled.py` from this folder
   (the venv is Python 3.13; the system Python 3.9 cannot run this code).
2. Relay the "TODAY'S READ" section conversationally: the market regime
   first, then the candidates with why each qualified and anything to
   watch for. Keep it tight — this should be readable in seconds.
3. If the coverage warning appears (a meaningful number of tickers
   missing), flag it prominently and diagnose the actual error before
   retrying anything.
4. If it's a weekend/market holiday, note that the data is from the last
   trading day.

Rules:
- Do NOT modify swing_scanner.py (the evaluate() checklist logic is
  verified against live Robinhood data). Fixes belong in
  swing_scanner_scaled.py; run qa_scanner_scaled.py after any change.
- Never place trades or suggest placing them automatically. The user QAs
  each candidate on the real chart and executes manually.
- The CSV is a secondary record — don't present it as the main output.

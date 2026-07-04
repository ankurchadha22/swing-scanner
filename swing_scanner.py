#!/usr/bin/env python3
"""
Swing Scanner — trend-following candidate finder.

Encodes the pre-trade checklist we built:
  Step 1  Regime  : SPY above a rising 200-day SMA (long-only gate)
  Step 3  Stage   : candidate above its own rising 200-day SMA
  Step 4  Context : 8 > 21 > 50 EMA (bullish stack)
  Step 5  Trigger : a FRESH 8/21 bullish cross (within TRIGGER_WINDOW days)
                    + volume confirmation
  Guard   Extension: flag names already stretched far above the 21 EMA ("don't chase")
  Sizing  Position : shares = risk_dollars / (entry - stop)

Data source: yfinance (free, no API key). Runs standalone on your machine.
This is a screening aid, NOT financial advice or a recommendation to trade.
"""

from dataclasses import dataclass, field
import pandas as pd

# ----------------------------------------------------------------------
# CONFIG — edit these
# ----------------------------------------------------------------------
WATCHLIST = [
    "BE", "ABT", "PLTR", "NVDA", "CEG", "MU", "AMZN", "GOOGL", "MSFT",
    "CRM", "CRWD", "OKTA", "S", "IONQ", "PANW",
]
MARKET_SYMBOL   = "SPY"     # broad-market regime gauge
ACCOUNT_SIZE    = 8000.0    # your active-sleeve size in $
RISK_PCT        = 0.01      # max risk per trade (1% = $80 on $8k)

TRIGGER_WINDOW  = 7         # a cross is "fresh" if it happened within N trading days
EXTENSION_FLAG  = 8.0       # flag if price is >N% above the 21 EMA (chase risk)
VOL_LOOKBACK_S  = 5         # recent volume window (days)
VOL_LOOKBACK_L  = 50        # baseline volume window (days)
SLOPE_LOOKBACK  = 10        # bars used to measure 200-SMA slope


# ----------------------------------------------------------------------
# INDICATOR MATH  (pure functions on a price/volume DataFrame)
# ----------------------------------------------------------------------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """df needs columns: close, high, low, volume (daily bars, oldest first)."""
    out = df.copy()
    out["ema8"]  = out["close"].ewm(span=8,  adjust=False).mean()
    out["ema21"] = out["close"].ewm(span=21, adjust=False).mean()
    out["ema50"] = out["close"].ewm(span=50, adjust=False).mean()
    out["sma200"] = out["close"].rolling(200).mean()
    return out


def days_since_bull_cross(df: pd.DataFrame, window: int) -> int | None:
    """Trading days since the last 8/21 bullish cross, or None if none in window."""
    diff = (df["ema8"] - df["ema21"])
    # a bullish cross at bar i: diff[i-1] <= 0 and diff[i] > 0
    crossed_up = (diff.shift(1) <= 0) & (diff > 0)
    recent = crossed_up.iloc[-window:]
    if not recent.any():
        return None
    # position of the most recent True, counted back from the last bar
    idxs = [k for k, v in enumerate(recent.values) if v]
    last = idxs[-1]
    return (len(recent) - 1) - last


@dataclass
class Verdict:
    symbol: str
    price: float
    ema8: float
    ema21: float
    ema50: float
    sma200: float
    sma200_slope: float
    stacked: bool
    above_rising_200: bool
    days_since_cross: int | None
    fresh_trigger: bool
    rvol: float
    vol_ok: bool
    extension_pct: float
    extended: bool
    status: str = "FAIL"
    reasons: list = field(default_factory=list)


def evaluate(symbol: str, df: pd.DataFrame) -> Verdict:
    """Run one ticker's indicator DataFrame through the checklist."""
    df = add_indicators(df)
    last = df.iloc[-1]
    price = float(last["close"])
    e8, e21, e50, s200 = (float(last[c]) for c in ("ema8", "ema21", "ema50", "sma200"))

    slope = float((last["sma200"] - df["sma200"].iloc[-1 - SLOPE_LOOKBACK])
                  / df["sma200"].iloc[-1 - SLOPE_LOOKBACK] * 100)

    stacked = e8 > e21 > e50
    above_rising_200 = (price > s200) and (slope > 0)

    dsc = days_since_bull_cross(df, TRIGGER_WINDOW)
    fresh = dsc is not None

    v_s = df["volume"].iloc[-VOL_LOOKBACK_S:].mean()
    v_l = df["volume"].iloc[-VOL_LOOKBACK_L:].mean()
    rvol = float(v_s / v_l) if v_l else 0.0
    vol_ok = rvol >= 1.0

    ext = (price - e21) / e21 * 100
    extended = ext > EXTENSION_FLAG

    v = Verdict(symbol, price, e8, e21, e50, s200, slope, stacked,
                above_rising_200, dsc, fresh, rvol, vol_ok, ext, extended)

    # ---- classification ----
    if not above_rising_200:
        v.status = "FAIL"
        if price <= s200:  v.reasons.append("price below 200-day SMA (Stage 3/4)")
        if slope <= 0:     v.reasons.append(f"200-day SMA not rising ({slope:+.1f}%)")
        return v

    # context passes from here
    if fresh and stacked and vol_ok and not extended:
        v.status = "CANDIDATE"
        v.reasons.append(f"fresh 8/21 cross {dsc}d ago, stacked, vol ok")
    elif fresh and not extended:
        v.status = "CANDIDATE (soft)"
        if not stacked: v.reasons.append("fresh cross but EMAs not fully stacked")
        if not vol_ok:  v.reasons.append(f"fresh cross but volume light (rvol {rvol:.2f})")
    elif fresh and extended:
        v.status = "WATCH"
        v.reasons.append(f"fresh cross but extended {ext:+.1f}% above 21 EMA (chase risk)")
    else:
        v.status = "WATCH"
        v.reasons.append("healthy trend, no fresh trigger — wait for pullback/cross")
        if extended:
            v.reasons.append(f"also extended {ext:+.1f}% above 21 EMA")
    return v


def size_position(entry: float, stop: float,
                  account: float = ACCOUNT_SIZE, risk_pct: float = RISK_PCT) -> dict:
    """Position size from stop distance (checklist Step 7)."""
    risk_dollars = account * risk_pct
    per_share = entry - stop
    if per_share <= 0:
        return {"error": "stop must be below entry"}
    shares = int(risk_dollars // per_share)
    return {
        "risk_dollars": round(risk_dollars, 2),
        "per_share_risk": round(per_share, 2),
        "shares": shares,
        "position_value": round(shares * entry, 2),
    }


# ----------------------------------------------------------------------
# DATA SOURCE (yfinance) + RUNNER
# ----------------------------------------------------------------------
def fetch(symbol: str, period: str = "2y") -> pd.DataFrame:
    import yfinance as yf
    raw = yf.download(symbol, period=period, interval="1d",
                      auto_adjust=True, progress=False)
    if raw.empty:
        raise ValueError(f"no data for {symbol}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = raw.rename(columns=str.lower)[["close", "high", "low", "volume"]]
    return df.dropna()


def run(watchlist=WATCHLIST):
    # regime gate first
    market = add_indicators(fetch(MARKET_SYMBOL))
    m_last = market.iloc[-1]
    m_slope = (m_last["sma200"] - market["sma200"].iloc[-1 - SLOPE_LOOKBACK]) \
              / market["sma200"].iloc[-1 - SLOPE_LOOKBACK] * 100
    regime_ok = (m_last["close"] > m_last["sma200"]) and (m_slope > 0)
    print(f"REGIME [{MARKET_SYMBOL}]: price {m_last['close']:.2f} vs 200-SMA "
          f"{m_last['sma200']:.2f} (slope {m_slope:+.1f}%) -> "
          f"{'RISK-ON (longs allowed)' if regime_ok else 'RISK-OFF (stand down on new longs)'}\n")

    results = []
    for sym in watchlist:
        try:
            results.append(evaluate(sym, fetch(sym)))
        except Exception as e:
            print(f"  {sym}: fetch/eval error: {e}")

    rank = {"CANDIDATE": 0, "CANDIDATE (soft)": 1, "WATCH": 2, "FAIL": 3}
    results.sort(key=lambda v: (rank.get(v.status, 9), v.days_since_cross
                                if v.days_since_cross is not None else 999))

    hdr = f"{'SYM':<6}{'STATUS':<18}{'PRICE':>9}{'200slope':>9}{'xCross':>8}{'rvol':>6}{'ext%':>7}  reasons"
    print(hdr); print("-" * len(hdr))
    for v in results:
        xc = f"{v.days_since_cross}d" if v.days_since_cross is not None else "-"
        print(f"{v.symbol:<6}{v.status:<18}{v.price:>9.2f}{v.sma200_slope:>8.1f}%"
              f"{xc:>8}{v.rvol:>6.2f}{v.extension_pct:>6.1f}%  {'; '.join(v.reasons)}")

    if not regime_ok:
        print("\nNOTE: market regime is RISK-OFF. Even CANDIDATE names should be treated "
              "as watch-only until the broad market is back above its rising 200-day SMA.")
    return results


if __name__ == "__main__":
    run()

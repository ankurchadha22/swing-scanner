# Plan — Free GitHub-hosted website for the Swing Scanner

Handoff spec for the Fable model to implement. The goal: publish the scanner's
**"TODAY'S READ"** as a free, always-on webpage I can open on my phone or laptop
and read in a few seconds — no terminal, no file to open.

Everything below is decided. The copy-paste prompt for Fable is at the bottom.

---

## The core constraint (and the pattern that solves it)

GitHub Pages serves **static files only** — it cannot run Python on request.
The scanner needs Python to fetch live data. Free solution:

> **GitHub Actions runs the Python scan on a schedule → generates a static
> `index.html` → GitHub Pages serves it.**

Actions is the "server" that runs once per trading day; Pages just displays the
result. Both are free on a **public** repo. No server, no API keys, no database.

```
┌─ GitHub Actions (cron, weekdays after the close) ─────┐
│  1. pip install -r requirements.txt                   │
│  2. run the scan  (existing, verified logic)          │
│  3. render_site.py -> docs/index.html + docs/data.json│
│  4. commit the generated files                        │
└───────────────────────────────────────────────────────┘
                          ↓
┌─ GitHub Pages (serves /docs) ─────────────────────────┐
│  <username>.github.io/<repo>  →  "TODAY'S READ"       │
│  Bookmarked on phone home screen. Read in 5 seconds.  │
└───────────────────────────────────────────────────────┘
```

**Cost: $0.** Public repo = unlimited Actions minutes + free Pages. yfinance is
keyless, so no secrets are needed.

---

## Locked decisions

1. **No position sizing on the page.** The site shows candidates + *why* they
   qualified + watch-fors. Account size / risk % / share counts never appear on
   the published page. (Sizing stays local via `size_position()` in
   `swing_scanner.py`.)
2. **Schedule:** weekdays, after the close, once per day. Cron
   `30 21 * * 1-5` (21:30 UTC ≈ 5:30pm ET). Note the 1-hour daylight-saving
   drift; bumping to `0 22 * * 1-5` (22:00 UTC) keeps it safely after the close
   year-round if preferred. Also add a manual `workflow_dispatch` trigger.
3. **Freshness label:** the page shows **"data as of \<last bar date\>"**. Cron
   doesn't know market holidays; on a holiday it re-emits the last trading day's
   data, and the date label makes that obvious. No holiday calendar needed in v1.
4. **Scope:** **today's read only.** No history/archive in v1 (clean follow-up
   later — that's why we also emit `data.json`).

---

## Files to add (nothing existing gets rewritten)

1. **`requirements.txt`** — `yfinance`, `pandas`, `lxml`. So the Actions runner
   installs the same deps the local `.venv` uses.
2. **`render_site.py`** — NEW. An *output adapter* on top of the existing scan.
   It runs the scan (or consumes what `run()` already computes) and writes:
   - `docs/index.html` — the "TODAY'S READ" as a polished, mobile-first page.
   - `docs/data.json` — the same data as structured JSON (enables a future
     interactive/archive version without a rewrite).
   It must **exclude** all position-sizing fields.
3. **`.github/workflows/scan.yml`** — the scheduled workflow (cron +
   `workflow_dispatch` + run + commit generated files back to the repo).
4. **Pages setup** — publish from the `/docs` folder on `main` (simplest). No
   custom domain needed; the default `github.io` URL is fine.

**Do NOT touch:**
- `swing_scanner.py` — the `evaluate()` checklist logic is verified against live
  Robinhood data. Reuse it; never reimplement or edit it.
- The scan/caching/universe machinery in `swing_scanner_scaled.py` — only add a
  clean way to get results out (e.g. have `run()` return its data, or import the
  pieces). Don't change the logic.
- The `.venv`, the `/scan` command, or the CSV output — the website is an
  **additional** channel, not a replacement.

---

## Design requirements (this matters — make it genuinely nice)

**Aesthetic: clean, editorial, financial. Think a well-designed markets page in
a serious newspaper or a restrained pro trading dashboard — NOT a flashy AI
landing page.**

Hard rules:
- **Light theme.** White / near-white background (`#ffffff` or `#fafafa`), dark
  legible text (`#1a1a1a`-ish). No dark mode in v1.
- **NO "AI-looking" gimmicks.** None of: glowing dots, neon gradients, animated
  gradient blobs/orbs, floating particles, glassmorphism blur panels, purple
  glow shadows, emoji used as UI icons.
- Color is **semantic and sparing**, not decorative: a calm green for
  RISK-ON / full candidates, a muted red or amber for RISK-OFF / warnings.
  Everything else is neutral grays.
- **Typography does the work.** A clean sans (system-ui / Inter) for text; a
  **monospace with tabular figures** for tickers, prices, and numeric columns so
  they align. Clear hierarchy, generous whitespace.
- **Practical > pretty-but-useless.** Every element earns its place.

Layout (top to bottom):
1. **Header** — scanner name + a prominent **"Data as of \<bar date\>"** label.
2. **Regime banner** — a solid, calm band: green "RISK-ON — new longs allowed"
   or red "RISK-OFF — stand down on new longs," with the SPY slope. Solid fill,
   no glow.
3. **Coverage line** — "514 / 517 tickers evaluated." If a meaningful number
   failed (the existing INCOMPLETE-scan condition), show a clear, un-missable
   warning strip so a broken data day is never mistaken for a real "no setups."
4. **Full candidates** — the primary content. A clean, scannable table or card
   list: ticker, price, "cross Nd ago," volume vs baseline, and any watch-for
   (e.g. "+7.5% above 21 EMA — getting stretched"). Aligned numeric columns.
5. **Soft candidates** — visually secondary (lighter/smaller), each showing the
   one unchecked box ("EMAs not stacked" / "volume light 0.75x").
6. **Empty state** — if nothing qualifies: a calm "No fresh setups today —
   nothing to do" message, not an error.
7. **Footer** — quiet: the disclaimer ("screening aid, not financial advice;
   a candidate is the start of your own chart analysis, not a buy signal"),
   and a note on when it last updated / next run.

Practical features to include:
- **Fully responsive**, phone-first (this is opened on mobile).
- **Tabular-aligned numbers** so the table is scannable at a glance.
- Clear visual separation between full and soft candidates.
- Watch-fors surfaced inline on each candidate.
- Loud, obvious incomplete-data / stale-date states.
- Fast: a single static HTML file with inline (or one small) CSS — **no React,
  no framework, no build step, no external JS/CSS CDNs** if avoidable. It should
  load instantly and work offline once cached.

---

## Data contract for `render_site.py`

Consume, per the existing `Verdict` dataclass and regime check:
- Regime: `regime_ok` (bool/None) + SPY 200-SMA slope.
- Per candidate: `symbol`, `price`, `status` (CANDIDATE / CANDIDATE (soft)),
  `days_since_cross`, `rvol`, `extension_pct`, `stacked`, `vol_ok`, and the
  human-readable `reasons` list.
- Coverage: number evaluated vs universe size + the skipped tickers.
- The last bar's date (for the "data as of" label) — from the price index.

**Exclude** `size_position` and any account/risk fields from both `index.html`
and `data.json`.

---

## Risks to know

- **yfinance from datacenter IPs is the main risk.** GitHub runners are Azure
  IPs, and Yahoo rate-limits/blocks cloud ranges harder than home connections.
  The existing retry/backoff + INCOMPLETE-scan warning degrade gracefully;
  keep it to one run/day. If Yahoo blocks it persistently, fall back to a keyed
  free data source via a GitHub Secret — but try yfinance first.
- **Scheduled workflows auto-disable after 60 days of repo inactivity.** The
  daily commit of generated output keeps the repo active, so this self-solves.
- **Cron isn't exact** — Actions can delay scheduled jobs under load. Fine for
  end-of-day data.

---

## Testing / preserve checklist

- Reuse `evaluate()` and the scan machinery unchanged. **Re-run
  `qa_scanner_scaled.py` — it must still report 17/17** after any refactor.
- Add a QA check that `render_site.py` produces valid HTML + JSON from a
  **mocked** result set (no network), so the renderer is covered too.
- Test the workflow via the manual `workflow_dispatch` trigger before trusting
  the cron.
- Confirm the page contains **no** account size, risk %, or share counts.
- Verify the page renders correctly at phone width.

---

## Optional follow-ups (NOT in v1 — mention only)

- Daily notification ("3 new candidates today") — the same Action can email or
  hit a webhook once the scan runs.
- History/archive view, powered by the emitted `data.json`.
- US-market-holiday skip.

---

## COPY-PASTE PROMPT FOR FABLE

> Build a free, GitHub-Pages-hosted website for my swing scanner, following
> `PLAN_website.md` in this folder exactly. Work in this repo:
> `/Users/ankurchadha/Documents/Investing/Swing Trading`.
>
> **Do this:**
> 1. Add `requirements.txt` (yfinance, pandas, lxml).
> 2. Add `render_site.py` — an output adapter that runs the existing scan and
>    writes `docs/index.html` + `docs/data.json`. Reuse the verified logic; do
>    NOT modify `swing_scanner.py` or the scan/caching/universe machinery in
>    `swing_scanner_scaled.py` (only add a clean way to get results out).
>    **Exclude all position-sizing / account / risk fields** from the page and
>    JSON.
> 3. Add `.github/workflows/scan.yml`: cron `30 21 * * 1-5` plus a manual
>    `workflow_dispatch`, that installs deps, runs the scan, generates the site,
>    and commits `docs/` back to `main`. Configure Pages to serve `/docs`.
> 4. The page shows: header with **"Data as of <last bar date>"**, a solid
>    (non-glowing) green/red **regime banner**, a **coverage line** with a loud
>    warning when the scan is incomplete, the **full candidates** (ticker,
>    price, cross age, volume, watch-fors) as a scannable table with
>    tabular-aligned numbers, **soft candidates** as a visually secondary list,
>    a calm empty state, and a quiet footer with the "screening aid, not
>    financial advice" disclaimer.
>
> **Design (important):** light/white background, dark legible text. NO
> AI-looking gimmicks — no glowing dots, neon gradients, animated blobs,
> glassmorphism, glow shadows, or emoji-as-icons. Clean editorial/financial
> aesthetic (like a serious newspaper's markets page or a restrained pro
> dashboard). Color is semantic and sparing (calm green = risk-on/candidate,
> muted red/amber = risk-off/warning); everything else neutral gray. Clean sans
> for text, monospace with tabular figures for tickers/prices/numbers. Fully
> responsive, phone-first. Single static HTML file with inline/one small CSS —
> no React, no framework, no build step, no external CDNs. Make it genuinely
> beautiful and practical.
>
> **Before finishing:** re-run `.venv/bin/python qa_scanner_scaled.py` and
> confirm it still reports **17/17**; add a no-network QA check that
> `render_site.py` produces valid HTML/JSON from a mocked result set; and
> confirm the page contains no account/sizing data and renders at phone width.
> Use the existing `.venv` (Python 3.13); the system Python 3.9 cannot run this
> code.

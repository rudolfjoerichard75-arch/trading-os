# Bosque Forex AI — V5.2 repair

This branch rebuilds the engine from the user's V5 source, retaining H1 → M15 → M5, liquidity/pullback/breakout setups, score >= 70, and TP2 full-exit policy. It does not continue the broken V6 implementation. No winrate target is claimed.

## What changed

- Complete H1/M15 bars only; validated OHLC; stale prices block signals.
- Trend/BOS score components must match the candidate direction. Breakouts are no longer called retests without a retest test.
- Shared strategy, execution and journal functions for live paper signals and historical replay.
- Signed P/L, correct bar count, persistent replay cursor, conservative SL-first ambiguity handling, next-bar-open fills and entry-gap rejection.
- One pending/open paper position at a time, 30-minute signal cooldown, daily -2R and three-loss paper locks.
- Journal JSON is committed by the workflow and backed up as an artifact. Corrupt JSON fails loudly. Legacy open trades are marked unresolved rather than assigned invented outcomes.
- News validates schema, timezone and current-week dates; cache lasts at most 30 minutes, no stale-cache PASS. Unknown news blocks entries.
- Telegram responses check API `ok`. Send intent is saved before sending; failed or uncertain sends are recorded, not retried automatically.
- Dashboard disables stale/error signals, displays WAIT when blocked, labels paper statistics, and shows a Wilson winrate interval.
- Dedicated manual backtest workflow with explicit trading costs and optional predeclared holdout split.

## Install and verify

Python 3.11+:

```sh
pip install -r engine/requirements.txt
python -m unittest discover -s tests -v
node tests/test_dashboard.cjs
```

GitHub Actions secrets: `TWELVEDATA_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
Optional repository variable `ROUND_TRIP_COST_PIPS`: spread + two-sided slippage + commission equivalent, using **1 pip = 0.10 price**. Without it, paper P/L is explicitly gross/unconfigured; it must not be described as validated net performance.

After reviewing/merging, run **Run Bosque Forex AI** manually and inspect its result. The 5-minute workflow schedule is polling, not a continuous trading server. Entries delayed more than two minutes after candle close are skipped. The chart is OANDA/TradingView while the engine uses Twelve Data: quotes can differ.

Live engine writes a health error and disables the signal when data/API/calendar validation fails. No trades are placed on MT5. Paper entries use the next M5 open as a stated simulation reference; actual alert receipt and broker execution can be later and different.

## Backtest data

Add real M5 CSV files under `backtest_data/`. Required columns:

```text
datetime,open,high,low,close
```

Dates must be candle **opening** timestamps, ideally ISO 8601 with UTC offsets. Naive CSV timestamps require the CLI `--timezone` option; the manual workflow expects offset-aware timestamps. The validator rejects duplicate timestamps, missing/nonfinite prices, impossible OHLC, and non-M5 alignment.

Example command (illustrative costs only; replace with measured broker costs):

```sh
python engine/bosque_backtest.py --csv backtest_data/XAUUSD_M5.csv --spread-pips 2 --slippage-pips 1 --commission-pips 0 --split 2025-01-01T00:00:00Z --without-news
```

`--without-news` explicitly labels results **not equivalent to the news-filtered live configuration**. No historical performance has been established by the software tests.

To test historical news, supply `--news backtest_data/news.json` with:

```json
{
  "coverage_start": "2022-01-01T00:00:00Z",
  "coverage_end": "2026-01-01T00:00:00Z",
  "events": [
    {"date":"2022-01-07T08:30:00-05:00", "country":"USD", "impact":"High", "title":"Example schema only — replace with actual historical events"}
  ]
}
```

Coverage must come from a complete, trusted historical source. This schema example is not a calendar dataset. A current-week calendar cannot retrospectively supply 2022–2025 events. Historical revisions, unexpected news and intrabar event timing are not modeled.

Results are written to `engine/backtest_results.json`; per-trade details are also saved separately. Workflow artifacts retain the trades. Dashboard displays summary metadata; research statistics and data hashes are in the result file. The holdout boundary is selected in advance; changing the strategy after examining it compromises its independence. The runner does not optimize parameters or promise 80–90% winrate.

## Execution and reporting limits

- TP1 is observed, not a partial exit; TP3 is informational; the strategy exits fully at TP2, SL or 72 observed M5 bars.
- Spread/slippage/commission are an aggregate P/L cost charged once at exit. Bid/ask triggers and variable spread are not modeled. Tick/broker execution data is needed for that fidelity.
- SL and TP inside the same candle resolve conservatively to SL. Exact touch times are unknown; `closed_at` records the bar close.
- A missing candle while a trade is open, including a session gap, yields `DATA_GAP`. It is excluded from winrate and blocks further paper entries. This intentionally prevents an unobserved loss becoming a win. Supply the missing price path and investigate before resetting a run; do not silently delete unresolved losses. Long datasets with session gaps may therefore produce incomplete runs.
- OPEN/PENDING trades and censored/legacy outcomes are reported separately, never counted as wins. Low trade counts and unresolved outcomes limit conclusions. The confidence interval is descriptive and assumes independent trials, which trades may not satisfy.
- Session names are fixed Malaysia-time display buckets, not DST-aware exchange calendars. Session does not block entry, but preferred sessions contribute five score points as in V5.
- Fresh-zone mitigation, position sizing, real account fills, partial closes, MFE/MAE and MT5 account profit are not implemented in this V5 baseline. UI does not claim these are active.
- No historical dataset or production credentials were provided during repair. Automated tests use synthetic data and mocked API/Telegram calls.

## Files

- `engine/bosque_engine.py`: live/paper strategy and shared replay helpers.
- `engine/bosque_backtest.py`: historical runner, cost inputs and holdout split.
- `index.html`: existing dashboard/chart with health and signal fixes.
- `.github/workflows/bosque-engine.yml`: scheduled live scan and persistence.
- `.github/workflows/bosque-backtest.yml`: manual historical replay.
- `tests/`: regression and integration tests.

Prior V6 source remains in git history. The old `engine/state.json` is retained but unused; V5.2 uses its journal as the persistent source of signal deduplication.

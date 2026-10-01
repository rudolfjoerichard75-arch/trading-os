# Bosque Forex AI — V5.3 candidate

## V5.3 signal quality and workspace

V5.3 is an unvalidated strategy candidate, not a demonstrated win-rate upgrade.
The default evaluator now requires 30 complete UTC-aligned H4 candles (up to
2,000 M5 bars), aligned H4/H1 structure, M15 premium/discount, a first FVG retest
within 16 M15 bars with rejection, directional M15 sweep/BOS, and M5 BOS plus
candle and momentum. London/New York use the existing fixed Malaysia-time
buckets, not DST-aware exchange sessions. A low-efficiency compressed market
is excluded. Every gate is mandatory; the score is checklist completion,
not a probability. No order-block detector is claimed.

Live V5.3 entries require configured costs and net reward/risk >= 2, rechecked
at the simulated next-bar fill. The paper weekly lock is -4R, alongside the
existing daily -2R lock and single-position policy. Existing V5.2 positions
retain their original execution rules and remain in the journal. Summary
statistics are version-specific. Set `BOSQUE_STRATEGY=v52` for baseline replay.
Broker lot sizing remains unavailable until contract/tick-value/volume-step
specifications are verified; stop distance is not an account risk rating.

The responsive workspace keeps the chart, signal and active plan visible.
Journal, research and service status use native modal dialogs (Close/Escape,
keyboard focus containment). Detailed gates/news use collapsible sections.
Stale data disables plans while historical research remains viewable; older
strategy backtests are explicitly identified. Public JSON is rendered as text
or escaped table cells. No fabricated trades or performance are displayed.

The sections below describe the retained V5.2 baseline and research tooling.

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


## Activated service follow-up

`Bosque automatic research backtest` requests up to 5,000 closed XAU/USD M5 bars using the existing Twelve Data secret. It runs on activation and at 22:15 UTC Monday–Friday (06:15 MY the following day), using a fixed chronological 80/20 split. The scheduled run uses one provider request as a cheap rolling smoke baseline. For a longer real-data study, run it manually with `history_start`, `history_end`, and enough `max_chunks`; the downloader walks backwards in provider-sized chunks, records the number of requests, and fails if the requested range is not fully covered. It never fills gaps with synthetic candles.

If `ROUND_TRIP_COST_PIPS` is absent, results explicitly remain **gross only**. Historical news is explicitly disabled for this baseline because a current-week feed cannot supply earlier news. A committed `backtest_data/news.json` can be supplied to the manual automatic run with `historical_news_path`; its coverage must span the complete candle range. The separate CSV/news runner remains available for full historical inputs. Gap-censored research reports `INCOMPLETE_DATA` and its unresolved count instead of a misleading completed winrate. Results now include data-gap diagnostics, breakdowns by session/setup/direction/exit reason, and optional chronological walk-forward folds. These are research diagnostics, not parameter optimization and not a guarantee of a target win rate.

Telegram readiness checks `getMe` and `getChat` once daily; it sends no test message and does not claim delivery has been proven. Actual delivery remains recorded per signal.

Weekly calendar snapshots are archived from the first observation in `engine/news_archive/`; previous years are not backfilled or inferred. Paper journal export is `paper_journal.csv`, including an honest header-only file when no signals exist. The dashboard shows recent real journal records and the most recent research results directly.

Still requires external inputs: measured broker costs, historical economic calendar coverage, account-specific sizing information and MT5 executions. No automatic broker trading or account-profit synchronization is enabled.

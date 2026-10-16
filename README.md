# Bosque Forex AI — SCALPING V3

The original September 28 V3 setup, score, confirmation, aggregation and trade-plan functions are preserved. `tests/fixtures/v3_original.py` is the immutable regression reference. No V5.3 entry filters are active.

Current risk range: **20–80 pips inclusive**, changed from 15–80 at the owner's request on 2026-10-16. Other V3 entry parameters and functions remain unchanged. Live diagnostics and replay read this same engine setting; older journal/backtest records retain the settings used when generated.

## Execution

cron-job.org dispatches `.github/workflows/bosque-engine.yml` on `main`. The engine workflow has no internal cron. Manual workflow dispatch also works. Secrets: `TWELVEDATA_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

`bosque_tracking.py` adds persistent paper journaling, TP/SL outcome tracking, CSV exports, forward statistics, news observations and delivery-attempt status. V3 entry calculations stay unchanged; alert delivery now uses a persistent setup cycle to suppress repeat notifications. Existing journal rows and historical news files are preserved. Only V3 rows enter V3 statistics.

Set optional repository variable `ROUND_TRIP_COST_PIPS` to measured round-trip spread, slippage and commission in project pips (0.10 price). Without it, statistics are clearly labelled gross/unconfigured, not net validated.

## Paper execution and limitations

Signal entry prices are unchanged. Paper execution uses the next M5 open after the alert timestamp. An open beyond the original SL or TP2 is `NOT_ENTERED` (older rows retain `CANCELLED`); it is not a loss and is excluded from paper win rate. Reasons distinguish `OPEN_BEYOND_SL` and `OPEN_BEYOND_TP2`, with the next-open price recorded. TP2 remains the paper exit, SL wins same-bar ambiguity, gaps through SL fill at the worse open, and the holding limit is 72 M5 bars. Missing candles yield `DATA_GAP`. For new `GUARDED_NEXT_M5_OPEN_TP2_V2` records, next-open risk must also remain 20–80 pips and gross RR TP2 must be at least 2; otherwise the row is `NOT_ENTERED` with the measured risk/RR and reason. Original signal entry/SL/TP levels are never moved to force a fill. Older fills retain their actual outcome and get a separate `fill_audit`; the dashboard exposes both overall forward statistics and the new execution-model group. Historical fill-policy exceptions are not retroactively removed from performance. This is not a broker ledger.

Target observations are stored separately in each row's `signal_path`, including after the paper trade exits TP2 or has no fill. They track first TP1/TP2/TP3 touches and first observed SL for up to 72 candles. `TP3_AFTER_SL` is not a paper win; touching TP3 and SL in the same candle is explicitly ambiguous. The partial candle in progress at alert time is excluded because M5 OHLC cannot isolate movement before/after that timestamp. These are observations, not a second win-rate calculation. Old records are enriched from available provider history without rewriting their original results. If a fresh UTC replay contradicts an old closed/cancelled result, the row is marked `REVIEW_REQUIRED` and excluded from forward performance; its historical replay and original result remain separately visible. A timezone repair is applied only if several unique price observations establish a consistent nonzero clock offset (or the provider declares a non-UTC timezone), and recreating that exact offset reproduces the original saved result (and exact fill/pips where available). Such rows become `RESOLVED_TIMEZONE`, retain `original_execution` and repair evidence, and remain in a separate `HISTORICAL_RECONSTRUCTION` group excluded from forward win rate. The source and missing-history status are visible.

`engine/market_snapshot.csv` preserves the latest closed M5 window in UTC used by the engine for auditing. A one-time diagnostic (`market_data_audit.json`) compares the legacy omitted-timezone request with explicit UTC and records provider metadata and price discrepancies, using one additional market-data request. New observations are persisted as they occur; missing older candles are never invented. CSV exports include target-path evidence. The original alert plan remains immutable in the journal even when the current analysis panel changes.

## Repeat-alert policy

`bosque_signals.py` implements `DIRECTION_SETUP_CYCLE_V1`: one delivered alert for a continuous BUY or SELL setup cycle. Small price changes, score/risk/news fluctuations, and changes from pullback to breakout within that direction do not create another alert. Two distinct observed closed M5 candles without a valid directional setup rearm delivery; a direction change starts another cycle. This is a delivery policy, not a change to the V3 setup, score, confirmation, SL/TP or RR calculations.

Cycle state survives workflow restarts. A failed send is not journaled as a received alert, and it can retry only on a later closed candle. The current candidate remains visible with a duplicate-suppression explanation. Network delivery is not exactly-once: an ambiguous Telegram timeout can still leave delivery uncertain. No automatic test messages are sent.

The original partial H1/M15 aggregation, directional scoring behavior and setup definitions are intentionally preserved. No new daily-loss lock, fresh-zone gate or session gate is introduced. These are labelled informational/not configured rather than pretending to operate. Telegram status reports actual send attempts; no test messages are sent automatically.

## Research backtest

`bosque-auto-backtest.yml` retains its separate weekday research schedule and downloads up to 5000 recent M5 bars by default. It can also be dispatched manually with history bounds. `bosque_backtest.py` uses the same V3 functions with prefix-only data, the same setup-cycle delivery policy, next-open paper fills, costs, and a chronological development/out-of-sample split. Recent-window research is not proof of long-term profitability. Results record the risk parameters and signal policy; the dashboard marks older configurations `OUTDATED_SETTINGS` until replay completes. Changes to engine/tracking/lifecycle code trigger a bounded research refresh. Backtest decisions use M5 close while live alerts arrive after the scan, so historical fill timing is not exact live equivalence.

Manual CSV workflow needs committed `backtest_data/*.csv`. CSV columns: datetime, open, high, low, close. Historical calendar JSON requires `coverage_start`, `coverage_end`, and `events` with timezone-aware `time`, `currency` and `impact`. Without historical news the result is explicitly labelled WITHOUT_NEWS_NOT_LIVE_EQUIVALENT. Live news observations are not a complete historical calendar. Engine sees news PASS/BLOCK; malformed provider responses block rather than silently become CLEAR.

Dashboard fetches current engine data and current V3 backtest results. The original navy theme is retained with refined spacing and layout; an expandable section exposes journal/research status and CSV download. Inactive or stale signals show WAIT. Session preference is informational.

## Tests

`python -m unittest discover -s tests -v`

`node tests/test_dashboard.cjs`

Validation runs on PRs and main. Engine/research workflows test before execution. Obsolete V5.3 support modules and their incompatible tests were removed; their history remains in Git.


## October 17 operational audit

- **Market data:** validated OHLC, timestamps and duplicates; only fully closed M5 candles enter analysis. Future timestamps, gaps in the last 12 bars, or last-candle close more than 10 minutes old block new alerts with a reason. A failed scan persists an ERROR snapshot without advancing the last successful engine timestamp. Browser freshness checks inspect the candle close as well as the engine timestamp.
- **News:** unknown high-impact geography, unreadable USD event times, malformed/truncated responses and API failures block alerts. Every response exposes normalized event titles/times and coverage status. A successful empty response means only that the provider listed no events; it is not a guarantee of calendar completeness. The window is 30 minutes before/after any matching USD event. Generic GDP/CPI names do not establish USD.
- **Backtest:** the same guarded paper-fill policy applies. Default simulated alert delay is 1 second after the M5 decision close; this deliberately excludes the in-progress alert candle so fill starts at the following whole M5 open. It is a declared timing assumption, not measured broker latency. OOS warm-up preserves the delivery cycle across the split. Original V3 partial H1/M15 aggregation is retained, using only data available at the decision time.
- **Historical news:** automatic research attempts to download the provider schedule for the complete requested period, validates every high-impact event and archives the normalized calendar with the workflow artifact. This is labelled `HISTORICAL_PROVIDER_SCHEDULE_NOT_POINT_IN_TIME`; revisions and unlisted events remain unknown. If coverage cannot be verified, research explicitly reports `WITHOUT_NEWS_NOT_LIVE_EQUIVALENT` with the failure status. Live observed news states are never passed off as a complete historical calendar.
- **Costs:** `ROUND_TRIP_COST_PIPS` remains unset until measured costs are supplied. No broker spread/commission is invented; results continue to identify gross versus configured-cost accounting. Paper fills are not a broker ledger.

External cron-job.org continues to dispatch `bosque-engine.yml`; there is no second engine schedule. Workflow concurrency prevents overlapping engine jobs. Support changes and the new execution model are covered by focused tests, alongside the immutable original V3 entry-function regression test.

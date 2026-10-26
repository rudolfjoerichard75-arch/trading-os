# Bosque Forex AI — SCALPING V3

V3 entry logic was refined on 2026-10-21 after the SL audit: complete M15/H1 candles, confirmed breakout retests, and direction-aligned scoring. `tests/fixtures/v3_original.py` remains the immutable historical reference; unchanged functions still have parity tests. No V5.3 entry filters are active.

Current risk range: **20–80 pips inclusive**, changed from 15–80 at the owner's request on 2026-10-16. Risk/target parameters remain unchanged; the entry refinements below apply to future signals. Live diagnostics and replay read this same engine setting; older journal/backtest records retain the settings used when generated.

## Execution

cron-job.org dispatches `.github/workflows/bosque-engine.yml` on `main`. The engine workflow has no internal cron. Manual workflow dispatch also works. Secrets: `TWELVEDATA_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

`bosque_tracking.py` adds persistent paper journaling, TP/SL outcome tracking, CSV exports, forward statistics, news observations and delivery-attempt status. Live and replay share the refined V3 entry calculations; alert delivery uses a persistent setup cycle to suppress repeat notifications. Existing journal rows and historical news files are preserved. Only V3 rows enter V3 statistics.

Set optional repository variable `ROUND_TRIP_COST_PIPS` to measured round-trip spread, slippage and commission in project pips (0.10 price). Without it, statistics are clearly labelled gross/unconfigured, not net validated.

## Paper execution and limitations

Signal entry prices are unchanged. Paper execution uses the next M5 open after the alert timestamp. An open beyond the original SL or TP2 is `NOT_ENTERED` (older rows retain `CANCELLED`); it is not a loss and is excluded from paper win rate. Reasons distinguish `OPEN_BEYOND_SL` and `OPEN_BEYOND_TP2`, with the next-open price recorded. TP2 remains the paper exit, SL wins same-bar ambiguity, gaps through SL fill at the worse open, and the holding limit is 72 M5 bars. Missing candles yield `DATA_GAP`. For new `GUARDED_NEXT_M5_OPEN_TP2_V2` records, next-open risk must also remain 20–80 pips and gross RR TP2 must be at least 2; otherwise the row is `NOT_ENTERED` with the measured risk/RR and reason. Original signal entry/SL/TP levels are never moved to force a fill. Older fills retain their actual outcome and get a separate `fill_audit`; the dashboard exposes both overall forward statistics and the new execution-model group. Historical fill-policy exceptions are not retroactively removed from performance. This is not a broker ledger.

Target observations are stored separately in each row's `signal_path`, including after the paper trade exits TP2 or has no fill. They track first TP1/TP2/TP3 touches and first observed SL for up to 72 candles. `TP3_AFTER_SL` is not a paper win; touching TP3 and SL in the same candle is explicitly ambiguous. The partial candle in progress at alert time is excluded because M5 OHLC cannot isolate movement before/after that timestamp. These are observations, not a second win-rate calculation. Old records are enriched from available provider history without rewriting their original results. If a fresh UTC replay contradicts an old closed/cancelled result, the row is marked `REVIEW_REQUIRED` and excluded from forward performance; its historical replay and original result remain separately visible. A timezone repair is applied only if several unique price observations establish a consistent nonzero clock offset (or the provider declares a non-UTC timezone), and recreating that exact offset reproduces the original saved result (and exact fill/pips where available). Such rows become `RESOLVED_TIMEZONE`, retain `original_execution` and repair evidence, and remain in a separate `HISTORICAL_RECONSTRUCTION` group excluded from forward win rate. The source and missing-history status are visible.

`engine/market_snapshot.csv` preserves the latest closed M5 window in UTC used by the engine for auditing. A one-time diagnostic (`market_data_audit.json`) compares the legacy omitted-timezone request with explicit UTC and records provider metadata and price discrepancies, using one additional market-data request. New observations are persisted as they occur; missing older candles are never invented. CSV exports include target-path evidence. The original alert plan remains immutable in the journal even when the current analysis panel changes.

## Repeat-alert policy

`bosque_signals.py` implements `DIRECTION_SETUP_CYCLE_V1`: one delivered alert for a continuous BUY or SELL setup cycle. Small price changes, score/risk/news fluctuations, and changes from pullback to breakout within that direction do not create another alert. Two distinct observed closed M5 candles without a valid directional setup rearm delivery; a direction change starts another cycle. This is a delivery policy, not a change to the V3 setup, score, confirmation, SL/TP or RR calculations.

Cycle state survives workflow restarts. A failed send is not journaled as a received alert, and it can retry only on a later closed candle. The current candidate remains visible with a duplicate-suppression explanation. Network delivery is not exactly-once: an ambiguous Telegram timeout can still leave delivery uncertain. No automatic test messages are sent.

The original partial H1/M15 aggregation, directional scoring behavior and setup definitions are intentionally preserved. No new daily-loss lock, fresh-zone gate or session gate is introduced. These are labelled informational/not configured rather than pretending to operate. Telegram status reports actual send attempts; no test messages are sent automatically.

## TP and close notifications

`bosque_trade_alerts.py` adds forward Telegram notifications for acknowledged V3 signals created after activation. It uses the existing closed-M5 journal evidence: TP1 touch, TP2 touch plus full-close instruction, TP3 observation after the TP2 paper exit, and close instructions for SL or the 72-candle holding limit. TP/SL touches in the same candle are not reported as certain TP successes; SL retains priority. No TP-success notification is sent after SL/time exit or for a rejected paper fill. `NOT_ENTERED` instead reports the fill rejection; missing candles request a manual MT5 check, never claim a close. No MT5 account is connected or automatically closed. Plans, entry rules, SL and target levels remain unchanged.

The first run persists an activation timestamp, so old journal history is not broadcast. New events have stable `signal_id:event_kind` IDs in `engine/trade_alerts.json`. Messages identify the signal, planned entry, original signal time and candle-close time in Malaysia time. Detection is on closed M5 candles at the existing approximately five-minute cron interval, with workflow/provider delay; it is not tick-by-tick. Events older than 10 minutes are recorded as `EXPIRED`, preventing stale close instructions after downtime. Market-derived events wait for healthy data; a confirmed data gap can produce a manual-check warning.

The engine collects events without sending. The workflow then reserves a bounded batch and **pushes the reservation to GitHub before sending**. Confirmed receipts are persisted afterward. A previous interrupted reservation or ambiguous Telegram outcome becomes `DELIVERY_UNKNOWN` and is never automatically resent. Explicit rejections can retry on a later scan, at most three attempts within the freshness window. This deliberately prefers a visible uncertain/missed delivery over a duplicate close alert; network delivery is not exactly-once. A manual workflow rerun has a different run-attempt identifier. No test notifications are sent to the real chat. Delivery counts are exposed under `services.trade_alerts` and in `telegram_health.json`.

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
- **Backtest:** the same guarded paper-fill policy applies. Default simulated alert delay is 1 second after the M5 decision close; this deliberately excludes the in-progress alert candle so fill starts at the following whole M5 open. It is a declared timing assumption, not measured broker latency. OOS warm-up preserves the delivery cycle across the split. Only complete, contiguous M5 groups form H1/M15 candles; partial higher-timeframe bars are excluded in live and replay.
- **Historical news:** automatic research attempts to download the provider schedule for the complete requested period, validates every high-impact event and archives the normalized calendar with the workflow artifact. This is labelled `HISTORICAL_PROVIDER_SCHEDULE_NOT_POINT_IN_TIME`; revisions and unlisted events remain unknown. If coverage cannot be verified, research explicitly reports `WITHOUT_NEWS_NOT_LIVE_EQUIVALENT` with the failure status. Live observed news states are never passed off as a complete historical calendar.
- **Costs:** `ROUND_TRIP_COST_PIPS` remains unset until measured costs are supplied. No broker spread/commission is invented; results continue to identify gross versus configured-cost accounting. Paper fills are not a broker ledger.

External cron-job.org continues to dispatch `bosque-engine.yml`; there is no second engine schedule. Workflow concurrency prevents overlapping engine jobs. Support changes and the new execution model are covered by focused tests, alongside historical parity tests for unchanged functions and behavioral tests for refined entry rules.

## Entry refinement — 2026-10-21

`V3_CLOSED_MTF_RETEST_ALIGNED_20261021` identifies new signal parameters and invalidates older backtest configurations. Existing journal outcomes and open-trade SL/TP are not rewritten.

- H1/M15 aggregation requires every constituent M5 bar, on aligned unique UTC timestamps. Incomplete groups, including the leading history boundary, are excluded.
- BREAKOUT RETEST requires a closed M15 crossing of a previously known swing within the last four closed M15 bars, followed by a later M5 candle touching the frozen level and closing away from it in the trade direction. A close back through the level invalidates the break. The breakout candle itself cannot count as its retest.
- H1 trend/BOS, M15 sweep/BOS and M5 BOS earn points only in the proposed trade direction. A score of 70 is a rule score, not a win probability. Independent pullback and sweep setups retain their priority.
- Session remains a preference, not a hard gate. TP1 remains observational; automatic breakeven/partial exits have not been introduced. Risk stays 20–80 pips, with the existing guarded fill and TP2 exit.

Validation covers partial/missing/duplicate candles, both retest directions, premature/failed/expired breaks, opposing score evidence, live/replay consistency and existing journal/alert behavior. This change has no validated win-rate claim; research must use the new configuration.

## Entry range display — 2026-10-21

New Telegram signals display `📍 Entry : low-high` to two decimal places. The shared risk/RR window is capped at 40 pips (4.00 price units at PIP_SIZE=0.10): The range is anchored at the planned entry, clamped inward to valid risk/RR bounds: SELL extends upward toward better prices and BUY downward. When no planned price is supplied, it uses the valid lower SELL / upper BUY boundary. Narrower valid windows remain narrower. For example, SELL SL 4119.26 / TP2 4096.27 gives 4111.60–4115.60. This is a risk/RR-derived entry window, not a newly detected supply/demand zone.

Risk, target distances and RR in the signal now describe both ends of the entry range. SL and TP prices remain frozen. New journal parameters record `RISK_RR_DIRECTIONAL_40_PIPS_V1`; paper fills outside the displayed range are NOT_ENTERED with `FILL_OUTSIDE_ENTRY_ZONE`. Existing trades without that policy keep their original fill rules. Backtests with earlier configurations are marked outdated on the next sync. Existing Telegram messages are not edited or resent.

## Learning Journal — data collection only

The live engine now freezes an input snapshot **before Telegram delivery**, once per observed M5 source candle and configuration. It records the engine-selected candidate (including rejected candidates and NO_SETUP scans), H1/M15/M5 structure, confirmation, PD/range/liquidity, ATR, session, observed news, hard-gate results, risk/targets, entry range, signed distances to swings and the last M5 candle. It does not enumerate hypothetical alternative setups that V3 never selected. No new API request is made.

- `engine/learning/features/YYYY-MM-DD.json`: first-observed feature records, partitioned by source candle date in Malaysia. Repeated scans are idempotent. A revised decision on the same candle cannot overwrite the snapshot or be linked to mismatched features.
- `engine/learning/outcomes/YYYY-MM-DD.json`: separate delivery observations and linked forward paper-trade outcomes, including fills, R/pips, costs, TP1 flag, target path and M5-based MFE/MAE where complete history exists. Exit-bar extremes may occur after exit, so these excursions are not tick-accurate.
- `engine/learning_summary.json` and the dashboard journal panel: current collection status, today's snapshot/candidate/rejection counts, linked paper outcomes and counts with/without configured costs. Collection is not model training.

Only new trades carrying a learning ID may join to a pre-delivery snapshot. Historical/reconstructed rows are not backfilled into training data. NO ENTRY, unresolved/data-gap rows, ambiguous same-bar SL/TP, unhealthy inputs, mismatched settings and fills preceding capture receive no classifier WIN/LOSS label. Unconfigured costs are never treated as a confirmed net result. Rejected or undelivered candidates have no fabricated counterfactual trade outcomes; they are retained for later research, not labeled as losses. This means the observed outcome set is still selected by V3 and is not an unbiased sample of all setups.

Features and results remain separate; future training must explicitly select input fields, split chronologically, group overlapping setups and separate logic versions. `eligible_for_gross_research`/`eligible_for_net_research` are data-quality flags, not authorization to train or deploy. No trained model, automatic optimization, new signal filter or extra Telegram alert is introduced. Risk stays 20–80 pips and entry-zone width stays at most 40 pips. Learning I/O failures surface as ERROR/DEGRADED and never alter V3 signal decisions. Daily files are retained in Git by the existing engine workflow; no silent pruning is applied.


### Range and waiting-entry repair (23 October 2026)

Entry version `V3_RANGE_EFFICIENCY_20261023` replaces the price-percentage range
heuristic with a 20-bar test: net/travel efficiency <= 0.35, absolute fitted
close drift / high-low width <= 0.50, and width / mean last-14 true range <= 6.
Flat or insufficient data is not a range. These are predeclared operational
thresholds, not optimized or proven profitable. Liquidity sweep remains a
separate fallback setup; changing the range label need not remove that signal.

New paper model `ZONE_WAIT_M5_OPEN_TP2_V3` waits up to 30 minutes from the
acknowledged alert for a full M5 open in the frozen entry zone, with original
risk/RR checks. The deadline is exclusive. Entry/SL/TP are never moved. A
pre-fill SL/TP2 touch retires the plan; gaps remain DATA_GAP. Wick-only zone
touches are recorded but cannot fill this conservative OHLC model. This is
not tick execution or a broker limit-order simulation. Waiting does not
re-evaluate news or the setup; it tracks the original published plan.

Pending rows persist across scans/restarts. Waiting bars do not consume the
72-bar holding limit. Expired/unfilled rows are not losses or training labels.
TP alerts for new trades use post-fill TP1/TP2 evidence; earlier price touches
are not counted as trade successes. Old trades retain their execution rules.

Dashboard headline journal metrics use the exact current configuration;
`forward_test.json` retains all-history and entry-version/execution groups.
Backtests with old settings become outdated. Learning records include the
execution model and deadline. No trained AI or POI/Volume Profile filter is
activated by this repair.

## Direct iPhone Web Push and manual refresh

The header Refresh button fetches the latest published snapshot; it does not run
TwelveData or create a new signal. The engine timestamp remains visible.

Web Push runs in the existing engine workflow alongside Telegram. Setup requires
repository-owner access to **Settings → Secrets and variables → Actions**:

1. On iPhone, add the dashboard to Home Screen and open that icon.
2. Open **Notifikasi**, select **Sediakan kunci pemilik** once. Copy the generated
   value to a repository secret named `PUSH_VAPID_PRIVATE_KEY`. Do not commit it.
   Keep the app open until this value has been saved; the app does not store it.
3. Select **Aktifkan notifikasi**, grant permission, and copy the JSON to a secret
   named `PUSH_SUBSCRIPTIONS`. This JSON is an array; preserve other devices if adding one.
4. The next normal engine workflow publishes only the public key and sends one
   **Ujian server** notification to each new subscription. This confirms the
   server-to-phone path when actually received. **Uji paparan telefon** is local
   only and is not proof that the server is configured.

No user subscription, endpoint, auth key, or private key is committed. Public
push_state.json contains hashed device IDs and delivery metadata only. Never
paste either secret in chat or an issue. Remove subscriptions from the secret
when decommissioning devices. The app's off button also unsubscribes locally.

Delivery reservations are committed before sending. Ambiguous/interrupted sends
are not automatically retried, so duplicates are avoided at the cost of possibly
missing one alert. Provider acceptance does not prove phone display. Expired
subscriptions require re-registration. Focus, connectivity, and iOS settings can
delay display. Entry pushes mirror newly recorded SENT signals; target alerts use
the existing closed-M5 event stream. This does not add tick data or MT5 execution.
Old events before push activation and events older than ten minutes are excluded;
push TTL is five minutes. Telegram configuration and entry logic are unchanged.

Activation is incomplete until both secrets are saved, the workflow succeeds,
and the server test is received on the phone. GitHub Pages alone cannot store
subscriptions; this owner-operated setup deliberately uses private Actions Secrets.

### Offline MT5 import and entry gate audit

`engine/bosque_mt5.py` converts a tab-separated MT5 M1 export to the existing
replay CSV schema. Supply the **broker server timezone**, not the computer timezone:

```sh
python engine/bosque_mt5.py --m1 /path/to/export.csv --server-timezone Etc/GMT-3 --output /tmp/m5.csv
```

`Etc/GMT-3` means UTC+3 and is an example, not a verified HFM setting. The audit
sidecar explicitly marks the timezone as unverified. Duplicate timestamps,
invalid OHLC, unordered rows and missing timezone are rejected. Only M5 groups
containing all five distinct M1 timestamps are retained; gaps are never filled.
An import does not change the production data feed.

Replay results now include `gate_audit`: evaluated scans, health-blocked scans
and non-exclusive rejection counts for score, setup, M5 confirmation, plan, RR
and news. One scan can fail multiple gates. This exposes why there are no
signals without relaxing entry rules. Historical news must still be supplied
for meaningful news-filter evaluation. M1-derived M5 replay does not simulate
Bid/Ask tick fills. Raw broker exports should stay outside this public repository.

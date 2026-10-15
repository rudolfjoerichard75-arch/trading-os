# Bosque Forex AI — SCALPING V3

The original September 28 V3 setup, score, confirmation, aggregation and trade-plan functions are preserved. `tests/fixtures/v3_original.py` is the immutable regression reference. No V5.3 entry filters are active.

Current risk range: **20–80 pips inclusive**, changed from 15–80 at the owner's request on 2026-10-16. Other V3 entry parameters and functions remain unchanged. Live diagnostics and replay read this same engine setting; older journal/backtest records retain the settings used when generated.

## Execution

cron-job.org dispatches `.github/workflows/bosque-engine.yml` on `main`. The engine workflow has no internal cron. Manual workflow dispatch also works. Secrets: `TWELVEDATA_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

`bosque_tracking.py` adds persistent paper journaling, TP/SL outcome tracking, CSV exports, forward statistics, news observations and delivery-attempt status. These do not gate V3 entries. Existing journal rows and historical news files are preserved. Only V3 rows enter V3 statistics.

Set optional repository variable `ROUND_TRIP_COST_PIPS` to measured round-trip spread, slippage and commission in project pips (0.10 price). Without it, statistics are clearly labelled gross/unconfigured, not net validated.

## Paper execution and limitations

Signal entry prices are unchanged. Paper execution uses the next available M5 open after the signal timestamp; open outside SL/TP2 cancels the simulated fill. TP2 is the primary exit; TP1/TP3 touches are descriptive, not three separate trades. SL wins same-bar ambiguity. Gaps through SL fill at the worse open. Maximum holding time is 72 M5 bars. Missing bars yield DATA_GAP and are excluded from win rate. Signals may overlap because original V3 does not block them. This is not a broker ledger.

The original partial H1/M15 aggregation, directional scoring behavior and setup definitions are intentionally preserved. No new daily-loss lock, fresh-zone gate or session gate is introduced. These are labelled informational/not configured rather than pretending to operate. Telegram status reports actual send attempts; no test messages are sent automatically.

## Research backtest

`bosque-auto-backtest.yml` retains its separate weekday research schedule and downloads up to 5000 recent M5 bars by default. It can also be dispatched manually with history bounds. `bosque_backtest.py` uses the same V3 functions with prefix-only data, next-open paper fills, costs, and a chronological development/out-of-sample split. Recent-window research is not proof of long-term profitability.

Manual CSV workflow needs committed `backtest_data/*.csv`. CSV columns: datetime, open, high, low, close. Historical calendar JSON requires `coverage_start`, `coverage_end`, and `events` with timezone-aware `time`, `currency` and `impact`. Without historical news the result is explicitly labelled WITHOUT_NEWS_NOT_LIVE_EQUIVALENT. Live news observations are not a complete historical calendar. Engine sees news PASS/BLOCK; malformed provider responses block rather than silently become CLEAR.

Dashboard fetches current engine data and current V3 backtest results. The original navy theme is retained with refined spacing and layout; an expandable section exposes journal/research status and CSV download. Inactive or stale signals show WAIT. Session preference is informational.

## Tests

`python -m unittest discover -s tests -v`

`node tests/test_dashboard.cjs`

Validation runs on PRs and main. Engine/research workflows test before execution. Obsolete V5.3 support modules and their incompatible tests were removed; their history remains in Git.

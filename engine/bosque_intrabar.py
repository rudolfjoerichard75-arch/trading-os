"""Observed M5 snapshots for live decisions; never reconstruct them from final OHLC."""
import pandas as pd
from bosque_tracking import bars, stamp
from bosque_health import market_health


def decision_frames(raw, at):
    now = stamp(at)
    df = bars(raw)
    closed = df[df.datetime + pd.Timedelta(minutes=5) <= now].copy()
    closed.attrs.update(raw.attrs)
    current = df[(df.datetime <= now) & (df.datetime + pd.Timedelta(minutes=5) > now)]
    health = market_health(closed, now)
    reasons = [] if health['allowed'] else [health['reason']]
    if (df.datetime > now).any(): reasons.append('Timestamp candle mendahului masa semasa')
    if len(current) != 1 or current.iloc[-1].datetime != now.floor('5min'):
        reasons.append('Snapshot candle M5 semasa belum tersedia daripada provider')
    if len(current) and len(closed) and current.iloc[-1].datetime - closed.iloc[-1].datetime != pd.Timedelta(minutes=5):
        reasons.append('Candle M5 sebelum snapshot semasa hilang')
    # Analysis may use the observed forming candle. Accounting receives only closed.
    observed = df[df.datetime <= now].copy()
    observed.attrs.update(raw.attrs)
    health.update(status='BLOCKED' if reasons else 'INTRABAR_SNAPSHOT', allowed=not reasons,
                  reason='; '.join(reasons) or 'Snapshot M5 sedang terbentuk; belum candle close',
                  entry_mode='INTRABAR', observed_at=now.isoformat(),
                  current_candle_open=current.iloc[-1].datetime.isoformat() if len(current) else None,
                  current_candle_close=(current.iloc[-1].datetime+pd.Timedelta(minutes=5)).isoformat() if len(current) else None,
                  quote_timestamp_verified=False)
    return observed, closed, health

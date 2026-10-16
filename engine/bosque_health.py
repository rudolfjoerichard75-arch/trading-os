"""Freshness checks and failure reporting; no V3 strategy calculations."""
import pandas as pd

MAX_CLOSE_AGE_MINUTES = 10


def market_health(frame, at):
    from bosque_tracking import bars, stamp
    df = bars(frame)
    now = stamp(at)
    latest = df.iloc[-1].datetime if len(df) else None
    close = latest + pd.Timedelta(minutes=5) if latest is not None else None
    age = (now-close).total_seconds()/60 if close is not None else None
    recent = df.datetime.tail(12).diff().dt.total_seconds()/60
    reasons = []
    if latest is None: reasons.append('Tiada candle M5')
    elif age < 0 or frame.attrs.get('future_candles', 0): reasons.append('Timestamp candle mendahului masa semasa')
    elif age > MAX_CLOSE_AGE_MINUTES: reasons.append(f'Candle M5 lama: {age:.1f} minit selepas penutupan')
    if (recent > 5).any(): reasons.append('Candle M5 hilang dalam 12 candle terkini')
    if len(df) and ((df.datetime.dt.minute % 5 != 0) | (df.datetime.dt.second != 0)).any():
        reasons.append('Timestamp candle tidak sejajar dengan M5')
    return {'status': 'FRESH' if not reasons else 'BLOCKED', 'allowed': not reasons,
            'checked_at': now.isoformat(), 'last_candle_open': latest.isoformat() if latest is not None else None,
            'last_candle_close': close.isoformat() if close is not None else None,
            'close_age_minutes': round(age, 2) if age is not None else None,
            'max_close_age_minutes': MAX_CLOSE_AGE_MINUTES,
            'recent_gap_count': int((recent > 5).sum()),
            'reason': '; '.join(reasons) or 'Candle M5 lengkap dan terkini'}


def record_failure(exc):
    import bosque_engine as e
    from bosque_tracking import load, save
    now = e.now_my().isoformat()
    try: d = load(e.DASHBOARD_FILE)
    except (ValueError, OSError): d = {}
    d.setdefault('engine', {'version': 'SCALPING V3'})
    d['engine']['last_failure_at'] = now
    d['data_health'] = {'status': 'ERROR', 'allowed': False, 'checked_at': now,
                        'error_type': type(exc).__name__, 'reason': 'Scan gagal; data terkini belum disahkan'}
    d['signal'] = {**d.get('signal', {}), 'active': False,
                   'delivery': {'status': 'DATA_BLOCKED', 'allowed': False}}
    d.setdefault('filters', {})['data'] = False
    d.setdefault('gate_reasons', {})['data'] = d['data_health']['reason']
    d.setdefault('services', {})['engine'] = d['data_health']
    save(e.DASHBOARD_FILE, d)
    save(e.ENGINE_DIR/'engine_health.json', d['data_health'])

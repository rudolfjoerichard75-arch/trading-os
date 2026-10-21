"""Offline experimental persistent M15 zones. No live engine imports this module."""
import hashlib
import pandas as pd

POLICY = 'M15_OB_FVG_PERSISTENT_V1'
TTL = pd.Timedelta(hours=4)
CONFIRM_WINDOW = pd.Timedelta(minutes=15)

def form_zones(m15):
    """Only call with closed, contiguous M15 bars; emit zones formed on last bar.

    FVG: three-candle gap and middle body >=1.5x preceding 10-body median.
    OB: last opposite candle in preceding three candles before a displacement
    close beyond the preceding five highs/lows. Zone uses entire OB candle.
    Both require a nonzero reference median and directional displacement body.
    """
    if len(m15) < 13:
        return []
    df = m15.tail(13).reset_index(drop=True)
    if not (df.datetime.diff().dropna() == pd.Timedelta(minutes=15)).all():
        return []
    a, mid, last = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    known = last.datetime + pd.Timedelta(minutes=15)
    body = (df.close - df.open).abs()
    out = []
    def add(kind, direction, low, high, origin):
        key = f'{POLICY}|{kind}|{direction}|{known.isoformat()}|{low}|{high}'
        out.append({'id':hashlib.sha256(key.encode()).hexdigest()[:20],
            'type':kind,'direction':direction,'low':float(low),'high':float(high),
            'origin_at':origin.isoformat(),'formed_at':known.isoformat(),
            'expires_at':(known+TTL).isoformat(),'status':'WAITING','touched_at':None})
    ref = body.iloc[-12:-2].median()
    if ref > 0 and body.iloc[-2] >= 1.5*ref:
        if mid.close > mid.open and last.low > a.high:
            add('FVG','BUY',a.high,last.low,mid.datetime)
        if mid.close < mid.open and last.high < a.low:
            add('FVG','SELL',last.high,a.low,mid.datetime)
    ref = body.iloc[-11:-1].median()
    if ref > 0 and body.iloc[-1] >= 1.5*ref:
        for direction, sign in [('BUY',1),('SELL',-1)]:
            boundary = df.high.iloc[-6:-1].max() if sign==1 else df.low.iloc[-6:-1].min()
            if sign*(last.close-last.open)<=0 or sign*(last.close-boundary)<=0:
                continue
            for j in range(len(df)-2,len(df)-5,-1):
                ob = df.iloc[j]
                if sign*(ob.close-ob.open)<0:
                    add('OB',direction,ob.low,ob.high,ob.datetime)
                    break
    return out

def advance_zone(zone, candle):
    """Touch then a later M5 confirmation opportunity, never formation-bar entry.

    Invalidate on a close through the far edge; expire four hours after formation.
    First touch allows the next three M5 closes to confirm, then zone retires.
    """
    if zone['status'] not in ('WAITING','TOUCHED'):
        return False
    opened = candle.datetime
    closed = opened + pd.Timedelta(minutes=5)
    if opened < pd.Timestamp(zone['formed_at']):
        return False
    if closed > pd.Timestamp(zone['expires_at']):
        zone['status']='EXPIRED'; return False
    invalid = candle.close < zone['low'] if zone['direction']=='BUY' else candle.close > zone['high']
    if invalid:
        zone['status']='INVALIDATED'; return False
    if zone['touched_at'] is None:
        if candle.low <= zone['high'] and candle.high >= zone['low']:
            zone.update(status='TOUCHED',touched_at=closed.isoformat())
        return False
    touched = pd.Timestamp(zone['touched_at'])
    if closed > touched + CONFIRM_WINDOW:
        zone['status']='CONFIRM_TIMEOUT'; return False
    # Confirmation must be after touch and close back beyond the near edge.
    reclaimed = candle.close > zone['high'] if zone['direction']=='BUY' else candle.close < zone['low']
    return bool(closed > touched and reclaimed)

"""Experimental POI annotations. Not imported by the live signal engine.

All levels use closed candles. Liquidity levels are confirmed H1 pivots known
before the latest M5 candle, expire after 24 hours from confirmation, and are
invalidated by an earlier M5 close through the level. A fresh sweep and reclaim
on the latest M5 is required. These are level tests, not new entry zones.
"""
import pandas as pd
import bosque_engine as e
import bosque_tracking as t

POLICY = 'POI_SHADOW_V1_H1_24H_FRESH_SWEEP'

def annotate(frame, at, direction):
    df = t.bars(frame)
    at = t.stamp(at)
    if (df.datetime + pd.Timedelta(minutes=5) > at).any():
        raise ValueError('POI received an unclosed candle')
    result = {'policy': POLICY, 'breakout_retest': False,
              'h1_liquidity': False, 'either': False, 'levels': []}
    if direction not in ('BUY', 'SELL') or len(df) < 2:
        return result
    retest = e.breakout_retest(e.aggregate(df, 15), df)
    if retest and retest['direction'] == direction:
        result['breakout_retest'] = True
        result['levels'].append({'type': 'BREAKOUT_RETEST', **retest})
    latest = df.iloc[-1]
    # Exclude the reaction candle when establishing previously known H1 levels.
    h1 = e.aggregate(df.iloc[:-1], 60)
    pivots = e.find_swing_lows(h1) if direction == 'BUY' else e.find_swing_highs(h1)
    sign = 1 if direction == 'BUY' else -1
    for pivot in reversed(pivots):
        confirmed = h1.iloc[pivot['index'] + 2].datetime + pd.Timedelta(hours=1)
        if at - confirmed > pd.Timedelta(hours=24):
            continue
        level = pivot['price']
        prior = df[(df.datetime >= confirmed) & (df.datetime < latest.datetime)]
        if (sign * (prior.close - level) <= 0).any():
            continue
        swept = latest.low < level if sign == 1 else latest.high > level
        if swept and sign * (latest.close - level) > 0 and sign * (latest.close - latest.open) > 0:
            result['h1_liquidity'] = True
            result['levels'].append({'type': 'H1_LIQUIDITY', 'level': level,
                                     'confirmed_at': confirmed.isoformat()})
            break
    result['either'] = result['breakout_retest'] or result['h1_liquidity']
    return result

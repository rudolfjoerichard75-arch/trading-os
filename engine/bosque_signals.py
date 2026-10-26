"""Delivery lifecycle only; V3 setup/score/risk decisions remain in the engine."""
import hashlib
import pandas as pd

POLICY = 'DIRECTION_SETUP_CYCLE_V1'
REARM_BARS = 2


def select_alert(state, setup_valid, direction, eligible, candle):
    """One alert per directional setup cycle, regardless of small price changes.

    Two distinct observed M5 periods without a valid directional setup rearm it.
    In intrabar mode these are sampled observations, not close confirmations.
    A direction change starts a new cycle. Score/risk/news fluctuations and
    opportunity-name changes alone do not rearm an already delivered alert.
    The caller persists attempts before sending and marks successful delivery.
    """
    candle = pd.to_datetime(candle, utc=True).isoformat()
    seen = state.get('last_seen_bar')
    if seen and pd.to_datetime(candle) < pd.to_datetime(seen):
        return {'allowed':False, 'status':'STALE_CANDLE', 'id':None, 'policy':POLICY}
    state['last_seen_bar'] = candle
    cycle = state.get('signal_cycle')
    if cycle and cycle.get('last_bar')==candle and cycle.get('direction')!=direction and direction in ('BUY','SELL'):
        return {'allowed':False, 'status':'SAME_BAR_REEVALUATION', 'id':cycle['id'], 'policy':POLICY}
    if not setup_valid or direction not in ('BUY', 'SELL'):
        if cycle and cycle.get('last_bar') != candle:
            cycle['last_bar'] = candle
            cycle['inactive_bars'] = cycle.get('inactive_bars', 0) + 1
            if cycle['inactive_bars'] >= REARM_BARS:
                state['signal_cycle'] = None
        return {'allowed': False, 'status': 'WAIT', 'id': None, 'policy': POLICY}
    if not cycle or cycle.get('direction') != direction:
        sid = hashlib.sha256(f'{POLICY}|XAU/USD|{direction}|{candle}'.encode()).hexdigest()[:16]
        cycle = {'id': sid, 'direction': direction, 'started_bar': candle,
                 'delivered': False, 'inactive_bars': 0}
        state['signal_cycle'] = cycle
    cycle.update(last_bar=candle, inactive_bars=0)
    if not eligible:
        status = 'WAIT'
    elif cycle.get('delivery_pending') or cycle.get('delivery_uncertain'):
        status = 'DELIVERY_UNKNOWN'
    elif cycle.get('delivered'):
        status = 'DUPLICATE_SUPPRESSED'
    elif cycle.get('last_attempt_bar') == candle:
        status = 'ALREADY_ATTEMPTED_BAR'
    else:
        cycle['last_attempt_bar'] = candle
        status = 'READY'
    return {'allowed': status == 'READY', 'status': status, 'id': cycle['id'], 'policy': POLICY}


def mark_delivered(state, at):
    cycle = state['signal_cycle']
    cycle.update(delivered=True, sent_at=at)
    state.update(last_signal_id=cycle['id'], last_signal_sent=at)

"""Paper execution checks, separate from unchanged V3 signal generation."""
import math

MODEL = 'GUARDED_NEXT_M5_OPEN_TP2_V2'
LEGACY_MODEL = 'NEXT_M5_OPEN_TP2_V1'


def assess(trade, price):
    import bosque_engine as e
    limits = trade.get('parameters') or {}
    minimum = limits.get('min_risk_pips', e.MIN_RISK_PIPS)
    maximum = limits.get('max_risk_pips', e.MAX_RISK_PIPS)
    min_rr = limits.get('min_rr', e.MIN_RR)
    sign = 1 if trade['direction'] == 'BUY' else -1
    risk = sign * (price - trade['sl']) / e.PIP_SIZE
    reward = sign * (trade['tp2'] - price) / e.PIP_SIZE
    rr = reward / risk if risk > 0 else None
    reasons = []
    if not all(math.isfinite(x) for x in (price, risk, reward)):
        reasons.append('INVALID_FILL_PRICE')
    elif risk <= 0:
        reasons.append('OPEN_BEYOND_SL')
    elif reward <= 0:
        reasons.append('OPEN_BEYOND_TP2')
    else:
        if risk < minimum - 1e-8: reasons.append('FILL_RISK_BELOW_MIN')
        if risk > maximum + 1e-8: reasons.append('FILL_RISK_ABOVE_MAX')
        if rr < min_rr - 1e-8: reasons.append('FILL_RR_BELOW_MIN')
    return {'status': 'PASS' if not reasons else 'OUTSIDE_LIMITS',
            'price': price, 'risk_pips': round(risk, 4) if math.isfinite(risk) else None,
            'rr_tp2': round(rr, 4) if rr is not None and math.isfinite(rr) else None,
            'reasons': reasons, 'limits': {'min_risk_pips': minimum,
                'max_risk_pips': maximum, 'min_rr': min_rr}}


def annotate(trade):
    """Old results remain immutable; expose their original fill risk and RR."""
    price = trade.get('entry')
    if price is None: price = trade.get('next_open')
    if price is None: return
    audit = assess(trade, float(price))
    audit['enforced'] = trade.get('execution_model') == MODEL
    if not audit['enforced']: audit['note'] = 'Historical fill audit only; original outcome retained.'
    trade['fill_audit'] = audit

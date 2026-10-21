"""Shared entry zone and guarded paper execution checks."""
import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

MODEL = 'GUARDED_NEXT_M5_OPEN_TP2_V2'
LEGACY_MODEL = 'NEXT_M5_OPEN_TP2_V1'
ENTRY_ZONE_MAX_PIPS = 40
ENTRY_ZONE_POLICY = 'RISK_RR_DIRECTIONAL_40_PIPS_V1'


def entry_window(trade):
    """Inclusive cent prices for frozen SL/TP2, capped at 40 pips before costs."""
    import bosque_engine as e
    limits = trade.get('parameters') or {}
    try:
        sl, tp = Decimal(str(trade['sl'])), Decimal(str(trade['tp2']))
        minimum = Decimal(str(limits.get('min_risk_pips', e.MIN_RISK_PIPS)))
        maximum = Decimal(str(limits.get('max_risk_pips', e.MAX_RISK_PIPS)))
        rr = Decimal(str(limits.get('min_rr', e.MIN_RR)))
        pip = Decimal(str(e.PIP_SIZE))
        if not all(x.is_finite() for x in (sl,tp,minimum,maximum,rr,pip)):
            raise ValueError('Nonfinite plan')
        if minimum <= 0 or maximum < minimum or rr <= 0 or pip <= 0:
            raise ValueError('Invalid limits')
        boundary = (tp + rr * sl) / (1 + rr)
        if trade['direction'] == 'BUY' and tp > sl:
            low, high = sl + minimum*pip, min(sl+maximum*pip,boundary)
        elif trade['direction'] == 'SELL' and tp < sl:
            low, high = max(sl-maximum*pip,boundary), sl-minimum*pip
        else:
            raise ValueError('Invalid direction/targets')
        low=low.quantize(Decimal('.01'),rounding=ROUND_CEILING)
        high=high.quantize(Decimal('.01'),rounding=ROUND_FLOOR)
        if low > high: return {'status':'EMPTY'}
        # Anchor at the planned entry (never the changing paper fill).
        anchor = trade.get('planned_entry', trade.get('entry'))
        if anchor is not None:
            anchor = Decimal(str(anchor))
            if not anchor.is_finite(): raise ValueError('Nonfinite entry')
            anchor = min(high, max(low, anchor))
            if trade['direction'] == 'SELL':
                low = anchor.quantize(Decimal('.01'), rounding=ROUND_CEILING)
            else:
                high = anchor.quantize(Decimal('.01'), rounding=ROUND_FLOOR)
        # SELL extends upward toward a better price; BUY downward.
        width = Decimal(str(ENTRY_ZONE_MAX_PIPS)) * pip
        if trade['direction'] == 'SELL':
            high = min(high, low + width).quantize(Decimal('.01'), rounding=ROUND_FLOOR)
        else:
            low = max(low, high - width).quantize(Decimal('.01'), rounding=ROUND_CEILING)
        return {'status':'AVAILABLE','low':float(low),'high':float(high),
                'min_risk_pips':float(minimum),'max_risk_pips':float(maximum),
                'width_pips':float((high-low)/pip),'max_width_pips':ENTRY_ZONE_MAX_PIPS,
                'policy':ENTRY_ZONE_POLICY,'min_rr':float(rr),'basis':'FROZEN_SL_TP2_BEFORE_COSTS'}
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return {'status':'UNAVAILABLE'}


def entry_window_text(trade):
    w=entry_window(trade)
    if w['status'] != 'AVAILABLE':
        return 'Julat entry risk/RR: tiada julat sah' if w['status']=='EMPTY' else 'Julat entry risk/RR: belum tersedia'
    return (f"Julat entry risk/RR: {w['low']:.2f}–{w['high']:.2f} "
            f"(risk {w['min_risk_pips']:g}–{w['max_risk_pips']:g} pips; RR TP2 ≥ 1:{w['min_rr']:g}). "
            'Berdasarkan SL/TP2 asal, sebelum kos. Bukan zon teknikal atau arahan entry; semak setup semasa.')


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
    if limits.get('entry_zone_policy') == ENTRY_ZONE_POLICY and math.isfinite(price):
        zone = entry_window(trade)
        if zone['status'] != 'AVAILABLE' or not (zone['low'] - 1e-8 <= price <= zone['high'] + 1e-8):
            reasons.append('FILL_OUTSIDE_ENTRY_ZONE')
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

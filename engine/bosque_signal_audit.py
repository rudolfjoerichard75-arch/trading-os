"""Observed target paths, kept separate from paper fills and paper win rate."""
import pandas as pd

MODEL = 'M5_TARGET_PATH_V1'
MAX_BARS = 72


def observe(trade, frame, reconstructed=False):
    start = pd.to_datetime(trade['timestamp'], utc=True).ceil('5min')
    path = trade.setdefault('signal_path', {
        'model': MODEL, 'status': 'TRACKING', 'bars_observed': 0,
        'observation_start': start.isoformat(), 'targets': {},
        'source': 'RECONSTRUCTED_PROVIDER_HISTORY' if reconstructed else 'FORWARD_OBSERVED',
        'partial_signal_bar_excluded': start != pd.to_datetime(trade['timestamp'], utc=True),
        'max_bars': MAX_BARS, 'sl_first_at': None
    })
    if path['status'] != 'TRACKING':
        return
    last = pd.to_datetime(path['last_bar'], utc=True) if path.get('last_bar') else None
    rows = frame[frame.datetime >= start] if last is None else frame[frame.datetime > last]
    buy = trade['direction'] == 'BUY'
    for r in rows.itertuples(index=False):
        expected = start if last is None else last + pd.Timedelta(minutes=5)
        if r.datetime > expected:
            path.update(status='DATA_GAP', missing_from=expected.isoformat())
            return
        path['bars_observed'] += 1
        path['last_bar'] = r.datetime.isoformat()
        last = r.datetime
        slhit = r.low <= trade['sl'] if buy else r.high >= trade['sl']
        stopped_before = path['sl_first_at'] is not None
        for target in ('tp1', 'tp2', 'tp3'):
            hit = r.high >= trade[target] if buy else r.low <= trade[target]
            if hit and target not in path['targets']:
                order = 'AFTER_SL' if stopped_before else 'SAME_BAR_AS_SL' if slhit else 'BEFORE_OBSERVED_SL'
                path['targets'][target] = {'bar': r.datetime.isoformat(), 'order': order}
        if slhit and not stopped_before:
            path['sl_first_at'] = r.datetime.isoformat()
        if 'tp3' in path['targets']:
            order = path['targets']['tp3']['order']
            path['status'] = {'AFTER_SL': 'TP3_AFTER_SL', 'SAME_BAR_AS_SL': 'AMBIGUOUS_SL_TP3',
                              'BEFORE_OBSERVED_SL': 'TP3_OBSERVED'}[order]
            return
        if path['bars_observed'] >= MAX_BARS:
            path['status'] = 'WINDOW_ENDED'
            return


def audit_unfilled(trade, frame):
    """Enrich old cancelled records without rewriting their original outcome."""
    if trade.get('result') not in ('CANCELLED', 'NOT_ENTERED') or trade.get('execution_audit'):
        return
    at = pd.to_datetime(trade['timestamp'], utc=True).ceil('5min')
    row = frame[frame.datetime == at]
    if row.empty:
        trade['execution_audit'] = {'status': 'HISTORY_UNAVAILABLE', 'expected_bar': at.isoformat()}
        return
    price = float(row.iloc[0].open)
    sign = 1 if trade['direction'] == 'BUY' else -1
    reason = ('OPEN_BEYOND_SL' if sign * (price - trade['sl']) <= 0 else
              'OPEN_BEYOND_TP2' if sign * (trade['tp2'] - price) <= 0 else
              'CURRENT_HISTORY_DIFFERS_FROM_ORIGINAL_RESULT')
    trade['execution_audit'] = {'status': 'RECONSTRUCTED_PROVIDER_HISTORY',
                              'bar': at.isoformat(), 'next_open': price, 'reason': reason}


def summary(trades):
    paths = [x['signal_path'] for x in trades if x.get('signal_path')]
    return {'model': MODEL, 'recorded_signals': len(trades),
            'tp3_observed_before_sl': sum(p['status'] == 'TP3_OBSERVED' for p in paths),
            'tp3_after_sl': sum(p['status'] == 'TP3_AFTER_SL' for p in paths),
            'ambiguous_tp3_sl': sum(p['status'] == 'AMBIGUOUS_SL_TP3' for p in paths),
            'tracking': sum(p['status'] == 'TRACKING' for p in paths),
            'data_gaps': sum(p['status'] == 'DATA_GAP' for p in paths),
            'note': 'Observed target touches only; excludes the partial candle at alert time. Not fills, not paper wins. Up to 72 M5 bars.'}


def review_legacy_execution(trade, frame, observed_at):
    """Flag contradictions; never silently turn historical records into wins."""
    import bosque_tracking as t
    if trade.get('historical_replay'):
        t.advance(trade['historical_replay'], frame)
        return
    if trade.get('execution_model') or trade.get('legacy_review_checked'):
        return
    if trade.get('result') not in ('CANCELLED','WIN','LOSS','BE'):
        return
    plan={k:trade['planned_entry'] if k=='entry' else trade[k]
          for k in ['entry','sl','tp1','tp2','tp3']}
    replay=t.new_trade({'id':trade['signal_id'],'direction':trade['direction']},
                       plan,trade['timestamp'],trade.get('cost_pips'))
    t.advance(replay,frame)
    if replay['result'] in ('DATA_GAP','PENDING'):
        return
    trade['legacy_review_checked']=observed_at
    original_result='NOT_ENTERED' if trade['result']=='CANCELLED' else trade['result']
    if replay['result']==original_result:
        return
    # Preserve the old result and money fields; exclude unreliable legacy rows
    # from forward performance. The replay is an explicitly separate finding.
    replay.pop('parameters',None)
    trade.update(review_status='REVIEW_REQUIRED',
                 review_reason='CURRENT_UTC_HISTORY_CONTRADICTS_ORIGINAL_EXECUTION',
                 history_observed_at=observed_at,historical_replay=replay)

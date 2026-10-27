"""Forward-only learning data collection. No model, signal gate or network calls.

Features are frozen before delivery. Outcomes are a separate paper-ledger view;
rejected/unfilled candidates never receive a fabricated WIN/LOSS label.
"""
import hashlib
import json
import re
from pathlib import Path

import pandas as pd

SCHEMA = 'BOSQUE_LEARNING_V1'


def read(path, default):
    if not path.exists():
        return default
    obj = json.loads(path.read_text())
    if obj.get('schema') != SCHEMA:
        raise ValueError('Unexpected learning schema')
    return obj


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False))
    tmp.replace(path)


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, allow_nan=False).encode()).hexdigest()


def capture(directory, payload, frame, captured_at):
    """Freeze closed-bar inputs once per candle/config; intrabar inputs per observation.

    A revised decision on a repeated candle cannot overwrite original features
    or be linked to them. Features contain only inputs available at capture.
    """
    from bosque_engine import clean_for_json
    from bosque_tracking import bars, configuration, cost
    from bosque_execution import entry_window
    at = pd.to_datetime(captured_at, utc=True)
    df = bars(frame)
    intrabar = payload.get('entry_timing', {}).get('mode') == 'INTRABAR'
    cutoff = df.datetime if intrabar else df.datetime + pd.Timedelta(minutes=5)
    if df.empty or (cutoff > at).any():
        raise ValueError('Unclosed/future input candle')
    source = df.iloc[-1].datetime.isoformat()
    params = configuration()
    params['round_trip_cost_pips'] = cost()
    snapshot = clean_for_json(payload)
    # Receipt/outcome fields are never accepted into the feature payload.
    allowed = ('direction','opportunity','score','session','h1','m15','m5',
               'confirmation','pd','liquidity','range','volatility','news',
               'gates','plan','data_health','entry_timing','setup_evidence')
    features = {key: snapshot.get(key) for key in allowed}
    features['last_m5'] = clean_for_json(df.iloc[-1].to_dict())
    features['entry_window'] = entry_window({**(features['plan'] or {}),
                                             'direction': features['direction']})
    price = float(df.iloc[-1].close)
    features['distances_price'] = {
        tf: {key: None if (features[tf] or {}).get(key) is None else
             price - float(features[tf][key]) for key in ('swing_high','swing_low')}
        for tf in ('h1','m15','m5')}
    # Exclude scan-clock metadata from revision comparison, not market evidence.
    signature = {k: features[k] for k in allowed if k not in ('news','data_health')}
    signature['news'] = {k: (features['news'] or {}).get(k)
                         for k in ('filter','status','event','time','high_impact')}
    signature['last_m5'] = features['last_m5']
    # Source date keeps repeated scans across midnight in the same daily file.
    day = pd.Timestamp(source).tz_convert('Asia/Kuala_Lumpur').strftime('%Y-%m-%d')
    identity = {'source':source, 'parameters':params}
    if intrabar: identity['observed_at'] = at.isoformat()
    record_id = day + '-' + digest(identity)[:20]
    path = Path(directory)/'learning'/'features'/(day+'.json')
    doc = read(path, {'schema':SCHEMA, 'records':{}})
    previous = doc['records'].get(record_id)
    signature_hash = digest(signature)
    if previous is not None:
        matches = previous['decision_hash'] == signature_hash
        return {'status':'ALREADY_CAPTURED' if matches else 'REVISION_NOT_LINKED',
                'id':record_id if matches else None, 'file':'engine/learning/features/'+day+'.json'}
    gates = features['gates'] or {}
    candidate = features['direction'] in ('BUY','SELL')
    blocked = [k for k,v in gates.items() if v is not True]
    doc['records'][record_id] = {
        'id':record_id, 'schema':SCHEMA, 'captured_at':at.isoformat(),
        'source_candle':source, 'parameters':params, 'decision_hash':signature_hash,
        'decision':'ACCEPTED_CANDIDATE' if candidate and not blocked else
                   'REJECTED_CANDIDATE' if candidate else 'NO_SETUP',
        'blocked_by':blocked, 'features':features,
        'provenance':'LIVE_PRE_DELIVERY', 'broker_execution':'UNKNOWN'}
    write(path, doc)
    return {'status':'CAPTURED', 'id':record_id,
            'file':'engine/learning/features/'+day+'.json'}


def safe_capture(directory, payload, frame, captured_at):
    try:
        return capture(directory, payload, frame, captured_at)
    except Exception as exc:
        # Learning failure must not change V3 decisions or expose exception text.
        return {'status':'ERROR', 'id':None, 'error_type':type(exc).__name__}


def sync(directory, trades, dashboard, frame=None):
    """Join only acknowledged trades carrying a pre-delivery learning ID."""
    root = Path(directory)/'learning'
    feature_docs, outcome_docs = {}, {}
    def feature_doc(day):
        if day not in feature_docs:
            feature_docs[day] = read(root/'features'/(day+'.json'), {'schema':SCHEMA,'records':{}})
        return feature_docs[day]
    counts = {'linked_trades':0, 'closed_paper':0, 'gross_research_rows':0,
              'net_research_rows':0, 'no_entry':0, 'excluded_legacy_trades':0}
    current_id = (dashboard.get('learning_capture') or {}).get('id')
    if current_id and re.fullmatch(r'\d{4}-\d{2}-\d{2}-[0-9a-f]{20}', current_id):
        day = current_id[:10]
        if current_id in feature_doc(day)['records']:
            outcome_docs[day] = read(root/'outcomes'/(day+'.json'), {'schema':SCHEMA,'records':{}})
            observation = outcome_docs[day]['records'].setdefault(current_id, {
                'id':current_id, 'classification_label':None,
                'eligible_for_gross_research':False, 'eligible_for_net_research':False,
                'basis':'CANDIDATE_NOT_A_PAPER_TRADE'})
            observation['delivery'] = (dashboard.get('signal') or {}).get('delivery',{})
            observation['updated_at'] = dashboard['engine']['timestamp']
    for trade in trades:
        record_id = trade.get('learning_id')
        if not record_id or not re.fullmatch(r'\d{4}-\d{2}-\d{2}-[0-9a-f]{20}', record_id):
            counts['excluded_legacy_trades'] += 1
            continue
        day = record_id[:10]
        record = feature_doc(day)['records'].get(record_id)
        if not record:
            continue
        counts['linked_trades'] += 1
        result = trade.get('result')
        closed = result in ('WIN','LOSS','BE')
        counts['closed_paper'] += closed
        counts['no_entry'] += result in ('NOT_ENTERED','CANCELLED')
        exclusions = []
        if not closed: exclusions.append(result or 'UNRESOLVED')
        if trade.get('performance_group') == 'HISTORICAL_RECONSTRUCTION':
            exclusions.append('RECONSTRUCTED_HISTORY')
        if trade.get('review_status') == 'REVIEW_REQUIRED': exclusions.append('REVIEW_REQUIRED')
        if trade.get('exit_reason') == 'SL_AMBIGUOUS': exclusions.append('AMBIGUOUS_INTRABAR_ORDER')
        if trade.get('telegram_status') != 'SENT': exclusions.append('DELIVERY_NOT_CONFIRMED')
        if (record['features'].get('data_health') or {}).get('allowed') is not True:
            exclusions.append('INPUT_DATA_NOT_HEALTHY')
        for key, value in (trade.get('parameters') or {}).items():
            if record['parameters'].get(key) != value:
                exclusions.append('CONFIGURATION_MISMATCH'); break
        if trade.get('filled_at') is None or pd.to_datetime(record['captured_at'],utc=True) >= pd.to_datetime(trade['filled_at'],utc=True):
            exclusions.append('NO_POST_CAPTURE_FILL')
        if trade.get('cost_pips') != record['parameters'].get('round_trip_cost_pips'):
            exclusions.append('COST_CONFIGURATION_MISMATCH')
        gross_ok = not exclusions
        net_ok = gross_ok and trade.get('cost_status') == 'CONFIGURED'
        counts['gross_research_rows'] += gross_ok
        counts['net_research_rows'] += net_ok
        if day not in outcome_docs:
            outcome_docs[day] = read(root/'outcomes'/(day+'.json'), {'schema':SCHEMA,'records':{}})
        selected = ('signal_id','result','entry','filled_at','sl','tp1','tp2','tp3',
                    'closed_at','exit_reason','result_r','result_pips','cost_pips',
                    'cost_status','bars_held','fill_audit','tp1_hit','tp2_hit','tp3_hit',
                    'execution_model','entry_deadline','entry_wait_bars','wick_zone_touch_observed')
        previous = outcome_docs[day]['records'].get(record_id, {})
        outcome_docs[day]['records'][record_id] = {
            'id':record_id, 'updated_at':dashboard['engine']['timestamp'],
            'basis':'FORWARD_PAPER_TP2_EXIT', 'broker_execution':'UNKNOWN',
            'classification_label':result if gross_ok else None,
            'eligible_for_gross_research':gross_ok, 'eligible_for_net_research':net_ok,
            'exclusions':exclusions, 'paper':{k:trade.get(k) for k in selected},
            'tp1_before_exit': bool(trade.get('tp1_hit')),
            'delivery':previous.get('delivery',{'status':trade.get('telegram_status')}),
            'excursions':excursions(trade, frame) or previous.get('excursions'),
            # Observation path is separate from the exit/financial outcome.
            'price_path_observation':trade.get('signal_path')}
    for day, doc in outcome_docs.items():
        write(root/'outcomes'/(day+'.json'), doc)
    day = pd.to_datetime(dashboard['engine']['timestamp'],utc=True).tz_convert('Asia/Kuala_Lumpur').strftime('%Y-%m-%d')
    today = list(feature_doc(day)['records'].values())
    capture_status = dashboard.get('learning_capture',{}).get('status','NOT_CAPTURED')
    summary = {'schema':SCHEMA, 'status':'COLLECTING' if capture_status in ('CAPTURED','ALREADY_CAPTURED') else 'DEGRADED',
               'mode':'DATA_COLLECTION_ONLY', 'model_trained':False, 'live_decisions_modified':False,
               'updated_at':dashboard['engine']['timestamp'], 'day_myt':day,
               'captured_today':len(today),
               'candidates_today':sum(r['decision']!='NO_SETUP' for r in today),
               'rejected_today':sum(r['decision']=='REJECTED_CANDIDATE' for r in today),
               **counts, 'last_capture':dashboard.get('learning_capture'),
               'features_file':'engine/learning/features/'+day+'.json',
               'note':'No model or automatic optimization. Rejected candidates have no invented outcomes; old trades have no reconstructed training features.'}
    write(Path(directory)/'learning_summary.json', summary)
    return summary


def excursions(trade, frame):
    """OHLC excursions, not tick ordering; never invent missing trade history."""
    from bosque_engine import PIP_SIZE
    if frame is None or not trade.get('filled_at') or trade.get('entry') is None:
        return None
    start = pd.to_datetime(trade['filled_at'],utc=True)
    rows = frame[frame.datetime >= start]
    if trade.get('closed_at'):
        rows = rows[rows.datetime < pd.to_datetime(trade['closed_at'],utc=True)]
    if rows.empty or rows.iloc[0].datetime != start:
        return None
    if (rows.datetime.diff().dropna() != pd.Timedelta(minutes=5)).any():
        return None
    entry = float(trade['entry'])
    high, low = float(rows.high.max()), float(rows.low.min())
    favorable, adverse = (high-entry, entry-low) if trade['direction']=='BUY' else (entry-low, high-entry)
    return {'mfe_pips':round(max(0,favorable)/PIP_SIZE,3),
            'mae_pips':round(max(0,adverse)/PIP_SIZE,3),
            'through_bar':rows.iloc[-1].datetime.isoformat(),
            'basis':'M5_FULL_BARS_INCLUDING_EXIT_BAR',
            'note':'Exit-bar extremes may occur after exit; not tick-accurate excursions.'}


def safe_sync(directory, trades, dashboard, frame=None):
    try:
        return sync(directory, trades, dashboard, frame)
    except Exception as exc:
        return {'schema':SCHEMA,'status':'ERROR','mode':'DATA_COLLECTION_ONLY',
                'model_trained':False,'live_decisions_modified':False,
                'error_type':type(exc).__name__,'updated_at':dashboard['engine']['timestamp']}

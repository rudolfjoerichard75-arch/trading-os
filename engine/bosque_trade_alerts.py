"""Forward-only TP/exit notifications. No changes to entry or paper execution.

Workflow: collect -> persist -> reserve -> persist reservation -> dispatch ->
persist receipt. A reservation from an interrupted run is never resent blindly.
"""
import argparse
import os
from pathlib import Path

import pandas as pd
from bosque_tracking import load, save, stamp
from bosque_execution import entry_window_text

VERSION = 'V3_TRADE_ALERTS_V1'
FILENAME = 'trade_alerts.json'
MAX_AGE_MINUTES = 10
MAX_ATTEMPTS = 3
MAX_BATCH = 10


def read_state(path, at=None):
    if not Path(path).exists():
        if at is None:
            raise ValueError('Trade alerts not initialized by a live scan')
        return {'version': VERSION, 'activated_at': stamp(at).isoformat(), 'events': {}}
    state = load(path)
    if state.get('version') != VERSION or not isinstance(state.get('events'), dict):
        raise ValueError('Invalid trade alert state')
    if pd.isna(stamp(state['activated_at'])):
        raise ValueError('Invalid activation time')
    return state


def my_time(value):
    return stamp(value).tz_convert('Asia/Kuala_Lumpur').strftime('%d/%m %H:%M MYT')


def message(trade, kind, event_at):
    direction = trade['direction']
    if kind.startswith('OBS_'):
        level=kind[4:]
        price=trade[level.lower()]
        title=f'👁 PARAS {level} SIGNAL DISENTUH — {price:.2f}'
        action=('Pemerhatian harga sahaja. Simulasi kekal NO ENTRY; bukan pengesahan keuntungan, kerugian atau posisi MT5. '
                'Tiada arahan buka/tutup posisi. Semak fill dan posisi sebenar di MT5.')
        if level == 'SL' and any(h.get('order')=='SAME_BAR_AS_SL' for h in (trade.get('signal_path') or {}).get('targets',{}).values()):
            action += '\nTP dan SL disentuh dalam candle sama; urutan tidak dapat disahkan.'
    elif kind.startswith('TP'):
        price = trade[kind.lower()]
        title = f'🎯 {kind} DISENTUH — {price:.2f}'
        if kind == 'TP2':
            action = '🔴 CLOSE POSITION: tutup posisi signal ini jika masih terbuka, mengikut exit TP2 SOP V3.'
        elif kind == 'TP3':
            action = 'Pemerhatian lanjutan selepas exit TP2. Bukan trade atau keuntungan tambahan dalam journal.'
        else:
            action = f'Sasaran TP1 disentuh. Exit penuh SOP V3 kekal TP2 {trade["tp2"]:.2f} atau SL {trade["sl"]:.2f}.'
        if trade.get('exit_reason') in ('SL', 'SL_AMBIGUOUS', 'TIME_EXIT'):
            action += '\nJournal sudah ditutup pada candle berikutnya; rujuk alert CLOSE, jangan buka semula posisi.'
    elif kind == 'CLOSE':
        reason = trade.get('exit_reason')
        title = '🔴 CLOSE POSITION — ' + ('HAD 72 CANDLE M5' if reason == 'TIME_EXIT' else 'SL DISENTUH')
        action = 'Tutup posisi signal ini jika masih terbuka, mengikut SOP V3.'
        if reason == 'SL_AMBIGUOUS':
            action += '\nSL dan TP2 disentuh dalam candle sama; urutan tidak dapat disahkan. Journal mengutamakan SL.'
        action += f'\nHarga exit simulasi: {trade["exit_price"]:.2f}'
    elif kind == 'NO_ENTRY':
        title = '⛔ ENTRY TIDAK DITERIMA DALAM SIMULASI'
        audit=trade.get('fill_audit') or {}
        def num(value, digits=2):
            return '--' if value is None else f'{value:.{digits}f}'
        limits=audit.get('limits') or trade.get('parameters') or {}
        reasons={'FILL_RR_BELOW_MIN':'RR pada harga simulasi di bawah minimum',
                 'FILL_RISK_BELOW_MIN':'Risk pada harga simulasi di bawah minimum',
                 'FILL_RISK_ABOVE_MAX':'Risk pada harga simulasi melebihi maksimum',
                 'OPEN_BEYOND_SL':'Harga simulasi sudah melepasi SL',
                 'OPEN_BEYOND_TP2':'Harga simulasi sudah melepasi TP2'}
        code=trade.get('exit_reason','UNKNOWN')
        action=(f"Sebab: {reasons.get(code,code)} ({code}).\n"
                f"Harga simulasi: {num(audit.get('price',trade.get('next_open')))}\n"
                f"Risk simulasi: {num(audit.get('risk_pips'))} pips "
                f"(had {limits.get('min_risk_pips',20)}–{limits.get('max_risk_pips',80)}).\n"
                f"RR TP2 simulasi: 1:{num(audit.get('rr_tp2'),3)} "
                f"(minimum 1:{limits.get('min_rr',2):g}).\n"
                + entry_window_text(trade) + '\n\n'
                'Ini penolakan paper entry sahaja, bukan pembatalan signal atau posisi MT5. '
                'Jika sudah masuk manual, semak risk/RR menggunakan fill sebenar. '
                'Paras signal terus diperhatikan dalam tempoh pemantauan; sentuhan paras bukan keuntungan journal.')
    else:
        title = '⚠️ STATUS POSISI PERLU SEMAKAN MANUAL'
        action = 'Candle M5 hilang; TP/SL dan status posisi tidak dapat disahkan. Semak posisi di MT5. Ini bukan pengesahan close.'
    return ('👑 BOSQUE FOREX AI · V3\n\n' + title + '\n'
            + f'XAUUSD | {direction}\nSignal ID: {trade["signal_id"]}\n'
            + f'Signal asal: {my_time(trade["timestamp"])}\n'
            + f'Entry pelan: {trade["planned_entry"]:.2f} | SL: {trade["sl"]:.2f}\n'
            + f'Waktu rujukan M5: {my_time(event_at)}\n\n' + action
            + '\n\nBerdasarkan candle M5 tertutup, bukan pengesahan fill/close akaun MT5. Semakan setiap kira-kira 5 minit; bukan tick langsung.')


def candidates(trade):
    """Derive immutable events from forward evidence, not current price alone."""
    path = trade.get('signal_path') or {}
    if (trade.get('telegram_status') != 'SENT'
            or trade.get('strategy_version') != 'SCALPING V3'
            or trade.get('performance_group') == 'HISTORICAL_RECONSTRUCTION'
            or trade.get('review_status') == 'REVIEW_REQUIRED'
            or path.get('source') != 'FORWARD_OBSERVED'):
        return []
    result = trade.get('result')
    if result in ('NOT_ENTERED', 'CANCELLED'):
        events=[('NO_ENTRY', (stamp(trade['closed_at']) + pd.Timedelta(minutes=5)).isoformat())]
        sl=path.get('sl_first_at')
        for key in ('tp1','tp2','tp3'):
            hit=path.get('targets',{}).get(key)
            if hit and hit.get('order')=='BEFORE_OBSERVED_SL' and (not sl or stamp(hit['bar']) < stamp(sl)):
                events.append(('OBS_'+key.upper(),(stamp(hit['bar'])+pd.Timedelta(minutes=5)).isoformat()))
        if sl:
            events.append(('OBS_SL',(stamp(sl)+pd.Timedelta(minutes=5)).isoformat()))
        if path.get('status')=='DATA_GAP':
            events.append(('DATA_GAP',path.get('missing_from') or trade['closed_at']))
        return events
    if result == 'DATA_GAP' or (path.get('status') == 'DATA_GAP' and result in ('PENDING', 'OPEN')):
        # A gap in later target tracking does not reopen a closed paper trade.
        gap = [('DATA_GAP', path.get('missing_from') or trade['closed_at'])]
    else:
        gap = []
    events = []
    if trade.get('entry') is not None and trade.get('filled_at'):
        for kind in ('TP1', 'TP2', 'TP3'):
            hit = path.get('targets', {}).get(kind.lower())
            if not hit or hit.get('order') != 'BEFORE_OBSERVED_SL':
                continue
            at = stamp(hit['bar']) + pd.Timedelta(minutes=5)
            # Observed targets after SL/time exit are not actionable successes.
            if trade.get('exit_reason') in ('SL', 'SL_AMBIGUOUS', 'TIME_EXIT') and at >= stamp(trade['closed_at']):
                continue
            events.append((kind, at.isoformat()))
        if result in ('WIN', 'LOSS', 'BE') and trade.get('exit_reason') in ('SL', 'SL_AMBIGUOUS', 'TIME_EXIT'):
            events.append(('CLOSE', trade['closed_at']))
    return events + gap


def collect(trades, path, at, healthy):
    state = read_state(path, at)
    # New observation alerts start here; never replay old target touches on upgrade.
    state.setdefault('observation_activated_at', stamp(at).isoformat())
    for trade in trades:
        if stamp(trade['timestamp']) < stamp(state['activated_at']):
            continue  # Activation is a forward boundary; never replay old alerts.
        for kind, event_at in candidates(trade):
            if kind.startswith('OBS_') and stamp(event_at) < stamp(state['observation_activated_at']):
                continue
            if not healthy and kind != 'DATA_GAP':
                continue
            event_id = trade['signal_id'] + ':' + kind
            if event_id in state['events']:
                continue
            age = (stamp(at) - stamp(event_at)).total_seconds() / 60
            state['events'][event_id] = {
                'signal_id': trade['signal_id'], 'kind': kind,
                'event_at': stamp(event_at).isoformat(), 'created_at': stamp(at).isoformat(),
                'text': message(trade, kind, event_at), 'attempts': 0,
                'status': 'PENDING' if 0 <= age <= MAX_AGE_MINUTES else 'EXPIRED',
            }
    save(path, state)
    return health(state)


def health(state):
    counts = {}
    for event in state['events'].values():
        status = event['status']; counts[status] = counts.get(status, 0) + 1
    return {'status': 'ACTIVE', 'version': VERSION, 'activated_at': state['activated_at'],
            'counts': counts, 'source': 'CLOSED_M5_FORWARD_PAPER_JOURNAL',
            'last_confirmed_at': max((x['sent_at'] for x in state['events'].values() if x.get('sent_at')), default=None),
            'note': 'TP2 is the V3 full exit; TP3 is later target observation. No MT5 execution.'}


def expired(event, at):
    age = (stamp(at) - stamp(event['event_at'])).total_seconds() / 60
    return age < 0 or age > MAX_AGE_MINUTES


def reserve(path, run_id, at):
    state = read_state(path); selected = 0
    # Sort at equal timestamps by target number, with CLOSE after earlier TPs.
    rank = {'TP1': 1, 'TP2': 2, 'TP3': 3, 'CLOSE': 4, 'NO_ENTRY': 5, 'DATA_GAP': 6, 'OBS_TP1': 1, 'OBS_TP2': 2, 'OBS_TP3': 3, 'OBS_SL': 4}
    events = sorted(state['events'].values(), key=lambda x: (x['event_at'], rank[x['kind']]))
    for event in events:
        if event['status'] == 'SENDING' or (event['status'] == 'RESERVED' and event.get('run_id') != run_id):
            event.update(status='DELIVERY_UNKNOWN', reason='PREVIOUS_RUN_INTERRUPTED')
        if event['status'] not in ('PENDING', 'FAILED'):
            continue
        if expired(event, at):
            event.update(status='EXPIRED', reason='EVENT_TOO_OLD'); continue
        if event['attempts'] >= MAX_ATTEMPTS:
            event.update(status='FAILED_FINAL'); continue
        if event.get('reserved_at') and (stamp(at) - stamp(event['reserved_at'])).total_seconds() < 300:
            continue
        if selected >= MAX_BATCH:
            break
        event.update(status='RESERVED', run_id=run_id, reserved_at=stamp(at).isoformat(), attempts=event['attempts'] + 1)
        selected += 1
    save(path, state)
    return selected


def dispatch(path, run_id, sender, clock):
    state = read_state(path)
    for event in state['events'].values():
        if event['status'] != 'RESERVED' or event.get('run_id') != run_id:
            continue
        at = clock()
        if expired(event, at):
            event.update(status='EXPIRED', reason='EVENT_TOO_OLD'); save(path, state); continue
        event.update(status='SENDING'); save(path, state)
        try:
            receipt = sender(event['text'])
        except Exception:
            receipt = {'status': 'DELIVERY_UNKNOWN', 'reason': 'SENDER_EXCEPTION'}
        status = receipt.get('status') if isinstance(receipt, dict) else None
        if status == 'SENT' and type(receipt.get('message_id')) is int:
            event.update(status='SENT', sent_at=stamp(clock()).isoformat(), message_id=receipt['message_id'])
        elif status == 'FAILED':
            event.update(status='FAILED', reason=receipt.get('reason', 'TELEGRAM_REJECTED'))
        else:
            event.update(status='DELIVERY_UNKNOWN', reason='ACK_NOT_CONFIRMED')
        save(path, state)
    return health(state)


def main():
    import bosque_engine as e
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('reserve', 'dispatch'))
    args = parser.parse_args()
    run_id = os.environ['GITHUB_RUN_ID'] + ':' + os.environ['GITHUB_RUN_ATTEMPT']
    path = e.ENGINE_DIR / FILENAME
    if args.phase == 'reserve':
        print('Reserved trade alerts:', reserve(path, run_id, pd.Timestamp.now(tz='UTC')))
    else:
        status = dispatch(path, run_id, e.send_telegram, lambda: pd.Timestamp.now(tz='UTC'))
        dashboard = load(e.DASHBOARD_FILE)
        dashboard.setdefault('services', {})['trade_alerts'] = status
        save(e.DASHBOARD_FILE, dashboard)
        telegram = load(e.ENGINE_DIR / 'telegram_health.json')
        telegram['trade_alerts'] = status
        save(e.ENGINE_DIR / 'telegram_health.json', telegram)
        print('Trade alert delivery counts:', status['counts'])


if __name__ == '__main__':
    main()

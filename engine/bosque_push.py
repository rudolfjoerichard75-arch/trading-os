"""Direct Web Push, separate from Telegram delivery. Secrets never enter repository data.
Reserve state is committed before dispatch; interrupted/uncertain sends are not replayed.
"""
import argparse
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / 'engine/push_state.json'
CONFIG = ROOT / 'push-config.json'


def now():
    return datetime.now(timezone.utc)


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def read(path, fallback):
    return json.loads(path.read_text()) if path.exists() else fallback


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')


def device_id(sub):
    return hashlib.sha256(sub['endpoint'].encode()).hexdigest()[:32]


def settings():
    key = os.getenv('PUSH_VAPID_PRIVATE_KEY', '').strip()
    raw = os.getenv('PUSH_SUBSCRIPTIONS', '').strip()
    if not key:
        return None, []
    from cryptography.hazmat.primitives.serialization import load_der_private_key
    from cryptography.hazmat.primitives.asymmetric import ec
    private = load_der_private_key(base64.urlsafe_b64decode(key + '=' * (-len(key) % 4)), None)
    if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(private.curve, ec.SECP256R1):
        raise ValueError('Invalid VAPID curve')
    subs = json.loads(raw) if raw else []
    if not isinstance(subs, list) or len(subs)>10:
        raise ValueError('Expected at most 10 devices')
    for sub in subs:
        url=urlparse(sub['endpoint'])
        host=url.hostname or ''
        if (url.scheme!='https' or url.username or url.password or url.port not in (None,443)
            or not (host=='web.push.apple.com' or host.endswith('.push.apple.com')
                    or host=='fcm.googleapis.com' or host.endswith('.push.services.mozilla.com'))):
            raise ValueError('Unsupported push endpoint')
        for name in ('auth','p256dh'):
            if not isinstance(sub.get('keys',{}).get(name),str):
                raise ValueError('Invalid subscription keys')
    return private, subs


def candidates(journal, alerts, at, activated):
    result=[]
    def fresh(t):
        try: return stamp(t)>=stamp(activated) and 0 <= (at-stamp(t)).total_seconds() <= 600
        except (ValueError, TypeError, AttributeError): return False
    for trade in journal.get('trades',[]):
        if (trade.get('strategy_version')!='SCALPING V3' or trade.get('telegram_status')!='SENT'
            or trade.get('review_status')=='REVIEW_REQUIRED'
            or trade.get('performance_group')=='HISTORICAL_RECONSTRUCTION'
            or not fresh(trade.get('timestamp'))):
            continue
        result.append({'id':trade['signal_id']+':ENTRY','at':trade['timestamp'],
            'title':'Bosque Forex AI — '+trade['direction']+' XAUUSD',
            'body':f"Entry {trade['planned_entry']} | SL {trade['sl']} | TP1 {trade['tp1']} | TP2 {trade['tp2']} | TP3 {trade['tp3']}. Buka app untuk semak pelan dan masa signal."})
    for eid,event in alerts.get('events',{}).items():
        if fresh(event.get('event_at')):
            result.append({'id':eid,'at':event['event_at'],'title':'Bosque Forex AI — '+event['kind'],
                           'body':event['text']})
    return result


def reserve(at, run_id, private, subs):
    from cryptography.hazmat.primitives import serialization
    public=base64.urlsafe_b64encode(private.public_key().public_bytes(
        serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)).decode().rstrip('=')
    write(CONFIG, {'status':'CONFIGURED' if subs else 'AWAITING_DEVICE', 'public_key':public})
    state=read(STATE, {'activated_at':at.isoformat(),'deliveries':{}})
    events=candidates(read(ROOT/'engine/trade_journal.json',{}),read(ROOT/'engine/trade_alerts.json',{}),at,state['activated_at'])
    for sub in subs:
        did=device_id(sub)
        test={'id':'SERVER_TEST','at':at.isoformat(),'title':'Bosque Forex AI — Ujian server',
              'body':'Push dari server diterima. Alert Bosque boleh sampai tanpa Telegram. Berdasarkan scan engine, bukan tick langsung.'}
        for event in [test]+events:
            identity=did+':'+event['id']
            if identity in state['deliveries']: continue
            # Store event identity/time only; never subscription URLs or auth keys.
            state['deliveries'][identity]={'status':'RESERVED','device':did,'event':event,
                                           'run_id':run_id,'reserved_at':at.isoformat()}
    write(STATE,state)


def dispatch(at, run_id, private, subs, sender=None):
    from py_vapid import Vapid
    from pywebpush import webpush, WebPushException
    sender=sender or webpush
    state=read(STATE, {'deliveries':{}})
    devices={device_id(s):s for s in subs}
    for item in state['deliveries'].values():
        if item['status']!='RESERVED': continue
        if item['run_id']!=run_id:
            item['status']='DELIVERY_UNKNOWN'; continue
        event=item['event']
        if not 0 <= (at-stamp(event['at'])).total_seconds() <= 600:
            item['status']='EXPIRED'; continue
        sub=devices.get(item['device'])
        if not sub:
            item['status']='DEVICE_REMOVED'; continue
        # The durable reservation prevents another run retrying an ambiguous send.
        item['status']='SENDING';write(STATE,state)
        try:
            response=sender(subscription_info=sub,data=json.dumps({
                'id':event['id'],'title':event['title'],'body':event['body']},ensure_ascii=False),
                vapid_private_key=Vapid(private),
                vapid_claims={'sub':'https://rudolfjoerichard75-arch.github.io'},
                ttl=300,timeout=20,headers={'Urgency':'high'})
            item['status']='ACCEPTED_BY_PUSH_SERVICE' if response.status_code in (200,201,202) else 'DELIVERY_UNKNOWN'
        except WebPushException as exc:
            code=exc.response.status_code if exc.response is not None else None
            item['status']='SUBSCRIPTION_EXPIRED' if code in (404,410) else 'DELIVERY_UNKNOWN'
        except Exception:
            item['status']='DELIVERY_UNKNOWN'
        write(STATE,state)
    write(STATE,state)


def main():
    phase=argparse.ArgumentParser();phase.add_argument('phase',choices=['reserve','dispatch'])
    args=phase.parse_args()
    try: private,subs=settings()
    except Exception:
        # Never print library errors: they can include endpoint/auth details.
        print('Web Push configuration invalid. Check GitHub Secrets.');return
    if private is None:
        write(CONFIG,{'status':'NOT_CONFIGURED','public_key':None})
        print('Web Push not configured; Telegram continues unchanged.');return
    run_id=os.environ['GITHUB_RUN_ID']+':'+os.environ.get('GITHUB_RUN_ATTEMPT','1')
    if args.phase=='reserve': reserve(now(),run_id,private,subs)
    else: dispatch(now(),run_id,private,subs)
    print('Web Push phase completed; provider acceptance does not confirm phone display.')


if __name__=='__main__': main()

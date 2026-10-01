"""Observable service health and paper exports. Never fabricates delivery or broker fills."""
import csv, os
from pathlib import Path
import bosque_engine as e


def archive_calendar(at):
    cache=e.load_json(e.NEWS_CACHE_FILE)
    if not cache or e.news_at(cache,at)['status']=='UNKNOWN': return {'status':'NO_VALID_CALENDAR'}
    directory=e.DATA_DIR/'news_archive'; directory.mkdir(parents=True,exist_ok=True)
    name=e.stamp(cache['coverage_start']).strftime('%Y-%m-%d')+'.json'
    path=directory/name
    previous=e.load_json(path,{'first_observed_at':cache['fetched_at'],'snapshots':[]})
    # Keep changes, not hundreds of identical snapshots. This is observed history only.
    snapshots=previous['snapshots']
    if not snapshots or snapshots[-1]['events']!=cache['events']:
        snapshots.append(cache)
        e.save_json(path,previous)
    return {'status':'ARCHIVING_FROM_NOW','weeks':len(list(directory.glob('*.json'))),'current_week':name,'coverage_start':cache['coverage_start'],'coverage_end':cache['coverage_end'],'note':'No calendar history is invented before first observation'}


def telegram_health(at):
    path=e.DATA_DIR/'telegram_health.json'; previous=e.load_json(path)
    if previous.get('checked_at') and 0<=(e.stamp(at)-e.stamp(previous['checked_at'])).total_seconds()<86400:
        return previous
    token=os.getenv('TELEGRAM_BOT_TOKEN'); chat=os.getenv('TELEGRAM_CHAT_ID')
    result={'checked_at':e.stamp(at).isoformat(),'status':'NOT_CONFIGURED','delivery_tested':False}
    if token and chat:
        try:
            root='https://api.telegram.org/bot'+token+'/'
            me=e.http_json(root+'getMe')
            room=e.http_json(root+'getChat',{'chat_id':chat})
            result['status']='BOT_AND_CHAT_ACCESS_VERIFIED' if me.get('ok') is True and room.get('ok') is True else 'API_REJECTED'
        except Exception as exc: result['status']='UNAVAILABLE'; result['error']=type(exc).__name__
    e.save_json(path,result); return result


def export_journal(journal):
    path=e.REPO_DIR/'paper_journal.csv'
    fields=['signal_id','timestamp','direction','opportunity','score','planned_entry','entry','sl','tp1','tp2','result','result_pips','result_r','closed_at','exit_reason','cost_status','telegram_status']
    tmp=path.with_suffix('.csv.tmp')
    with tmp.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); writer.writeheader()
        for trade in journal['trades']: writer.writerow(trade)
    tmp.replace(path)
    return {'status':'EXPORTED','file':path.name,'rows':len(journal['trades'])}


def refresh(dashboard,journal,at):
    result={}
    for name,operation in [('news_archive',lambda:archive_calendar(at)),('telegram_health',lambda:telegram_health(at)),('journal_export',lambda:export_journal(journal))]:
        try: result[name]=operation()
        except Exception as exc: result[name]={'status':'ERROR','error':type(exc).__name__}
    dashboard['services']=result
    dashboard['journal_recent']=journal['trades'][-30:][::-1]
    return dashboard

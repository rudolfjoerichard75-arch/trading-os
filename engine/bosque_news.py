"""Validate provider events before declaring the USD news window clear."""
import re
import pandas as pd

SOURCE = 'FinanceCalendar'
SOURCE_URL = 'https://www.financecalendar.com/'
NON_US = r'\b(UK|U\.K\.|UNITED KINGDOM|EUROZONE|EURO AREA|ECB|BOE|BOJ|BOC|RBA|RBNZ|SNB|JAPAN|JAPANESE|CHINA|CHINESE|CANADA|CANADIAN|AUSTRALIA|AUSTRALIAN|GERMANY|GERMAN|FRANCE|FRENCH|NEW ZEALAND|SWITZERLAND|SWISS|INDIA|BRAZIL|MEXICO|SOUTH KOREA)\b'


def geography(event):
    currency = str(event.get('currency') or '').strip().upper()
    country = str(event.get('country') or event.get('country_code') or '').strip().upper()
    if currency:
        if currency=='USD': return 'USD'
        return 'NON_US' if currency in {'EUR','GBP','JPY','CAD','AUD','NZD','CHF','CNY','CNH','INR','BRL','MXN','KRW','ZAR','SGD','HKD','SEK','NOK','DKK','TRY','IDR','MYR','THB','PHP','PLN','HUF','CZK','RUB','SAR','ILS','TWD'} else 'UNKNOWN'
    if country:
        if country in {'US','USA','U.S.','UNITED STATES','UNITED STATES OF AMERICA'}: return 'USD'
        if country in {'GB','UK','JP','CN','CA','AU','NZ','CH','DE','FR','EU','IN','BR','MX','KR','ZA','SG','HK','SE','NO','DK','TR','ID','MY','TH','PH','PL','HU','CZ','RU','SA','IL','TW'} or re.search(NON_US,country): return 'NON_US'
        return 'UNKNOWN'
    title = ' '.join(str(event.get(k) or '') for k in ('name','title')).upper()
    if re.search(NON_US, title): return 'NON_US'
    if re.search(r'\b(US|USA|UNITED STATES|FOMC|FEDERAL RESERVE)\b|\bU\.S\.', title): return 'USD'
    return 'UNKNOWN'


def event_time(event):
    raw = event.get('time_utc') or event.get('time')
    if not isinstance(raw, str) or not raw.strip(): return None
    try:
        ts = pd.Timestamp(raw)
        if pd.isna(ts): return None
        if ts.tzinfo is None:
            # Only the documented time_utc field establishes UTC for naive text.
            if not event.get('time_utc'): return None
            ts = ts.tz_localize('UTC')
        return ts.tz_convert('UTC').to_pydatetime()
    except (ValueError, TypeError, OverflowError): return None


def fetch_calendar(now, get, url, before=30, after=30, lookahead=2):
    now = pd.Timestamp(now)
    date_from = (now-pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    date_to = (now+pd.Timedelta(days=lookahead)).strftime('%Y-%m-%d')
    evidence = {'checked_at': now.isoformat(), 'requested_from': date_from,
                'requested_to': date_to, 'events_received': None, 'usd_high_events': 0,
                'nearby_usd_high_events': 0, 'skipped_usd_times': 0,
                'unclassified_high_events': 0, 'coverage_status': 'UNVERIFIED',
                'events': []}
    base = {'evidence': evidence, 'block_before_minutes': before,
            'block_after_minutes': after, 'currency': 'USD', 'source': SOURCE,
            'source_url': SOURCE_URL, 'event': None, 'time': None,
            'minutes_to_news': None, 'high_impact': None}
    try:
        response = get(url, params={'from': date_from, 'to': date_to,
                       'impact': 'high', 'limit': 500}, timeout=15)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            if data.get('error') or not isinstance(data.get('events'), list):
                raise ValueError('Unrecognized news response')
            events = data['events']
            count = data.get('count')
            if count is not None and (not isinstance(count, int) or count != len(events)):
                raise ValueError('Calendar count mismatch or truncated response')
        elif isinstance(data, list): events, count = data, len(data)
        else: raise ValueError('Unrecognized news response')
        evidence.update(events_received=len(events), provider_count=count)
        if len(events) >= 500: raise ValueError('Calendar limit reached; coverage uncertain')
        nearby = []
        uncertain = []
        for item in events:
            if not isinstance(item, dict): raise ValueError('Invalid calendar event')
            impact = str(item.get('impact') or '').strip().lower()
            if impact not in ('high','medium','low'): raise ValueError('Unknown event impact')
            if impact != 'high': continue
            geo = geography(item)
            at = event_time(item)
            title = str(item.get('title') or item.get('name') or 'Unnamed event')
            evidence['events'].append({'title': title, 'geography': geo,
                'time': at.isoformat() if at else None, 'impact': impact})
            if at is not None and not pd.Timestamp(date_from,tz='UTC') <= pd.Timestamp(at) < pd.Timestamp(date_to,tz='UTC')+pd.Timedelta(days=1):
                raise ValueError('Calendar event outside requested date range')
            if geo == 'NON_US': continue
            if geo == 'UNKNOWN':
                evidence['unclassified_high_events'] += 1
                uncertain.append('Negara/mata wang acara high impact belum dapat disahkan')
                continue
            evidence['usd_high_events'] += 1
            if at is None:
                evidence['skipped_usd_times'] += 1
                uncertain.append('Waktu berita USD high impact tidak dapat disahkan')
                continue
            minutes = (pd.Timestamp(at)-now).total_seconds()/60
            if -180 <= minutes <= 120: nearby.append((minutes, item, at))
        evidence['nearby_usd_high_events'] = len(nearby)
        if uncertain:
            evidence['coverage_status'] = 'INCOMPLETE'
            return {**base, 'status': 'UNKNOWN', 'filter': 'BLOCK',
                    'reason': '; '.join(dict.fromkeys(uncertain))+'; signal ditahan'}
        # An API response is evidence of listed events, not proof of a complete
        # worldwide economic calendar. Preserve titles/times for review.
        evidence['coverage_status'] = 'PROVIDER_RESPONSE_ONLY'
        if not nearby:
            return {**base, 'status': 'CLEAR', 'filter': 'PASS', 'high_impact': False,
                    'reason': 'Tiada berita USD high impact berdekatan dalam acara yang dipulangkan sumber'}
        blocked = [x for x in nearby if -after <= x[0] <= before]
        minutes, item, at = min(blocked or nearby, key=lambda x: abs(x[0]))
        return {**base, 'status': 'HIGH IMPACT', 'filter': 'BLOCK' if blocked else 'PASS',
            'high_impact': True, 'minutes_to_news': round(minutes, 1),
            'event': item.get('title') or item.get('name') or 'USD HIGH IMPACT NEWS',
            'time': pd.Timestamp(at).tz_convert(now.tzinfo).isoformat(),
            'reason': 'Berita USD high impact dalam tetingkap sekatan' if blocked else 'Berita USD high impact di luar tetingkap sekatan',
            **{key: item.get(key) for key in ('category','actual','consensus','prior','url')}}
    except Exception as exc:
        evidence['coverage_status'] = 'UNVERIFIED'
        return {**base, 'status': 'ERROR', 'filter': 'BLOCK',
                'reason': 'Sumber berita gagal disahkan; signal ditahan',
                'error_type': type(exc).__name__}


def historical_calendar(start, end, get, url):
    """Provider schedule, explicitly not a point-in-time news archive."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError('Invalid historical news coverage')
    # Include events just outside the price dataset whose block window overlaps.
    cursor = (start-pd.Timedelta(minutes=30)).tz_convert('UTC').normalize()
    stop = (end+pd.Timedelta(minutes=30)).tz_convert('UTC').normalize()+pd.Timedelta(days=1)
    coverage_start = cursor
    events = {}
    requests_count = 0
    while cursor < stop:
        last = min(cursor+pd.Timedelta(days=30), stop)
        response = get(url, params={'from':cursor.strftime('%Y-%m-%d'),
            'to':(last-pd.Timedelta(days=1)).strftime('%Y-%m-%d'),
            'impact':'high','limit':500}, timeout=20)
        response.raise_for_status()
        data = response.json()
        items = data.get('events') if isinstance(data,dict) and not data.get('error') else None
        if not isinstance(items,list) or len(items)>=500:
            raise ValueError('Historical news missing or truncated')
        if data.get('count') is not None and data['count'] != len(items):
            raise ValueError('Historical news count mismatch')
        for item in items:
            if not isinstance(item,dict): raise ValueError('Invalid historical event')
            impact = str(item.get('impact') or '').strip().lower()
            if impact not in ('high','medium','low'): raise ValueError('Unknown historical impact')
            if impact != 'high': continue
            geo = geography(item)
            if geo=='NON_US': continue
            at = event_time(item)
            if geo=='UNKNOWN' or at is None: raise ValueError('Historical USD coverage cannot be verified')
            title = str(item.get('title') or item.get('name') or 'USD high impact')
            events[(at.isoformat(),title)] = {'time':at.isoformat(), 'currency':'USD',
                                            'impact':'high', 'title':title}
        cursor=last;requests_count+=1
    if (end-start)>pd.Timedelta(days=7) and not events:
        raise ValueError('No USD events returned for multiweek history; coverage unverified')
    return {'coverage_start':coverage_start.isoformat(), 'coverage_end':stop.isoformat(),
            'events':list(events.values()),'events_count':len(events),'requests':requests_count,
            'source':SOURCE,'source_url':SOURCE_URL,
            'fetched_at':pd.Timestamp.now(tz='UTC').isoformat(),
            'coverage_status':'PROVIDER_SCHEDULE_NOT_POINT_IN_TIME'}

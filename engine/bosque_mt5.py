"""Offline MT5 M1 import. Server timezone must be supplied explicitly."""
import argparse
import hashlib
import json
from pathlib import Path
import pandas as pd
from bosque_tracking import bars


def load_m1(path, timezone):
    if not timezone:
        raise ValueError('MT5 server timezone is required; do not infer it from PC time')
    path = Path(path)
    raw = pd.read_csv(path, sep='\t')
    required = ['<DATE>', '<TIME>', '<OPEN>', '<HIGH>', '<LOW>', '<CLOSE>']
    if not set(required).issubset(raw.columns):
        raise ValueError('Expected MT5 tab-separated M1 bars, not tick quotes')
    dt = pd.to_datetime(raw['<DATE>'] + ' ' + raw['<TIME>'], format='%Y.%m.%d %H:%M:%S')
    if (dt.dt.second != 0).any():
        raise ValueError('M1 timestamps must align to minute boundaries')
    df = pd.DataFrame({'datetime': dt.dt.tz_localize(timezone, ambiguous='raise', nonexistent='raise').dt.tz_convert('UTC')})
    for name in ('open', 'high', 'low', 'close'):
        df[name] = raw[f'<{name.upper()}>']
    if not df.datetime.is_monotonic_increasing:
        raise ValueError('MT5 bars must be in ascending time order')
    df = bars(df)
    grouped = df.set_index('datetime').resample('5min', closed='left', label='left')
    count = grouped.close.count()
    m5 = grouped.agg({'open':'first','high':'max','low':'min','close':'last'})
    # Never fill missing minutes or manufacture candles through a market closure.
    m5 = m5[count == 5].reset_index()
    report = {'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
              'server_timezone_assumption':timezone, 'timezone_verified':False,
              'm1_rows':len(df), 'm5_complete':len(m5),
              'partial_m5_discarded':int(((count > 0) & (count < 5)).sum()),
              'm1_gaps_over_one_minute':int((df.datetime.diff() > pd.Timedelta(minutes=1)).sum()),
              'price_basis':'MT5 exported bar OHLC; verify symbol chart mode',
              'note':'Gaps may include broker market closures. No synthetic bars.'}
    return m5, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--m1', required=True); p.add_argument('--server-timezone', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    frame, report = load_m1(args.m1, args.server_timezone)
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(dest, index=False)
    dest.with_suffix('.audit.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()

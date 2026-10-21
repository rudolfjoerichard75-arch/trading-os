"""Offline matched-signal POI study; no live state writes or network calls."""
import hashlib, io, json, subprocess, sys
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'engine'))
import bosque_tracking as t
import bosque_engine as e
import bosque_backtest as b
import bosque_poi as poi

def dataset():
    # Hourly snapshots from a fixed post-feed-repair window. Latest revision wins.
    rows = subprocess.check_output(['git', 'log', '--format=%H %cI',
        '--since=2026-10-20T00:00:00Z', '--', 'engine/market_snapshot.csv'], cwd=ROOT, text=True)
    selected = {}; frames = []; sources = []
    for row in rows.splitlines():
        sha, timestamp = row.split()
        selected.setdefault(timestamp[:13], (sha, timestamp))
    for sha, timestamp in reversed(list(selected.values())):
        raw = subprocess.check_output(['git', 'show', sha + ':engine/market_snapshot.csv'], cwd=ROOT)
        df = t.bars(pd.read_csv(io.BytesIO(raw)))
        df = df[df.datetime + pd.Timedelta(minutes=5) <= t.stamp(timestamp)]
        frames.append(df)
        sources.append({'commit': sha, 'saved_at': timestamp,
                        'sha256': hashlib.sha256(raw).hexdigest()})
    all_rows = pd.concat(frames, ignore_index=True)
    conflicts = int((all_rows.groupby('datetime')[['open','high','low','close']].nunique().max(axis=1) > 1).sum())
    df = t.bars(all_rows.drop_duplicates('datetime', keep='last'))
    return df, sources, conflicts

def main():
    df, sources, conflicts = dataset()
    df.to_csv(Path(__file__).parent / 'input.csv', index=False)
    print(f'Replaying {len(df)} bars from {df.datetime.min()} to {df.datetime.max()}', flush=True)
    replay = b.replay(df)  # Uses unchanged V3 and existing paper execution policy.
    trades = replay['trades']
    for trade in trades:
        at = t.stamp(trade['decision_at'])
        window = df[df.datetime + pd.Timedelta(minutes=5) <= at].tail(e.OUTPUT_SIZE)
        trade['poi'] = poi.annotate(window, at, trade['direction'])
    groups = {'V3': trades}
    for key in ('breakout_retest', 'h1_liquidity', 'either'):
        groups[key] = [x for x in trades if x['poi'][key]]
        groups[key + '_rejected'] = [x for x in trades if not x['poi'][key]]
    metrics = {}
    for name, selected in groups.items():
        sensitivity = {}
        for cost in (0, 5, 10):
            adjusted = json.loads(json.dumps(selected))
            for tr in adjusted:
                if tr['result'] in ('WIN','LOSS','BE'):
                    tr['result_pips'] -= cost
                    tr['result_r'] = tr['result_pips'] / (abs(tr['entry']-tr['sl']) / e.PIP_SIZE)
                    tr['result'] = 'WIN' if tr['result_pips'] > 0 else 'LOSS' if tr['result_pips'] < 0 else 'BE'
                    tr['cost_pips'] = cost
            sensitivity[str(cost)] = t.stats(adjusted)
        metrics[name] = {'signals': len(selected), 'cost_sensitivity_pips': sensitivity}
    out = {'policy': poi.POLICY, 'source_head': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
           'parameters': t.configuration(), 'bars': len(df), 'start': df.datetime.min().isoformat(),
           'end': df.datetime.max().isoformat(), 'gaps': int((df.datetime.diff()>pd.Timedelta(minutes=5)).sum()),
           'revised_candles': conflicts, 'sources': sources, 'metrics': metrics, 'trades': trades,
           'limitations': ['Exploratory historical matched-signal subsets, not an independent holdout or live trial.',
             'Filtering does not rearm the original delivery cycle or generate replacement entries.',
             'Historical news not applied. Prices use latest stored revisions, not point-in-time feed.',
             'Costs 0/5/10 pips are illustrative sensitivity assumptions, not measured broker costs.',
             'Existing next-full-M5-open fills, 40 pip entry cap, TP2 exit, SL first if ambiguous; not tick execution.',
             'POI parameters fixed before this run; short sample cannot establish profitability.']}
    t.save(Path(__file__).parent/'results.json', out)
    print(json.dumps(metrics, indent=2), flush=True)

if __name__ == '__main__': main()

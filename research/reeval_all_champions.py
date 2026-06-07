#!/usr/bin/env python3
"""
reeval_all_champions.py - Re-evaluacion completa de TODOS los champions
con el sig_*_short fix aplicado. Solo lectura.

Output: tabla + JSON con verdict por champion.
"""
import sys, json, pickle
from pathlib import Path
import pandas as pd

sys.path.insert(0, '/opt/sigma')
sys.path.insert(0, '/opt/sigma/engine/optimization')

from asset_pipeline import add_features, backtest, metrics
import asset_pipeline as ap

CACHE = Path('/opt/sigma/research/cache_ohlcv')
MODELS = Path('/opt/sigma/models')
RESULTS = Path('/opt/sigma/research/results/reeval_all_2026_05_14.json')

# Load all champions excluding archive
champs = []
for tf_dir in sorted(MODELS.iterdir()):
    if not tf_dir.is_dir() or tf_dir.name == 'archive':
        continue
    tf = tf_dir.name
    for jf in sorted(tf_dir.glob('*.json')):
        try:
            d = json.loads(jf.read_text())
            champs.append({'path': jf, 'tf': tf, 'data': d})
        except Exception as e:
            print(f'SKIP {jf}: {e}')


def get_sig_fn(strategy):
    fn = getattr(ap, f'sig_{strategy}', None)
    return fn


def load_data(sym, tf):
    p = CACHE / f'{sym}_USDT_{tf}_365d.pkl'
    if not p.exists():
        return None
    df = pickle.load(open(p, 'rb'))
    return add_features(df.copy())


results = []
print('=' * 130)
print(f'{"SYM":<5} {"TF":<5} {"STRATEGY":<28} {"DIR":<5} {"OFICIAL_OOS%":>13} {"RE_CAGR%":>10} {"WR%":>6} {"TRADES":>7} {"DD%":>7} {"PF":>5} {"VERDICT":<22}')
print('=' * 130)

for c in champs:
    d = c['data']
    tf = c['tf']
    sym = d.get('symbol', '').replace('/USDT', '')
    strat = d.get('strategy', '')
    direction = d.get('direction', '?')
    cagr_oficial = d.get('metrics_oos', {}).get('cagr')
    if cagr_oficial is None:
        cagr_oficial = d.get('cagr')

    fn = get_sig_fn(strat)
    if fn is None:
        row = {'sym': sym, 'tf': tf, 'strategy': strat, 'direction': direction,
               'cagr_oficial': cagr_oficial, 'verdict': 'NO_FN'}
        results.append(row)
        print(f'{sym:<5} {tf:<5} {strat:<28} {direction:<5} {str(cagr_oficial):>13} {"--":>10} {"--":>6} {"--":>7} {"--":>7} {"--":>5} NO_FN_IN_PIPELINE')
        continue

    df = load_data(sym, tf)
    if df is None or len(df) < 200:
        row = {'sym': sym, 'tf': tf, 'strategy': strat, 'direction': direction,
               'cagr_oficial': cagr_oficial, 'verdict': 'NO_DATA'}
        results.append(row)
        print(f'{sym:<5} {tf:<5} {strat:<28} {direction:<5} {str(cagr_oficial):>13} {"--":>10} {"--":>6} {"--":>7} {"--":>7} {"--":>5} NO_CACHED_OHLCV')
        continue

    params = dict(d.get('params', {}))
    params['risk_pct'] = d.get('risk_pct', 5.0)

    try:
        sig, sl, tp = fn(df, params)
    except Exception as e:
        row = {'sym': sym, 'tf': tf, 'strategy': strat, 'direction': direction,
               'cagr_oficial': cagr_oficial, 'verdict': f'EXC: {e}'}
        results.append(row)
        print(f'{sym:<5} {tf:<5} {strat:<28} {direction:<5} {str(cagr_oficial):>13} EXC: {str(e)[:40]}')
        continue

    n_long = int((sig == 1).sum())
    n_short = int((sig == -1).sum())
    df_t, eq = backtest(df, sig, sl, tp, params.get('risk_pct', 5.0), use_kelly=True)
    days = (df.index[-1] - df.index[0]).days
    m = metrics(df_t, eq, days, min_t=1) or {}
    re_cagr = m.get('cagr', 0)
    wr = m.get('wr', 0)
    trades = m.get('trades', 0)
    dd = m.get('dd', 0)
    pf = m.get('pf', 0)

    # Verdict
    if trades == 0:
        verdict = 'NO_SIGNALS'
    elif re_cagr is None:
        verdict = 'NO_METRICS'
    elif cagr_oficial is not None and abs(re_cagr - cagr_oficial) < 5:
        verdict = 'CHAMPION_OK'
    elif re_cagr is not None and re_cagr > 5:
        verdict = 'OK_SHIFTED'
    elif re_cagr is not None and re_cagr > -5:
        verdict = 'MARGINAL'
    else:
        verdict = 'FANTASMA'

    row = {
        'sym': sym, 'tf': tf, 'strategy': strat, 'direction': direction,
        'cagr_oficial': cagr_oficial, 're_cagr': re_cagr, 'wr': wr, 'trades': trades,
        'dd': dd, 'pf': pf, 'n_long': n_long, 'n_short': n_short, 'verdict': verdict,
    }
    results.append(row)
    of_s = f'{cagr_oficial:.1f}' if isinstance(cagr_oficial, (int, float)) else str(cagr_oficial)
    rc_s = f'{re_cagr:.1f}' if isinstance(re_cagr, (int, float)) else '--'
    wr_s = f'{wr:.1f}' if isinstance(wr, (int, float)) else '--'
    dd_s = f'{dd:.1f}' if isinstance(dd, (int, float)) else '--'
    pf_s = f'{pf}' if pf is not None else '--'
    print(f'{sym:<5} {tf:<5} {strat:<28} {direction:<5} {of_s:>13} {rc_s:>10} {wr_s:>6} {trades:>7} {dd_s:>7} {pf_s:>5} {verdict:<22}')

print('=' * 130)
# Summary
from collections import Counter
ctr = Counter(r['verdict'] for r in results)
print('\nVERDICT SUMMARY:')
for k, v in sorted(ctr.items(), key=lambda x: -x[1]):
    print(f'  {k}: {v}')

RESULTS.parent.mkdir(parents=True, exist_ok=True)
RESULTS.write_text(json.dumps(results, indent=2, default=str))
print(f'\nReport saved: {RESULTS}')

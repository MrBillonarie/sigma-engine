"""
Monte Carlo masivo — valida TODOS los modelos sin validacion MC previa.
Prioriza los de CAGR > 15% (los sospechosos primero).
"""
import sys, os, json
sys.path.insert(0, '/opt/sigma')
os.chdir('/opt/sigma')

import numpy as np
from pathlib import Path
from datetime import datetime

BASE = Path('/opt/sigma/models')
CAPITAL = 1000.0


def monte_carlo(pnl_list, n_sim=2000):
    if len(pnl_list) < 5:
        return None
    pnl = np.array(pnl_list)
    years = max(len(pnl) / 56, 0.3)
    cagrs = []
    for _ in range(n_sim):
        s = np.random.choice(pnl, size=len(pnl), replace=True)
        final = CAPITAL + s.sum()
        if final <= 0:
            cagrs.append(-100); continue
        cagrs.append(((final/CAPITAL)**(1/years)-1)*100)
    cagrs = np.array(cagrs)
    return {
        'p_pos':   round(float((cagrs > 0).mean()*100), 1),
        'median':  round(float(np.median(cagrs)), 1),
        'ic95_lo': round(float(np.percentile(cagrs, 2.5)), 1),
        'ic95_hi': round(float(np.percentile(cagrs, 97.5)), 1),
        'n_trades':len(pnl_list), 'n_sims': n_sim,
    }


def get_pnl_from_backtest(symbol, tf, strategy, params, risk_pct):
    from engine.optimization.asset_pipeline import (
        fetch_asset, add_features, backtest, SIG_FN, SIG_FN_1M
    )
    df_raw = fetch_asset(symbol, tf, days=4000)
    if df_raw is None or len(df_raw) < 500:
        return None
    df = add_features(df_raw)
    split  = int(len(df) * 0.80)
    df_oos = df.iloc[split:]
    days   = (df_oos.index[-1] - df_oos.index[0]).days

    sig_fn = SIG_FN.get(strategy) or SIG_FN_1M.get(strategy)
    if not sig_fn:
        return None
    try:
        sig, sl, tp = sig_fn(df_oos, params)
        dt, eq = backtest(df_oos, sig, sl, tp, risk_pct)
        return dt['pnl'].tolist() if not dt.empty else None
    except:
        return None


def confidence(p_pos):
    if p_pos >= 75: return 'ALTA'
    if p_pos >= 60: return 'MEDIA'
    return 'BAJA'


# Collect all models needing MC
candidates = []
for p in sorted(BASE.rglob('*.json')):
    try:
        d = json.loads(p.read_text())
        oos = d.get('metrics_oos') or {}
        cagr = oos.get('cagr', 0)
        if cagr <= 0:
            continue
        already = d.get('validation', {}).get('monte_carlo', {}).get('p_pos', 0)
        if already > 0:
            continue  # already validated
        sym    = d.get('symbol', 'BTC/USDT')
        tf     = d.get('tf', p.parent.name)
        strat  = d.get('strategy', 'breakout')
        params = d.get('params', {})
        rp     = d.get('risk_pct', 3.3)
        trades = oos.get('trades', 0)
        candidates.append((cagr, sym, tf, strat, params, rp, trades, p, d))
    except:
        continue

# Sort by CAGR descending (most suspicious first)
candidates.sort(reverse=True)

print(f'\n{"="*60}')
print(f'  MONTE CARLO MASIVO — {len(candidates)} modelos sin validar')
print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
print(f'{"="*60}\n')

results_summary = []
data_cache = {}  # cache downloads per symbol+tf

for cagr, sym, tf, strat, params, rp, trades, path, data in candidates:
    asset = sym.replace('/USDT', '')
    label = f'{asset} {tf.upper()} {strat}'
    print(f'[{label}] CAGR {cagr:+.1f}% {trades}T', end=' ... ', flush=True)

    # Use cached data if available
    cache_key = f'{sym}_{tf}'
    if cache_key not in data_cache:
        data_cache[cache_key] = get_pnl_from_backtest(sym, tf, strat, params, rp)

    pnl = data_cache[cache_key]
    if not pnl or len(pnl) < 5:
        print(f'sin trades suficientes')
        continue

    mc = monte_carlo(pnl)
    if not mc:
        print(f'MC error')
        continue

    conf = confidence(mc['p_pos'])
    icon = {'ALTA': '⭐', 'MEDIA': '~', 'BAJA': '✗'}.get(conf, '?')
    print(f'P(>0)={mc["p_pos"]:.0f}% {icon} {conf}  IC95=[{mc["ic95_lo"]:+.0f}%,{mc["ic95_hi"]:+.0f}%]')

    # Save validation to model
    val = data.get('validation', {})
    val['monte_carlo']  = mc
    val['monte_carlo']['passed'] = mc['p_pos'] >= 65
    val['confidence']   = conf
    val['validated_at'] = str(datetime.now())
    data['validation']  = val
    path.write_text(json.dumps(data, indent=2, default=str))

    results_summary.append({'label': label, 'cagr': cagr, 'p_pos': mc['p_pos'], 'conf': conf})

# Final summary
print(f'\n{"="*60}')
print(f'  RESUMEN FINAL')
print(f'{"="*60}')
by_conf = {'ALTA': [], 'MEDIA': [], 'BAJA': []}
for r in results_summary:
    by_conf[r['conf']].append(r)

for conf, items in by_conf.items():
    if items:
        print(f'\n  {conf} ({len(items)} modelos):')
        for r in items:
            print(f'    {r["label"]:30s} CAGR {r["cagr"]:+.1f}%  P(>0)={r["p_pos"]:.0f}%')

print(f'\n  Total validados: {len(results_summary)}')
print(f'{"="*60}\n')

"""
Corre Monte Carlo dedicado sobre los modelos que aun no tienen validacion MC.
- BTC 15m best_validated (+35.7%)
- ETH 1H eth_breakout (+14.8%, WR 80%)
- BNB 4H bnb_breakout (+6.1%, WR 71%)
"""
import sys, os, json
sys.path.insert(0, '/opt/sigma')
os.chdir('/opt/sigma')

import numpy as np
from pathlib import Path
from datetime import datetime

MODELS_TO_VALIDATE = [
    ('models/1h/eth_breakout.json',   'ETH/USDT', '1h'),
    ('models/4h/bnb_breakout.json',   'BNB/USDT', '4h'),
    ('models/4h/sol_breakout.json',   'SOL/USDT', '4h'),
    ('models/15m/best_validated.json','BTC/USDT', '15m'),
]


def monte_carlo(pnl_list, n_sim=3000):
    if len(pnl_list) < 5:
        return None
    pnl = np.array(pnl_list)
    cagrs = []
    capital = 1000.0
    years = max(len(pnl) / 56, 0.5)
    for _ in range(n_sim):
        sample = np.random.choice(pnl, size=len(pnl), replace=True)
        final  = capital + sample.sum()
        if final <= 0:
            cagrs.append(-100)
            continue
        cagr = ((final / capital) ** (1 / years) - 1) * 100
        cagrs.append(cagr)
    cagrs = np.array(cagrs)
    return {
        'p_pos':    round(float((cagrs > 0).mean() * 100), 1),
        'median':   round(float(np.median(cagrs)), 1),
        'ic95_lo':  round(float(np.percentile(cagrs, 2.5)), 1),
        'ic95_hi':  round(float(np.percentile(cagrs, 97.5)), 1),
        'n_trades': len(pnl_list),
        'n_sims':   n_sim,
    }


def run_backtest_generic(symbol, tf, params, risk_pct, strategy):
    """Ejecuta backtest usando el pipeline o el sistema core segun estrategia."""
    from engine.optimization.asset_pipeline import (
        fetch_asset, add_features, backtest, metrics, SIG_FN, SIG_FN_1M
    )
    df_raw = fetch_asset(symbol, tf, days=4000)
    if df_raw is None:
        return None, None
    df = add_features(df_raw)
    split  = int(len(df) * 0.80)
    df_oos = df.iloc[split:]
    days_oos = (df_oos.index[-1] - df_oos.index[0]).days

    # Try pipeline signal functions first
    sig_fn = SIG_FN.get(strategy) or SIG_FN_1M.get(strategy)

    if sig_fn is None:
        # Fallback: usa core.signals (modelos del sistema antiguo)
        try:
            from core.signals import get_signals
            from core.backtest import run_backtest, calc_metrics
            sig, quality = get_signals(df_oos, params)
            trades, eq   = run_backtest(df_oos, sig, quality, params)
            m = calc_metrics(trades, eq, days_oos)
            if m and trades:
                pnl = [t.get('pnl', t.get('pnl_usd', 0)) for t in trades if isinstance(t, dict)]
                return pnl, m
        except Exception as e:
            print(f'  Fallback core.signals error: {e}')
        return None, None

    sig, sl, tp = sig_fn(df_oos, params)
    dt, eq = backtest(df_oos, sig, sl, tp, risk_pct)
    m = metrics(dt, eq, days_oos)
    return (dt['pnl'].tolist() if not dt.empty else []), m


print('='*60)
print('  SIGMA MONTE CARLO — Validacion Multi-Modelo')
print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
print('='*60)

for model_path, symbol, tf in MODELS_TO_VALIDATE:
    p = Path(model_path)
    if not p.exists():
        print(f'\n[SKIP] {model_path} no existe')
        continue

    data     = json.loads(p.read_text())
    oos      = data.get('metrics_oos', {})
    strategy = data.get('strategy', 'breakout')
    params   = data.get('params', {})
    risk_pct = data.get('risk_pct', 3.3)
    existing_val = data.get('validation', {})

    print(f'\n[{symbol} {tf.upper()}] {p.name}')
    print(f'  OOS: CAGR {oos.get("cagr",0):+.1f}% | WR {oos.get("wr",0):.0f}% | {oos.get("trades",0)}T | Strategy: {strategy}')

    # Skip if already validated with MC
    if existing_val.get('monte_carlo', {}).get('p_pos', 0) > 0:
        mc = existing_val['monte_carlo']
        print(f'  Ya validado: P(>0)={mc["p_pos"]:.1f}% — saltando')
        continue

    print(f'  Descargando datos y ejecutando backtest OOS...')
    pnl_list, m_real = run_backtest_generic(symbol, tf, params, risk_pct, strategy)

    if not pnl_list or len(pnl_list) < 5:
        print(f'  Sin trades suficientes en OOS ({len(pnl_list) if pnl_list else 0} trades)')
        continue

    print(f'  Backtest OOS: {len(pnl_list)} trades | CAGR real confirmado')
    print(f'  Monte Carlo 3000 simulaciones...')

    mc = monte_carlo(pnl_list, n_sim=3000)
    if not mc:
        print(f'  MC fallido'); continue

    if mc['p_pos'] >= 75:
        conf = 'ALTA'
    elif mc['p_pos'] >= 60:
        conf = 'MEDIA'
    else:
        conf = 'BAJA'

    print(f'  P(CAGR>0): {mc["p_pos"]:.1f}% → Confianza {conf}')
    print(f'  IC 95%: [{mc["ic95_lo"]:+.1f}%, {mc["ic95_hi"]:+.1f}%]')
    print(f'  Mediana: {mc["median"]:+.1f}%')

    # Save to model
    data['validation'] = existing_val
    data['validation']['monte_carlo'] = mc
    data['validation']['monte_carlo']['passed'] = mc['p_pos'] >= 65
    data['validation']['confidence'] = conf
    data['validation']['validated_at'] = str(datetime.now())
    p.write_text(json.dumps(data, indent=2, default=str))
    print(f'  [SAVED] Validacion guardada')

print('\n' + '='*60)
print('  COMPLETADO')
print('='*60)

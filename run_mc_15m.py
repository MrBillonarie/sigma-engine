"""Corre Monte Carlo dedicado sobre BTC 15m best_validated (+35.7% CAGR, WR 46%)."""
import sys, os, json
sys.path.insert(0, '/opt/sigma')
os.chdir('/opt/sigma')

import numpy as np
from pathlib import Path

MODEL = Path('models/15m/best_validated.json')
data  = json.loads(MODEL.read_text())
oos   = data.get('metrics_oos', {})
print(f'BTC 15m best_validated: CAGR {oos.get("cagr",0):+.1f}% WR {oos.get("wr",0):.1f}% {oos.get("trades",0)}T')

# Re-run backtest to get individual trade PnL
from engine.optimization.asset_pipeline import fetch_asset, add_features, backtest, metrics, SIG_FN
from engine.analysis.auto_validator import monte_carlo_bootstrap

symbol   = 'BTC/USDT'
tf       = '15m'
strategy = data.get('strategy', 'breakout')
params   = data.get('params', {})
risk_pct = data.get('risk_pct', 3.3)

print('Descargando datos BTC 15m...')
df_raw = fetch_asset(symbol, tf, days=4000)
df     = add_features(df_raw)
split  = int(len(df) * 0.80)
df_oos = df.iloc[split:]
days_oos = (df_oos.index[-1] - df_oos.index[0]).days

sig_fn = SIG_FN.get(strategy)
if not sig_fn:
    print(f'Estrategia {strategy} no encontrada en SIG_FN'); sys.exit(1)

print('Ejecutando backtest OOS...')
sig, sl, tp = sig_fn(df_oos, params)
dt, eq = backtest(df_oos, sig, sl, tp, risk_pct)
m_real = metrics(dt, eq, days_oos)
print(f'OOS real: {m_real}')

if dt.empty:
    print('Sin trades en OOS'); sys.exit(1)

pnl_list = dt['pnl'].tolist()
print(f'\nMonte Carlo con {len(pnl_list)} trades OOS, 5000 simulaciones...')
mc = monte_carlo_bootstrap(pnl_list, n_sim=5000)
if mc:
    print(f'P(CAGR>0):  {mc["p_pos"]:.1f}%')
    print(f'IC 95%:     [{mc["ic95_lo"]:+.1f}%, {mc["ic95_hi"]:+.1f}%]')
    print(f'IC 80%:     [{mc["ic80_lo"]:+.1f}%, {mc["ic80_hi"]:+.1f}%]')
    print(f'Mediana:    {mc["median"]:+.1f}%')
    if mc['p_pos'] >= 75:
        verdict = 'ALTA confianza'
    elif mc['p_pos'] >= 60:
        verdict = 'MEDIA confianza — paper trading con cuidado'
    else:
        verdict = 'BAJA confianza — posible sobreajuste'
    print(f'Veredicto:  {verdict}')

    # Save to model
    data['validation'] = data.get('validation', {})
    data['validation']['monte_carlo'] = mc
    data['validation']['monte_carlo']['passed'] = mc['p_pos'] >= 65
    MODEL.write_text(json.dumps(data, indent=2, default=str))
    print('Validacion guardada en best_validated.json')

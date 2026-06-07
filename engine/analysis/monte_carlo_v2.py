"""
SIGMA Monte Carlo v2 — Intervalos de confianza reales para OOS
Bootstrapea los trades OOS 1000 veces para dar:
  - IC 95% del CAGR
  - Probabilidad de CAGR > 0 (significancia estadistica)
  - Mejor/peor escenario realista
  - Numero minimo de trades para confiar en el resultado
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path(__file__).parent.parent.parent


def bootstrap_trades(trades_pnl, n_simulations=2000, days_oos=610, risk_pct=3.3):
    """
    Bootstrap de trades para obtener distribución de CAGRs.
    trades_pnl: lista de PnL por trade (en % del capital arriesgado)
    """
    capital = 1000.0
    years = days_oos / 365.25
    results = []

    for _ in range(n_simulations):
        # Resamplear con reemplazo
        sampled = np.random.choice(trades_pnl, size=len(trades_pnl), replace=True)
        # Simular equity curve
        cap = capital
        for pnl_r in sampled:
            cap = cap * (1 + pnl_r * risk_pct / 100)
        cagr = ((cap / capital) ** (1 / max(years, 0.1)) - 1) * 100
        results.append(cagr)

    results = np.array(results)
    return {
        'mean':      round(float(np.mean(results)), 2),
        'median':    round(float(np.median(results)), 2),
        'std':       round(float(np.std(results)), 2),
        'ci_95_low': round(float(np.percentile(results, 2.5)), 2),
        'ci_95_high':round(float(np.percentile(results, 97.5)), 2),
        'ci_80_low': round(float(np.percentile(results, 10)), 2),
        'ci_80_high':round(float(np.percentile(results, 90)), 2),
        'prob_positive': round(float((results > 0).mean() * 100), 1),
        'prob_gt_5':     round(float((results > 5).mean() * 100), 1),
        'prob_gt_10':    round(float((results > 10).mean() * 100), 1),
        'worst_5pct':    round(float(np.percentile(results, 5)), 2),
        'best_5pct':     round(float(np.percentile(results, 95)), 2),
    }


def analyze_model(tf, model_file='best_bull_breakout.json', n_sim=2000):
    """Analiza un modelo y calcula sus intervalos de confianza."""
    path = OUTPUT_DIR / 'models' / tf / model_file
    if not path.exists():
        return None

    with open(path) as f:
        data = json.load(f)

    m_oos = data.get('metrics_oos')
    if not m_oos:
        return None

    trades   = int(m_oos.get('trades', 0))
    wr       = m_oos.get('wr', m_oos.get('winrate', 50)) / 100
    rr       = m_oos.get('rr', 1.0)
    risk_pct = data.get('risk_pct', data.get('params', {}).get('risk_pct', 3.3))
    cagr_oos = m_oos.get('cagr', 0)

    if trades < 5:
        return None

    # Reconstruir trades sintéticos desde WR y RR
    # (si no tenemos los trades reales)
    n_wins  = round(trades * wr)
    n_loss  = trades - n_wins
    avg_win  = rr  if rr > 0 else 1.0
    avg_loss = -1.0

    trade_pnl = np.array(
        [avg_win] * n_wins + [avg_loss] * n_loss
    )

    days_oos = 610  # default OOS period

    mc = bootstrap_trades(trade_pnl, n_sim, days_oos, risk_pct)
    mc['trades_oos']  = trades
    mc['cagr_point']  = cagr_oos
    mc['wr']          = round(wr * 100, 1)
    mc['rr']          = rr
    mc['risk_pct']    = risk_pct

    # Nivel de confianza en el resultado
    if trades >= 100 and mc['prob_positive'] >= 80:
        confidence = 'ALTA — resultado confiable'
    elif trades >= 50 and mc['prob_positive'] >= 65:
        confidence = 'MEDIA — paper trading primero'
    elif mc['prob_positive'] >= 55:
        confidence = 'BAJA — necesita mas trades'
    else:
        confidence = 'MUY BAJA — probablemente ruido'

    mc['confidence'] = confidence

    return mc


def run_full_analysis():
    """Analiza todos los modelos con OOS positivo."""
    print('\n' + '='*70)
    print('  SIGMA MONTE CARLO — Intervalos de Confianza OOS')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")} | 2000 simulaciones por modelo')
    print('='*70)

    models_to_analyze = [
        ('4h', 'best_validated.json',      '4H Aggressive'),
        ('4h', 'best_bull_pullback.json',   '4H Pullback'),
        ('1h', 'best_bull_breakout.json',   '1H Breakout'),
        ('1h', 'best_validated.json',       '1H Bull Period'),
        ('15m','best_bull_tma_bands.json',  '15m TMA'),
    ]

    all_results = {}

    for tf, fname, label in models_to_analyze:
        mc = analyze_model(tf, fname)
        if not mc:
            continue

        print(f'\n  {label} ({tf.upper()})')
        print(f'  CAGR punto: {mc["cagr_point"]:+.1f}% | {mc["trades_oos"]} trades OOS')
        print(f'  IC 95%: [{mc["ci_95_low"]:+.1f}%, {mc["ci_95_high"]:+.1f}%]')
        print(f'  IC 80%: [{mc["ci_80_low"]:+.1f}%, {mc["ci_80_high"]:+.1f}%]')
        print(f'  P(CAGR>0): {mc["prob_positive"]:.0f}% | P(CAGR>10%): {mc["prob_gt_10"]:.0f}%')
        print(f'  Peor 5%: {mc["worst_5pct"]:+.1f}% | Mejor 5%: {mc["best_5pct"]:+.1f}%')
        print(f'  Confianza: {mc["confidence"]}')

        all_results[f'{tf}_{fname.replace(".json","")}'] = mc

    # Guardar
    out = OUTPUT_DIR / 'results' / 'reports' / 'monte_carlo_results.json'
    with open(out, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'n_simulations': 2000,
            'results': all_results
        }, f, indent=2)
    print(f'\n  [SAVED] {out.name}')
    print('='*70)
    return all_results


if __name__ == '__main__':
    run_full_analysis()

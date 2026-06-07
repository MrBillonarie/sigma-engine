#!/usr/bin/env python3
"""
validate_models.py — Corre MC paramétrico en todos los modelos JSON.
Agrega mc_confidence, mc_cagr_p05/p50/p95, mc_dd_p95 a cada JSON.
Uso: python validate_models.py [--runs 2000]
"""
import json, glob, random, argparse
from pathlib import Path

OUTPUT_DIR = Path('/opt/sigma')
MODELS_DIR = OUTPUT_DIR / 'models'


def run_mc(wr_pct, trades_year, sl_mult, tp_mult, risk_pct, n_years=None, n_sim=2000):
    # Ajustar horizonte: estrategias de alta frecuencia usan ventana corta para evitar compounding irreal
    if n_years is None:
        n_years = 1.0 if trades_year > 50 else 2.0
    """MC paramétrico: simula n_sim portfolios de n_years años."""
    win_r  = wr_pct / 100
    win_p  = risk_pct / 100 * tp_mult   # retorno por trade ganador
    loss_p = risk_pct / 100 * sl_mult   # pérdida por trade perdedor
    n_trades = max(1, int(trades_year * n_years))

    cagrs, dds = [], []
    for _ in range(n_sim):
        eq = 1.0; peak = 1.0; max_dd = 0.0
        for _ in range(n_trades):
            eq *= (1 + win_p) if random.random() < win_r else (1 - loss_p)
            if eq > peak:
                peak = eq
            dd = (eq - peak) / peak
            if dd < max_dd:
                max_dd = dd
        cagr = (eq ** (1 / n_years) - 1) * 100
        cagrs.append(cagr)
        dds.append(max_dd * 100)

    cagrs.sort(); dds.sort()
    n = n_sim
    return {
        'mc_cagr_p05':    round(cagrs[n // 20], 1),
        'mc_cagr_p50':    round(cagrs[n // 2], 1),
        'mc_cagr_p95':    round(cagrs[n * 19 // 20], 1),
        'mc_dd_p50':      round(dds[n // 2], 1),
        'mc_dd_p95':      round(dds[n * 19 // 20], 1),
        'mc_confidence':  round(sum(1 for c in cagrs if c > 0) / n * 100, 1),
        'mc_sims':        n_sim,
    }


def validate_all(n_sim=2000):
    files = list(MODELS_DIR.glob('*/*.json'))
    updated = 0

    for f in files:
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
            sym = d.get('symbol', '')
            if not sym:
                continue

            m     = d.get('metrics_oos', {})
            p     = d.get('params', {})
            wr    = m.get('wr', 0)
            ty    = m.get('trades_year', 0)
            cagr  = m.get('cagr', 0)
            sl    = p.get('sl_mult', p.get('sl_mult_short', 2.0))
            tp    = p.get('tp_mult', p.get('tp_mult_short', 3.0))
            risk  = d.get('risk_pct', 1.0)

            # Solo validar modelos con suficientes datos
            if wr <= 0 or ty < 3 or cagr <= 0:
                continue

            mc = run_mc(wr, ty, sl, tp, risk, n_sim=n_sim)
            d['mc'] = mc

            f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding='utf-8')
            sym_s = sym.replace('/USDT', '')
            tf    = d.get('tf', '?')
            strat = d.get('strategy', '?')
            conf  = mc['mc_confidence']
            p05   = mc['mc_cagr_p05']
            p95   = mc['mc_cagr_p95']
            dd95  = mc['mc_dd_p95']
            print(f'[MC] {sym_s:4} {tf:3} {strat:22} conf={conf:.0f}% '
                  f'CAGR p05={p05:+.0f}% p95={p95:+.0f}% DD_p95={dd95:.1f}%')
            updated += 1
        except Exception as e:
            print(f'[ERROR] {f}: {e}')

    print(f'\n{updated} modelos validados con Monte Carlo ({n_sim} simulaciones c/u)')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--runs', type=int, default=2000)
    args = p.parse_args()
    validate_all(n_sim=args.runs)

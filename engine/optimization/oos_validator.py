"""
SIGMA ENGINE — True OOS Validator
Separa los datos en IS (optimizacion) y OOS (validacion real).
El OOS nunca lo toca el optimizer — es la prueba de fuego final.

Split:
  IS:  primeros 70% de datos → optimizer puede usar estos
  VAL: 10% medio → walk-forward
  OOS: ultimos 20% → nunca tocar hasta que tengas la estrategia final

Con 180 dias de 15m:
  IS:  126 dias (primera parte)
  VAL: 18 dias  (medio)
  OOS: 36 dias  → solo se usa para el veredicto final
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
import numpy as np
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent.parent

SPLITS = {
    'IS':  (0.00, 0.70),   # 70% para optimizar
    'VAL': (0.70, 0.80),   # 10% para walk-forward
    'OOS': (0.80, 1.00),   # 20% reservado — NO TOCAR
}


def get_split(df, split='IS'):
    """Retorna el DataFrame para el split solicitado."""
    start_pct, end_pct = SPLITS[split]
    n = len(df)
    start_idx = int(n * start_pct)
    end_idx   = int(n * end_pct)
    return df.iloc[start_idx:end_idx]


def full_oos_validation(tf='15m', params=None):
    """
    Validacion completa IS → VAL → OOS.
    El resultado OOS es el numero real que importa.
    """
    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.hud_score import compute_hud_score, get_hud_signals
    from core.signals import get_signals
    from core.backtest import run_backtest, calc_metrics

    print(f"\n{'='*65}")
    print(f"  OOS VALIDATION — {tf.upper()}")
    print(f"  IS: 70% | VAL: 10% | OOS: 20% (nunca tocado)")
    print(f"{'='*65}")

    # Cargar config
    if params is None:
        model_path = OUTPUT_DIR / 'models' / tf / 'config.json'
        if not model_path.exists():
            print(f"  Sin modelo para {tf}.")
            return
        with open(model_path) as f:
            params = json.load(f).get('params', {})

    TF_MAP = {'15m':('1h','4h',180),'1h':('4h','1d',365),'5m':('15m','1h',90)}
    htf1, htf2, days = TF_MAP.get(tf, ('1h','4h',180))

    df_b  = fetch_ohlcv(tf=tf,   days=days)
    df_h1 = fetch_ohlcv(tf=htf1, days=days*2)
    df_h2 = fetch_ohlcv(tf=htf2, days=days*3)
    df    = build_features(df_b, {htf1: df_h1, htf2: df_h2})
    df.dropna(subset=['close','atr','ema50'], inplace=True)

    results = {}
    for split_name in ['IS', 'VAL', 'OOS']:
        df_s = get_split(df, split_name)
        n_days = (df_s.index[-1] - df_s.index[0]).days

        sig, qual = get_signals(df_s, params)
        trades, equity = run_backtest(df_s, sig, qual, params)
        m = calc_metrics(trades, equity, days_period=n_days)
        results[split_name] = m

        label = "*** RESULTADO REAL ***" if split_name == 'OOS' else ""
        print(f"\n  {split_name} ({df_s.index[0].strftime('%Y-%m-%d')} → "
              f"{df_s.index[-1].strftime('%Y-%m-%d')}) {label}")
        print(f"  Trades: {m['trades']} | WR: {m['winrate']:.1f}% | "
              f"CAGR: {m.get('cagr', m['pnl_pct']):+.1f}%/año | "
              f"PF: {m['profit_factor']:.2f} | DD: {m['max_dd']:.1f}%")

    # Comparacion
    print(f"\n  {'':20} {'IS':>12} {'VAL':>12} {'OOS':>12}")
    print(f"  {'-'*58}")
    for metric in ['trades','winrate','pnl_pct','profit_factor','max_dd','sharpe']:
        row = f"  {metric:<20}"
        for s in ['IS','VAL','OOS']:
            v = results[s].get(metric, 0)
            row += f" {v:>11.2f}" if isinstance(v, float) else f" {v:>12}"
        print(row)

    # Eficiencia IS→OOS
    is_cagr  = results['IS'].get('cagr', results['IS']['pnl_pct'])
    oos_cagr = results['OOS'].get('cagr', results['OOS']['pnl_pct'])
    efficiency = oos_cagr / abs(is_cagr) if abs(is_cagr) > 0.1 else 0

    print(f"\n  Eficiencia IS→OOS: {efficiency:.2f}")
    if efficiency >= 0.5:
        verdict = "EDGE REAL — listo para produccion"
    elif efficiency >= 0.2:
        verdict = "EDGE MODERADO — paper trading primero"
    elif oos_cagr > 0:
        verdict = "EDGE DEBIL — mas datos o mejor estrategia"
    else:
        verdict = "SIN EDGE — no llevar a produccion"

    print(f"  VEREDICTO: {verdict}")

    # Guardar
    report = {
        'tf': tf, 'splits': {k: {m: round(v,4) if isinstance(v,float) else v
                                  for m,v in r.items()}
                               for k, r in results.items()},
        'efficiency': round(efficiency, 3),
        'verdict': verdict,
    }
    import json as _j
    path = OUTPUT_DIR / 'results' / 'reports' / f'oos_validation_{tf}.json'
    with open(path, 'w') as f:
        _j.dump(report, f, indent=2)
    print(f"\n  [SAVED] {path.name}")

    return results, efficiency


def hud_score_oos_test(tf='15m', min_score=10, elite_score=14):
    """
    Testea el HUD scoring en OOS — el verdadero test del edge.
    Compara: señales con score >= 10 vs >= 14 en OOS.
    """
    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.hud_score import compute_hud_score
    from core.backtest import run_backtest, calc_metrics
    from core.signals import get_signals

    print(f"\n{'='*65}")
    print(f"  HUD SCORE OOS TEST — {tf.upper()}")
    print(f"  Testea si score >= {min_score} y >= {elite_score} tienen edge real en OOS")
    print(f"{'='*65}")

    TF_MAP = {'15m':('1h','4h',180),'1h':('4h','1d',365),'5m':('15m','1h',90)}
    htf1, htf2, days = TF_MAP.get(tf, ('1h','4h',180))

    df_b  = fetch_ohlcv(tf=tf,   days=days)
    df_h1 = fetch_ohlcv(tf=htf1, days=days*2)
    df_h2 = fetch_ohlcv(tf=htf2, days=days*3)
    df    = build_features(df_b, {htf1: df_h1, htf2: df_h2})
    df    = compute_hud_score(df)
    df.dropna(subset=['close','atr','ema50'], inplace=True)

    df_oos  = get_split(df, 'OOS')
    n_days  = (df_oos.index[-1] - df_oos.index[0]).days

    # Cargar params base
    model_path = OUTPUT_DIR / 'models' / tf / 'config.json'
    if not model_path.exists():
        print("  Sin modelo guardado.")
        return
    with open(model_path) as f:
        params = json.load(f).get('params', {})

    print(f"\n  OOS: {df_oos.index[0].strftime('%Y-%m-%d')} → "
          f"{df_oos.index[-1].strftime('%Y-%m-%d')} ({n_days} dias)")

    results = {}
    for threshold, label in [(0, 'Sin filtro HUD'), (min_score, f'Score >= {min_score}'),
                              (elite_score, f'Score >= {elite_score} (ELITE+)')]:
        p = dict(params)
        if threshold > 0:
            # Inyectar filtro de score en las señales
            p['_hud_min_score'] = threshold

        try:
            sig, qual = get_signals(df_oos, p)
            # Filtrar por score si aplica
            if threshold > 0:
                mask_l = sig == 1
                mask_s = sig == -1
                score_l = df_oos.get('hud_score_long', pd.Series(0, index=df_oos.index))
                score_s = df_oos.get('hud_score_short', pd.Series(0, index=df_oos.index))
                sig = sig.copy()
                sig[mask_l & (score_l < threshold)] = 0
                sig[mask_s & (score_s < threshold)] = 0

            trades, equity = run_backtest(df_oos, sig, qual, p)
            m = calc_metrics(trades, equity, days_period=n_days)
            results[label] = m

            print(f"\n  [{label}]")
            print(f"  Trades: {m['trades']} | WR: {m['winrate']:.1f}% | "
                  f"CAGR: {m.get('cagr', m['pnl_pct']):+.1f}%/año | "
                  f"PF: {m['profit_factor']:.2f} | DD: {m['max_dd']:.1f}%")
        except Exception as e:
            print(f"  [{label}] Error: {e}")

    import pandas as pd_
    print(f"\n  CONCLUSION: El filtro HUD {'MEJORA' if results.get(f'Score >= {elite_score}', {}).get('winrate',0) > results.get('Sin filtro HUD', {}).get('winrate',0) else 'NO MEJORA'} el WR en OOS")

    return results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--tf',    default='15m')
    parser.add_argument('--hud',   action='store_true', help='Test HUD scoring en OOS')
    args = parser.parse_args()

    if args.hud:
        hud_score_oos_test(args.tf)
    else:
        full_oos_validation(args.tf)

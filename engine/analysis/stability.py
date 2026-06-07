"""
SIGMA ENGINE — Parameter Stability Analysis
Antes de ir a produccion: verifica que la estrategia sea robusta.

Pregunta clave: si cambio un parametro en ±10%,
el resultado colapsa o se degrada gradualmente?

Si colapsa → overfit → NO llevar a produccion
Si degrada gradualmente → robusto → OK para produccion

Tests:
  1. Sensitivity: varia cada param ±10/20/30% y mide impacto en score
  2. Stability score: 0-100 (100 = muy robusto)
  3. Heatmap SL vs TP: cual es la zona segura
  4. Walk-window stability: el edge es consistente en el tiempo?
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
import numpy as np
import copy
import warnings
from pathlib import Path
warnings.filterwarnings('ignore')

OUTPUT_DIR = Path(__file__).parent.parent.parent


def load_best_config(tf='15m'):
    """Carga la mejor config guardada para un TF."""
    path = OUTPUT_DIR / 'models' / tf / 'config.json'
    if not path.exists():
        return None, None
    with open(path) as f:
        model = json.load(f)
    return model.get('params', {}), model.get('metrics', {})


def run_single(df, params):
    """Corre un backtest rapido y retorna el score."""
    try:
        from core.signals import get_signals
        from core.backtest import run_backtest, calc_metrics, score_config
        days = (df.index[-1] - df.index[0]).days
        sig, qual = get_signals(df, params)
        if (sig != 0).sum() < 10:
            return -999, {}
        trades, equity = run_backtest(df, sig, qual, params)
        m = calc_metrics(trades, equity, days_period=days)
        return score_config(m, min_trades=20), m
    except Exception:
        return -999, {}


def sensitivity_analysis(tf='15m', df=None, n_perturbations=5):
    """
    Varia cada parametro numerico ±10/20/30% y mide el impacto.
    Retorna dict {param: sensitivity_score} donde 0=muy sensible, 1=muy estable
    """
    from core.data import fetch_ohlcv
    from core.features import build_features

    params, base_metrics = load_best_config(tf)
    if not params:
        print(f"  Sin modelo para {tf}. Correr optimizer primero.")
        return {}

    TF_MAP = {'15m': ('1h','4h',180), '1h': ('4h','1d',365),
              '5m': ('15m','1h',90),  '4h': ('1d','1d',730)}
    htf1, htf2, days = TF_MAP.get(tf, ('1h','4h',180))

    if df is None:
        print(f"  [DATA] Cargando {tf}...")
        df_b  = fetch_ohlcv(tf=tf, days=days)
        df_h1 = fetch_ohlcv(tf=htf1, days=days*2)
        df_h2 = fetch_ohlcv(tf=htf2, days=days*3)
        df    = build_features(df_b, {htf1: df_h1, htf2: df_h2})
        df.dropna(subset=['close','atr','ema50'], inplace=True)

    base_score, _ = run_single(df, params)
    print(f"  Score base: {base_score:.4f}")

    # Parametros numericos a perturbar
    numeric_params = {k: v for k, v in params.items()
                      if isinstance(v, (int, float)) and not isinstance(v, bool)}

    sensitivities = {}
    perturbations = [-0.30, -0.20, -0.10, 0.10, 0.20, 0.30]

    print(f"\n  {'Parametro':<22} {'Score base':>10} {'±10%':>8} {'±20%':>8} {'±30%':>8} {'Estabilidad':>12}")
    print(f"  {'-'*75}")

    for param, val in numeric_params.items():
        if val == 0:
            continue
        scores_perturbed = []
        for pct in perturbations:
            p2 = copy.deepcopy(params)
            new_val = val * (1 + pct)
            # Respetar tipos
            if isinstance(val, int):
                new_val = max(1, int(round(new_val)))
            else:
                new_val = round(new_val, 3)
            p2[param] = new_val
            s, _ = run_single(df, p2)
            scores_perturbed.append(s if s > -100 else base_score * 0.5)

        # Estabilidad: que tan cerca estan los scores perturbados del base
        if base_score != 0 and len(scores_perturbed) > 0:
            deviations = [abs(s - base_score) / max(abs(base_score), 0.01)
                          for s in scores_perturbed]
            stability = max(0, 1 - np.mean(deviations))
        else:
            stability = 0.5

        sensitivities[param] = {
            'stability': stability,
            'base': base_score,
            'scores': scores_perturbed,
            'mean_perturbed': np.mean(scores_perturbed),
        }

        # Scores por nivel de perturbacion
        s10 = np.mean([scores_perturbed[2], scores_perturbed[3]])
        s20 = np.mean([scores_perturbed[1], scores_perturbed[4]])
        s30 = np.mean([scores_perturbed[0], scores_perturbed[5]])
        flag = "FRAGIL" if stability < 0.5 else "OK" if stability < 0.75 else "ROBUSTO"
        print(f"  {param:<22} {base_score:>10.4f} {s10:>8.4f} {s20:>8.4f} {s30:>8.4f} "
              f"{stability:>10.2f} {flag:>8}")

    return sensitivities


def stability_score(sensitivities):
    """Calcula el score global de estabilidad 0-100."""
    if not sensitivities:
        return 0
    stabs = [v['stability'] for v in sensitivities.values()]
    return round(np.mean(stabs) * 100, 1)


def sl_tp_heatmap(tf='15m', df=None):
    """
    Genera heatmap de performance para distintas combinaciones SL x TP.
    Muestra la zona segura y la zona fragil.
    """
    from core.data import fetch_ohlcv
    from core.features import build_features

    params, _ = load_best_config(tf)
    if not params:
        return

    TF_MAP = {'15m': ('1h','4h',180), '1h': ('4h','1d',365),
              '5m': ('15m','1h',90)}
    htf1, htf2, days = TF_MAP.get(tf, ('1h','4h',180))

    if df is None:
        df_b  = fetch_ohlcv(tf=tf, days=days)
        df_h1 = fetch_ohlcv(tf=htf1, days=days*2)
        df_h2 = fetch_ohlcv(tf=htf2, days=days*3)
        df    = build_features(df_b, {htf1: df_h1, htf2: df_h2})
        df.dropna(subset=['close','atr','ema50'], inplace=True)

    sl_range = [1.0, 1.2, 1.5, 1.7, 2.0, 2.3, 2.5]
    tp_range = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5]

    print(f"\n  HEATMAP SL x TP — {tf.upper()} (score)")
    header = f"  {'SL\\TP':>8} " + " ".join(f"{tp:>6.1f}" for tp in tp_range)
    print(header)
    print(f"  {'-'*65}")

    best_combo = None
    best_s     = -999
    grid       = []

    for sl in sl_range:
        row_scores = []
        for tp in tp_range:
            p2 = copy.deepcopy(params)
            p2['elite_sl_mult'] = sl
            p2['elite_tp_mult'] = tp
            p2['exec_sl_mult']  = sl
            p2['exec_tp_mult']  = tp
            s, _ = run_single(df, p2)
            row_scores.append(s if s > -100 else -1)
            if s > best_s:
                best_s = s; best_combo = (sl, tp)
        grid.append(row_scores)

        # Color simple en texto
        row_str = ""
        for s in row_scores:
            if s >= best_s * 0.9:  sym = "  ++++"
            elif s >= best_s * 0.7: sym = "   ++"
            elif s >= 0:             sym = "    +"
            else:                    sym = "    -"
            row_str += sym
        print(f"  SL={sl:.1f} {row_str}")

    if best_combo:
        print(f"\n  Mejor combo: SL={best_combo[0]:.1f} x TP={best_combo[1]:.1f} "
              f"(score {best_s:.4f})")

    # Guardar como CSV
    import pandas as pd
    df_hm = pd.DataFrame(grid, index=[f"SL{s}" for s in sl_range],
                         columns=[f"TP{t}" for t in tp_range])
    path  = OUTPUT_DIR / 'results' / 'reports' / f'heatmap_sltp_{tf}.csv'
    df_hm.to_csv(path)
    print(f"  [CSV] {path.name}")

    return grid, sl_range, tp_range


def time_window_stability(tf='15m', df=None, n_windows=6):
    """
    Divide los datos en N ventanas temporales iguales.
    Testa si el edge es consistente o solo funciona en ciertos periodos.
    Un sistema robusto deberia ser positivo en >60% de las ventanas.
    """
    from core.data import fetch_ohlcv
    from core.features import build_features

    params, base_m = load_best_config(tf)
    if not params:
        return

    TF_MAP = {'15m': ('1h','4h',180), '1h': ('4h','1d',365),
              '5m': ('15m','1h',90)}
    htf1, htf2, days = TF_MAP.get(tf, ('1h','4h',180))

    if df is None:
        df_b  = fetch_ohlcv(tf=tf, days=days)
        df_h1 = fetch_ohlcv(tf=htf1, days=days*2)
        df_h2 = fetch_ohlcv(tf=htf2, days=days*3)
        df    = build_features(df_b, {htf1: df_h1, htf2: df_h2})
        df.dropna(subset=['close','atr','ema50'], inplace=True)

    window_size = len(df) // n_windows
    results     = []

    print(f"\n  TIME-WINDOW STABILITY — {tf.upper()} ({n_windows} ventanas)")
    print(f"  {'Ventana':<20} {'Periodo':<25} {'Trades':>7} {'WR%':>7} "
          f"{'CAGR%':>8} {'Score':>8} {'Estado':>8}")
    print(f"  {'-'*90}")

    for i in range(n_windows):
        start = i * window_size
        end   = start + window_size
        df_w  = df.iloc[start:end]
        if len(df_w) < 100:
            continue

        s, m = run_single(df_w, params)
        period = (f"{df_w.index[0].strftime('%Y-%m-%d')} "
                  f"→ {df_w.index[-1].strftime('%Y-%m-%d')}")
        status = "POSITIVO" if m.get('pnl_pct', 0) > 0 else "NEGATIVO"
        results.append({'window': i+1, 'score': s, 'pnl': m.get('pnl_pct',0),
                        'wr': m.get('winrate',0), 'trades': m.get('trades',0)})
        print(f"  Ventana {i+1:<13} {period:<25} {m.get('trades',0):>7} "
              f"{m.get('winrate',0):>6.1f}% "
              f"{m.get('cagr', m.get('pnl_pct',0)):>7.1f}% "
              f"{s:>8.4f} {status:>8}")

    n_pos = sum(1 for r in results if r['pnl'] > 0)
    consistency = n_pos / len(results) * 100 if results else 0

    print(f"\n  Ventanas positivas: {n_pos}/{len(results)} ({consistency:.0f}%)")
    if consistency >= 70:
        print(f"  VEREDICTO: EDGE CONSISTENTE — seguro para produccion")
    elif consistency >= 50:
        print(f"  VEREDICTO: EDGE MODERADO — paper trading antes de capital real")
    else:
        print(f"  VEREDICTO: EDGE INCONSISTENTE — no llevar a produccion")

    return results, consistency


def full_stability_report(tf='15m'):
    """Reporte completo de estabilidad para un TF."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    params, metrics = load_best_config(tf)
    if not params:
        print(f"  Sin modelo para {tf}.")
        return

    print(f"\n{'='*70}")
    print(f"  STABILITY REPORT — {tf.upper()}")
    print(f"{'='*70}")
    m = metrics
    print(f"  Modelo actual: {m.get('trades',0)}T | WR {m.get('winrate',0):.1f}% | "
          f"CAGR {m.get('cagr', m.get('pnl_pct',0)):+.1f}%/año | "
          f"PF {m.get('profit_factor',0):.2f} | DD {m.get('max_dd',0):.1f}%\n")

    # Pre-cargar datos
    from core.data import fetch_ohlcv
    from core.features import build_features
    TF_MAP = {'15m': ('1h','4h',180), '1h': ('4h','1d',365), '5m': ('15m','1h',90)}
    htf1, htf2, days = TF_MAP.get(tf, ('1h','4h',180))
    df_b  = fetch_ohlcv(tf=tf, days=days)
    df_h1 = fetch_ohlcv(tf=htf1, days=days*2)
    df_h2 = fetch_ohlcv(tf=htf2, days=days*3)
    df    = build_features(df_b, {htf1: df_h1, htf2: df_h2})
    df.dropna(subset=['close','atr','ema50'], inplace=True)

    # Test 1: Sensibilidad
    print(f"  [1/3] ANALISIS DE SENSIBILIDAD...")
    sensitivities = sensitivity_analysis(tf, df=df)
    stab_score    = stability_score(sensitivities)
    print(f"\n  Score de estabilidad global: {stab_score:.1f}/100")
    if stab_score >= 70:
        print(f"  ROBUSTO — los parametros son estables")
    elif stab_score >= 50:
        print(f"  MODERADO — algunos parametros son sensibles")
    else:
        print(f"  FRAGIL — posible overfitting")

    # Test 2: Heatmap SL/TP
    print(f"\n  [2/3] HEATMAP SL x TP...")
    sl_tp_heatmap(tf, df=df)

    # Test 3: Ventanas temporales
    print(f"\n  [3/3] ESTABILIDAD TEMPORAL...")
    window_results, consistency = time_window_stability(tf, df=df)

    # Veredicto final
    print(f"\n{'='*70}")
    print(f"  VEREDICTO FINAL — {tf.upper()}")
    print(f"{'='*70}")
    print(f"  Estabilidad params: {stab_score:.1f}/100")
    print(f"  Consistencia temporal: {consistency:.0f}%")

    go_score = (stab_score / 100 * 0.4 + consistency / 100 * 0.6) * 100

    if go_score >= 65:
        verdict = "LISTO PARA PRODUCCION"
        action  = "Conectar Pine Script → HUD → Make.com → Excel"
    elif go_score >= 45:
        verdict = "PAPER TRADING PRIMERO"
        action  = "30 dias paper trading antes de capital real"
    else:
        verdict = "NO LLEVAR A PRODUCCION"
        action  = "Re-optimizar con mas historia o cambiar estrategia"

    print(f"\n  GO SCORE: {go_score:.1f}/100")
    print(f"  VEREDICTO: {verdict}")
    print(f"  ACCION:    {action}")

    # Guardar reporte
    report = {
        'tf': tf, 'model_metrics': metrics,
        'stability_score': stab_score,
        'temporal_consistency': consistency,
        'go_score': go_score,
        'verdict': verdict,
        'action': action,
        'timestamp': __import__('datetime').datetime.now().isoformat(),
    }
    import json as _json
    path = OUTPUT_DIR / 'results' / 'reports' / f'stability_{tf}.json'
    with open(path, 'w') as f:
        _json.dump(report, f, indent=2)
    print(f"\n  [SAVED] {path.name}")

    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--tf',  default='15m')
    parser.add_argument('--all', action='store_true')
    args = parser.parse_args()

    if args.all:
        for tf in ['15m', '1h', '5m']:
            full_stability_report(tf)
    else:
        full_stability_report(args.tf)

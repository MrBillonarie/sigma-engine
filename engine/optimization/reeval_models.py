#!/usr/bin/env python3
"""
SIGMA — Re-evaluación de modelos con backtest actualizado.
Aplica funding rates + slippage a todos los modelos guardados.
Actualiza metrics_oos y score en cada JSON.

Uso: python engine/optimization/reeval_models.py
     python engine/optimization/reeval_models.py --dry_run   (solo muestra, no guarda)
     python engine/optimization/reeval_models.py --tf 4h     (solo un TF)
"""
import sys, os, json, argparse
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from pathlib import Path
from datetime import datetime
import pandas as pd
import warnings; warnings.filterwarnings('ignore')

BASE = Path(__file__).parent.parent.parent

SKIP = {'config.json', 'adaptive_params.json', 'walk_forward_v2.json',
        'current_params.json', 'regime_params.json', 'config_aggressive.json',
        'new_strategy.json', 'conservative.json'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry_run', action='store_true')
    parser.add_argument('--tf',     default='', help='Filtrar por TF')
    parser.add_argument('--symbol', default='', help='Filtrar por símbolo')
    args = parser.parse_args()

    from engine.optimization.asset_pipeline import (
        fetch_asset, add_features, backtest, metrics, score,
        SIG_FN, SIG_FN_SHORT, SIG_FN_ADAPTIVE, SIG_FN_1M
    )
    ALL_SIG = {**SIG_FN, **SIG_FN_SHORT, **SIG_FN_ADAPTIVE, **SIG_FN_1M}

    print(f'\n{"="*65}')
    print(f'  SIGMA RE-EVALUACIÓN CON FUNDING + SLIPPAGE')
    print(f'  {"[DRY RUN] " if args.dry_run else ""}{ datetime.now().strftime("%Y-%m-%d %H:%M") }')
    print('='*65)

    results = []
    data_cache = {}  # (symbol, tf) → df

    for tf_dir in sorted((BASE / 'models').iterdir()):
        if not tf_dir.is_dir() or tf_dir.name == 'archive': continue
        tf = tf_dir.name
        if args.tf and tf != args.tf: continue

        for jf in sorted(tf_dir.glob('*.json')):
            if jf.name in SKIP: continue
            try:
                data = json.loads(jf.read_text(encoding='utf-8'))
            except:
                continue

            symbol   = data.get('symbol', '')
            strategy = data.get('strategy', '')
            params   = data.get('params', {})
            m_old    = data.get('metrics_oos', {})

            if not symbol or not strategy or not params: continue
            if m_old.get('cagr', 0) <= 0: continue
            if args.symbol and args.symbol.upper() not in symbol.upper(): continue
            if strategy not in ALL_SIG: continue

            # Datos (con cache)
            key = (symbol, tf)
            if key not in data_cache:
                print(f'  Descargando {symbol} {tf}...')
                df_raw = fetch_asset(symbol, tf, days=3200 if tf in ('1h','4h') else 1000)
                if df_raw is None or len(df_raw) < 500:
                    data_cache[key] = None
                    continue
                data_cache[key] = add_features(df_raw)
            df = data_cache[key]
            if df is None: continue

            # Split IS/OOS igual que el pipeline (80/20)
            n = len(df); split = int(n * 0.80)
            df_oos   = df.iloc[split:]
            days_oos = max((df_oos.index[-1] - df_oos.index[0]).days, 1)

            # Re-backtest OOS con funding + slippage
            try:
                sig_fn = ALL_SIG[strategy]
                sig_oos, sl_oos, tp_oos = sig_fn(df_oos, params)
                dt_oos, eq_oos = backtest(df_oos, sig_oos, sl_oos, tp_oos, 1.0, use_kelly=False)
                m_new = metrics(dt_oos, eq_oos, days_oos, min_t=5)
            except Exception as e:
                print(f'  {jf.name}: error backtest — {e}')
                continue

            if m_new is None:
                m_new = {'trades': 0, 'wr': 0, 'cagr': 0, 'dd': 0, 'pf': 0, 'trades_year': 0}

            s_old = score(m_old)
            s_new = score(m_new)
            delta_cagr = m_new.get('cagr', 0) - m_old.get('cagr', 0)

            sym = symbol.replace('/USDT', '')
            flag = '↓' if delta_cagr < -3 else ('↑' if delta_cagr > 1 else '~')
            print(f'  {jf.name:<35} {sym:<5} {tf:<5} '
                  f'CAGR: {m_old.get("cagr",0):+5.1f}% → {m_new.get("cagr",0):+5.1f}% {flag}  '
                  f'score: {s_old:.3f} → {s_new:.3f}')

            results.append({
                'file': str(jf), 'name': jf.name, 'symbol': symbol, 'tf': tf,
                'strategy': strategy,
                'cagr_old': m_old.get('cagr', 0), 'cagr_new': m_new.get('cagr', 0),
                'wr_old': m_old.get('wr', 0), 'wr_new': m_new.get('wr', 0),
                'score_old': s_old, 'score_new': s_new,
            })

            if not args.dry_run and m_new.get('trades', 0) >= 5:
                # Actualizar JSON con métricas reales (funding + slippage incluidos)
                data['metrics_oos'] = m_new
                data['reeval_date'] = str(datetime.now())[:16]
                data['reeval_note'] = 'funding+slippage aplicados'
                jf.write_text(json.dumps(data, indent=2, default=str), encoding='utf-8')

    # Resumen
    if not results:
        print('  Sin modelos procesados.')
        return

    print(f'\n{"="*65}')
    print(f'  RESUMEN — {len(results)} modelos re-evaluados')
    improved = [r for r in results if r['cagr_new'] > r['cagr_old']]
    degraded = [r for r in results if r['cagr_new'] < r['cagr_old'] - 5]
    print(f'  Mejoraron: {len(improved)} | Degradaron >5pp: {len(degraded)} | Estables: {len(results)-len(improved)-len(degraded)}')
    if degraded:
        print(f'\n  Modelos más afectados por funding+slippage:')
        for r in sorted(degraded, key=lambda x: x['cagr_new']-x['cagr_old'])[:5]:
            sym = r['symbol'].replace('/USDT','')
            print(f'    {sym} {r["tf"]} {r["strategy"]:<20} '
                  f'CAGR: {r["cagr_old"]:+.1f}% → {r["cagr_new"]:+.1f}% '
                  f'(score {r["score_old"]:.3f} → {r["score_new"]:.3f})')

    avg_delta = sum(r['cagr_new']-r['cagr_old'] for r in results) / len(results)
    print(f'\n  Delta CAGR promedio: {avg_delta:+.1f}%')
    print(f'  {"[DRY RUN — nada guardado]" if args.dry_run else "JSONs actualizados con métricas reales."}')
    print('='*65 + '\n')


if __name__ == '__main__':
    main()

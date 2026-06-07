"""
SIGMA ENGINE — Strategy Correlation Analysis
Verifica que las estrategias de distintos TFs no esten correlacionadas.
Si estan correlacionadas, en un crash todas pierden al mismo tiempo.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
import numpy as np
import pandas as pd
import warnings
from pathlib import Path
warnings.filterwarnings('ignore')

OUTPUT_DIR = Path(__file__).parent.parent.parent


def compute_equity_returns(tf, days=None):
    """Genera la serie de retornos de una estrategia."""
    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.signals import get_signals
    from core.backtest import run_backtest

    model_path = OUTPUT_DIR / 'models' / tf / 'config.json'
    if not model_path.exists():
        return None

    with open(model_path) as f:
        model = json.load(f)
    params = model.get('params', {})

    TF_MAP = {'15m':('1h','4h',180),'1h':('4h','1d',365),'5m':('15m','1h',90),'4h':('1d','1d',730)}
    htf1, htf2, d = TF_MAP.get(tf, ('1h','4h',180))
    days = days or d

    try:
        df_b  = fetch_ohlcv(tf=tf,   days=days)
        df_h1 = fetch_ohlcv(tf=htf1, days=days*2)
        df_h2 = fetch_ohlcv(tf=htf2, days=days*3)
        df    = build_features(df_b, {htf1: df_h1, htf2: df_h2})
        df.dropna(subset=['close','atr','ema50'], inplace=True)
        sig, qual = get_signals(df, params)
        _, equity = run_backtest(df, sig, qual, params)
        return equity.resample('1D').last().pct_change().dropna()
    except Exception:
        return None


def analyze_correlation(tfs=None):
    """
    Calcula la correlacion entre las equity curves de distintos TFs.
    Si correlacion > 0.7 → riesgo alto de drawdown simultaneo.
    """
    tfs = tfs or ['5m', '15m', '1h', '4h']

    print(f"\n{'='*60}")
    print(f"  CORRELATION ANALYSIS — {tfs}")
    print(f"{'='*60}\n")

    returns = {}
    for tf in tfs:
        print(f"  Cargando {tf}...")
        r = compute_equity_returns(tf)
        if r is not None and len(r) > 10:
            returns[tf] = r
        else:
            print(f"  {tf}: sin datos suficientes")

    if len(returns) < 2:
        print("  Necesitas al menos 2 TFs con modelos para analizar correlacion.")
        return

    # Alinear fechas
    df_ret = pd.DataFrame(returns)
    df_ret.dropna(how='all', inplace=True)
    df_ret.fillna(0, inplace=True)

    # Matriz de correlacion
    corr = df_ret.corr()

    print(f"  MATRIZ DE CORRELACION (retornos diarios):")
    print(f"  {corr.to_string()}\n")

    # Evaluar riesgo
    print(f"  {'Par':<15} {'Correlacion':>12} {'Riesgo':>10}")
    print(f"  {'-'*40}")
    for i, tf1 in enumerate(corr.columns):
        for j, tf2 in enumerate(corr.columns):
            if j <= i:
                continue
            c = corr.loc[tf1, tf2]
            if c > 0.7:
                risk = "ALTO - diversificar"
            elif c > 0.4:
                risk = "MODERADO"
            else:
                risk = "BAJO - bien diversificado"
            print(f"  {tf1}-{tf2:<12} {c:>12.3f} {risk:>10}")

    # Portfolio DD analysis
    print(f"\n  ANALISIS DE DRAWDOWN COMBINADO:")
    n_strategies = len(returns)
    equal_weight = 1.0 / n_strategies

    portfolio_ret = sum(r * equal_weight for r in returns.values()
                        if isinstance(r, pd.Series))

    if isinstance(portfolio_ret, pd.Series) and len(portfolio_ret) > 0:
        portfolio_eq = (1 + portfolio_ret).cumprod()
        portfolio_dd = (portfolio_eq / portfolio_eq.cummax() - 1) * 100
        max_dd_portfolio = portfolio_dd.min()

        # Individual max DDs
        print(f"  {'Estrategia':<12} {'Max DD individual':>20}")
        for tf, r in returns.items():
            eq = (1 + r).cumprod()
            dd = (eq / eq.cummax() - 1) * 100
            print(f"  {tf:<12} {dd.min():>19.1f}%")

        print(f"\n  Portfolio equi-ponderado: {max_dd_portfolio:.1f}%")

        if abs(max_dd_portfolio) < 5:
            print(f"  VEREDICTO: BUENA DIVERSIFICACION")
        elif abs(max_dd_portfolio) < 15:
            print(f"  VEREDICTO: DIVERSIFICACION MODERADA")
        else:
            print(f"  VEREDICTO: ALTA CORRELACION — revisar weights")

    # Recomendacion de sizing
    print(f"\n  SIZING RECOMENDADO (Kelly portfolio):")
    for tf in returns.keys():
        model_path = OUTPUT_DIR / 'models' / tf / 'config.json'
        if model_path.exists():
            with open(model_path) as f:
                m = json.load(f).get('metrics', {})
            wr = m.get('winrate', 50) / 100
            pf = m.get('profit_factor', 1)
            if pf > 1 and wr > 0.3:
                b = pf
                f_kelly = wr - (1-wr)/b
                f_quarter = max(0, f_kelly * 0.25)
                print(f"  {tf}: Quarter Kelly = {f_quarter:.3f} "
                      f"({f_quarter*100:.1f}% del capital)")

    # Guardar
    path = OUTPUT_DIR / 'results' / 'reports' / 'correlation_analysis.csv'
    corr.to_csv(path)
    print(f"\n  [CSV] correlation_analysis.csv")

    return corr


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--tfs', nargs='+', default=['5m','15m','1h'])
    args = parser.parse_args()
    analyze_correlation(args.tfs)

"""
SIGMA ENGINE — Performance Heatmap
Analiza performance por hora del dia x dia de la semana.
Identifica las mejores franjas horarias para operar.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd
import warnings
from pathlib import Path
warnings.filterwarnings('ignore')

OUTPUT_DIR = Path(__file__).parent.parent.parent
DAYS   = ['Lun', 'Mar', 'Mie', 'Jue', 'Vie', 'Sab', 'Dom']
HOURS  = list(range(0, 24))


def build_heatmap(trades_df, df_features=None):
    """
    Construye heatmap hora x dia desde trades reales o simulados.
    trades_df: DataFrame con columnas exit_time, pnl, side
    """
    if trades_df.empty:
        return None

    df = trades_df.copy()
    if 'exit_time' in df.columns:
        df['hour'] = pd.to_datetime(df['exit_time']).dt.hour
        df['dow']  = pd.to_datetime(df['exit_time']).dt.dayofweek
    else:
        return None

    # Matriz de WR por hora x dia
    wr_matrix  = np.full((24, 7), np.nan)
    pnl_matrix = np.full((24, 7), np.nan)
    n_matrix   = np.zeros((24, 7))

    for h in range(24):
        for d in range(7):
            mask = (df['hour'] == h) & (df['dow'] == d)
            subset = df[mask]
            if len(subset) >= 2:
                wins = (subset['pnl'] > 0).sum()
                wr_matrix[h, d]  = wins / len(subset) * 100
                pnl_matrix[h, d] = subset['pnl'].mean()
                n_matrix[h, d]   = len(subset)

    return wr_matrix, pnl_matrix, n_matrix


def print_heatmap(wr_matrix, n_matrix, metric='WR'):
    """Imprime heatmap en consola."""
    print(f"\n  HEATMAP {metric} por HORA x DIA")
    print(f"  {'Hora':>5} " + " ".join(f"{d:>6}" for d in DAYS))
    print(f"  {'-'*55}")

    for h in HOURS:
        row = f"  {h:02d}:00 "
        for d in range(7):
            val = wr_matrix[h, d]
            n   = n_matrix[h, d]
            if np.isnan(val) or n < 2:
                row += "     - "
            elif val >= 65:
                row += f"  {val:.0f}%*"
            elif val >= 55:
                row += f"  {val:.0f}% "
            else:
                row += f" ({val:.0f}%)"
        print(row)
    print(f"  (* = WR >= 65%, buenos momentos para operar)")


def get_best_windows(wr_matrix, n_matrix, min_wr=55, min_trades=3):
    """Retorna las mejores franjas horarias."""
    best = []
    for h in range(24):
        for d in range(7):
            if not np.isnan(wr_matrix[h, d]) and n_matrix[h, d] >= min_trades:
                if wr_matrix[h, d] >= min_wr:
                    best.append({
                        'hour': h, 'day': DAYS[d],
                        'wr': round(wr_matrix[h, d], 1),
                        'n': int(n_matrix[h, d]),
                    })
    return sorted(best, key=lambda x: x['wr'], reverse=True)


def generate_session_filter_pine(best_windows, top_n=5):
    """Genera el filtro de sesion Pine Script basado en los mejores horarios."""
    if not best_windows:
        return ""

    top = best_windows[:top_n]
    conditions = []
    for w in top:
        h   = w['hour']
        dow = {'Lun':2,'Mar':3,'Mie':4,'Jue':5,'Vie':6,'Sab':7,'Dom':1}[w['day']]
        conditions.append(f"(hour(time,\"UTC\")=={h} and dayofweek=={dow})")

    pine = f"""
// ── OPTIMAL SESSION FILTER (generado por heatmap analysis) ──────────────────
// Top {top_n} franjas horarias con mejor WR historico
optimal_window = {" or ".join(conditions)}
// Agregar a las condiciones de entrada:
// entry_long  = entry_long_raw  and optimal_window
// entry_short = entry_short_raw and optimal_window
"""
    return pine


def run_heatmap_analysis(tf='15m', df_features=None, params=None):
    """Analisis completo de heatmap para un TF."""
    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.signals import get_signals
    from core.backtest import run_backtest, calc_metrics

    print(f"\n{'='*60}")
    print(f"  HEATMAP ANALYSIS — {tf.upper()}")
    print(f"{'='*60}")

    # Cargar config
    if params is None:
        model_path = OUTPUT_DIR / 'models' / tf / 'config.json'
        if not model_path.exists():
            print(f"  Sin modelo para {tf}.")
            return
        with open(model_path) as f:
            model = json.load(f) if (model_path := model_path) else {}
        params = model.get('params', {})

    import json
    # Cargar datos
    TF_MAP = {'15m': ('1h','4h',180), '1h': ('4h','1d',365), '5m': ('15m','1h',90)}
    htf1, htf2, days = TF_MAP.get(tf, ('1h','4h',180))

    if df_features is None:
        print(f"  [DATA] Cargando {tf}...")
        df_b  = fetch_ohlcv(tf=tf, days=days)
        df_h1 = fetch_ohlcv(tf=htf1, days=days*2)
        df_h2 = fetch_ohlcv(tf=htf2, days=days*3)
        df    = build_features(df_b, {htf1: df_h1, htf2: df_h2})
        df.dropna(subset=['close','atr','ema50'], inplace=True)
    else:
        df = df_features

    # Correr backtest para obtener trades
    sig, qual = get_signals(df, params)
    trades, equity = run_backtest(df, sig, qual, params)

    if trades.empty or len(trades) < 10:
        print(f"  Sin suficientes trades ({len(trades)}). Necesitas mas datos.")
        return

    m = calc_metrics(trades, equity, days_period=days)
    print(f"  Trades totales: {len(trades)} | WR: {m['winrate']:.1f}%\n")

    # Construir heatmap
    wr_m, pnl_m, n_m = build_heatmap(trades, df)

    # Imprimir
    print_heatmap(wr_m, n_m, 'WR%')

    # Mejores franjas
    best = get_best_windows(wr_m, n_m, min_wr=55, min_trades=2)
    if best:
        print(f"\n  TOP FRANJAS HORARIAS (WR >= 55%):")
        print(f"  {'Hora':>6} {'Dia':>5} {'WR%':>6} {'N':>4}")
        print(f"  {'-'*25}")
        for w in best[:8]:
            print(f"  {w['hour']:02d}:00h {w['day']:>5} {w['wr']:>5.1f}% {w['n']:>4}")

        # Pine Script snippet
        pine = generate_session_filter_pine(best, top_n=5)
        path = OUTPUT_DIR / 'results' / 'pine_scripts' / f'session_filter_{tf}.pine'
        with open(path, 'w', encoding='utf-8') as f:
            f.write(pine.strip())
        print(f"\n  [PINE] Filtro de sesion guardado: {path.name}")

    # CSV
    df_hm = pd.DataFrame(wr_m, index=[f"{h:02d}:00" for h in HOURS],
                         columns=DAYS)
    csv_path = OUTPUT_DIR / 'results' / 'reports' / f'heatmap_{tf}.csv'
    df_hm.to_csv(csv_path)
    print(f"  [CSV] {csv_path.name}")

    # Imagen
    _plot_heatmap(wr_m, n_m, tf)

    return wr_m, best


def _plot_heatmap(wr_matrix, n_matrix, tf):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(12, 8))
        fig.patch.set_facecolor('#0f0f23')
        ax.set_facecolor('#1a1a2e')

        # Filtrar celdas con pocos datos
        display = np.where(n_matrix >= 2, wr_matrix, np.nan)

        im = ax.imshow(display, cmap='RdYlGn', aspect='auto',
                       vmin=30, vmax=80, origin='upper')

        # Texto en cada celda
        for h in range(24):
            for d in range(7):
                if not np.isnan(display[h, d]):
                    n   = int(n_matrix[h, d])
                    val = display[h, d]
                    ax.text(d, h, f'{val:.0f}%\nn={n}',
                            ha='center', va='center', fontsize=7,
                            color='white' if val < 50 or val > 70 else 'black')

        ax.set_xticks(range(7)); ax.set_xticklabels(DAYS, color='#aaa')
        ax.set_yticks(range(0, 24, 2))
        ax.set_yticklabels([f'{h:02d}:00' for h in range(0, 24, 2)], color='#aaa')
        ax.set_title(f'SIGMA {tf.upper()} — WR% por Hora x Dia de la Semana',
                     color='white', fontsize=12, pad=10)

        plt.colorbar(im, ax=ax, label='Win Rate %')
        plt.tight_layout()

        path = OUTPUT_DIR / 'results' / 'charts' / f'heatmap_{tf}.png'
        plt.savefig(path, dpi=130, bbox_inches='tight', facecolor='#0f0f23')
        plt.close()
        print(f"  [CHART] {path.name}")
    except Exception:
        pass


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--tf', default='15m')
    args = parser.parse_args()
    run_heatmap_analysis(args.tf)

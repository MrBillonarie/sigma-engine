"""
SIGMA ENGINE — SL/TP Auto-Calibration from MAE/MFE
====================================================
Usa los datos MAE/MFE de los trades reales para sugerir
los multiplicadores óptimos de SL y TP.

Principios:
  MAE (Maximum Adverse Excursion): cuán lejos fue el precio
      en contra antes de cerrar. Para los winners, MAE_p90
      dice "si un trade va a ganar, casi nunca fue más de X ATR en contra".
      Ese es el SL óptimo: dai justo el espacio que necesitan.

  MFE (Maximum Favorable Excursion): cuán lejos fue a favor
      en su mejor momento. Para los winners, MFE_p30 es el
      TP1 conservador (70% lo alcanza), MFE_p65 es el TP2.

Uso:
  from analysis.sl_tp_calibration import calibrate_sl_tp, cross_year_validation
  result = calibrate_sl_tp(trades_df)
  print_calibration_report(result)

  yearly = cross_year_validation(df, cfg, tf="1h")
  print_cross_year_report(yearly)
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy  as np
from pathlib import Path


# ─── SL/TP CALIBRACIÓN ────────────────────────────────────────────────────────

def calibrate_sl_tp(trades_df, verbose=True):
    """
    Analiza la distribución MAE/MFE para sugerir SL/TP óptimos.

    Requiere columnas: mae_atr, mfe_atr, won, quality, sl_mult, tp_mult.
    Retorna dict con recomendaciones y diagnóstico.
    """
    if trades_df is None or trades_df.empty:
        print("  [WARN] Sin trades para calibrar.")
        return None

    if "mae_atr" not in trades_df.columns or "mfe_atr" not in trades_df.columns:
        print("  [WARN] No hay columnas MAE/MFE. Corre el backtest con el engine nuevo.")
        return None

    w = trades_df[trades_df["won"] == True].copy()
    l = trades_df[trades_df["won"] == False].copy()

    if len(w) < 5 or len(l) < 5:
        print("  [WARN] Pocas trades para análisis estadístico (mínimo 5 winners + 5 losers).")
        return None

    # MAE: negativo = fue en contra (en ATR)
    # Lo convertimos a positivo para que sea "cuánto margen usó"
    mae_w = w["mae_atr"].abs()   # winners: cuánto ATR en contra toleraron
    mae_l = l["mae_atr"].abs()   # losers: cuánto ATR en contra antes de rendirse

    # MFE: positivo = fue a favor (en ATR)
    mfe_w = w["mfe_atr"]         # winners: cuánto llegaron a ganar como máximo

    # ── Diagnóstico del SL actual ──────────────────────────────────────────────
    current_sl = trades_df["sl_mult"].mean() if "sl_mult" in trades_df.columns else None

    # ¿Cuántos winners toleraron más MAE que el SL actual? (falsos stops potenciales)
    if current_sl:
        winners_would_stop = (mae_w > current_sl).sum()
        pct_false_stops = winners_would_stop / len(w) * 100
    else:
        winners_would_stop = 0
        pct_false_stops    = 0

    # El SL óptimo: nivel donde separamos winners de losers con máxima eficiencia
    # Criterio: SL = MAE_p90 de winners (das espacio al 90% de ellos)
    sl_tight   = mae_w.quantile(0.75)   # muy ajustado: 25% de winners se stop prematuro
    sl_optimal = mae_w.quantile(0.85)   # balanceado: 15% falsos stops
    sl_wide    = mae_w.quantile(0.95)   # amplio: solo 5% falsos stops

    # Eficiencia por SL: qué % de losers ya pasaron ese nivel (buenos stops)
    loser_pct_at_sl_opt = (mae_l > sl_optimal).mean() * 100

    # ── Diagnóstico del TP actual ─────────────────────────────────────────────
    current_tp = trades_df["tp_mult"].mean() if "tp_mult" in trades_df.columns else None

    # TP1 óptimo: MFE_p35 de winners (65% lo alcanza antes de revertir)
    tp1_conservative = mfe_w.quantile(0.25)  # 75% de winners lo alcanzan
    tp1_optimal      = mfe_w.quantile(0.35)  # 65% de winners lo alcanzan
    tp1_ambitious    = mfe_w.quantile(0.50)  # 50% de winners lo alcanzan

    # TP2 óptimo: MFE_p65 de winners
    tp2_conservative = mfe_w.quantile(0.50)
    tp2_optimal      = mfe_w.quantile(0.65)
    tp2_ambitious    = mfe_w.quantile(0.80)

    # ¿Cuánto MFE dejamos sin capturar? (dinero en la mesa)
    if current_tp:
        tp1_actual = current_tp
        tp2_actual = current_tp * 1.5
        missed_tp1 = max(0, tp1_optimal - tp1_actual)
        missed_tp2 = max(0, tp2_optimal - tp2_actual)
    else:
        missed_tp1 = missed_tp2 = 0

    # ── Ratio MAE/MFE: calidad de los winners ────────────────────────────────
    # Un ratio MFE/MAE alto significa que los winners ganan mucho más de lo que arriesgan
    ratio_w = (mfe_w / mae_w.replace(0, np.nan)).dropna()
    avg_ratio = ratio_w.mean()
    ratio_p50 = ratio_w.median()

    # ── Análisis por calidad ──────────────────────────────────────────────────
    quality_analysis = {}
    if "quality" in trades_df.columns:
        for q in trades_df["quality"].unique():
            qt = trades_df[trades_df["quality"] == q]
            qw = qt[qt["won"] == True]
            ql = qt[qt["won"] == False]
            if len(qw) >= 3:
                quality_analysis[q] = {
                    "n": len(qt), "wr": len(qw)/len(qt)*100,
                    "mae_p85": qw["mae_atr"].abs().quantile(0.85),
                    "mfe_p50": qw["mfe_atr"].quantile(0.50),
                    "avg_ratio": (qw["mfe_atr"] / qw["mae_atr"].abs().replace(0, np.nan)).mean(),
                }

    result = {
        # SL
        "sl_tight":    round(sl_tight,   2),
        "sl_optimal":  round(sl_optimal, 2),
        "sl_wide":     round(sl_wide,    2),
        "sl_current":  round(current_sl, 2) if current_sl else None,
        "pct_false_stops":       round(pct_false_stops, 1),
        "pct_losers_past_sl":    round(loser_pct_at_sl_opt, 1),
        # TP
        "tp1_conservative": round(tp1_conservative, 2),
        "tp1_optimal":      round(tp1_optimal, 2),
        "tp1_ambitious":    round(tp1_ambitious, 2),
        "tp2_conservative": round(tp2_conservative, 2),
        "tp2_optimal":      round(tp2_optimal, 2),
        "tp2_ambitious":    round(tp2_ambitious, 2),
        "tp_current":    round(current_tp, 2) if current_tp else None,
        "missed_tp1_atr":    round(missed_tp1, 2),
        "missed_tp2_atr":    round(missed_tp2, 2),
        # Ratio MFE/MAE
        "mfe_mae_ratio_avg":    round(avg_ratio, 2),
        "mfe_mae_ratio_median": round(ratio_p50, 2),
        # Counts
        "n_winners": len(w),
        "n_losers":  len(l),
        "winrate":   round(len(w) / len(trades_df) * 100, 1),
        # Por calidad
        "quality_analysis": quality_analysis,
    }

    if verbose:
        print_calibration_report(result)

    return result


def print_calibration_report(r):
    """Imprime el reporte de calibración de SL/TP."""
    if r is None:
        return

    print(f"\n{'='*65}")
    print(f"  CALIBRACIÓN SL/TP — MAE/MFE Analysis")
    print(f"  {r['n_winners']} winners | {r['n_losers']} losers | WR {r['winrate']:.1f}%")
    print(f"{'='*65}")

    sl_cur = f"{r['sl_current']:.2f}" if r['sl_current'] else "desconocido"
    tp_cur = f"{r['tp_current']:.2f}" if r['tp_current'] else "desconocido"

    print(f"\n  ── STOP LOSS (en ATR) ────────────────────────────────────")
    print(f"  SL actual:          {sl_cur} ATR")
    print(f"  SL ajustado        (p75 winners): {r['sl_tight']:.2f} ATR  ← 25% falsos stops")
    print(f"  SL óptimo   ★      (p85 winners): {r['sl_optimal']:.2f} ATR  ← 15% falsos stops")
    print(f"  SL amplio          (p95 winners): {r['sl_wide']:.2f} ATR  ←  5% falsos stops")
    print(f"\n  Con SL óptimo: {r['pct_losers_past_sl']:.0f}% de los losers ya pasaron ese nivel")

    if r['sl_current']:
        diff = r['sl_optimal'] - r['sl_current']
        if abs(diff) > 0.1:
            dir_str = "AMPLIAR" if diff > 0 else "APRETAR"
            print(f"  → RECOMENDACIÓN: {dir_str} el SL de {r['sl_current']:.2f} a {r['sl_optimal']:.2f} ATR ({diff:+.2f})")
        else:
            print(f"  → SL actual está bien calibrado ✓")

    print(f"\n  ── TAKE PROFIT (en ATR) ──────────────────────────────────")
    print(f"  TP actual:          {tp_cur} ATR (TP2 = {float(tp_cur)*1.5:.2f} ATR)" if r['tp_current'] else f"  TP actual: desconocido")
    print(f"  TP1 conservador    (MFE p25): {r['tp1_conservative']:.2f} ATR  ← 75% winners lo alcanzan")
    print(f"  TP1 óptimo  ★      (MFE p35): {r['tp1_optimal']:.2f} ATR  ← 65% winners lo alcanzan")
    print(f"  TP1 ambicioso      (MFE p50): {r['tp1_ambitious']:.2f} ATR  ← 50% winners lo alcanzan")
    print(f"  TP2 óptimo  ★      (MFE p65): {r['tp2_optimal']:.2f} ATR  ← 35% winners lo alcanzan desde TP1")

    if r['missed_tp1_atr'] > 0.1:
        print(f"\n  → TP1 actual deja {r['missed_tp1_atr']:.2f} ATR sin capturar por trade ganador")
    if r['missed_tp2_atr'] > 0.1:
        print(f"  → TP2 actual deja {r['missed_tp2_atr']:.2f} ATR sin capturar por trade ganador")

    print(f"\n  ── RATIO MFE/MAE (calidad de los winners) ───────────────")
    ratio = r['mfe_mae_ratio_median']
    print(f"  MFE/MAE mediano: {ratio:.2f}x")
    if ratio >= 3.0:
        print(f"  → Excelente: los winners ganan {ratio:.1f}x más de lo que arriesgan ✓")
    elif ratio >= 2.0:
        print(f"  → Bueno: los winners ganan {ratio:.1f}x más de lo que arriesgan")
    else:
        print(f"  → Bajo: el TP puede estar demasiado cerca del SL")

    if r.get("quality_analysis"):
        print(f"\n  ── Por calidad de señal ─────────────────────────────────")
        print(f"  {'Calidad':<14} {'N':>4} {'WR%':>6} {'MAE_p85':>9} {'MFE_p50':>9} {'Ratio':>7}")
        print(f"  {'-'*52}")
        for q, qa in sorted(r["quality_analysis"].items(), key=lambda x: -x[1]["wr"]):
            print(f"  {q:<14} {qa['n']:>4} {qa['wr']:>5.1f}% "
                  f"{qa['mae_p85']:>9.2f} {qa['mfe_p50']:>9.2f} "
                  f"{qa.get('avg_ratio', 0):>7.2f}x")

    print(f"\n  CONFIGURACIÓN SUGERIDA para settings.json:")
    print(f'  "sl_atr_mult": {r["sl_optimal"]:.1f},   // p85 MAE de winners')
    print(f'  "tp_atr_mult": {r["tp1_optimal"]:.1f},   // p35 MFE de winners (TP1)')
    print(f"{'='*65}")


# ─── VALIDACIÓN CROSS-YEAR ────────────────────────────────────────────────────

def cross_year_validation(df, cfg, tf="1h", min_trades=5):
    """
    Ejecuta el backtest año por año para ver consistencia temporal.

    Un edge real debería funcionar en al menos 3 de 4 años.
    Si solo funciona en 2021-2022 (bull run), no es robusto.

    Retorna dict: {año: métricas}
    """
    from core.signals import get_signals
    from core.backtest import run_backtest, calc_metrics

    years   = df.index.year.unique()
    results = {}

    print(f"\n{'='*65}")
    print(f"  VALIDACIÓN CROSS-YEAR [{tf.upper()}]")
    print(f"  Testando {len(years)} años por separado")
    print(f"{'='*65}")

    for year in sorted(years):
        df_y = df[df.index.year == year].copy()
        if len(df_y) < 100:   # menos de 100 barras = año incompleto
            continue
        try:
            sig, qual = get_signals(df_y, cfg)
            n_sigs = (sig != 0).sum()
            if n_sigs < min_trades:
                results[year] = {"trades": 0, "cagr": 0, "winrate": 0,
                                 "max_dd": 0, "note": "sin señales"}
                continue
            trades, equity = run_backtest(df_y, sig, qual, cfg)
            days = (df_y.index[-1] - df_y.index[0]).days
            m    = calc_metrics(trades, equity, days_period=days)
            m["year"] = year
            results[year] = m
        except Exception as e:
            results[year] = {"trades": 0, "cagr": 0, "winrate": 0,
                             "max_dd": 0, "note": str(e)[:40]}

    print_cross_year_report(results)
    return results


def print_cross_year_report(results):
    """Imprime el reporte de consistencia año por año."""
    if not results:
        return

    print(f"\n  {'Año':>4} {'Trades':>7} {'WR%':>7} {'CAGR%':>8} {'DD%':>7} {'PF':>6} {'Veredicto'}")
    print(f"  {'-'*58}")

    n_positive = 0
    for year in sorted(results.keys()):
        m = results[year]
        if m.get("trades", 0) == 0:
            print(f"  {year:>4} {'—':>7} {'—':>7} {'—':>8} {'—':>7} {'—':>6}  {m.get('note','sin datos')}")
            continue
        cagr = m.get("cagr", 0)
        wr   = m.get("winrate", 0)
        dd   = m.get("max_dd", 0)
        pf   = m.get("profit_factor", 0)
        tr   = m.get("trades", 0)
        verdict = "✓ POSITIVO" if cagr > 0 else "✗ NEGATIVO"
        if cagr > 0:
            n_positive += 1
        print(f"  {year:>4} {tr:>7} {wr:>6.1f}% {cagr:>+7.1f}% {dd:>6.1f}% "
              f"{pf:>6.2f}  {verdict}")

    n_years = sum(1 for m in results.values() if m.get("trades", 0) > 0)
    pct_pos = n_positive / max(n_years, 1) * 100

    print(f"\n  Años positivos: {n_positive}/{n_years} ({pct_pos:.0f}%)")
    if pct_pos >= 75:
        print("  VEREDICTO: CONSISTENTE ✓ — Edge robusto en múltiples años")
    elif pct_pos >= 50:
        print("  VEREDICTO: INCONSISTENTE — Funciona en años alcistas, falla en bajistas")
    else:
        print("  VEREDICTO: NO ROBUSTO ✗ — Edge es específico de un periodo")

    # Detectar año del fallo más severo
    worst = min(results.items(),
                key=lambda x: x[1].get("cagr", 0) if x[1].get("trades", 0) > 0 else 0,
                default=(None, {}))
    if worst[0] and worst[1].get("cagr", 0) < -5:
        print(f"\n  Año más duro: {worst[0]} (CAGR {worst[1].get('cagr',0):+.1f}%) — "
              f"revisar régimen de mercado de ese año")


# ─── QUICK REPORT (uso desde main.py) ─────────────────────────────────────────

def run_full_calibration(df, cfg, tf="1h"):
    """
    Pipeline completo de calibración. Llama desde main.py después de un backtest.
    Retorna (calib_result, yearly_results).
    """
    from core.signals import get_signals
    from core.backtest import run_backtest, calc_metrics

    print(f"\n[CALIBRATION] Corriendo backtest completo para calibración...")
    try:
        sig, qual    = get_signals(df, cfg)
        trades, eq   = run_backtest(df, sig, qual, cfg)
        days         = (df.index[-1] - df.index[0]).days
        metrics      = calc_metrics(trades, eq, days_period=days)
        print(f"  {metrics['trades']} trades | WR {metrics['winrate']:.1f}% | "
              f"CAGR {metrics.get('cagr',0):+.1f}%")
    except Exception as e:
        print(f"  [ERROR] Backtest fallido: {e}")
        return None, None

    calib  = calibrate_sl_tp(trades, verbose=True)
    yearly = cross_year_validation(df, cfg, tf=tf)

    return calib, yearly

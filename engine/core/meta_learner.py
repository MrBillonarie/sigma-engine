"""
SIGMA ENGINE — Meta-Learner
Aprende de los errores historicos para guiar futuras optimizaciones.

Analiza el historial de experimentos y detecta:
1. Que parametros SIEMPRE llevan a overfitting IS->OOS
2. Que rangos de parametros funcionan mejor por regimen de mercado
3. Score de confianza basado en consistencia historica IS/OOS
4. Blacklist de configuraciones probadas y fallidas

Uso:
  from core.meta_learner import get_overfitting_penalty, get_safe_ranges
  penalty = get_overfitting_penalty(params)  # 0.0 = ok, 1.0 = probablemente overfit
"""
import sqlite3
import json
import numpy as np
import pandas as pd
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "models" / "sigma.db"


def get_all_runs(tf=None, min_trades=10):
    """Lee todos los experimentos del historial."""
    try:
        conn = sqlite3.connect(DB_PATH)
        q = "SELECT tf, params, score, trades, winrate, cagr, max_dd FROM runs WHERE trades >= ?"
        args = [min_trades]
        if tf:
            q += " AND tf = ?"
            args.append(tf)
        df = pd.read_sql(q, conn, params=args)
        conn.close()
        df["params"] = df["params"].apply(json.loads)
        return df
    except Exception:
        return pd.DataFrame()


def analyze_param_importance(tf="1h", top_n=10):
    """
    Analiza que parametros tienen mayor correlacion con el score.
    Ayuda a enfocar la busqueda en lo que realmente importa.
    """
    df = get_all_runs(tf=tf)
    if df.empty:
        return {}

    # Extraer parametros numericos
    param_cols = {}
    for _, row in df.iterrows():
        for k, v in row["params"].items():
            if isinstance(v, (int, float)):
                param_cols.setdefault(k, []).append((v, row["score"]))

    correlations = {}
    for param, pairs in param_cols.items():
        if len(pairs) < 20:
            continue
        vals, scores = zip(*pairs)
        vals   = np.array(vals)
        scores = np.array(scores)
        valid  = ~(np.isnan(vals) | np.isnan(scores) | np.isinf(scores))
        if valid.sum() < 20:
            continue
        corr = np.corrcoef(vals[valid], scores[valid])[0, 1]
        correlations[param] = round(float(corr), 4)

    return dict(sorted(correlations.items(), key=lambda x: abs(x[1]), reverse=True)[:top_n])


def detect_overfit_patterns(tf="1h"):
    """
    Detecta que configuraciones producen overfitting.
    Criterio: IS score muy alto pero historicamente conocido que falla.
    """
    df = get_all_runs(tf=tf)
    if df.empty:
        return {}

    patterns = {}

    # Patron 1: adx_min muy bajo → overfit
    low_adx = df[df["params"].apply(lambda p: p.get("adx_min", 20) < 14)]
    if not low_adx.empty:
        avg_score = low_adx["score"].mean()
        patterns["low_adx_min"] = {
            "condition": "adx_min < 14",
            "n_samples": len(low_adx),
            "avg_score": round(float(avg_score), 4),
            "risk": "HIGH" if avg_score < 0 else "MEDIUM"
        }

    # Patron 2: risk_pct muy alto → DD extremo
    high_risk = df[df["params"].apply(lambda p: p.get("risk_pct", 1.0) > 1.8)]
    if not high_risk.empty:
        avg_dd = high_risk["max_dd"].mean()
        patterns["high_risk_pct"] = {
            "condition": "risk_pct > 1.8",
            "n_samples": len(high_risk),
            "avg_dd": round(float(avg_dd), 2),
            "risk": "HIGH"
        }

    # Patron 3: cooldown muy corto → sobretrading
    low_cd = df[df["params"].apply(lambda p: p.get("signal_cooldown", 10) < 5)]
    if not low_cd.empty:
        avg_score = low_cd["score"].mean()
        patterns["low_cooldown"] = {
            "condition": "signal_cooldown < 5",
            "n_samples": len(low_cd),
            "avg_score": round(float(avg_score), 4),
            "risk": "HIGH" if avg_score < -0.1 else "MEDIUM"
        }

    return patterns


def get_best_param_ranges(tf="1h", percentile=80):
    """
    Retorna los rangos de parametros de los mejores experimentos.
    Usado para enfocar futuras busquedas en zonas prometedoras.
    """
    df = get_all_runs(tf=tf)
    if df.empty:
        return {}

    threshold = df["score"].quantile(percentile/100)
    top = df[df["score"] >= threshold]

    ranges = {}
    numeric_params = ["adx_min","adx_t","adx_r","signal_cooldown","temp_min","temp_max",
                      "risk_pct","qty_tp1","hurst_t","hurst_r","elite_sl_mult","elite_tp_mult",
                      "exec_sl_mult","exec_tp_mult"]

    for param in numeric_params:
        vals = top["params"].apply(lambda p: p.get(param))
        vals = vals.dropna()
        if len(vals) < 5:
            continue
        ranges[param] = {
            "min":  round(float(vals.min()), 3),
            "max":  round(float(vals.max()), 3),
            "mean": round(float(vals.mean()), 3),
            "p25":  round(float(vals.quantile(0.25)), 3),
            "p75":  round(float(vals.quantile(0.75)), 3),
        }

    return ranges


def get_overfit_score(params, tf="1h"):
    """
    Estima probabilidad de overfitting para un set de params dado.
    Retorna 0.0 (seguro) a 1.0 (probable overfit).
    """
    risk = 0.0
    p = params

    # Factores de riesgo conocidos
    if p.get("adx_min", 20) < 14:          risk += 0.3
    if p.get("signal_cooldown", 10) < 4:   risk += 0.3
    if p.get("risk_pct", 1.0) > 2.0:       risk += 0.2
    if p.get("hurst_t", 0.55) > 0.63:      risk += 0.15
    if p.get("adx_t", 25) > 38:            risk += 0.1

    # Combinaciones peligrosas
    if (p.get("elite_sl_mult", 2.0) < 1.0 and
            p.get("elite_tp_mult", 2.0) > 4.0):
        risk += 0.2  # SL muy tight + TP muy amplio = suena bien pero sobreoptimizado

    return min(risk, 1.0)


def print_learning_report(tf="1h"):
    """Imprime un reporte de todo lo aprendido para un TF."""
    print(f"\n{'='*60}")
    print(f"  META-LEARNING REPORT — {tf.upper()}")
    print(f"{'='*60}")

    df = get_all_runs(tf=tf)
    if df.empty:
        print("  Sin datos suficientes")
        return

    print(f"\n  Total experimentos: {len(df):,}")
    print(f"  Score promedio: {df['score'].mean():.4f}")
    print(f"  Score maximo IS: {df['score'].max():.4f}")

    print(f"\n  Top parametros mas correlacionados con el score:")
    imp = analyze_param_importance(tf=tf)
    for param, corr in list(imp.items())[:8]:
        bar = '#' * int(abs(corr) * 20)
        sign = '+' if corr > 0 else '-'
        print(f"    {param:<25} {sign}{abs(corr):.3f}  {bar}")

    print(f"\n  Rangos optimos (top {80}% configs):")
    ranges = get_best_param_ranges(tf=tf, percentile=80)
    key_params = ["signal_cooldown","adx_min","risk_pct","elite_tp_mult","exec_tp_mult"]
    for param in key_params:
        if param in ranges:
            r = ranges[param]
            print(f"    {param:<25} [{r['p25']:.2f} — {r['p75']:.2f}] (media: {r['mean']:.2f})")

    print(f"\n  Patrones de overfitting detectados:")
    patterns = detect_overfit_patterns(tf=tf)
    if patterns:
        for name, info in patterns.items():
            print(f"    {name}: {info['condition']} "
                  f"({info['n_samples']} casos, riesgo: {info['risk']})")
    else:
        print("    Ningun patron de overfitting significativo detectado")

    print(f"{'='*60}\n")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--tf", default="1h", choices=["1h","4h","15m","5m","1m"])
    p.add_argument("--all", action="store_true")
    a = p.parse_args()

    if a.all:
        for tf in ["1h","4h","15m","5m"]:
            print_learning_report(tf)
    else:
        print_learning_report(a.tf)

"""
SIGMA ENGINE - Ensemble Model
Combina multiples modelos 1H y solo opera cuando la mayoria coincide.

Ventaja del ensemble:
  Un modelo solo: WR ~54%, puede tener rachas malas
  Ensemble 3 modelos: solo opera cuando 2/3 coinciden
  Resultado esperado: WR +5-8% mas alto, menos trades pero mas calidad

Los 3 modelos usados:
  1. Bayesian General (el mejor IS/OOS)
  2. Top 3 configs del historial por score
  3. Adaptive (params del mes actual)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent.parent


def load_top_configs(tf="1h", n=3):
    """Carga los N mejores configs del historial en sigma.db."""
    from core.database import get_top_runs
    runs = get_top_runs(tf, n=n*3)
    if not runs:
        return []
    seen = set()
    configs = []
    for r in runs:
        p = r.get("params", {})
        key = str(sorted(p.items()))
        if key not in seen:
            seen.add(key)
            configs.append(p)
        if len(configs) >= n:
            break
    return configs


def get_ensemble_signals(df, tf="1h", min_agreement=2):
    """
    Genera señales de ensemble: solo opera cuando min_agreement modelos coinciden.

    Returns:
        signals: Series (1=long, -1=short, 0=flat)
        quality: Series (calidad de la señal)
        agreement: Series (cuantos modelos coinciden, 0-3)
    """
    from core.signals import get_signals

    # Cargar los 3 modelos
    models = []

    # Modelo 1: best_validated
    bv_path = OUTPUT_DIR / "models" / tf / "best_validated.json"
    if bv_path.exists():
        with open(bv_path) as f:
            bv = json.load(f)
        models.append(("best_validated", bv["params"]))

    # Modelo 2: config.json (3-year Bayesian)
    cfg_path = OUTPUT_DIR / "models" / tf / "config.json"
    if cfg_path.exists():
        with open(cfg_path) as f:
            cfg = json.load(f)
        p = cfg.get("params", {})
        # No duplicar si son los mismos params
        if not models or models[0][1] != p:
            models.append(("config_3yr", p))

    # Modelo 3: adaptive (current_params)
    ap_path = OUTPUT_DIR / "models" / tf / "current_params.json"
    if ap_path.exists():
        with open(ap_path) as f:
            p = json.load(f)
        models.append(("adaptive", p))

    # Si no hay suficientes modelos, completar con top configs del DB
    if len(models) < 3:
        top_cfgs = load_top_configs(tf, n=3-len(models))
        for i, p in enumerate(top_cfgs):
            models.append((f"db_top_{i+1}", p))

    print(f"  Ensemble: {len(models)} modelos cargados")
    for name, _ in models:
        print(f"    - {name}")

    if not models:
        from core.signals import get_signals
        sig, qual = get_signals(df, {})
        return sig, qual, pd.Series(1, index=df.index)

    # Generar señales de cada modelo
    all_signals = []
    for name, params in models:
        try:
            sig, _ = get_signals(df, params)
            all_signals.append(sig)
        except Exception as e:
            print(f"    [{name}] Error: {e}")

    if not all_signals:
        return pd.Series(0, index=df.index), pd.Series("NONE", index=df.index), pd.Series(0, index=df.index)

    # Calcular acuerdo
    sig_matrix = pd.DataFrame({i: s for i, s in enumerate(all_signals)})

    long_votes  = (sig_matrix == 1).sum(axis=1)
    short_votes = (sig_matrix == -1).sum(axis=1)

    final = pd.Series(0, index=df.index)
    agreement = pd.Series(0, index=df.index)

    final[long_votes  >= min_agreement] = 1
    final[short_votes >= min_agreement] = -1
    agreement[long_votes  >= min_agreement] = long_votes[long_votes   >= min_agreement]
    agreement[short_votes >= min_agreement] = short_votes[short_votes >= min_agreement]

    # Calidad basada en nivel de acuerdo
    quality = pd.Series("NONE", index=df.index)
    quality[agreement == len(models)] = "ELITE_ICT"   # todos de acuerdo
    quality[agreement == min_agreement] = "EXECUTE"    # acuerdo minimo

    print(f"  Señales ensemble: {(final!=0).sum()} ({(final==1).sum()}L / {(final==-1).sum()}S)")
    print(f"  Acuerdo total ({len(models)}/{len(models)}): {(agreement==len(models)).sum()} señales")
    print(f"  Acuerdo minimo ({min_agreement}/{len(models)}): {(final!=0).sum()} señales")

    return final, quality, agreement


def backtest_ensemble(df, tf="1h", risk_pct=1.5, min_agreement=2):
    """Backtest completo del ensemble."""
    from core.backtest import run_backtest, calc_metrics
    import json

    bv_path = OUTPUT_DIR / "models" / tf / "best_validated.json"
    with open(bv_path) as f:
        params = json.load(f)["params"]

    signals, quality, agreement = get_ensemble_signals(df, tf, min_agreement)
    params_bt = dict(params)
    params_bt["risk_pct"] = risk_pct

    trades, equity = run_backtest(df, signals, quality, params_bt)
    days = (df.index[-1] - df.index[0]).days
    m = calc_metrics(trades, equity, days_period=days)
    return m, signals, quality, agreement


def validate_ensemble(tf="1h", min_agreement=2):
    """IS/OOS validation del ensemble con datos historicos."""
    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.backtest import run_backtest, calc_metrics

    print(f"\n{'='*60}")
    print(f"  ENSEMBLE VALIDATION -- {tf.upper()}")
    print(f"  Requiere acuerdo de {min_agreement}/3 modelos")
    print(f"{'='*60}")

    df_b  = fetch_ohlcv(tf=tf, days=3200)
    df_4h = fetch_ohlcv(tf="4h", days=3200)
    df_1d = fetch_ohlcv(tf="1d", days=3200)
    df = build_features(df_b, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close","atr","ema50"], inplace=True)

    n = len(df)
    split    = int(n * 0.80)
    df_is    = df.iloc[:split]
    df_oos   = df.iloc[split:]
    days_is  = (df_is.index[-1]-df_is.index[0]).days
    days_oos = (df_oos.index[-1]-df_oos.index[0]).days

    print(f"\n  IS:  {df_is.index[0].strftime('%Y-%m-%d')} -> {df_is.index[-1].strftime('%Y-%m-%d')} ({days_is}d)")
    print(f"  OOS: {df_oos.index[0].strftime('%Y-%m-%d')} -> {df_oos.index[-1].strftime('%Y-%m-%d')} ({days_oos}d)")

    bv_path = OUTPUT_DIR / "models" / tf / "best_validated.json"
    with open(bv_path) as f:
        base_params = json.load(f)["params"]

    results = {}
    for split_name, df_s, days in [("IS", df_is, days_is), ("OOS", df_oos, days_oos)]:
        print(f"\n  [{split_name}]")
        sig, qual, agree = get_ensemble_signals(df_s, tf, min_agreement)
        tr, eq = run_backtest(df_s, sig, qual, base_params)
        m = calc_metrics(tr, eq, days_period=days)
        cagr = m.get("cagr", m.get("pnl_pct", 0))
        print(f"  {m['trades']}T | WR {m['winrate']:.1f}% | CAGR {cagr:+.1f}% | DD {m['max_dd']:.1f}% | PF {m['profit_factor']:.2f}")
        results[split_name] = m

    # Comparar con modelo individual
    print(f"\n  Comparacion: Ensemble vs Modelo Individual")
    sig_solo, qual_solo = __import__('core.signals', fromlist=['get_signals']).get_signals(df_oos, base_params)
    tr_solo, eq_solo = run_backtest(df_oos, sig_solo, qual_solo, base_params)
    m_solo = calc_metrics(tr_solo, eq_solo, days_period=days_oos)
    cagr_solo = m_solo.get("cagr", m_solo.get("pnl_pct", 0))
    cagr_ens  = results.get("OOS", {}).get("cagr", results.get("OOS", {}).get("pnl_pct", 0))

    print(f"  Individual OOS: {m_solo['trades']}T | WR {m_solo['winrate']:.1f}% | CAGR {cagr_solo:+.1f}%")
    print(f"  Ensemble OOS:  {results['OOS']['trades']}T | WR {results['OOS']['winrate']:.1f}% | CAGR {cagr_ens:+.1f}%")
    print(f"  Mejora WR: {results['OOS']['winrate'] - m_solo['winrate']:+.1f}%")
    print(f"{'='*60}")

    return results


if __name__ == "__main__":
    validate_ensemble("1h", min_agreement=2)

"""
SIGMA ENGINE — Bayesian Macro-Aware
Optimiza señales ICT + filtro F&G + sizing dinámico JUNTOS.

Descubrimiento: F&G<60 convierte OOS -3% en +1.6% sobre 8.7 anos.
Este script hace lo mismo pero DENTRO del Bayesian — el optimizador
encuentra el umbral F&G óptimo, el sizing por régimen, y los params ICT
de forma conjunta.

Arquitectura del modelo resultante:
  - Señales ICT normales (ELITE_ICT, ELITE, EXECUTE)
  - Filtro F&G: no operar cuando mercado esta codicioso
  - Sizing dinámico: más size en miedo extremo, menos en codicia
  - Confluence 4H: confirmar con bias semanal

Objetivo: OOS >5% CAGR consistente sobre todos los ciclos BTC.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

optuna.logging.set_verbosity(optuna.logging.WARNING)

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION  = 0.0004; SLIPPAGE = 0.0001; COST = COMMISSION + SLIPPAGE
CAPITAL     = 1000.0


def load_data():
    from core.data import fetch_ohlcv
    from core.features import build_features
    print("[DATA] Cargando 8.7 anos con features enriquecidas...")
    df_1h = fetch_ohlcv(tf="1h",  days=3200)
    df_4h = fetch_ohlcv(tf="4h",  days=3200)
    df_1d = fetch_ohlcv(tf="1d",  days=3200)
    df = build_features(df_1h, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close","atr","ema50"], inplace=True)
    days = (df.index[-1]-df.index[0]).days
    fg_avail = df["fg_value"].notna().mean()*100 if "fg_value" in df.columns else 0
    fr_avail = df["fr_z"].notna().mean()*100 if "fr_z" in df.columns else 0
    print(f"  {len(df):,} velas | {days}d ({days/365:.1f}y)")
    print(f"  Fear & Greed: {fg_avail:.0f}% disponible")
    print(f"  Funding Rate: {fr_avail:.0f}% disponible")
    return df


def backtest_macro(df, signals, quality, cfg):
    """
    Backtest con sizing dinámico basado en régimen F&G.
    Más size en miedo, menos en codicia.
    """
    c   = df["close"].to_numpy()
    h   = df["high"].to_numpy()
    lo  = df["low"].to_numpy()
    a   = df["atr"].to_numpy()
    sig = signals.to_numpy()

    # F&G para sizing dinámico
    fg  = df.get("fg_value", pd.Series(50, index=df.index)).fillna(50).to_numpy()

    # Params
    base_risk = cfg.get("risk_pct", 1.0)
    size_fear = cfg.get("size_fear_mult", 2.0)   # multiplicador en miedo extremo
    size_greed= cfg.get("size_greed_mult", 0.5)  # multiplicador en codicia
    fear_thr  = cfg.get("fear_threshold",  30)   # F&G < X = miedo
    greed_thr = cfg.get("greed_threshold", 70)   # F&G > X = codicia
    e_sl      = cfg.get("elite_sl_mult",   2.4)
    e_tp      = cfg.get("elite_tp_mult",   2.0)
    x_sl      = cfg.get("exec_sl_mult",    1.9)
    x_tp      = cfg.get("exec_tp_mult",    3.5)
    q65       = cfg.get("qty_tp1",         0.65)

    # Quality mapping
    qual_arr = quality.map({"ELITE_ICT":3,"ELITE":2,"EXECUTE":1}).fillna(0).astype(int).to_numpy()

    cap = CAPITAL; eq = [cap]
    pos = 0; entry = sl = tp1 = tp2 = sz = sz2 = 0.0
    tp1_done = False; trades = []

    for i in range(1, len(c)):
        pr = c[i]; atr = a[i-1]; hi_ = h[i]; low_ = lo[i]
        s = sig[i-1]; q = qual_arr[i-1]
        fg_i = fg[i-1]

        if pos != 0:
            closed = False; pnl = 0.0
            if pos == 1:
                if low_ <= sl:
                    pnl = (sz+sz2)*(sl-entry) - (sz+sz2)*(entry+sl)*COST; closed = True
                elif hi_ >= tp1 and not tp1_done:
                    p1 = sz*(tp1-entry) - sz*(entry+tp1)*COST; cap += p1
                    trades.append({"pnl": p1, "won": p1>0}); sz = 0; tp1_done = True
                elif hi_ >= tp2:
                    pnl = sz2*(tp2-entry) - sz2*(entry+tp2)*COST; closed = True
            else:
                if hi_ >= sl:
                    pnl = (sz+sz2)*(entry-sl) - (sz+sz2)*(entry+sl)*COST; closed = True
                elif low_ <= tp1 and not tp1_done:
                    p1 = sz*(entry-tp1) - sz*(entry+tp1)*COST; cap += p1
                    trades.append({"pnl": p1, "won": p1>0}); sz = 0; tp1_done = True
                elif low_ <= tp2:
                    pnl = sz2*(entry-tp2) - sz2*(entry+tp2)*COST; closed = True
            if not closed and s == -pos:
                rem = sz+sz2
                pnl = pos*rem*(pr-entry) - rem*(entry+pr)*COST; closed = True
            if closed:
                cap += pnl; trades.append({"pnl":pnl,"won":pnl>0}); pos=0; tp1_done=False

        if pos == 0 and s != 0 and cap > 50:
            is_el = q >= 2
            sl_m = e_sl if is_el else x_sl
            tp_m = e_tp if is_el else x_tp

            # Sizing dinámico por régimen F&G
            risk = base_risk
            if fg_i < fear_thr:   risk *= size_fear
            elif fg_i > greed_thr: risk *= size_greed
            risk = min(risk, 5.0)  # cap en 5%

            rsl = atr * sl_m
            if rsl <= 0: continue
            tsz = (cap * risk/100) / rsl
            sz  = tsz * q65; sz2 = tsz * (1-q65)
            pos = s; entry = pr
            sl  = entry - rsl if pos == 1 else entry + rsl
            tp1 = entry + atr*tp_m if pos==1 else entry - atr*tp_m
            tp2 = entry + atr*tp_m*1.5 if pos==1 else entry - atr*tp_m*1.5
            tp1_done = False

        eq.append(cap)

    df_t = pd.DataFrame(trades)
    eq_s = pd.Series(eq[:len(df)], index=df.index[:len(eq)])
    return df_t, eq_s


def score_macro(df_t, eq_s, days):
    if df_t.empty or len(df_t) < 10: return -9999
    w  = df_t[df_t["pnl"]>0]; l = df_t[df_t["pnl"]<=0]
    gp = w["pnl"].sum(); gl = abs(l["pnl"].sum())
    pf = gp/gl if gl > 0 else 999
    wr = len(w)/len(df_t)
    peak = eq_s.cummax(); dd = (eq_s-peak)/peak*100
    cagr = ((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1))-1)*100
    if cagr <= 0: return cagr * 0.1
    calmar = cagr/abs(dd.min()) if dd.min() < 0 else 0
    rr = w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
    return (0.30*min(calmar,6)/6 + 0.25*(wr-0.42)/0.38 +
            0.20*min(pf,4)/4 + 0.25*min(cagr,80)/80)


def run_macro_bayesian(n_trials=700):
    print(f"\n{'='*65}")
    print("  SIGMA MACRO-AWARE BAYESIAN -- 1H")
    print(f"  {n_trials} trials | F&G threshold + ICT signals + dynamic sizing")
    print(f"  Objetivo: OOS >5% CAGR en 8.7 anos completos")
    print(f"{'='*65}")

    df = load_data()

    n = len(df); split = int(n*0.80)
    df_is  = df.iloc[:split]; df_oos = df.iloc[split:]
    days_is  = (df_is.index[-1]-df_is.index[0]).days
    days_oos = (df_oos.index[-1]-df_oos.index[0]).days

    print(f"\n  IS:  {df_is.index[0].strftime('%Y-%m-%d')} -> {df_is.index[-1].strftime('%Y-%m-%d')} ({days_is}d = {days_is/365:.1f}y)")
    print(f"  OOS: {df_oos.index[0].strftime('%Y-%m-%d')} -> {df_oos.index[-1].strftime('%Y-%m-%d')} ({days_oos}d = {days_oos/365:.1f}y)\n")

    from core.signals import get_signals

    def objective(trial):
        # Params ICT (señales)
        p = {
            "use_execute":     trial.suggest_categorical("use_execute",   [True, True, False]),
            "use_trend":       trial.suggest_categorical("use_trend",     [True, True, False]),
            "use_range":       trial.suggest_categorical("use_range",     [True, False]),
            "use_watch":       trial.suggest_categorical("use_watch",     [False, True]),
            "use_sess_b":      trial.suggest_categorical("use_sess_b",    [True, True, False]),
            "use_asia":        trial.suggest_categorical("use_asia",      [True, False]),
            "allow_friday":    trial.suggest_categorical("allow_friday",  [True, False]),
            "req_htf2":        trial.suggest_categorical("req_htf2",      [True, True, False]),
            "use_be":          trial.suggest_categorical("use_be",        [True, False]),
            "adx_min":         trial.suggest_int("adx_min",         12, 28),
            "hurst_t":         trial.suggest_float("hurst_t",       0.50, 0.62, step=0.01),
            "adx_t":           trial.suggest_int("adx_t",           18, 35),
            "hurst_r":         trial.suggest_float("hurst_r",       0.44, 0.52, step=0.01),
            "adx_r":           trial.suggest_int("adx_r",           14, 24),
            "temp_min":        trial.suggest_int("temp_min",        5, 22),
            "temp_max":        trial.suggest_int("temp_max",        72, 98),
            "ofi_threshold":   trial.suggest_float("ofi_threshold", 0.35, 0.75, step=0.05),
            "elite_sl_mult":   trial.suggest_float("elite_sl_mult", 1.0, 2.5, step=0.1),
            "elite_tp_mult":   trial.suggest_float("elite_tp_mult", 1.5, 5.0, step=0.25),
            "exec_sl_mult":    trial.suggest_float("exec_sl_mult",  1.2, 2.5, step=0.1),
            "exec_tp_mult":    trial.suggest_float("exec_tp_mult",  1.5, 4.5, step=0.25),
            "signal_cooldown": trial.suggest_int("signal_cooldown", 4, 22),
            "qty_tp1":         trial.suggest_float("qty_tp1",       0.35, 0.65, step=0.05),
        }

        # Params MACRO (nuevos)
        cfg = dict(p)
        cfg["risk_pct"]          = trial.suggest_float("risk_pct",       0.5, 2.0, step=0.1)
        cfg["fg_threshold"]      = trial.suggest_int("fg_threshold",     35, 70)  # max F&G para operar
        cfg["size_fear_mult"]    = trial.suggest_float("size_fear_mult", 1.0, 3.0, step=0.5)  # x en miedo extremo
        cfg["size_greed_mult"]   = trial.suggest_float("size_greed_mult",0.3, 1.0, step=0.1)  # x en codicia
        cfg["fear_threshold"]    = trial.suggest_int("fear_threshold",   15, 40)  # F&G < X = miedo
        cfg["greed_threshold"]   = trial.suggest_int("greed_threshold",  60, 85)  # F&G > X = codicia

        try:
            sigs, qual = get_signals(df_is, p)
            if (sigs!=0).sum() < 8: return -9999

            # Aplicar filtro F&G
            if "fg_value" in df_is.columns:
                fg_ok = df_is["fg_value"].fillna(50) <= cfg["fg_threshold"]
                sigs = sigs.copy(); sigs[~fg_ok] = 0

            if (sigs!=0).sum() < 8: return -9999
            dt, eq = backtest_macro(df_is, sigs, qual, cfg)
            return score_macro(dt, eq, days_is)
        except Exception:
            return -9999

    # Warm start desde best_validated
    bv_path = OUTPUT_DIR / "models" / "1h" / "best_validated.json"
    base_p = {}
    if bv_path.exists():
        with open(bv_path) as f: base_p = json.load(f)["params"]

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=100)
    )
    if base_p:
        try:
            warm = dict(base_p)
            warm["fg_threshold"] = 60
            warm["size_fear_mult"] = 2.0
            warm["size_greed_mult"] = 0.5
            warm["fear_threshold"] = 25
            warm["greed_threshold"] = 70
            study.enqueue_trial(warm)
        except Exception:
            pass

    best_score = [-9999]; best_cfg = [{}]

    def callback(study, trial):
        if trial.value and trial.value > best_score[0]:
            best_score[0] = trial.value
            best_cfg[0]   = trial.params.copy()
            if trial.value > 0.35:
                print(f"  [T{trial.number}] NUEVO MEJOR score={trial.value:.4f} "
                      f"| fg_threshold={trial.params.get('fg_threshold',60)}")

    study.optimize(objective, n_trials=n_trials, callbacks=[callback], show_progress_bar=False)

    print(f"\n  Mejor IS score: {study.best_value:.4f}")
    p_best = {k:v for k,v in study.best_params.items()
              if k not in ("fg_threshold","size_fear_mult","size_greed_mult",
                           "fear_threshold","greed_threshold","risk_pct")}
    macro_p = {k:study.best_params[k] for k in
               ("fg_threshold","size_fear_mult","size_greed_mult",
                "fear_threshold","greed_threshold") if k in study.best_params}
    print(f"  F&G threshold optimo: {macro_p.get('fg_threshold', 60)}")
    print(f"  Size en miedo (<{macro_p.get('fear_threshold',25)}): {macro_p.get('size_fear_mult',2.0)}x")
    print(f"  Size en codicia (>{macro_p.get('greed_threshold',70)}): {macro_p.get('size_greed_mult',0.5)}x")

    # OOS Validation
    print(f"\n{'='*65}")
    print("  OOS VALIDATION MACRO-AWARE")
    try:
        from core.signals import get_signals as gs
        sigs_oos, qual_oos = gs(df_oos, p_best)
        fg_thr = macro_p.get("fg_threshold", 60)
        if "fg_value" in df_oos.columns:
            fg_ok = df_oos["fg_value"].fillna(50) <= fg_thr
            sigs_oos = sigs_oos.copy(); sigs_oos[~fg_ok] = 0

        cfg_full = dict(p_best)
        cfg_full.update(macro_p)
        cfg_full["risk_pct"] = study.best_params.get("risk_pct", 1.0)

        dt_oos, eq_oos = backtest_macro(df_oos, sigs_oos, qual_oos, cfg_full)

        if not dt_oos.empty and len(dt_oos) >= 10:
            w = dt_oos[dt_oos["pnl"]>0]; l = dt_oos[dt_oos["pnl"]<=0]
            cagr = ((eq_oos.iloc[-1]/CAPITAL)**(365.25/max(days_oos,1))-1)*100
            wr   = len(w)/len(dt_oos)*100
            dd   = ((eq_oos-eq_oos.cummax())/eq_oos.cummax()*100).min()
            pf   = w["pnl"].sum()/abs(l["pnl"].sum()) if not l.empty else 999
            rr   = w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
            tpm  = len(dt_oos)/max(days_oos/30.44,0.1)
            calmar = cagr/abs(dd) if dd < 0 else 0
            print(f"  {len(dt_oos)}T ({tpm:.1f}/mes) | WR {wr:.1f}% | CAGR {cagr:+.1f}% | "
                  f"DD {dd:.1f}% | PF {pf:.2f} | Calmar {calmar:.2f}")

            # Guardar si supera F&G filtered model (+1.6%)
            if cagr > 1.6 and len(dt_oos) >= 15:
                import numpy as np_
                def ser(v):
                    if isinstance(v,(np_.integer,)): return int(v)
                    if isinstance(v,(np_.floating,)): return float(v)
                    if isinstance(v,(np_.bool_,)): return bool(v)
                    return v

                result = {
                    "tf": "1h",
                    "params": {k:ser(v) for k,v in p_best.items()},
                    "macro_params": {k:ser(v) for k,v in macro_p.items()},
                    "risk_pct": study.best_params.get("risk_pct",1.0),
                    "metrics_is":  {},
                    "metrics_oos": {"trades":len(dt_oos),"wr":wr,"cagr":cagr,
                                   "dd":dd,"pf":pf,"rr":rr,"calmar":calmar},
                    "score": study.best_value,
                    "source": "bayesian_macro_aware_8.7yr",
                    "note": f"Macro-Aware: F&G<{fg_thr} + dynamic sizing. OOS {cagr:+.1f}% CAGR."
                }
                out = OUTPUT_DIR/"models"/"1h"/"best_macro_aware.json"
                with open(out,"w") as f: json.dump(result, f, indent=2)
                print(f"\n  [SAVED] best_macro_aware.json: OOS {cagr:+.1f}%")
        else:
            print("  Sin trades OOS suficientes")

    except Exception as e:
        print(f"  Error OOS: {e}")

    print(f"{'='*65}")
    return study.best_params


if __name__ == "__main__":
    import argparse
    a = argparse.ArgumentParser()
    a.add_argument("--trials", type=int, default=700)
    args = a.parse_args()
    run_macro_bayesian(args.trials)

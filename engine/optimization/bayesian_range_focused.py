"""
SIGMA ENGINE — Bayesian focusado en regimen RANGE

Insight: el 1H tiene su edge en RANGE (70% del tiempo).
Este script optimiza parametros SOLO en barras de regimen RANGE.
El resultado: mejores SL/TP/filtros para mercados laterales.

Diferencia clave vs Bayesian normal:
  - Solo evalua performance durante RANGE
  - No importa si pierde en TREND — ese es otro modelo
  - Busca maximizar calmar/WR en el 70% del tiempo que importa
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
    max_p = OUTPUT_DIR / "models" / "data_1h_max.csv"
    df_b  = pd.read_csv(max_p, index_col=0, parse_dates=True); df_b.index.name = "timestamp"
    df_4h = fetch_ohlcv(tf="4h", days=1500)
    df_1d = fetch_ohlcv(tf="1d", days=1500)
    df    = build_features(df_b, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close","atr","ema50"], inplace=True)
    return df


def get_regime_mask(df, regime="RANGE"):
    """Mascara para el regimen especificado."""
    ema50  = df["close"].ewm(span=50,  adjust=False).mean()
    ema200 = df["close"].ewm(span=200, adjust=False).mean()
    adx    = df.get("adx", pd.Series(20, index=df.index))
    rn50   = df["close"].rolling(50).apply(lambda x: x.max()-x.min(), raw=True)
    rn25   = df["close"].rolling(25).apply(lambda x: x.max()-x.min(), raw=True)
    hurst  = np.where(rn25>0, np.log(rn50/rn25.clip(1e-6))/np.log(2), 0.5)
    hurst  = pd.Series(hurst, index=df.index)

    trending = (hurst>0.55) & (adx>20)
    bull_reg = trending & (ema50>ema200)
    bear_reg = trending & (ema50<ema200)

    if regime == "RANGE":
        return ~trending
    elif regime == "BULL":
        return bull_reg
    elif regime == "BEAR":
        return bear_reg
    return pd.Series(True, index=df.index)


def backtest_regime_only(df, signals, quality, cfg, mask):
    """Backtest aplicando solo durante el regimen dado."""
    closes=df["close"].to_numpy(); highs=df["high"].to_numpy()
    lows=df["low"].to_numpy();     atrs=df["atr"].to_numpy()
    sigs=signals.to_numpy()
    quals=quality.map({"ELITE_ICT":3,"ELITE":2,"EXECUTE":1}).fillna(0).astype(int).to_numpy()
    mask_arr=mask.to_numpy()

    e_sl=cfg.get("elite_sl_mult",2.4); e_tp=cfg.get("elite_tp_mult",2.0)
    x_sl=cfg.get("exec_sl_mult",1.9);  x_tp=cfg.get("exec_tp_mult",3.5)
    risk=cfg.get("risk_pct",1.5);      q65=cfg.get("qty_tp1",0.65)

    cap=CAPITAL; eq=[cap]; pos=0
    entry=sl=tp1=tp2=sz=sz2=0.0; tp1_done=False; trades=[]

    for i in range(1, len(closes)):
        pr=closes[i]; atr=atrs[i-1]; h_=highs[i]; lo=lows[i]
        s=sigs[i-1]; q=quals[i-1]
        if not mask_arr[i-1]: s=0

        if pos!=0:
            closed=False; pnl=0.0
            if pos==1:
                if lo<=sl: pnl=(sz+sz2)*(sl-entry)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif h_>=tp1 and not tp1_done:
                    p1=sz*(tp1-entry)-sz*(entry+tp1)*COST; cap+=p1
                    trades.append({"pnl":p1,"won":p1>0}); sz=0; tp1_done=True
                elif h_>=tp2: pnl=sz2*(tp2-entry)-sz2*(entry+tp2)*COST; closed=True
            else:
                if h_>=sl: pnl=(sz+sz2)*(entry-sl)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif lo<=tp1 and not tp1_done:
                    p1=sz*(entry-tp1)-sz*(entry+tp1)*COST; cap+=p1
                    trades.append({"pnl":p1,"won":p1>0}); sz=0; tp1_done=True
                elif lo<=tp2: pnl=sz2*(entry-tp2)-sz2*(entry+tp2)*COST; closed=True
            if not closed and s==-pos:
                rem=sz+sz2; pnl=pos*rem*(pr-entry)-rem*(entry+pr)*COST; closed=True
            if closed: cap+=pnl; trades.append({"pnl":pnl,"won":pnl>0}); pos=0; tp1_done=False

        if pos==0 and s!=0 and cap>50:
            is_el=q>=2; sl_m=e_sl if is_el else x_sl; tp_m=e_tp if is_el else x_tp
            pos=s; entry=pr; rsl=atr*sl_m
            sl=entry-rsl if pos==1 else entry+rsl
            tp1=entry+atr*tp_m if pos==1 else entry-atr*tp_m
            tp2=entry+atr*tp_m*1.5 if pos==1 else entry-atr*tp_m*1.5
            tsz=(cap*risk/100)/rsl if rsl>0 else 0; sz=tsz*q65; sz2=tsz*(1-q65); tp1_done=False
        eq.append(cap)

    df_t=pd.DataFrame(trades); eq_s=pd.Series(eq[:len(df)], index=df.index[:len(eq)])
    if df_t.empty or len(df_t)<5: return -9999
    w=df_t[df_t["pnl"]>0]; l=df_t[df_t["pnl"]<=0]
    gp=w["pnl"].sum(); gl=abs(l["pnl"].sum())
    peak=eq_s.cummax(); dd=(eq_s-peak)/peak*100
    days=(eq_s.index[-1]-eq_s.index[0]).days
    cagr=((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1))-1)*100
    wr=len(w)/len(df_t)
    if cagr<=0: return -abs(cagr)*0.1
    calmar=cagr/abs(dd.min()) if dd.min()<0 else 0
    return 0.35*(wr-0.45)/0.35 + 0.35*min(calmar,5)/5 + 0.30*min(cagr,60)/60


def run_bayesian_range(n_trials=500, regime="RANGE"):
    print(f"\n{'='*65}")
    print(f"  SIGMA BAYESIAN — FOCUSADO EN REGIMEN {regime}")
    print(f"  {n_trials} trials | Solo optimiza durante {regime}")
    print(f"{'='*65}")

    print("\n[DATA] Cargando datos...")
    df = load_data()
    from core.signals import get_signals

    mask_full = get_regime_mask(df, regime)
    pct = mask_full.mean()*100
    print(f"  {regime}: {pct:.1f}% del tiempo ({mask_full.sum():,} barras)")

    split = int(len(df)*0.80)
    df_is    = df.iloc[:split]; mask_is = mask_full.iloc[:split]
    df_oos   = df.iloc[split:]; mask_oos= mask_full.iloc[split:]

    print(f"  IS: {(df_is.index[-1]-df_is.index[0]).days}d | "
          f"OOS: {(df_oos.index[-1]-df_oos.index[0]).days}d\n")

    def objective(trial):
        cfg = {
            "use_execute":     trial.suggest_categorical("use_execute",   [True, True, False]),
            "use_trend":       trial.suggest_categorical("use_trend",     [True, True, False]),
            "use_range":       trial.suggest_categorical("use_range",     [True, False]),
            "use_watch":       trial.suggest_categorical("use_watch",     [False, True]),
            "use_sess_b":      trial.suggest_categorical("use_sess_b",    [True, True, False]),
            "use_asia":        trial.suggest_categorical("use_asia",      [True, False]),
            "allow_friday":    trial.suggest_categorical("allow_friday",  [True, False]),
            "req_htf2":        trial.suggest_categorical("req_htf2",      [True, True, False]),
            "use_be":          trial.suggest_categorical("use_be",        [True, True, False]),
            "adx_min":         trial.suggest_int("adx_min",        12, 30),
            "hurst_t":         trial.suggest_float("hurst_t",      0.50, 0.62, step=0.01),
            "adx_t":           trial.suggest_int("adx_t",          18, 32),
            "hurst_r":         trial.suggest_float("hurst_r",      0.44, 0.52, step=0.01),
            "adx_r":           trial.suggest_int("adx_r",          14, 24),
            "temp_min":        trial.suggest_int("temp_min",        5, 25),
            "temp_max":        trial.suggest_int("temp_max",        75, 100),
            "ofi_threshold":   trial.suggest_float("ofi_threshold", 0.35, 0.75, step=0.05),
            "elite_sl_mult":   trial.suggest_float("elite_sl_mult", 0.8, 2.5, step=0.1),
            "elite_tp_mult":   trial.suggest_float("elite_tp_mult", 1.0, 4.0, step=0.25),
            "exec_sl_mult":    trial.suggest_float("exec_sl_mult",  0.8, 2.5, step=0.1),
            "exec_tp_mult":    trial.suggest_float("exec_tp_mult",  1.0, 4.0, step=0.25),
            "risk_pct":        trial.suggest_float("risk_pct",      0.3, 2.0, step=0.1),
            "qty_tp1":         trial.suggest_float("qty_tp1",       0.35, 0.65, step=0.05),
            "signal_cooldown": trial.suggest_int("signal_cooldown", 1, 15),
        }
        try:
            sigs, qual = get_signals(df_is, cfg)
            if (sigs!=0).sum() < 5: return -9999
            return backtest_regime_only(df_is, sigs, qual, cfg, mask_is)
        except: return -9999

    # Warm start desde el mejor config conocido
    base_cfg = json.load(open(OUTPUT_DIR/"models"/"1h"/"config.json"))["params"]

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=50)
    )
    # Enqueue warm start
    try:
        study.enqueue_trial(base_cfg)
    except: pass

    best_score = -9999; best_cfg = {}
    def callback(study, trial):
        nonlocal best_score, best_cfg
        if trial.value and trial.value > best_score:
            best_score = trial.value
            best_cfg   = trial.params.copy()
            sigs, qual = get_signals(df_is, best_cfg)
            m_is = backtest_regime_only(df_is, sigs, qual, best_cfg, mask_is)
            if m_is > 0.3:
                print(f"  [Trial {trial.number}] NUEVO MEJOR score={trial.value:.4f}")

    study.optimize(objective, n_trials=n_trials, callbacks=[callback], show_progress_bar=False)

    print(f"\n  Mejor score IS: {study.best_value:.4f}")
    print(f"  Params: {study.best_params}")

    # OOS validation
    print(f"\n{'='*65}")
    print(f"  OOS VALIDATION ({regime})")
    sigs_oos, qual_oos = get_signals(df_oos, study.best_params)
    score_oos = backtest_regime_only(df_oos, sigs_oos, qual_oos, study.best_params, mask_oos)
    print(f"  Score OOS: {score_oos:.4f}")

    # Calcular metricas completas OOS
    def full_metrics(df_w, sigs, qual, cfg, mask):
        from analysis.walk_forward_real import backtest_window
        qual_n = qual.map({"ELITE_ICT":3,"ELITE":2,"EXECUTE":1}).fillna(0).astype(int)
        sigs_m = sigs.copy(); sigs_m[~mask] = 0
        return backtest_window(df_w, sigs_m, qual_n, cfg)

    m_oos = full_metrics(df_oos, sigs_oos, qual_oos, study.best_params, mask_oos)
    if m_oos:
        dd_val = m_oos.get('max_dd', m_oos.get('dd', 0))
        print(f"  {m_oos['trades']}T | WR {m_oos['wr']:.1f}% | CAGR {m_oos['cagr']:+.1f}% | "
              f"DD {dd_val:.1f}%")

    # Guardar si es mejor
    if m_oos and m_oos["cagr"] > 0:
        out = OUTPUT_DIR/"models"/"1h"/f"best_{regime.lower()}.json"
        result = {
            "tf": "1h", "regime": regime, "params": study.best_params,
            "metrics_oos": {k: float(v) if isinstance(v,(int,float,np.integer,np.floating)) else bool(v) if isinstance(v,(bool,np.bool_)) else v
                            for k,v in m_oos.items()},
            "pct_active": round(float(pct), 1)
        }
        with open(out,"w") as f: json.dump(result, f, indent=2)
        print(f"  [SAVED] {out}")

    print(f"{'='*65}")
    return study.best_params, m_oos


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--regime",  default="RANGE", choices=["RANGE","BULL","BEAR","ALL"])
    p.add_argument("--trials",  type=int, default=500)
    a = p.parse_args()
    run_bayesian_range(a.trials, a.regime)

"""
SIGMA ENGINE - Funding Rate Contrarian Strategy
Usa los 7,295 registros de funding rate (6.7 anos) para detectar crowding.

LOGICA:
  Funding muy alto (>p90): todos pagan por estar long → mercado sobrecargado
  → SHORT con alta probabilidad de corrección

  Funding muy bajo (<p10): todos pagan por estar short → mercado sobrecargado
  → LONG con alta probabilidad de rebote

  Edge real: en crypto, el funding rate es el "termometro del greed"
  Cuando sube mucho, una corrección es inminente (no hay mas buyers)
  Cuando baja mucho, un rebote es inminente (shorts se cubren)

  Con 6.7 anos de datos reales: ~11,000 registros (cada 8h)
  → OOS solido con cientos de señales
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

optuna.logging.set_verbosity(optuna.logging.WARNING)

OUTPUT_DIR = Path(__file__).parent.parent.parent
CAPITAL    = 1000.0
COMMISSION = 0.0004; SLIPPAGE = 0.0001; COST = COMMISSION + SLIPPAGE


def load_data_with_funding(tf="1h"):
    """Carga OHLCV + funding rate histórico completo."""
    from core.data import fetch_ohlcv
    from core.features import build_features

    print(f"[DATA] Cargando {tf} + funding rate...")
    df_b  = fetch_ohlcv(tf=tf,  days=3200)
    df_4h = fetch_ohlcv(tf="4h", days=3200)
    df_1d = fetch_ohlcv(tf="1d", days=3200)
    df = build_features(df_b, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close","atr","ema50"], inplace=True)

    # Verificar que funding rate fue cargado
    if "funding_rate" not in df.columns and "fr_z" not in df.columns:
        # Cargar manualmente
        fr_path = OUTPUT_DIR / "models" / "data_funding_full.csv"
        if fr_path.exists():
            df_fr = pd.read_csv(fr_path, index_col=0)
            df_fr.index = pd.to_datetime(df_fr.index)
            df_fr = df_fr[~df_fr.index.duplicated()].sort_index()
            df = pd.merge_asof(
                df.reset_index(), df_fr.reset_index(),
                on="timestamp", direction="backward"
            ).set_index("timestamp")

    # Calcular features del funding
    if "funding_rate" in df.columns:
        fr = df["funding_rate"].fillna(0)
        roll = fr.rolling(500, min_periods=50)
        df["fr_z"]        = (fr - roll.mean()) / roll.std().clip(lower=1e-8)
        df["fr_percentile"]= fr.rolling(500, min_periods=50).rank(pct=True)
        df["fr_ext_l"]    = (df["fr_percentile"] > 0.90).astype(float)
        df["fr_ext_s"]    = (df["fr_percentile"] < 0.10).astype(float)
        df["fr_very_ext_l"] = (df["fr_percentile"] > 0.95).astype(float)
        df["fr_very_ext_s"] = (df["fr_percentile"] < 0.05).astype(float)
        # Duracion del extreme
        df["fr_ext_l_dur"] = df["fr_ext_l"].groupby(
            (df["fr_ext_l"] != df["fr_ext_l"].shift()).cumsum()).cumcount()
        df["fr_ext_s_dur"] = df["fr_ext_s"].groupby(
            (df["fr_ext_s"] != df["fr_ext_s"].shift()).cumsum()).cumcount()

    days = (df.index[-1]-df.index[0]).days
    fr_coverage = df["fr_percentile"].notna().mean()*100 if "fr_percentile" in df.columns else 0
    print(f"  {len(df):,} velas | {days}d ({days/365:.1f}y)")
    print(f"  Funding Rate disponible: {fr_coverage:.0f}% de las barras")
    return df


def sig_funding_contrarian(df, cfg):
    """
    Genera señales basadas en extremos del funding rate.
    """
    min_dur   = cfg.get("min_dur",    2)    # minimo barras en extreme antes de entrar
    use_trend = cfg.get("use_trend",  True) # confirmar con tendencia 1H
    use_htf   = cfg.get("use_htf",    True) # requerir HTF alignment
    threshold = cfg.get("threshold",  0.90) # percentil para extremo
    cd        = cfg.get("cooldown",   12)

    if "fr_percentile" not in df.columns:
        return pd.Series(0, index=df.index)

    fr_p = df["fr_percentile"]
    ext_l = (fr_p > threshold)   # muy positivo = longs sobreextendidos
    ext_s = (fr_p < (1-threshold)) # muy negativo = shorts sobreextendidos

    # Duracion del extremo
    ext_l_dur = ext_l.groupby((ext_l != ext_l.shift()).cumsum()).cumcount()
    ext_s_dur = ext_s.groupby((ext_s != ext_s.shift()).cumsum()).cumcount()

    ema50 = df["ema50"]; ema200 = df.get("ema200", df["close"].ewm(span=200).mean())
    bull  = ema50 > ema200; bear = ema50 < ema200
    htf_l = df.get("htf1_long",  pd.Series(True,  index=df.index))
    htf_s = df.get("htf1_short", pd.Series(False, index=df.index))
    rsi   = df.get("rsi", pd.Series(50, index=df.index))

    # SHORT cuando funding extremo largo + duracion suficiente
    short_raw = (ext_l & (ext_l_dur >= min_dur) &
                 (rsi > cfg.get("rsi_ob", 60)))
    if use_trend: short_raw = short_raw & bear
    if use_htf:   short_raw = short_raw & htf_s

    # LONG cuando funding extremo corto + duracion suficiente
    long_raw = (ext_s & (ext_s_dur >= min_dur) &
                (rsi < cfg.get("rsi_os", 40)))
    if use_trend: long_raw = long_raw & bull
    if use_htf:   long_raw = long_raw & htf_l

    # Cooldown
    sig = pd.Series(0, index=df.index)
    last = -cd - 1
    for i in range(len(df)):
        if (i - last) < cd: continue
        if long_raw.iloc[i]:  sig.iloc[i] = 1;  last = i
        elif short_raw.iloc[i]: sig.iloc[i] = -1; last = i

    return sig


def backtest_fr(df, signals, sl_mult, tp_mult, risk_pct=1.0):
    c=df["close"].to_numpy(); h=df["high"].to_numpy()
    lo=df["low"].to_numpy(); a=df["atr"].to_numpy(); s=signals.to_numpy()
    cap=CAPITAL; eq=[cap]; pos=0; entry=sl=tp=size=0.0; trades=[]
    for i in range(1, len(c)):
        pr=c[i]; hi_=h[i]; low_=lo[i]
        if pos!=0:
            pnl=0.0; closed=False
            if pos==1:
                if low_<=sl: pnl=size*(sl-entry)-size*(entry+sl)*COST; closed=True
                elif hi_>=tp: pnl=size*(tp-entry)-size*(entry+tp)*COST; closed=True
            else:
                if hi_>=sl: pnl=size*(entry-sl)-size*(entry+sl)*COST; closed=True
                elif low_<=tp: pnl=size*(entry-tp)-size*(entry+tp)*COST; closed=True
            if closed: cap+=pnl; trades.append({"pnl":pnl,"won":pnl>0}); pos=0
        if pos==0 and s[i-1]!=0 and cap>50:
            at=a[i-1]; rsl=at*sl_mult
            if rsl<=0: continue
            size=(cap*risk_pct/100)/rsl; pos=int(s[i-1]); entry=pr
            sl=pr-rsl*pos; tp=pr+at*tp_mult*pos
        eq.append(cap)
    df_t=pd.DataFrame(trades)
    eq_s=pd.Series(eq[:len(df)], index=df.index[:len(eq)])
    return df_t, eq_s


def score_fr(df_t, eq_s, days):
    if df_t.empty or len(df_t)<10: return -9999
    w=df_t[df_t["pnl"]>0]; l=df_t[df_t["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if not l.empty else 999
    wr=len(w)/len(df_t)
    peak=eq_s.cummax(); dd=(eq_s-peak)/peak*100
    cagr=((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1))-1)*100
    if cagr<=0: return cagr*0.1
    calmar=cagr/abs(dd.min()) if dd.min()<0 else 0
    rr=w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
    return 0.35*min(calmar,5)/5 + 0.25*(wr-0.40)/0.35 + 0.25*min(pf,4)/4 + 0.15*min(rr,5)/5


def run_funding_contrarian(tf="1h", n_trials=500):
    print(f"\n{'='*60}")
    print(f"  FUNDING RATE CONTRARIAN -- {tf.upper()}")
    print(f"  {n_trials} trials | 6.7 anos de funding rate real")
    print(f"{'='*60}")

    df = load_data_with_funding(tf)
    if "fr_percentile" not in df.columns:
        print("  Sin datos de funding rate. Abortando.")
        return

    n = len(df); split = int(n*0.80)
    df_is = df.iloc[:split]; df_oos = df.iloc[split:]
    days_is  = (df_is.index[-1]-df_is.index[0]).days
    days_oos = (df_oos.index[-1]-df_oos.index[0]).days

    # Stats del funding en este periodo
    fr_avail_is = df_is["fr_percentile"].notna().sum()
    print(f"\n  IS: {fr_avail_is:,} barras con funding rate datos")
    ext_l_is = (df_is["fr_percentile"] > 0.90).sum()
    ext_s_is = (df_is["fr_percentile"] < 0.10).sum()
    print(f"  Extremo LONG (>p90): {ext_l_is} barras ({ext_l_is/fr_avail_is*100:.1f}%)")
    print(f"  Extremo SHORT (<p10): {ext_s_is} barras ({ext_s_is/fr_avail_is*100:.1f}%)")

    def objective(trial):
        cfg = {
            "min_dur":   trial.suggest_int("min_dur",   1, 8),
            "use_trend": trial.suggest_categorical("use_trend", [True, True, False]),
            "use_htf":   trial.suggest_categorical("use_htf",   [True, True, False]),
            "threshold": trial.suggest_float("threshold", 0.80, 0.95, step=0.05),
            "rsi_ob":    trial.suggest_int("rsi_ob",    55, 75),
            "rsi_os":    trial.suggest_int("rsi_os",    25, 45),
            "cooldown":  trial.suggest_int("cooldown",  8, 24),
        }
        sl  = trial.suggest_float("sl",  1.0, 2.5, step=0.1)
        tp  = trial.suggest_float("tp",  2.0, 6.0, step=0.5)
        rp  = trial.suggest_float("risk_pct", 0.5, 2.0, step=0.1)
        try:
            sg = sig_funding_contrarian(df_is, cfg)
            if (sg!=0).sum() < 10: return -9999
            dt, eq = backtest_fr(df_is, sg, sl, tp, rp)
            return score_fr(dt, eq, days_is)
        except: return -9999

    study = optuna.create_study(direction="maximize",
             sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=70))
    best = {"score":-9999,"cfg":{},"sl":1.5,"tp":3.0,"rp":1.0}

    def cb(s, t):
        if t.value and t.value > best["score"]:
            best["score"]=t.value
            best["cfg"]={k:v for k,v in t.params.items() if k not in("sl","tp","risk_pct")}
            best["sl"]=t.params["sl"]; best["tp"]=t.params["tp"]
            best["rp"]=t.params.get("risk_pct",1.0)
            if t.value > 0.3: print(f"  [T{t.number}] score={t.value:.4f}")

    study.optimize(objective, n_trials=n_trials, callbacks=[cb], show_progress_bar=False)

    # IS metrics
    sg_is = sig_funding_contrarian(df_is, best["cfg"])
    dt_is, eq_is = backtest_fr(df_is, sg_is, best["sl"], best["tp"], best["rp"])
    w=dt_is[dt_is["pnl"]>0]; l=dt_is[dt_is["pnl"]<=0]
    cagr_is=((eq_is.iloc[-1]/CAPITAL)**(365.25/max(days_is,1))-1)*100
    wr_is=len(w)/len(dt_is)*100 if len(dt_is)>0 else 0
    rr_is=w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
    print(f"\n  Mejor IS: {len(dt_is)}T | WR {wr_is:.1f}% | CAGR {cagr_is:+.1f}% | RR {rr_is:.2f}:1")

    # OOS
    print(f"\n{'='*60}")
    print(f"  OOS VALIDATION")
    sg_oos = sig_funding_contrarian(df_oos, best["cfg"])
    dt_oos, eq_oos = backtest_fr(df_oos, sg_oos, best["sl"], best["tp"], best["rp"])
    if dt_oos.empty or len(dt_oos)<5:
        print("  Sin trades OOS suficientes")
        return

    w=dt_oos[dt_oos["pnl"]>0]; l=dt_oos[dt_oos["pnl"]<=0]
    cagr_oos=((eq_oos.iloc[-1]/CAPITAL)**(365.25/max(days_oos,1))-1)*100
    wr_oos=len(w)/len(dt_oos)*100
    rr_oos=w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
    dd=((eq_oos-eq_oos.cummax())/eq_oos.cummax()*100).min()
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if not l.empty else 999
    tpm=len(dt_oos)/max(days_oos/30.44,0.1)
    print(f"  {len(dt_oos)}T ({tpm:.1f}/mes) | WR {wr_oos:.1f}% | CAGR {cagr_oos:+.1f}% | DD {dd:.1f}% | RR {rr_oos:.2f}:1")

    # Guardar si es bueno
    if cagr_oos > 0 and len(dt_oos) >= 15:
        import numpy as np_
        def ser(v):
            if isinstance(v,(np_.integer,)): return int(v)
            if isinstance(v,(np_.floating,)): return float(v)
            return v

        result = {
            "tf": tf, "strategy": f"Funding Rate Contrarian {tf}",
            "params": {k:ser(v) for k,v in best["cfg"].items()},
            "sl": best["sl"], "tp": best["tp"], "risk_pct": best["rp"],
            "metrics_oos": {"trades":len(dt_oos),"wr":wr_oos,"cagr":cagr_oos,
                           "dd":dd,"pf":pf,"rr":rr_oos,"trades_month":tpm},
            "trading_ready": cagr_oos > 5 and len(dt_oos) >= 20,
            "note": f"Funding Rate Contrarian OOS: {cagr_oos:+.1f}% CAGR"
        }
        out = OUTPUT_DIR/"models"/tf/f"best_funding_contrarian.json"
        with open(out,"w") as f: json.dump(result, f, indent=2)
        print(f"  [SAVED] {out.name}")

    print(f"{'='*60}")
    return best, cagr_oos


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--tf",     default="1h", choices=["1h","4h","15m","5m"])
    p.add_argument("--trials", type=int, default=500)
    a = p.parse_args()
    run_funding_contrarian(a.tf, a.trials)

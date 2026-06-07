"""
SIGMA ENGINE — Estrategias 5m de alto RR con 8.7 anos de datos
Adaptacion de advanced_15m para 5m con estrategias especificas.

VENTAJA 5m vs 15m:
  - 879k velas (8.7 anos) = mucho mas estadistica
  - ATR 5m ~0.15% → necesita RR 8:1 para superar costos con WR 40%
  - Solucion: usar SL de 5m (0.15%) + TP de 1H (1.5%) = RR 10:1

ESTRATEGIAS:
  1. CASCADE DIRECTO 1H->5m: señal 1H con entrada precisa en 5m
     SL=0.15% (5m), TP=1.5% (1H), RR~10:1 → breakeven con WR 9%!
  2. LIQUIDATION SPIKE 5m: spikes de liquidacion + reversal en 5m
  3. FUNDING EXTREME 5m: cuando funding esta en extremo → fade en 5m
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

optuna.logging.set_verbosity(optuna.logging.WARNING)

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION  = 0.0004
SLIPPAGE    = 0.0001
COST        = COMMISSION + SLIPPAGE
CAPITAL     = 1000.0


def load_data():
    from core.data import fetch_ohlcv
    from core.features import build_features
    print("[DATA] Cargando 5m, 15m, 1h, 4h con historial completo...")
    df_5m  = fetch_ohlcv(tf="5m",  days=3200)
    df_15m = fetch_ohlcv(tf="15m", days=3200)
    df_1h  = fetch_ohlcv(tf="1h",  days=3200)
    df_4h  = fetch_ohlcv(tf="4h",  days=3200)
    df_5m_f = build_features(df_5m, {"15m": df_15m, "1h": df_1h})
    df_1h_f = build_features(df_1h, {"4h": df_4h})
    df_5m_f.dropna(subset=["close","atr","ema50"], inplace=True)
    df_1h_f.dropna(subset=["close","atr","ema50"], inplace=True)
    days5 = (df_5m_f.index[-1]-df_5m_f.index[0]).days
    days1 = (df_1h_f.index[-1]-df_1h_f.index[0]).days
    print(f"  5m:  {len(df_5m_f):,} velas | {days5}d ({days5/365:.1f}y)")
    print(f"  1h:  {len(df_1h_f):,} velas | {days1}d ({days1/365:.1f}y)")
    return df_5m_f, df_1h_f, df_4h


def backtest_5m(df, signals, sl_s, tp_s, risk_pct=1.0):
    c  = df["close"].to_numpy()
    h  = df["high"].to_numpy()
    lo = df["low"].to_numpy()
    sg = signals.to_numpy()
    sl = sl_s.to_numpy()
    tp = tp_s.to_numpy()

    cap = CAPITAL; eq = [cap]; pos = 0
    entry = slv = tpv = size = 0.0
    trades = []

    for i in range(1, len(c)):
        pr = c[i]; hi_ = h[i]; low_ = lo[i]
        if pos != 0:
            pnl = 0.0; closed = False
            if pos == 1:
                if low_ <= slv: pnl=size*(slv-entry)-size*(entry+slv)*COST; closed=True
                elif hi_ >= tpv: pnl=size*(tpv-entry)-size*(entry+tpv)*COST; closed=True
            else:
                if hi_ >= slv: pnl=size*(entry-slv)-size*(entry+slv)*COST; closed=True
                elif low_ <= tpv: pnl=size*(entry-tpv)-size*(entry+tpv)*COST; closed=True
            if closed:
                cap += pnl; trades.append({"pnl":pnl,"won":pnl>0}); pos = 0

        if pos == 0 and sg[i-1] != 0 and sl[i-1] > 0 and cap > 50:
            rsl = abs(pr - sl[i-1])
            if rsl <= 0: continue
            size = (cap * risk_pct/100) / rsl
            pos = int(sg[i-1]); entry = pr; slv = sl[i-1]; tpv = tp[i-1]

        eq.append(cap)

    df_t = pd.DataFrame(trades)
    eq_s = pd.Series(eq[:len(df)], index=df.index[:len(eq)])
    return df_t, eq_s


def score_5m(df_t, eq_s, days):
    if df_t.empty or len(df_t) < 15: return -9999
    w  = df_t[df_t["pnl"]>0]; l = df_t[df_t["pnl"]<=0]
    gp = w["pnl"].sum(); gl = abs(l["pnl"].sum())
    pf = gp/gl if gl > 0 else 999
    wr = len(w)/len(df_t)
    peak = eq_s.cummax(); dd = (eq_s-peak)/peak*100
    cagr = ((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1))-1)*100
    if cagr <= 0: return cagr * 0.1
    calmar = cagr/abs(dd.min()) if dd.min() < 0 else 0
    rr = w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
    tpm = len(df_t)/max(days/30.44, 0.1)
    return (0.30*min(calmar,5)/5 + 0.25*(wr-0.35)/0.35 +
            0.20*min(pf,5)/5 + 0.15*min(cagr,200)/200 +
            0.10*min(tpm,20)/20)


# ─── ESTRATEGIA 1: CASCADE DIRECTO 1H -> 5m ─────────────────────────────────
def sig_1h_to_5m(df_5m, df_1h, cfg):
    """
    La señal SIGMA 1H define la direccion.
    Dentro de N barras de 5m, busca entrada en pullback a EMA9.
    SL: estructura de 5m (0.15%), TP: objetivo 1H (1.5%) → RR ~10:1
    """
    from core.signals import get_signals
    bv = OUTPUT_DIR / "models" / "1h" / "best_validated.json"
    with open(bv) as f: p1h = json.load(f)["params"]

    from core.features import build_features
    sig_1h, _ = get_signals(df_1h, p1h)

    window   = cfg.get("window", 24)   # barras 5m = 2h
    sl_mult  = cfg.get("sl_mult", 0.6)
    tp_mult  = cfg.get("tp_mult", 8.0) # TP en ATR 5m (amplio)
    use_1h_tp= cfg.get("use_1h_tp", True)
    cd       = cfg.get("cooldown", 24)

    ema9  = df_5m["close"].ewm(span=9, adjust=False).mean()
    ema20 = df_5m["close"].ewm(span=20, adjust=False).mean()
    atr5  = df_5m["atr"]
    atr1h_at = pd.Series(0.0, index=df_5m.index)

    # Alinear señales 1H → 5m
    active = pd.Series(0, index=df_5m.index)
    for ts, sig in sig_1h[sig_1h != 0].items():
        mask = (df_5m.index > ts) & (df_5m.index <= ts + pd.Timedelta(minutes=5*window))
        active[mask] = sig
        # ATR del 1H en el momento de la señal
        if ts in df_1h.index:
            atr1h_at[mask] = df_1h["atr"][ts]

    sigs = pd.Series(0, index=df_5m.index)
    sl_s = pd.Series(0.0, index=df_5m.index)
    tp_s = pd.Series(0.0, index=df_5m.index)
    last = -cd - 1

    c5 = df_5m["close"]; h5 = df_5m["high"]
    l5 = df_5m["low"];   o5 = df_5m["open"]

    for i in range(2, len(df_5m)):
        if (i - last) < cd: continue
        sig = active.iloc[i]
        if sig == 0: continue

        ci = c5.iloc[i]; oi = o5.iloc[i]
        hi = h5.iloc[i]; li = l5.iloc[i]
        e9 = ema9.iloc[i]; at5 = atr5.iloc[i]
        at1h = atr1h_at.iloc[i]

        if at5 <= 0: continue

        if sig == 1:
            pull = li <= e9 * 1.002 and ci > e9 and ci > oi
            if pull:
                slv = li - at5 * sl_mult
                tpv = ci + (at1h if use_1h_tp and at1h > 0 else at5 * tp_mult)
                rr = (tpv-ci)/max(ci-slv, 1e-8)
                if rr >= cfg.get("min_rr", 5.0):
                    sigs.iloc[i]=1; sl_s.iloc[i]=slv; tp_s.iloc[i]=tpv; last=i
        else:
            pull = hi >= e9 * 0.998 and ci < e9 and ci < oi
            if pull:
                slv = hi + at5 * sl_mult
                tpv = ci - (at1h if use_1h_tp and at1h > 0 else at5 * tp_mult)
                rr = (ci-tpv)/max(slv-ci, 1e-8)
                if rr >= cfg.get("min_rr", 5.0):
                    sigs.iloc[i]=-1; sl_s.iloc[i]=slv; tp_s.iloc[i]=tpv; last=i

    return sigs, sl_s, tp_s


# ─── ESTRATEGIA 2: LIQUIDATION SPIKE + REVERSAL ──────────────────────────────
def sig_liq_spike_5m(df_5m, cfg):
    """
    En 5m los spikes de liquidacion son mas frecuentes y predecibles.
    Spike > 2.5 ATR con wick largo → reversal en direccion contraria.
    """
    spike_min = cfg.get("spike_min", 2.0)
    wick_pct  = cfg.get("wick_pct",  0.60)
    htf_req   = cfg.get("htf_req",   True)
    cd        = cfg.get("cooldown",  24)

    c = df_5m["close"]; h = df_5m["high"]; l = df_5m["low"]; o = df_5m["open"]
    atr = df_5m["atr"]
    rng = h - l
    is_spike = rng > atr * spike_min

    uw = (h - c.clip(lower=o)) / rng.clip(lower=1e-8)
    lw = (c.clip(upper=o) - l) / rng.clip(lower=1e-8)

    bull_trap = is_spike & (uw > wick_pct) & (c > o.shift(1))
    bear_trap = is_spike & (lw > wick_pct) & (c < o.shift(1))

    htf_l = df_5m.get("htf_long_1h",  pd.Series(True, index=df_5m.index))
    htf_s = df_5m.get("htf_short_1h", pd.Series(False, index=df_5m.index))
    dow_ok = pd.Series(df_5m.index.dayofweek, index=df_5m.index).isin([1,2,3])

    sigs = pd.Series(0, index=df_5m.index)
    sl_s = pd.Series(0.0, index=df_5m.index)
    tp_s = pd.Series(0.0, index=df_5m.index)
    last = -cd - 1
    sl_mult = cfg.get("sl_mult", 0.5)
    tp_mult = cfg.get("tp_mult", 4.0)

    for i in range(2, len(df_5m)):
        if (i - last) < cd: continue
        if not dow_ok.iloc[i]: continue

        at5 = atr.iloc[i]; ci = c.iloc[i]
        if at5 <= 0: continue

        if bear_trap.iloc[i] and (not htf_req or htf_l.iloc[i]):
            slv = ci - at5 * sl_mult
            tpv = ci + at5 * tp_mult
            sigs.iloc[i]=1; sl_s.iloc[i]=slv; tp_s.iloc[i]=tpv; last=i
        elif bull_trap.iloc[i] and (not htf_req or htf_s.iloc[i]):
            slv = ci + at5 * sl_mult
            tpv = ci - at5 * tp_mult
            sigs.iloc[i]=-1; sl_s.iloc[i]=slv; tp_s.iloc[i]=tpv; last=i

    return sigs, sl_s, tp_s


# ─── ESTRATEGIA 3: VWAP INTRADAY EXTREMO ────────────────────────────────────
def sig_vwap_extreme_5m(df_5m, cfg):
    """
    Cuando el precio se desvía demasiado del VWAP intraday → mean reversion.
    En 5m, desviaciones de 2+ ATR del VWAP intraday son oportunidades.
    """
    dev_min  = cfg.get("dev_min",  1.5)  # desviacion minima en ATR
    dev_max  = cfg.get("dev_max",  5.0)  # no entrar si muy extremo (gap)
    cd       = cfg.get("cooldown", 20)
    sl_mult  = cfg.get("sl_mult",  0.4)
    tp_mult  = cfg.get("tp_mult",  3.0)

    # VWAP intraday (reinicia cada dia)
    c = df_5m["close"]; h = df_5m["high"]; l = df_5m["low"]
    v = df_5m["volume"]; atr = df_5m["atr"]

    tp_price = (h+l+c)/3
    cum_vol  = v.groupby(df_5m.index.date).cumsum()
    cum_tpv  = (tp_price*v).groupby(df_5m.index.date).cumsum()
    vwap     = cum_tpv / cum_vol.clip(lower=1)

    dev = (c - vwap) / atr.clip(lower=1e-8)

    htf_l = df_5m.get("htf_long_1h",  pd.Series(True, index=df_5m.index))
    htf_s = df_5m.get("htf_short_1h", pd.Series(False, index=df_5m.index))
    dow_ok = pd.Series(df_5m.index.dayofweek, index=df_5m.index).isin([1,2,3])
    h_utc  = pd.Series(df_5m.index.hour, index=df_5m.index)
    sess_ok = ((h_utc >= 8) & (h_utc < 12)) | ((h_utc >= 13) & (h_utc < 20))

    sigs = pd.Series(0, index=df_5m.index)
    sl_s = pd.Series(0.0, index=df_5m.index)
    tp_s = pd.Series(0.0, index=df_5m.index)
    last = -cd - 1

    for i in range(5, len(df_5m)):
        if (i - last) < cd: continue
        if not dow_ok.iloc[i] or not sess_ok.iloc[i]: continue

        di = dev.iloc[i]; ci = c.iloc[i]; at5 = atr.iloc[i]
        if at5 <= 0: continue
        vw = vwap.iloc[i]

        if di < -dev_min and di > -dev_max and htf_l.iloc[i]:
            # Precio muy por debajo del VWAP → long hacia VWAP
            slv = ci - at5 * sl_mult
            tpv = vw + at5 * tp_mult * 0.5
            rr = (tpv-ci)/max(ci-slv,1e-8)
            if rr >= 3.0:
                sigs.iloc[i]=1; sl_s.iloc[i]=slv; tp_s.iloc[i]=tpv; last=i

        elif di > dev_min and di < dev_max and htf_s.iloc[i]:
            # Precio muy por encima del VWAP → short hacia VWAP
            slv = ci + at5 * sl_mult
            tpv = vw - at5 * tp_mult * 0.5
            rr = (ci-tpv)/max(slv-ci,1e-8)
            if rr >= 3.0:
                sigs.iloc[i]=-1; sl_s.iloc[i]=slv; tp_s.iloc[i]=tpv; last=i

    return sigs, sl_s, tp_s


# ─── OPTIMIZACION ────────────────────────────────────────────────────────────
def run_advanced_5m(n_trials=500):
    print(f"\n{'='*65}")
    print("  SIGMA ADVANCED 5M -- 3 estrategias RR alto")
    print(f"  {n_trials} trials x 3 | 8.7 anos de datos | IS/OOS 80/20")
    print(f"{'='*65}")

    df_5m, df_1h, df_4h = load_data()

    n = len(df_5m)
    split    = int(n * 0.80)
    df_is    = df_5m.iloc[:split];  df_oos = df_5m.iloc[split:]
    df_1h_is = df_1h[df_1h.index <= df_is.index[-1]]
    df_1h_oos= df_1h[(df_1h.index >  df_is.index[-1]) &
                     (df_1h.index <= df_oos.index[-1])]
    days_is  = (df_is.index[-1]-df_is.index[0]).days
    days_oos = (df_oos.index[-1]-df_oos.index[0]).days

    print(f"  IS:  {df_is.index[0].strftime('%Y-%m-%d')} -> "
          f"{df_is.index[-1].strftime('%Y-%m-%d')} ({days_is}d = {days_is/365:.1f}y)")
    print(f"  OOS: {df_oos.index[0].strftime('%Y-%m-%d')} -> "
          f"{df_oos.index[-1].strftime('%Y-%m-%d')} ({days_oos}d = {days_oos/365:.1f}y)\n")

    results = {}

    # ── 1: CASCADE 1H->5m ────────────────────────────────────────────────────
    print("  [1] CASCADE DIRECTO 1H->5m (RR ~10:1)")
    def obj1(trial):
        cfg = {
            "window":   trial.suggest_int("window",   12, 36),
            "sl_mult":  trial.suggest_float("sl_mult",0.4, 1.2, step=0.1),
            "tp_mult":  trial.suggest_float("tp_mult",5.0,12.0, step=0.5),
            "use_1h_tp":trial.suggest_categorical("use_1h_tp",[True,True,False]),
            "min_rr":   trial.suggest_float("min_rr",  4.0, 10.0, step=0.5),
            "cooldown": trial.suggest_int("cooldown",  16, 40),
        }
        rp = trial.suggest_float("risk_pct", 0.5, 2.0, step=0.1)
        try:
            sg, sl, tp = sig_1h_to_5m(df_is, df_1h_is, cfg)
            if (sg!=0).sum() < 15: return -9999
            dt, eq = backtest_5m(df_is, sg, sl, tp, rp)
            return score_5m(dt, eq, days_is)
        except: return -9999

    st1 = optuna.create_study(direction="maximize",
           sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=70))
    b1 = {"score":-9999,"cfg":{},"rp":1.0}
    def cb1(s, t):
        if t.value and t.value > b1["score"]:
            b1["score"]=t.value
            b1["cfg"]={k:v for k,v in t.params.items() if k!="risk_pct"}
            b1["rp"]=t.params.get("risk_pct",1.0)
            if t.value > 0.3: print(f"    [T{t.number}] score={t.value:.4f}")
    st1.optimize(obj1, n_trials=n_trials, callbacks=[cb1], show_progress_bar=False)
    try:
        sg,sl,tp = sig_1h_to_5m(df_is, df_1h_is, b1["cfg"])
        dt,eq = backtest_5m(df_is, sg, sl, tp, b1["rp"])
        w=dt[dt["pnl"]>0]; l=dt[dt["pnl"]<=0]
        rr=w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
        cagr=((eq.iloc[-1]/CAPITAL)**(365.25/max(days_is,1))-1)*100
        wr=len(w)/len(dt)*100 if len(dt)>0 else 0
        print(f"    Mejor IS: {len(dt)}T | WR {wr:.1f}% | CAGR {cagr:+.1f}% | RR {rr:.1f}:1")
        results["cascade"] = {"b":b1,"is_trades":len(dt),"is_cagr":cagr,"is_wr":wr,"rr":rr}
    except Exception as e: print(f"    Error: {e}")

    # ── 2: LIQUIDATION SPIKE ─────────────────────────────────────────────────
    print("\n  [2] LIQUIDATION SPIKE 5m")
    def obj2(trial):
        cfg = {
            "spike_min":trial.suggest_float("spike_min",1.5,3.5,step=0.1),
            "wick_pct": trial.suggest_float("wick_pct", 0.5,0.8,step=0.05),
            "htf_req":  trial.suggest_categorical("htf_req",[True,True,False]),
            "sl_mult":  trial.suggest_float("sl_mult", 0.3,1.0,step=0.1),
            "tp_mult":  trial.suggest_float("tp_mult", 3.0,8.0,step=0.5),
            "cooldown": trial.suggest_int("cooldown", 16, 40),
        }
        rp = trial.suggest_float("risk_pct", 0.5, 2.0, step=0.1)
        try:
            sg,sl,tp = sig_liq_spike_5m(df_is, cfg)
            if (sg!=0).sum() < 15: return -9999
            dt,eq = backtest_5m(df_is, sg, sl, tp, rp)
            return score_5m(dt, eq, days_is)
        except: return -9999

    st2 = optuna.create_study(direction="maximize",
           sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=70))
    b2 = {"score":-9999,"cfg":{},"rp":1.0}
    def cb2(s, t):
        if t.value and t.value > b2["score"]:
            b2["score"]=t.value
            b2["cfg"]={k:v for k,v in t.params.items() if k!="risk_pct"}
            b2["rp"]=t.params.get("risk_pct",1.0)
            if t.value > 0.3: print(f"    [T{t.number}] score={t.value:.4f}")
    st2.optimize(obj2, n_trials=n_trials, callbacks=[cb2], show_progress_bar=False)
    try:
        sg,sl,tp = sig_liq_spike_5m(df_is, b2["cfg"])
        dt,eq = backtest_5m(df_is, sg, sl, tp, b2["rp"])
        w=dt[dt["pnl"]>0]; l=dt[dt["pnl"]<=0]
        rr=w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
        cagr=((eq.iloc[-1]/CAPITAL)**(365.25/max(days_is,1))-1)*100
        wr=len(w)/len(dt)*100 if len(dt)>0 else 0
        print(f"    Mejor IS: {len(dt)}T | WR {wr:.1f}% | CAGR {cagr:+.1f}% | RR {rr:.1f}:1")
        results["liq_spike"] = {"b":b2,"is_trades":len(dt),"is_cagr":cagr,"is_wr":wr,"rr":rr}
    except Exception as e: print(f"    Error: {e}")

    # ── 3: VWAP EXTREME ──────────────────────────────────────────────────────
    print("\n  [3] VWAP INTRADAY EXTREMO")
    def obj3(trial):
        cfg = {
            "dev_min":  trial.suggest_float("dev_min", 1.2, 3.5, step=0.1),
            "dev_max":  trial.suggest_float("dev_max", 3.0, 8.0, step=0.5),
            "sl_mult":  trial.suggest_float("sl_mult", 0.3, 1.0, step=0.1),
            "tp_mult":  trial.suggest_float("tp_mult", 2.0, 6.0, step=0.5),
            "cooldown": trial.suggest_int("cooldown", 12, 32),
        }
        rp = trial.suggest_float("risk_pct", 0.5, 2.0, step=0.1)
        try:
            sg,sl,tp = sig_vwap_extreme_5m(df_is, cfg)
            if (sg!=0).sum() < 15: return -9999
            dt,eq = backtest_5m(df_is, sg, sl, tp, rp)
            return score_5m(dt, eq, days_is)
        except: return -9999

    st3 = optuna.create_study(direction="maximize",
           sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=70))
    b3 = {"score":-9999,"cfg":{},"rp":1.0}
    def cb3(s, t):
        if t.value and t.value > b3["score"]:
            b3["score"]=t.value
            b3["cfg"]={k:v for k,v in t.params.items() if k!="risk_pct"}
            b3["rp"]=t.params.get("risk_pct",1.0)
            if t.value > 0.3: print(f"    [T{t.number}] score={t.value:.4f}")
    st3.optimize(obj3, n_trials=n_trials, callbacks=[cb3], show_progress_bar=False)
    try:
        sg,sl,tp = sig_vwap_extreme_5m(df_is, b3["cfg"])
        dt,eq = backtest_5m(df_is, sg, sl, tp, b3["rp"])
        w=dt[dt["pnl"]>0]; l=dt[dt["pnl"]<=0]
        rr=w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
        cagr=((eq.iloc[-1]/CAPITAL)**(365.25/max(days_is,1))-1)*100
        wr=len(w)/len(dt)*100 if len(dt)>0 else 0
        print(f"    Mejor IS: {len(dt)}T | WR {wr:.1f}% | CAGR {cagr:+.1f}% | RR {rr:.1f}:1")
        results["vwap"] = {"b":b3,"is_trades":len(dt),"is_cagr":cagr,"is_wr":wr,"rr":rr}
    except Exception as e: print(f"    Error: {e}")

    # ── OOS VALIDATION ───────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print("  OOS VALIDATION (2024-2026, periodo nunca visto)")

    strategies = [
        ("Cascade 1H->5m",   sig_1h_to_5m,        b1, [df_oos, df_1h_oos]),
        ("Liq Spike 5m",     sig_liq_spike_5m,     b2, [df_oos]),
        ("VWAP Extreme 5m",  sig_vwap_extreme_5m,  b3, [df_oos]),
    ]

    best_oos = -9999; best_name = ""; best_result = {}
    for name, fn, best, args in strategies:
        try:
            sg, sl, tp = fn(*args, best["cfg"])
            dt, eq = backtest_5m(df_oos, sg, sl, tp, best["rp"])
            if dt.empty or len(dt) < 8: print(f"  {name}: Sin trades OOS"); continue
            w=dt[dt["pnl"]>0]; l=dt[dt["pnl"]<=0]
            rr=w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
            cagr=((eq.iloc[-1]/CAPITAL)**(365.25/max(days_oos,1))-1)*100
            wr=len(w)/len(dt)*100
            dd=((eq-eq.cummax())/eq.cummax()*100).min()
            pf=w["pnl"].sum()/abs(l["pnl"].sum()) if not l.empty else 999
            tpm=len(dt)/max(days_oos/30.44,0.1)
            print(f"  {name}:")
            print(f"    {len(dt)}T ({tpm:.1f}/mes) | WR {wr:.1f}% | "
                  f"CAGR {cagr:+.1f}% | DD {dd:.1f}% | RR {rr:.1f}:1")
            if cagr > best_oos:
                best_oos = cagr; best_name = name
                best_result = {"name":name,"cfg":best,"oos":{"trades":len(dt),"wr":wr,
                               "cagr":cagr,"dd":dd,"pf":pf,"rr":rr,"trades_month":tpm}}
        except Exception as e:
            print(f"  {name}: Error — {e}")

    # Guardar si es bueno
    print(f"\n{'='*65}")
    if best_oos > 0:
        print(f"  GANADOR OOS: {best_name} — CAGR {best_oos:+.1f}%")
        cur_path = OUTPUT_DIR / "models" / "5m" / "best_validated.json"
        cur_cagr = -9999
        if cur_path.exists():
            try:
                with open(cur_path) as f:
                    cur = json.load(f)
                if cur.get("trading_ready", False):
                    cur_cagr = cur.get("metrics_oos",{}).get("cagr",-9999)
            except: pass

        if best_oos > cur_cagr + 1.0:
            import numpy as np_
            def ser(v):
                if isinstance(v,(np_.integer,)): return int(v)
                if isinstance(v,(np_.floating,)): return float(v)
                if isinstance(v,(list,)): return [ser(x) for x in v]
                return v

            result = {
                "tf": "5m", "strategy": best_result["name"],
                "params": {k:ser(v) for k,v in best_result["cfg"]["cfg"].items()},
                "risk_pct": best_result["cfg"]["rp"],
                "metrics_oos": {k:round(float(v),4) for k,v in best_result["oos"].items()},
                "trading_ready": best_oos > 5 and best_result["oos"]["trades"] >= 20,
                "note": f"OOS {best_oos:+.1f}% CAGR | {best_result['oos']['trades']} trades"
            }
            (OUTPUT_DIR/"models"/"5m").mkdir(parents=True, exist_ok=True)
            with open(cur_path, "w") as f: json.dump(result, f, indent=2)
            print(f"  [SAVED] models/5m/best_validated.json")
            print(f"  trading_ready: {result['trading_ready']}")
        else:
            print(f"  No supera modelo actual ({cur_cagr:+.1f}%)")
    else:
        print("  Ningun modelo OOS positivo en este intento.")

    print(f"{'='*65}")
    return results


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=500)
    a = p.parse_args()
    run_advanced_5m(a.trials)

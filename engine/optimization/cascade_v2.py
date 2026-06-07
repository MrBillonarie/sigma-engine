"""
SIGMA CASCADE v2 — Con filtros adicionales para mejorar RR
Problema anterior: RR bajaba de 6.0 IS a 4.5 OOS → rentabilidad borderline

Mejoras v2:
  1. MOMENTUM FILTER: solo entrar cuando momentum 15m confirma direccion
     (MACD hist positivo para longs, negativo para shorts)
  2. VOLUME FILTER: solo entrar cuando volumen > 1.3x promedio (confirmacion)
  3. CANDLE QUALITY: vela de confirmacion mas estricta (body > 50% rango)
  4. DYNAMIC TP: usar ATR 4H en vez de 1H para TP mas amplio → RR mas alto
  5. MULTI-ENTRY: si hay pullback mas profundo, entrar con mejor precio

Objetivo: RR OOS > 5.5 (vs 4.5 anterior) con WR similar (~18%)
Breakeven: RR 5.5 necesita WR > 15.4% (muy asequible)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

optuna.logging.set_verbosity(optuna.logging.WARNING)

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION = 0.0004
SLIPPAGE   = 0.0001
COST       = COMMISSION + SLIPPAGE
CAPITAL    = 1000.0


def load_tfs():
    from core.data import fetch_ohlcv
    from core.features import build_features
    print("[DATA] Cargando 4H, 1H, 15m...")
    df_4h  = fetch_ohlcv(tf="4h",  days=3200)
    df_1h  = fetch_ohlcv(tf="1h",  days=3200)
    df_15m = fetch_ohlcv(tf="15m", days=3200)
    df_4h2 = fetch_ohlcv(tf="4h",  days=3200)
    df_1d  = fetch_ohlcv(tf="1d",  days=3200)
    df_4h_f  = build_features(df_4h,  {"1d": df_1d})
    df_1h_f  = build_features(df_1h,  {"4h": df_4h, "1d": df_1d})
    df_15m_f = build_features(df_15m, {"1h": df_1h, "4h": df_4h})
    for df, n in [(df_4h_f,"4h"),(df_1h_f,"1h"),(df_15m_f,"15m")]:
        df.dropna(subset=["close","atr","ema50"], inplace=True)
        print(f"  {n}: {len(df):,} velas")
    return df_4h_f, df_1h_f, df_15m_f


def get_4h_bias(df_4h):
    ema50  = df_4h["ema50"]
    ema200 = df_4h["ema200"] if "ema200" in df_4h.columns else df_4h["close"].ewm(span=200).mean()
    adx    = df_4h.get("adx", pd.Series(20, index=df_4h.index))
    bull   = (ema50 > ema200) & (adx > 18)
    bear   = (ema50 < ema200) & (adx > 18)
    bias   = pd.Series("RANGE", index=df_4h.index)
    bias[bull] = "BULL"; bias[bear] = "BEAR"
    return bias


def find_entries_v2(df_15m, df_1h, signals_1h, quality_1h, df_4h_bias, df_4h, cfg):
    """
    Version mejorada con filtros de momentum y volumen.
    """
    window_15m   = cfg.get("window_15m",   14)
    sl_mult_15m  = cfg.get("sl_mult_15m",  0.8)
    tp_mult_4h   = cfg.get("tp_mult_4h",   3.0)   # TP basado en ATR 4H (mas amplio)
    min_rr       = cfg.get("min_rr",       5.0)
    require_4h   = cfg.get("require_4h",   True)
    cd_bars      = cfg.get("cooldown_15m", 20)
    use_momentum = cfg.get("use_momentum", True)   # NUEVO: filtro MACD
    use_volume   = cfg.get("use_volume",   True)   # NUEVO: filtro volumen
    vol_mult     = cfg.get("vol_mult",     1.3)    # NUEVO: umbral volumen
    body_pct     = cfg.get("body_pct",     0.40)   # NUEVO: calidad vela

    ema20   = df_15m["close"].ewm(span=20, adjust=False).mean()
    ema9    = df_15m["close"].ewm(span=9,  adjust=False).mean()
    atr15   = df_15m["atr"]
    c15     = df_15m["close"]; h15 = df_15m["high"]
    l15     = df_15m["low"];   o15 = df_15m["open"]
    vol15   = df_15m.get("volume", pd.Series(1, index=df_15m.index))
    vol_ma  = vol15.rolling(20).mean()

    # MACD 15m para momentum
    macd_l  = c15.ewm(span=12).mean() - c15.ewm(span=26).mean()
    macd_s  = macd_l.ewm(span=9).mean()
    macd_h  = macd_l - macd_s  # histogram

    # ATR 4H alineado a 15m (para TP mas amplio)
    atr_4h_aligned = pd.Series(0.0, index=df_15m.index)
    for ts in df_4h.index:
        mask = (df_15m.index >= ts) & (df_15m.index < ts + pd.Timedelta(hours=4))
        if "atr" in df_4h.columns:
            atr_4h_aligned[mask] = df_4h["atr"][ts]

    # Bias 4H
    bias_15m = pd.Series("RANGE", index=df_15m.index)
    for ts, b in df_4h_bias.items():
        mask = (df_15m.index >= ts) & (df_15m.index < ts + pd.Timedelta(hours=4))
        bias_15m[mask] = b

    # Ventanas activas de señales 1H
    active_sig = pd.Series(0, index=df_15m.index)
    atr_1h_sig = pd.Series(0.0, index=df_15m.index)
    for ts, sig in signals_1h[signals_1h != 0].items():
        mask = (df_15m.index > ts) & (df_15m.index <= ts + pd.Timedelta(minutes=15*window_15m))
        active_sig[mask] = sig
        ref = ts if ts in df_1h.index else (df_1h.index[df_1h.index <= ts][-1] if len(df_1h.index[df_1h.index <= ts]) > 0 else None)
        if ref is not None:
            atr_1h_sig[mask] = df_1h["atr"][ref]

    entries = pd.Series(0, index=df_15m.index)
    sl_vals = pd.Series(0.0, index=df_15m.index)
    tp_vals = pd.Series(0.0, index=df_15m.index)
    last_entry = -cd_bars - 1

    for i in range(5, len(df_15m)):
        if (i - last_entry) < cd_bars: continue
        sig = active_sig.iloc[i]
        if sig == 0: continue

        b = bias_15m.iloc[i]
        if require_4h:
            if sig == 1  and b not in ("BULL","RANGE"): continue
            if sig == -1 and b not in ("BEAR","RANGE"): continue

        ci = c15.iloc[i]; oi = o15.iloc[i]
        hi = h15.iloc[i]; li = l15.iloc[i]
        e20 = ema20.iloc[i]; e9 = ema9.iloc[i]
        at15 = atr15.iloc[i]
        at1h = atr_1h_sig.iloc[i]
        at4h = atr_4h_aligned.iloc[i]
        if at15 <= 0 or at1h <= 0: continue

        # Calidad de vela (body/range)
        body    = abs(ci - oi)
        rng     = hi - li + 1e-9
        body_q  = body / rng

        # Filtros adicionales
        if use_volume:
            vol_ok = vol15.iloc[i] >= vol_ma.iloc[i] * vol_mult
            if not vol_ok: continue

        if body_q < body_pct: continue

        entered = False
        if sig == 1:
            # LONG: pullback a EMA20/9 con momentum alcista
            pull = li <= e20 * 1.004 and ci > e9 and ci > oi
            if pull:
                if use_momentum and macd_h.iloc[i] <= 0: continue  # MACD debe ser positivo
                entered = True
        else:
            # SHORT: pullback a EMA20/9 con momentum bajista
            pull = hi >= e20 * 0.996 and ci < e9 and ci < oi
            if pull:
                if use_momentum and macd_h.iloc[i] >= 0: continue  # MACD debe ser negativo
                entered = True

        if entered:
            sl_d = at15 * sl_mult_15m
            # TP basado en ATR 4H (mas amplio → RR mayor)
            tp_d = at4h * tp_mult_4h if at4h > 0 else at1h * tp_mult_4h * 0.7

            if sig == 1:
                sl = ci - sl_d
                tp = ci + tp_d
            else:
                sl = ci + sl_d
                tp = ci - tp_d

            rr = abs(tp - ci) / max(abs(ci - sl), 1e-9)
            if rr < min_rr: continue

            entries.iloc[i] = sig
            sl_vals.iloc[i] = sl
            tp_vals.iloc[i] = tp
            last_entry = i

    return entries, sl_vals, tp_vals


def backtest_cascade(df_15m, entries, sl_series, tp_series, risk_pct=1.5):
    c = df_15m["close"].to_numpy(); h = df_15m["high"].to_numpy(); lo = df_15m["low"].to_numpy()
    ent = entries.to_numpy(); sl = sl_series.to_numpy(); tp = tp_series.to_numpy()
    cap = CAPITAL; eq = [cap]; pos = 0; entry_p = slv = tpv = size = 0.0; trades = []
    for i in range(1, len(c)):
        pr = c[i]; hi_ = h[i]; low_ = lo[i]
        if pos != 0:
            pnl = 0.0; closed = False
            if pos == 1:
                if low_ <= slv: pnl = size*(slv-entry_p)-size*(entry_p+slv)*COMMISSION; closed = True
                elif hi_ >= tpv: pnl = size*(tpv-entry_p)-size*(entry_p+tpv)*COMMISSION; closed = True
            else:
                if hi_ >= slv: pnl = size*(entry_p-slv)-size*(entry_p+slv)*COMMISSION; closed = True
                elif low_ <= tpv: pnl = size*(entry_p-tpv)-size*(entry_p+tpv)*COMMISSION; closed = True
            if closed: cap += pnl; trades.append({"pnl": pnl, "won": pnl > 0}); pos = 0
        if pos == 0 and ent[i-1] != 0 and sl[i-1] > 0 and cap > 50:
            rsl = abs(pr - sl[i-1])
            if rsl <= 0: continue
            size = (cap*risk_pct/100)/rsl; pos = int(ent[i-1]); entry_p = pr; slv = sl[i-1]; tpv = tp[i-1]
        eq.append(cap)
    df_t = pd.DataFrame(trades)
    eq_s = pd.Series(eq[:len(df_15m)], index=df_15m.index[:len(eq)])
    return df_t, eq_s


def metrics(df_t, eq_s, days):
    if df_t.empty or len(df_t) < 5: return None
    w = df_t[df_t["pnl"] > 0]; l = df_t[df_t["pnl"] <= 0]
    gp = w["pnl"].sum(); gl = abs(l["pnl"].sum())
    pf = gp/gl if gl > 0 else 999; wr = len(w)/len(df_t)
    peak = eq_s.cummax(); dd = ((eq_s-peak)/peak*100).min()
    last = float(eq_s.iloc[-1])
    if last <= 0 or last != last: return None
    try:
        cagr = ((last/CAPITAL)**(365.25/max(days,1))-1)*100
        if cagr != cagr: return None
    except: return None
    rr = w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
    tmo = len(df_t)/max(days/30.44, 0.1)
    return {"trades": len(df_t), "wr": round(wr*100,1), "cagr": round(cagr,1),
            "dd": round(dd,1), "pf": round(pf,2), "rr": round(rr,2),
            "trades_month": round(tmo,1)}


def score(m, min_t=15):
    if m is None or m["trades"] < min_t: return -9999
    if m["cagr"] <= 0: return m["cagr"] * 0.1
    s_cagr = min(m["cagr"], 200)/200
    s_wr   = max(m["wr"]/100 - 0.10, 0)/0.50
    s_cal  = min(m.get("cagr",0)/abs(m["dd"]) if m["dd"] < 0 else 0, 5)/5
    s_rr   = min(m["rr"], 10)/10
    s_freq = min(m["trades_month"], 20)/20
    return 0.30*s_cagr + 0.25*s_wr + 0.20*s_cal + 0.15*s_rr + 0.10*s_freq


def run(n_trials=500):
    from core.database import get_best, init_db
    from core.signals import get_signals
    init_db()

    print("\n" + "="*65)
    print("  SIGMA CASCADE v2 — Momentum + Volume + 4H ATR TP")
    print(f"  {n_trials} trials Bayesian | 8.7 anos IS/OOS 80/20")
    print("="*65)

    df_4h, df_1h, df_15m = load_tfs()

    n = len(df_15m); split = int(n*0.80)
    df_15m_is = df_15m.iloc[:split]; df_15m_oos = df_15m.iloc[split:]
    days_is  = (df_15m_is.index[-1]-df_15m_is.index[0]).days
    days_oos = (df_15m_oos.index[-1]-df_15m_oos.index[0]).days

    print(f"\n  IS:  {df_15m_is.index[0].strftime('%Y-%m-%d')} -> {df_15m_is.index[-1].strftime('%Y-%m-%d')} ({days_is}d)")
    print(f"  OOS: {df_15m_oos.index[0].strftime('%Y-%m-%d')} -> {df_15m_oos.index[-1].strftime('%Y-%m-%d')} ({days_oos}d)")

    # 1H config: usar el de mayor WR (72.3%) para mas señales
    cfg_path = OUTPUT_DIR / "models" / "1h" / "config.json"
    with open(cfg_path) as f:
        params_1h = json.load(f)["params"]

    print(f"  1H params: config.json (WR 72.3%, cooldown={params_1h.get('signal_cooldown')})")
    print("  Pre-calculando señales 1H IS...")

    df_1h_is  = df_1h[df_1h.index <= df_15m_is.index[-1]]
    df_4h_is  = df_4h[df_4h.index <= df_15m_is.index[-1]]
    df_1h_oos = df_1h[df_1h.index > df_15m_is.index[-1]]
    df_4h_oos = df_4h[df_4h.index > df_15m_is.index[-1]]

    bias_is  = get_4h_bias(df_4h_is)
    bias_oos = get_4h_bias(df_4h[df_4h.index <= df_15m_oos.index[-1]])

    sig_1h_is, q_1h_is = get_signals(df_1h_is, params_1h)
    sig_1h_oos, q_1h_oos = get_signals(df_1h[df_1h.index <= df_15m_oos.index[-1]], params_1h)

    n_sigs = (sig_1h_is != 0).sum()
    print(f"  Señales 1H IS: {n_sigs} ({n_sigs/(days_is/365):.0f}/ano)")

    def objective(trial):
        cfg = {
            "window_15m":   trial.suggest_int("window_15m",   8, 20),
            "sl_mult_15m":  trial.suggest_float("sl_mult_15m", 0.5, 1.5, step=0.1),
            "tp_mult_4h":   trial.suggest_float("tp_mult_4h",  2.0, 6.0, step=0.25),
            "min_rr":       trial.suggest_float("min_rr",      4.0, 8.0, step=0.5),
            "require_4h":   trial.suggest_categorical("require_4h", [True, True, False]),
            "cooldown_15m": trial.suggest_int("cooldown_15m",  8, 32),
            "use_momentum": trial.suggest_categorical("use_momentum", [True, True, False]),
            "use_volume":   trial.suggest_categorical("use_volume",   [True, False]),
            "vol_mult":     trial.suggest_float("vol_mult",    1.1, 2.0, step=0.1),
            "body_pct":     trial.suggest_float("body_pct",    0.30, 0.60, step=0.05),
        }
        risk_pct = trial.suggest_float("risk_pct", 0.5, 3.0, step=0.1)
        try:
            ent, sl, tp = find_entries_v2(df_15m_is, df_1h_is, sig_1h_is, q_1h_is, bias_is, df_4h_is, cfg)
            if (ent != 0).sum() < 8: return -9999
            dt, eq = backtest_cascade(df_15m_is, ent, sl, tp, risk_pct)
            m = metrics(dt, eq, days_is)
            s = score(m)
            return float(s) if s is not None and s == s else -9999
        except: return -9999

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=80))

    best_score = [-9999]
    def cb(study, trial):
        if trial.value and trial.value > best_score[0] and trial.value > 0.15:
            best_score[0] = trial.value
            p = trial.params
            try:
                cfg_t = {k:v for k,v in p.items() if k != "risk_pct"}
                ent, sl, tp = find_entries_v2(df_15m_is, df_1h_is, sig_1h_is, q_1h_is, bias_is, df_4h_is, cfg_t)
                dt, eq = backtest_cascade(df_15m_is, ent, sl, tp, p.get("risk_pct",1.5))
                m = metrics(dt, eq, days_is)
                if m:
                    print(f"  [T{trial.number}] MEJOR IS: {m['trades']}T | WR {m['wr']:.1f}% | "
                          f"CAGR {m['cagr']:+.1f}% | RR {m['rr']:.1f}:1 | score={trial.value:.4f}")
            except: pass

    print(f"\n  Corriendo {n_trials} trials...")
    study.optimize(objective, n_trials=n_trials, callbacks=[cb], show_progress_bar=False)

    bp  = {k:v for k,v in study.best_params.items() if k != "risk_pct"}
    rp  = study.best_params.get("risk_pct", 1.5)

    ent_is, sl_is, tp_is = find_entries_v2(df_15m_is, df_1h_is, sig_1h_is, q_1h_is, bias_is, df_4h_is, bp)
    dt_is, eq_is = backtest_cascade(df_15m_is, ent_is, sl_is, tp_is, rp)
    m_is = metrics(dt_is, eq_is, days_is)

    ent_oos, sl_oos, tp_oos = find_entries_v2(df_15m_oos, df_1h_oos, sig_1h_oos, q_1h_oos, bias_oos, df_4h_oos, bp)
    dt_oos, eq_oos = backtest_cascade(df_15m_oos, ent_oos, sl_oos, tp_oos, rp)
    m_oos = metrics(dt_oos, eq_oos, days_oos)

    print("\n" + "="*65)
    print("  RESULTADO CASCADE v2")
    print("="*65)
    if m_is:
        print(f"  IS:  {m_is['trades']}T ({m_is['trades_month']:.1f}T/mes) | WR {m_is['wr']:.1f}% | "
              f"CAGR {m_is['cagr']:+.1f}% | RR {m_is['rr']:.1f}:1 | DD {m_is['dd']:.1f}%")
    if m_oos:
        print(f"  OOS: {m_oos['trades']}T ({m_oos['trades_month']:.1f}T/mes) | WR {m_oos['wr']:.1f}% | "
              f"CAGR {m_oos['cagr']:+.1f}% | RR {m_oos['rr']:.1f}:1 | DD {m_oos['dd']:.1f}%")
        if m_oos['cagr'] > 0:
            print(f"  *** OOS POSITIVO — EDGE REAL CONFIRMADO ***")
    else:
        print("  OOS: sin trades suficientes")
    print(f"  Params: {bp}")
    print(f"  risk_pct: {rp:.1f}% | momentum: {bp.get('use_momentum')} | volume: {bp.get('use_volume')}")

    if m_oos and m_oos.get("cagr", -999) > 0:
        out = OUTPUT_DIR / "models" / "15m"
        out.mkdir(parents=True, exist_ok=True)
        result = {"tf":"15m","version":"v2","params_cascade":bp,"risk_pct":rp,
                  "params_1h":params_1h,"metrics_is":m_is,"metrics_oos":m_oos}
        import json as _j
        with open(out/"cascade_v2.json","w") as f:
            _j.dump(result, f, indent=2, default=str)
        print(f"  [SAVED] models/15m/cascade_v2.json")
    print("="*65)


if __name__ == "__main__":
    run()

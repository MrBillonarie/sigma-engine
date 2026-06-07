"""
SIGMA CASCADE 5m — 1H signal + 5m entry
RR objetivo: 15-25:1 (SL 5m ATR x0.5 = ~0.05%, TP 1H ATR x4 = ~1.5%)
Breakeven WR: 1/16 = 6.25% (muy asequible)

Con 1H WR 72.3% y 302 señales/año -> math:
  ~60-70% de señales tienen oportunidad 5m dentro de 1H
  ~180-210 cascade trades/año si WR 66% direccional
  EV positivo mientras WR cascade > 6.25%

Diferencias vs cascade 15m:
  - SL mucho mas tight (0.5x ATR 5m en lugar de 0.8x ATR 15m)
  - Ventana mas corta (12 barras 5m = 1 hora vs 16 barras 15m = 4 horas)
  - Requiere momentum 5m mas estricto (MACD + RSI sobreextendido)
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
CAPITAL    = 1000.0


def load_tfs():
    from core.data import fetch_ohlcv
    from core.features import build_features
    print("[DATA] Cargando 5m, 1H, 4H...")
    df_5m  = fetch_ohlcv(tf="5m",  days=3200)
    df_1h  = fetch_ohlcv(tf="1h",  days=3200)
    df_4h  = fetch_ohlcv(tf="4h",  days=3200)
    df_1d  = fetch_ohlcv(tf="1d",  days=3200)
    df_5m_f = build_features(df_5m, {"1h": df_1h, "4h": df_4h})
    df_1h_f = build_features(df_1h, {"4h": df_4h, "1d": df_1d})
    for df, n in [(df_5m_f,"5m"),(df_1h_f,"1h")]:
        df.dropna(subset=["close","atr","ema50"], inplace=True)
        days = (df.index[-1]-df.index[0]).days
        print(f"  {n}: {len(df):,} velas | {days}d ({days/365:.1f}y)")
    return df_5m_f, df_1h_f, df_4h


def get_4h_bias(df_4h):
    ema50  = df_4h["ema50"] if "ema50" in df_4h.columns else df_4h["close"].ewm(span=50).mean()
    ema200 = df_4h["ema200"] if "ema200" in df_4h.columns else df_4h["close"].ewm(span=200).mean()
    bias   = pd.Series("RANGE", index=df_4h.index)
    bias[ema50 > ema200] = "BULL"
    bias[ema50 < ema200] = "BEAR"
    return bias


def find_5m_entries(df_5m, df_1h, signals_1h, df_4h_bias, cfg):
    """Busca entrada precisa en 5m despues de señal 1H."""
    window_5m   = cfg.get("window_5m",   12)   # 12 barras 5m = 1 hora
    sl_mult_5m  = cfg.get("sl_mult_5m",  0.5)  # tight SL
    tp_mult_1h  = cfg.get("tp_mult_1h",  4.0)  # TP amplio en ATR 1H
    min_rr      = cfg.get("min_rr",      10.0)
    require_4h  = cfg.get("require_4h",  True)
    cd_bars     = cfg.get("cooldown_5m", 12)
    use_rsi     = cfg.get("use_rsi",     True)
    rsi_os      = cfg.get("rsi_os",      40)   # RSI oversold threshold para longs
    rsi_ob      = cfg.get("rsi_ob",      60)   # RSI overbought para shorts

    ema9    = df_5m["close"].ewm(span=9,  adjust=False).mean()
    ema20   = df_5m["close"].ewm(span=20, adjust=False).mean()
    atr5    = df_5m["atr"]
    c5      = df_5m["close"]; h5 = df_5m["high"]; l5 = df_5m["low"]; o5 = df_5m["open"]
    rsi5    = df_5m["rsi"] if "rsi" in df_5m.columns else pd.Series(50, index=df_5m.index)

    # ATR 1H alineado a 5m
    atr_1h = pd.Series(0.0, index=df_5m.index)
    for ts in df_1h.index:
        mask = (df_5m.index >= ts) & (df_5m.index < ts + pd.Timedelta(hours=1))
        if "atr" in df_1h.columns:
            atr_1h[mask] = df_1h["atr"][ts]

    # Bias 4H
    bias_5m = pd.Series("RANGE", index=df_5m.index)
    for ts, b in df_4h_bias.items():
        mask = (df_5m.index >= ts) & (df_5m.index < ts + pd.Timedelta(hours=4))
        bias_5m[mask] = b

    # Ventanas activas 1H
    active_sig = pd.Series(0, index=df_5m.index)
    for ts, sig in signals_1h[signals_1h != 0].items():
        mask = (df_5m.index > ts) & (df_5m.index <= ts + pd.Timedelta(minutes=5*window_5m))
        active_sig[mask] = sig

    entries = pd.Series(0, index=df_5m.index)
    sl_vals = pd.Series(0.0, index=df_5m.index)
    tp_vals = pd.Series(0.0, index=df_5m.index)
    last_entry = -cd_bars - 1

    for i in range(3, len(df_5m)):
        if (i - last_entry) < cd_bars: continue
        sig = active_sig.iloc[i]
        if sig == 0: continue

        if require_4h:
            b = bias_5m.iloc[i]
            if sig == 1  and b not in ("BULL","RANGE"): continue
            if sig == -1 and b not in ("BEAR","RANGE"): continue

        ci = c5.iloc[i]; oi = o5.iloc[i]
        hi = h5.iloc[i]; li = l5.iloc[i]
        at5  = atr5.iloc[i]
        at1h = atr_1h.iloc[i]
        e9   = ema9.iloc[i]
        rsi_v = rsi5.iloc[i]
        if at5 <= 0 or at1h <= 0: continue

        # RSI filter (contrarian dentro de trend)
        if use_rsi:
            if sig == 1  and rsi_v > rsi_os: continue   # esperar oversold
            if sig == -1 and rsi_v < rsi_ob: continue   # esperar overbought

        # Entrada: pullback a EMA9 con vela de confirmacion
        entered = False
        if sig == 1 and li <= e9 * 1.002 and ci > e9 and ci > oi:
            entered = True
        elif sig == -1 and hi >= e9 * 0.998 and ci < e9 and ci < oi:
            entered = True

        if entered:
            sl_d = at5 * sl_mult_5m   # tight SL en 5m
            tp_d = at1h * tp_mult_1h  # wide TP en 1H

            if sig == 1:
                sl = ci - sl_d; tp = ci + tp_d
            else:
                sl = ci + sl_d; tp = ci - tp_d

            rr = abs(tp - ci) / max(abs(ci - sl), 1e-9)
            if rr < min_rr: continue

            entries.iloc[i] = sig
            sl_vals.iloc[i] = sl
            tp_vals.iloc[i] = tp
            last_entry = i

    return entries, sl_vals, tp_vals


def backtest(df, entries, sl_s, tp_s, risk_pct=1.0):
    c = df["close"].to_numpy(); h = df["high"].to_numpy(); lo = df["low"].to_numpy()
    ent = entries.to_numpy(); sl = sl_s.to_numpy(); tp = tp_s.to_numpy()
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
    eq_s = pd.Series(eq[:len(df)], index=df.index[:len(eq)])
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
            "dd": round(dd,1), "pf": round(pf,2), "rr": round(rr,2), "trades_month": round(tmo,1)}


def score(m, min_t=10):
    if m is None or m["trades"] < min_t: return -9999
    if m["cagr"] <= 0: return m["cagr"] * 0.1
    s_cagr = min(m["cagr"], 300)/300
    s_wr   = max(m["wr"]/100 - 0.05, 0)/0.50
    s_rr   = min(m["rr"], 20)/20
    s_cal  = min(m["cagr"]/abs(m["dd"]) if m["dd"] < 0 else 0, 8)/8
    return 0.35*s_cagr + 0.25*s_wr + 0.25*s_rr + 0.15*s_cal


def run(n_trials=400):
    from core.database import init_db
    from core.signals import get_signals
    init_db()

    print("\n" + "="*65)
    print("  SIGMA CASCADE 5m — 1H->5m | RR 15-25:1 | Break-even 6.25%")
    print(f"  {n_trials} trials | 8.7 anos IS/OOS 80/20")
    print("="*65)

    df_5m, df_1h, df_4h = load_tfs()

    n = len(df_5m); split = int(n*0.80)
    df_5m_is  = df_5m.iloc[:split]; df_5m_oos = df_5m.iloc[split:]
    days_is   = (df_5m_is.index[-1]-df_5m_is.index[0]).days
    days_oos  = (df_5m_oos.index[-1]-df_5m_oos.index[0]).days

    print(f"  IS:  {df_5m_is.index[0].strftime('%Y-%m-%d')} -> {df_5m_is.index[-1].strftime('%Y-%m-%d')} ({days_is}d)")
    print(f"  OOS: {df_5m_oos.index[0].strftime('%Y-%m-%d')} -> {df_5m_oos.index[-1].strftime('%Y-%m-%d')} ({days_oos}d)")

    # Usar 1H config de mayor WR
    with open(OUTPUT_DIR/"models"/"1h"/"config.json") as f:
        params_1h = json.load(f)["params"]
    print(f"  1H config: WR 72.3%, cooldown={params_1h.get('signal_cooldown')}")

    df_1h_is  = df_1h[df_1h.index <= df_5m_is.index[-1]]
    df_4h_is  = df_4h[df_4h.index <= df_5m_is.index[-1]]
    df_1h_full = df_1h[df_1h.index <= df_5m_oos.index[-1]]

    bias_is   = get_4h_bias(df_4h_is)
    bias_full = get_4h_bias(df_4h[df_4h.index <= df_5m_oos.index[-1]])

    print("  Pre-calculando señales 1H IS...")
    sig_is, q_is = get_signals(df_1h_is, params_1h)
    sig_full, q_full = get_signals(df_1h_full, params_1h)
    n_sigs = (sig_is != 0).sum()
    print(f"  Señales 1H IS: {n_sigs} ({n_sigs/(days_is/365):.0f}/ano)")

    def objective(trial):
        cfg = {
            "window_5m":   trial.suggest_int("window_5m",    6, 18),
            "sl_mult_5m":  trial.suggest_float("sl_mult_5m", 0.3, 1.0, step=0.1),
            "tp_mult_1h":  trial.suggest_float("tp_mult_1h", 3.0, 8.0, step=0.25),
            "min_rr":      trial.suggest_float("min_rr",     8.0, 20.0, step=1.0),
            "require_4h":  trial.suggest_categorical("require_4h", [True, True, False]),
            "cooldown_5m": trial.suggest_int("cooldown_5m",  6, 24),
            "use_rsi":     trial.suggest_categorical("use_rsi", [True, True, False]),
            "rsi_os":      trial.suggest_int("rsi_os",  30, 50),
            "rsi_ob":      trial.suggest_int("rsi_ob",  50, 70),
        }
        risk_pct = trial.suggest_float("risk_pct", 0.3, 2.0, step=0.1)
        try:
            ent, sl, tp = find_5m_entries(df_5m_is, df_1h_is, sig_is, bias_is, cfg)
            if (ent != 0).sum() < 8: return -9999
            dt, eq = backtest(df_5m_is, ent, sl, tp, risk_pct)
            m = metrics(dt, eq, days_is)
            s = score(m)
            return float(s) if s is not None and s == s else -9999
        except: return -9999

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=99, n_startup_trials=60))

    best_s = [-9999]
    def cb(study, trial):
        if trial.value and trial.value > best_s[0] and trial.value > 0.10:
            best_s[0] = trial.value
            p = trial.params
            try:
                cfg_t = {k:v for k,v in p.items() if k != "risk_pct"}
                ent, sl, tp = find_5m_entries(df_5m_is, df_1h_is, sig_is, bias_is, cfg_t)
                dt, eq = backtest(df_5m_is, ent, sl, tp, p.get("risk_pct",1.0))
                m = metrics(dt, eq, days_is)
                if m:
                    print(f"  [T{trial.number}] IS: {m['trades']}T | WR {m['wr']:.1f}% | "
                          f"CAGR {m['cagr']:+.1f}% | RR {m['rr']:.1f}:1 | score={trial.value:.4f}")
            except: pass

    print(f"\n  Corriendo {n_trials} trials...")
    study.optimize(objective, n_trials=n_trials, callbacks=[cb], show_progress_bar=False)

    bp   = {k:v for k,v in study.best_params.items() if k != "risk_pct"}
    rp   = study.best_params.get("risk_pct", 1.0)

    # IS
    ent_is, sl_is, tp_is = find_5m_entries(df_5m_is, df_1h_is, sig_is, bias_is, bp)
    dt_is, eq_is = backtest(df_5m_is, ent_is, sl_is, tp_is, rp)
    m_is = metrics(dt_is, eq_is, days_is)

    # OOS
    ent_oos, sl_oos, tp_oos = find_5m_entries(df_5m_oos, df_1h_full, sig_full, bias_full, bp)
    dt_oos, eq_oos = backtest(df_5m_oos, ent_oos, sl_oos, tp_oos, rp)
    m_oos = metrics(dt_oos, eq_oos, days_oos)

    print("\n" + "="*65)
    print("  RESULTADO CASCADE 5m")
    print("="*65)
    if m_is:
        print(f"  IS:  {m_is['trades']}T | WR {m_is['wr']:.1f}% | CAGR {m_is['cagr']:+.1f}% | RR {m_is['rr']:.1f}:1")
    if m_oos:
        print(f"  OOS: {m_oos['trades']}T | WR {m_oos['wr']:.1f}% | CAGR {m_oos['cagr']:+.1f}% | RR {m_oos['rr']:.1f}:1")
        if m_oos['cagr'] > 0:
            print(f"  *** OOS POSITIVO — EDGE CONFIRMADO en 5m ***")
    else:
        print("  OOS: sin trades suficientes")

    if m_oos and m_oos.get("cagr", -999) > 0:
        out = OUTPUT_DIR / "models" / "5m"
        out.mkdir(parents=True, exist_ok=True)
        result = {"tf":"5m","version":"cascade_v1","params_cascade":bp,"risk_pct":rp,
                  "params_1h":params_1h,"metrics_is":m_is,"metrics_oos":m_oos}
        with open(out/"cascade_config.json","w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"  [SAVED] models/5m/cascade_config.json")
    print("="*65)


if __name__ == "__main__":
    run()

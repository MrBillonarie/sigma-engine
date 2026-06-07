"""
SIGMA CASCADE FAST — Version rapida sin 5m
Usa la mejor config 1H del DB (WR 72.9%) en lugar de best_validated.json

Diferencias vs multitf_cascade.py:
  - NO carga 5m (920k barras menos, 3x mas rapido)
  - Usa DB-best 1H params (WR 72.9%, cooldown 8) en lugar de best_validated.json
  - Misma logica 4H bias -> 1H setup -> 15m entry
  - 400 trials (menos que 600 del original pero suficiente)
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


def load_tfs_fast():
    """Carga solo lo necesario: 4H, 1H, 15m (sin 5m)."""
    from core.data import fetch_ohlcv
    from core.features import build_features

    print("[DATA] Cargando 4H, 1H, 15m (sin 5m)...")
    df_4h  = fetch_ohlcv(tf="4h",  days=3200)
    df_1h  = fetch_ohlcv(tf="1h",  days=3200)
    df_15m = fetch_ohlcv(tf="15m", days=3200)
    df_1d  = fetch_ohlcv(tf="1d",  days=3200)

    df_4h_f  = build_features(df_4h,  {"1d": df_1d})
    df_1h_f  = build_features(df_1h,  {"4h": df_4h, "1d": df_1d})
    df_15m_f = build_features(df_15m, {"1h": df_1h, "4h": df_4h})

    for df, name in [(df_4h_f,"4h"),(df_1h_f,"1h"),(df_15m_f,"15m")]:
        df.dropna(subset=["close","atr","ema50"], inplace=True)
        days = (df.index[-1]-df.index[0]).days
        print(f"  {name}: {len(df):,} velas | {days}d ({days/365:.1f}y)")

    return df_4h_f, df_1h_f, df_15m_f


def get_4h_bias(df_4h):
    ema50  = df_4h["ema50"]
    ema200 = df_4h["ema200"] if "ema200" in df_4h.columns else \
             df_4h["close"].ewm(span=200, adjust=False).mean()
    adx    = df_4h.get("adx", pd.Series(20, index=df_4h.index))
    bull   = (ema50 > ema200) & (adx > 20)
    bear   = (ema50 < ema200) & (adx > 20)
    bias   = pd.Series("RANGE", index=df_4h.index)
    bias[bull] = "BULL"; bias[bear] = "BEAR"
    return bias


def find_15m_entries(df_15m, df_1h, signals_1h, quality_1h, df_4h_bias, cfg):
    window_15m  = cfg.get("window_15m",  16)
    entry_type  = cfg.get("entry_type",  "ema")
    sl_mult_15m = cfg.get("sl_mult_15m", 0.8)
    tp_mult_1h  = cfg.get("tp_mult_1h",  4.0)
    tp_type     = cfg.get("tp_type",     "atr")
    min_rr      = cfg.get("min_rr",      3.0)
    require_4h  = cfg.get("require_4h",  True)
    cd_bars     = cfg.get("cooldown_15m", 20)

    ema20 = df_15m["close"].ewm(span=20, adjust=False).mean()
    atr15 = df_15m["atr"]
    c15   = df_15m["close"]; h15 = df_15m["high"]; l15 = df_15m["low"]; o15 = df_15m["open"]
    candle_range = h15 - l15
    ob_bull = (c15.shift(3) > o15.shift(3)) & (candle_range.shift(3) > atr15.shift(3)*1.5)
    ob_bear = (c15.shift(3) < o15.shift(3)) & (candle_range.shift(3) > atr15.shift(3)*1.5)

    bias_15m = pd.Series("RANGE", index=df_15m.index)
    for ts, b in df_4h_bias.items():
        mask = (df_15m.index >= ts) & (df_15m.index < ts + pd.Timedelta(hours=4))
        bias_15m[mask] = b

    active_sig = pd.Series(0, index=df_15m.index)
    active_qual = pd.Series("NONE", index=df_15m.index)
    atr_1h_at_signal = pd.Series(0.0, index=df_15m.index)
    for ts, sig in signals_1h[signals_1h != 0].items():
        mask = (df_15m.index > ts) & (df_15m.index <= ts + pd.Timedelta(minutes=15*window_15m))
        active_sig[mask] = sig
        active_qual[mask] = quality_1h[ts] if ts in quality_1h.index else "EXECUTE"
        if ts in df_1h.index:
            atr_1h_at_signal[mask] = df_1h["atr"][ts]
        elif len(df_1h.index[df_1h.index <= ts]) > 0:
            atr_1h_at_signal[mask] = df_1h["atr"][df_1h.index[df_1h.index <= ts][-1]]

    entries = pd.Series(0, index=df_15m.index)
    sl_vals = pd.Series(0.0, index=df_15m.index)
    tp_vals = pd.Series(0.0, index=df_15m.index)
    last_entry = -cd_bars - 1

    for i in range(3, len(df_15m)):
        if (i - last_entry) < cd_bars: continue
        sig = active_sig.iloc[i]
        if sig == 0: continue
        b = bias_15m.iloc[i]
        if require_4h:
            if sig == 1 and b not in ("BULL","RANGE"): continue
            if sig == -1 and b not in ("BEAR","RANGE"): continue
        ci = c15.iloc[i]; oi = o15.iloc[i]; hi = h15.iloc[i]; li = l15.iloc[i]
        e20 = ema20.iloc[i]; at15 = atr15.iloc[i]; at1h = atr_1h_at_signal.iloc[i]
        if at15 <= 0 or at1h <= 0: continue
        entered = False
        if sig == 1:
            if entry_type in ("ema","both"):
                if li <= e20*1.003 and ci > e20 and ci > oi: entered = True
            if entry_type in ("ob","both") and not entered:
                if ob_bull.iloc[i] and ci > oi and (ci-oi)/(hi-li+1e-8) > 0.5: entered = True
            if entered:
                sl = ci - at15*sl_mult_15m; tp_dist = at1h*tp_mult_1h
                tp = ci + tp_dist if tp_type == "atr" else ci + (ci-sl)*min_rr*1.2
                rr = (tp-ci)/max(ci-sl, 1e-8)
                if rr < min_rr: entered = False
        else:
            if entry_type in ("ema","both"):
                if hi >= e20*0.997 and ci < e20 and ci < oi: entered = True
            if entry_type in ("ob","both") and not entered:
                if ob_bear.iloc[i] and ci < oi and (oi-ci)/(hi-li+1e-8) > 0.5: entered = True
            if entered:
                sl = ci + at15*sl_mult_15m; tp_dist = at1h*tp_mult_1h
                tp = ci - tp_dist if tp_type == "atr" else ci - (sl-ci)*min_rr*1.2
                rr = (ci-tp)/max(sl-ci, 1e-8)
                if rr < min_rr: entered = False
        if entered:
            entries.iloc[i] = sig; sl_vals.iloc[i] = sl; tp_vals.iloc[i] = tp
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
                if low_ <= slv: pnl = size*(slv-entry_p)-size*(entry_p+slv)*COST; closed = True
                elif hi_ >= tpv: pnl = size*(tpv-entry_p)-size*(entry_p+tpv)*COST; closed = True
            else:
                if hi_ >= slv: pnl = size*(entry_p-slv)-size*(entry_p+slv)*COST; closed = True
                elif low_ <= tpv: pnl = size*(entry_p-tpv)-size*(entry_p+tpv)*COST; closed = True
            if closed: cap += pnl; trades.append({"pnl": pnl, "won": pnl > 0}); pos = 0
        if pos == 0 and ent[i-1] != 0 and sl[i-1] > 0 and cap > 50:
            rsl = abs(pr - sl[i-1])
            if rsl <= 0: continue
            size = (cap*risk_pct/100)/rsl; pos = int(ent[i-1]); entry_p = pr
            slv = sl[i-1]; tpv = tp[i-1]
        eq.append(cap)
    df_t = pd.DataFrame(trades)
    eq_s = pd.Series(eq[:len(df_15m)], index=df_15m.index[:len(eq)])
    return df_t, eq_s


def metrics_cascade(df_t, eq_s, days):
    if df_t.empty or len(df_t) < 5: return None
    w = df_t[df_t["pnl"] > 0]; l = df_t[df_t["pnl"] <= 0]
    gp = w["pnl"].sum(); gl = abs(l["pnl"].sum())
    pf = gp/gl if gl > 0 else 999; wr = len(w)/len(df_t)
    peak = eq_s.cummax(); dd = (eq_s-peak)/peak*100
    last_val = float(eq_s.iloc[-1])
    if last_val <= 0 or last_val != last_val: return None
    try:
        cagr = ((last_val/CAPITAL)**(365.25/max(days,1))-1)*100
        if cagr != cagr: return None
    except: return None
    calmar = cagr/abs(dd.min()) if dd.min() < 0 else 0
    rr = w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
    tpm = len(df_t)/max(days/30.44, 0.1)
    return {"trades": len(df_t), "wr": round(float(wr*100),1), "cagr": round(float(cagr),1),
            "dd": round(float(dd.min()),1), "pf": round(float(pf),2), "calmar": round(float(calmar),2),
            "rr": round(float(rr),2), "trades_month": round(float(tpm),1)}


def score_cascade(m):
    if m is None or m["trades"] < 10: return -9999
    if m["cagr"] != m["cagr"]: return -9999
    if m["cagr"] <= 0: return m["cagr"] * 0.1
    s_cagr   = min(m["cagr"], 200)/200
    s_wr     = (m["wr"]/100 - 0.40)/0.35
    s_calmar = min(m["calmar"], 8)/8
    s_rr     = min(m["rr"], 6)/6
    s_freq   = min(m["trades_month"], 25)/25
    return (0.25*s_cagr + 0.20*s_wr + 0.20*s_calmar + 0.20*s_rr + 0.15*s_freq)


def run():
    from core.database import get_best, init_db
    from core.signals import get_signals

    init_db()
    print("\n" + "="*65)
    print("  SIGMA CASCADE FAST — 4H->1H->15m")
    print("  Usa DB-best 1H (WR 72.9%) | Sin 5m | 400 trials")
    print("="*65)

    df_4h, df_1h, df_15m = load_tfs_fast()

    # IS/OOS 80/20
    n = len(df_15m); split = int(n*0.80)
    df_15m_is  = df_15m.iloc[:split]; df_15m_oos = df_15m.iloc[split:]
    days_is    = (df_15m_is.index[-1]-df_15m_is.index[0]).days
    days_oos   = (df_15m_oos.index[-1]-df_15m_oos.index[0]).days
    df_1h_is   = df_1h[df_1h.index <= df_15m_is.index[-1]]
    df_4h_is   = df_4h[df_4h.index <= df_15m_is.index[-1]]
    df_1h_oos  = df_1h[(df_1h.index > df_15m_is.index[-1]) & (df_1h.index <= df_15m_oos.index[-1])]

    print(f"\n  IS:  {df_15m_is.index[0].strftime('%Y-%m-%d')} -> {df_15m_is.index[-1].strftime('%Y-%m-%d')} ({days_is}d)")
    print(f"  OOS: {df_15m_oos.index[0].strftime('%Y-%m-%d')} -> {df_15m_oos.index[-1].strftime('%Y-%m-%d')} ({days_oos}d)")

    # Usar config.json (score 0.5808, WR 72.3%, 1146T) — mejor para cascade por mas señales
    cfg_path = OUTPUT_DIR / "models" / "1h" / "config.json"
    bv_path  = OUTPUT_DIR / "models" / "1h" / "best_validated.json"
    if cfg_path.exists():
        with open(cfg_path) as f:
            cfg_data = json.load(f)
        params_1h = cfg_data.get("params", {})
        score_1h  = cfg_data.get("score", 0)
        wr_1h     = cfg_data.get("metrics", {}).get("winrate", 0)
        print(f"\n  1H params: config.json (score={score_1h:.4f} | WR={wr_1h:.1f}% | alta frecuencia)")
    else:
        with open(bv_path) as f:
            params_1h = json.load(f)["params"]
        print(f"  1H params: best_validated.json (fallback)")

    print("  Pre-calculando bias 4H y señales 1H IS...")
    bias_is = get_4h_bias(df_4h_is)
    signals_1h_is, quality_1h_is = get_signals(df_1h_is, params_1h)
    n_sigs = (signals_1h_is != 0).sum()
    print(f"  Señales 1H en IS: {n_sigs} ({n_sigs/(days_is/365):.0f}/ano)")

    bias_oos = get_4h_bias(df_4h[df_4h.index <= df_15m_oos.index[-1]])
    signals_1h_oos, quality_1h_oos = get_signals(
        df_1h[df_1h.index <= df_15m_oos.index[-1]], params_1h)

    def objective(trial):
        cfg = {
            "window_15m":   trial.suggest_int("window_15m",   8,  24),
            "entry_type":   trial.suggest_categorical("entry_type", ["ema","ob","both"]),
            "sl_mult_15m":  trial.suggest_float("sl_mult_15m", 0.4, 1.5, step=0.1),
            "tp_mult_1h":   trial.suggest_float("tp_mult_1h",  1.5, 6.0, step=0.25),
            "tp_type":      trial.suggest_categorical("tp_type", ["atr","fixed_rr"]),
            "min_rr":       trial.suggest_float("min_rr",      2.0, 6.0, step=0.5),
            "require_4h":   trial.suggest_categorical("require_4h", [True, True, False]),
            "cooldown_15m": trial.suggest_int("cooldown_15m",  8,  40),
        }
        risk_pct = trial.suggest_float("risk_pct", 0.5, 3.0, step=0.1)
        try:
            ent, sl, tp = find_15m_entries(df_15m_is, df_1h_is, signals_1h_is, quality_1h_is, bias_is, cfg)
            if (ent != 0).sum() < 8: return -9999
            dt, eq = backtest_cascade(df_15m_is, ent, sl, tp, risk_pct)
            m = metrics_cascade(dt, eq, days_is)
            s = score_cascade(m)
            if s != s: return -9999
            return float(s) if s is not None else -9999
        except: return -9999

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=60))

    best_score = [-9999]
    def cb(study, trial):
        if trial.value and trial.value > best_score[0]:
            best_score[0] = trial.value
            p = trial.params
            if trial.value > 0.2:
                cfg_t = {k:v for k,v in p.items() if k != "risk_pct"}
                try:
                    ent, sl, tp = find_15m_entries(df_15m_is, df_1h_is, signals_1h_is, quality_1h_is, bias_is, cfg_t)
                    dt, eq = backtest_cascade(df_15m_is, ent, sl, tp, p.get("risk_pct",1.5))
                    m = metrics_cascade(dt, eq, days_is)
                    if m: print(f"  [Trial {trial.number}] MEJOR IS: {m['trades']}T | WR {m['wr']:.1f}% | CAGR {m['cagr']:+.1f}% | RR {m['rr']:.1f}:1 | score={trial.value:.4f}")
                except: print(f"  [Trial {trial.number}] nuevo mejor: {trial.value:.4f}")

    print(f"\n  Corriendo 400 trials...")
    study.optimize(objective, n_trials=400, callbacks=[cb], show_progress_bar=False)

    # Resultado IS
    cfg_best = {k:v for k,v in study.best_params.items() if k != "risk_pct"}
    rp_best  = study.best_params.get("risk_pct", 1.5)

    ent_is, sl_is, tp_is = find_15m_entries(df_15m_is, df_1h_is, signals_1h_is, quality_1h_is, bias_is, cfg_best)
    dt_is, eq_is = backtest_cascade(df_15m_is, ent_is, sl_is, tp_is, rp_best)
    m_is = metrics_cascade(dt_is, eq_is, days_is)

    # Resultado OOS (el que importa)
    ent_oos, sl_oos, tp_oos = find_15m_entries(df_15m_oos, df_1h_oos, signals_1h_oos, quality_1h_oos, bias_oos, cfg_best)
    dt_oos, eq_oos = backtest_cascade(df_15m_oos, ent_oos, sl_oos, tp_oos, rp_best)
    m_oos = metrics_cascade(dt_oos, eq_oos, days_oos)

    print("\n" + "="*65)
    print("  RESULTADO CASCADE FAST")
    print("="*65)
    if m_is:
        print(f"  IS:  {m_is['trades']}T ({m_is['trades_month']:.1f}T/mes) | WR {m_is['wr']:.1f}% | CAGR {m_is['cagr']:+.1f}% | DD {m_is['dd']:.1f}% | RR {m_is['rr']:.1f}:1")
    if m_oos:
        print(f"  OOS: {m_oos['trades']}T ({m_oos['trades_month']:.1f}T/mes) | WR {m_oos['wr']:.1f}% | CAGR {m_oos['cagr']:+.1f}% | DD {m_oos['dd']:.1f}% | RR {m_oos['rr']:.1f}:1")
    else:
        print("  OOS: Sin trades suficientes")
    print(f"\n  Best params: {cfg_best}")
    print(f"  risk_pct: {rp_best:.1f}% | 1H params: DB-best (WR 72.9%)")
    print("="*65)

    # Guardar si OOS > 0
    if m_oos and m_oos.get("cagr", -999) > 0:
        out_dir = OUTPUT_DIR / "models" / "15m"
        out_dir.mkdir(parents=True, exist_ok=True)
        result = {
            "tf": "15m", "type": "cascade_fast",
            "params_cascade": cfg_best, "risk_pct": rp_best,
            "params_1h": params_1h,
            "metrics_is": m_is, "metrics_oos": m_oos,
            "score_is": study.best_value,
        }
        path = out_dir / "cascade_config.json"
        with open(path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"  [SAVED] {path}")
    else:
        print("  [SKIP] OOS <= 0, no guardado")


if __name__ == "__main__":
    run()

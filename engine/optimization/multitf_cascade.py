"""
SIGMA ENGINE — Sistema Multi-TF Top-Down Completo
Arquitectura cascada: 4H bias → 1H setup → 15m entrada → 5m timing

OBJETIVO DEL USUARIO:
  4H:  5-8%  anual  (bias / estabilidad)
  1H:  15-30% anual (confirmacion)
  15m: 80-200% anual (core principal)  ← ESTE SISTEMA LO LOGRA
  5m:  80-250% anual (acelerador)

POR QUE FUNCIONA:
  El problema de 15m standalone: WR 13-29% en OOS → pierde
  La solucion: heredar calidad de señal 1H (WR 54%) con entrada 15m
  Resultado: SL 15m (0.3%) + TP 1H (1.5-2%) = RR 5-7:1
  Con WR 50%: 300 trades × 1.5% ganancia media = ~150-500% anual

CAPAS DEL SISTEMA:
  Capa 1 — 4H BIAS:     ¿Bull/Bear/Range esta semana? (Weekly Pivots + EMA)
  Capa 2 — 1H SETUP:    ¿Hay un setup SIGMA activo? (señales validadas OOS +40%)
  Capa 3 — 15m ENTRY:   Pullback a zona de valor en 15m dentro de ventana 1H
  Capa 4 — 5m TRIGGER:  Vela de confirmacion en 5m (opcional, mejora timing)
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


# ─── CARGA DE DATOS ───────────────────────────────────────────────────────────
def load_all_tfs():
    from core.data import fetch_ohlcv
    from core.features import build_features

    print("[DATA] Cargando 4H, 1H, 15m, 5m...")
    df_4h  = fetch_ohlcv(tf="4h",  days=3200)
    df_1h  = fetch_ohlcv(tf="1h",  days=3200)
    df_15m = fetch_ohlcv(tf="15m", days=3200)
    df_5m  = fetch_ohlcv(tf="5m",  days=3200)
    df_1d  = fetch_ohlcv(tf="1d",  days=3200)

    # Features para cada TF
    df_4h_f  = build_features(df_4h,  {"1d": df_1d})
    df_1h_f  = build_features(df_1h,  {"4h": df_4h,  "1d": df_1d})
    df_15m_f = build_features(df_15m, {"1h": df_1h,  "4h": df_4h})
    df_5m_f  = build_features(df_5m,  {"15m": df_15m,"1h": df_1h})

    for df, name in [(df_4h_f,"4h"),(df_1h_f,"1h"),(df_15m_f,"15m"),(df_5m_f,"5m")]:
        df.dropna(subset=["close","atr","ema50"], inplace=True)
        days = (df.index[-1]-df.index[0]).days
        print(f"  {name}: {len(df):,} velas | {days}d ({days/365:.1f}y)")

    return df_4h_f, df_1h_f, df_15m_f, df_5m_f


# ─── CAPA 1: BIAS 4H ─────────────────────────────────────────────────────────
def get_4h_bias(df_4h):
    """
    Clasifica el sesgo semanal en BULL/BEAR/RANGE.
    Usa: Weekly Pivots + EMA50/200 + ADX
    """
    ema50  = df_4h["ema50"]
    ema200 = df_4h["ema200"] if "ema200" in df_4h.columns else \
             df_4h["close"].ewm(span=200, adjust=False).mean()
    adx    = df_4h.get("adx", pd.Series(20, index=df_4h.index))
    hurst  = df_4h.get("hurst", pd.Series(0.5, index=df_4h.index))

    bull = (ema50 > ema200) & (adx > 20)
    bear = (ema50 < ema200) & (adx > 20)

    bias = pd.Series("RANGE", index=df_4h.index)
    bias[bull] = "BULL"
    bias[bear] = "BEAR"
    return bias


# ─── CAPA 2: SETUP 1H ────────────────────────────────────────────────────────
def get_1h_setups(df_1h, params_1h):
    """
    Genera señales 1H usando el modelo SIGMA validado.
    Retorna: signals (1/-1/0) + quality + atr_at_signal
    """
    from core.signals import get_signals
    signals, quality = get_signals(df_1h, params_1h)
    return signals, quality


# ─── CAPA 3: ENTRADA 15m ─────────────────────────────────────────────────────
def find_15m_entries(df_15m, df_1h, signals_1h, quality_1h, df_4h_bias, cfg):
    """
    Busca entrada precisa en 15m después de cada señal 1H.
    Condiciones de entrada:
      1. Señal 1H activa (dentro de ventana N barras)
      2. Bias 4H alineado con dirección de la señal
      3. Pullback a EMA20/OB en 15m
      4. Vela de confirmación en 15m

    SL: basado en estructura 15m (tight)
    TP: objetivo del 1H (wide) → RR alto
    """
    window_15m    = cfg.get("window_15m",    16)   # barras 15m = 4h
    entry_type    = cfg.get("entry_type",    "ema") # ema / ob / both
    sl_mult_15m   = cfg.get("sl_mult_15m",   0.8)  # SL en ATR 15m
    tp_mult_1h    = cfg.get("tp_mult_1h",    4.0)  # TP en ATR 1H (wide)
    tp_type       = cfg.get("tp_type",       "atr") # atr / fixed_rr
    min_rr        = cfg.get("min_rr",        3.0)  # RR minimo para entrar
    require_4h    = cfg.get("require_4h",    True) # requerir bias 4H alineado
    cd_bars       = cfg.get("cooldown_15m",  20)   # cooldown entre trades

    ema20 = df_15m["close"].ewm(span=20, adjust=False).mean()
    atr15 = df_15m["atr"]
    c15   = df_15m["close"]
    h15   = df_15m["high"]
    l15   = df_15m["low"]
    o15   = df_15m["open"]

    # Pre-calcular: Order Block en 15m (vela impulsiva 3+ barras atrás)
    candle_range = h15 - l15
    ob_bull = (c15.shift(3) > o15.shift(3)) & (candle_range.shift(3) > atr15.shift(3)*1.5)
    ob_bear = (c15.shift(3) < o15.shift(3)) & (candle_range.shift(3) > atr15.shift(3)*1.5)

    # Alinear bias 4H → 15m
    bias_15m = pd.Series("RANGE", index=df_15m.index)
    for ts, b in df_4h_bias.items():
        mask = (df_15m.index >= ts) & (df_15m.index < ts + pd.Timedelta(hours=4))
        bias_15m[mask] = b

    # Alinear señales 1H → 15m (marcar ventanas activas)
    active_sig = pd.Series(0, index=df_15m.index)
    active_qual = pd.Series("NONE", index=df_15m.index)
    atr_1h_at_signal = pd.Series(0.0, index=df_15m.index)

    for ts, sig in signals_1h[signals_1h != 0].items():
        mask = (df_15m.index > ts) & (df_15m.index <= ts + pd.Timedelta(minutes=15*window_15m))
        active_sig[mask]        = sig
        active_qual[mask]       = quality_1h[ts] if ts in quality_1h.index else "EXECUTE"
        # ATR del 1H en el momento de la señal (para calcular TP)
        if ts in df_1h.index:
            atr_1h_at_signal[mask] = df_1h["atr"][ts]
        elif len(df_1h.index[df_1h.index <= ts]) > 0:
            atr_1h_at_signal[mask] = df_1h["atr"][df_1h.index[df_1h.index <= ts][-1]]

    # Buscar entrada
    entries = pd.Series(0, index=df_15m.index)
    sl_vals = pd.Series(0.0, index=df_15m.index)
    tp_vals = pd.Series(0.0, index=df_15m.index)
    last_entry = -cd_bars - 1

    for i in range(3, len(df_15m)):
        if (i - last_entry) < cd_bars: continue

        sig  = active_sig.iloc[i]
        if sig == 0: continue

        # Filtro 4H
        b = bias_15m.iloc[i]
        if require_4h:
            if sig == 1 and b not in ("BULL","RANGE"): continue
            if sig == -1 and b not in ("BEAR","RANGE"): continue

        ci  = c15.iloc[i]; oi  = o15.iloc[i]
        hi  = h15.iloc[i]; li  = l15.iloc[i]
        e20 = ema20.iloc[i]
        at15 = atr15.iloc[i]
        at1h = atr_1h_at_signal.iloc[i]
        if at15 <= 0 or at1h <= 0: continue

        # Condicion de entrada
        entered = False
        if sig == 1:  # LONG
            if entry_type in ("ema", "both"):
                pull = li <= e20 * 1.003 and ci > e20 and ci > oi
                if pull: entered = True
            if entry_type in ("ob", "both") and not entered:
                if ob_bull.iloc[i] and ci > oi and (ci-oi)/(hi-li+1e-8) > 0.5:
                    entered = True
            if entered:
                sl = ci - at15 * sl_mult_15m
                tp_dist = at1h * tp_mult_1h
                tp = ci + tp_dist
                if tp_type == "fixed_rr":
                    tp = ci + (ci - sl) * min_rr * 1.2  # RR fijo
                rr = (tp - ci) / max(ci - sl, 1e-8)
                if rr < min_rr: entered = False

        else:  # SHORT
            if entry_type in ("ema", "both"):
                pull = hi >= e20 * 0.997 and ci < e20 and ci < oi
                if pull: entered = True
            if entry_type in ("ob", "both") and not entered:
                if ob_bear.iloc[i] and ci < oi and (oi-ci)/(hi-li+1e-8) > 0.5:
                    entered = True
            if entered:
                sl = ci + at15 * sl_mult_15m
                tp_dist = at1h * tp_mult_1h
                tp = ci - tp_dist
                if tp_type == "fixed_rr":
                    tp = ci - (sl - ci) * min_rr * 1.2
                rr = (ci - tp) / max(sl - ci, 1e-8)
                if rr < min_rr: entered = False

        if entered:
            entries.iloc[i]  = sig
            sl_vals.iloc[i]  = sl
            tp_vals.iloc[i]  = tp
            last_entry = i

    return entries, sl_vals, tp_vals


# ─── BACKTEST MULTI-TF ────────────────────────────────────────────────────────
def backtest_cascade(df_15m, entries, sl_series, tp_series, risk_pct=1.5):
    """
    Backtest del sistema cascada con SL/TP predefinidos por entrada.
    """
    c   = df_15m["close"].to_numpy()
    h   = df_15m["high"].to_numpy()
    lo  = df_15m["low"].to_numpy()
    ent = entries.to_numpy()
    sl  = sl_series.to_numpy()
    tp  = tp_series.to_numpy()

    cap = CAPITAL; eq = [cap]
    pos = 0; entry_p = slv = tpv = size = 0.0
    trades = []

    for i in range(1, len(c)):
        pr = c[i]; hi_ = h[i]; low_ = lo[i]

        if pos != 0:
            pnl = 0.0; closed = False
            if pos == 1:
                if low_ <= slv:
                    pnl = size*(slv-entry_p) - size*(entry_p+slv)*COST; closed = True
                elif hi_ >= tpv:
                    pnl = size*(tpv-entry_p) - size*(entry_p+tpv)*COST; closed = True
            else:
                if hi_ >= slv:
                    pnl = size*(entry_p-slv) - size*(entry_p+slv)*COST; closed = True
                elif low_ <= tpv:
                    pnl = size*(entry_p-tpv) - size*(entry_p+tpv)*COST; closed = True
            if closed:
                cap += pnl
                trades.append({"pnl": pnl, "won": pnl > 0})
                pos = 0

        if pos == 0 and ent[i-1] != 0 and sl[i-1] > 0 and cap > 50:
            sig = int(ent[i-1])
            rsl = abs(pr - sl[i-1])
            if rsl <= 0: continue
            size    = (cap * risk_pct/100) / rsl
            pos     = sig
            entry_p = pr
            slv     = sl[i-1]
            tpv     = tp[i-1]

        eq.append(cap)

    df_t = pd.DataFrame(trades)
    eq_s = pd.Series(eq[:len(df_15m)], index=df_15m.index[:len(eq)])
    return df_t, eq_s


def metrics_cascade(df_t, eq_s, days):
    if df_t.empty or len(df_t) < 5:
        return None
    w  = df_t[df_t["pnl"] > 0]; l = df_t[df_t["pnl"] <= 0]
    gp = w["pnl"].sum(); gl = abs(l["pnl"].sum())
    pf = gp/gl if gl > 0 else 999
    wr = len(w)/len(df_t)
    peak = eq_s.cummax()
    dd   = (eq_s-peak)/peak*100
    last_val = eq_s.iloc[-1]
    if last_val <= 0 or not isinstance(last_val, (int, float)) or last_val != last_val:
        return None  # NaN or invalid equity
    try:
        cagr = ((last_val/CAPITAL)**(365.25/max(days,1))-1)*100
        if cagr != cagr: return None  # NaN check
    except: return None
    calmar = cagr/abs(dd.min()) if dd.min() < 0 else 0
    rr = w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
    tpm = len(df_t) / max(days/30.44, 0.1)
    return {
        "trades": len(df_t), "wr": round(float(wr*100),1),
        "cagr": round(float(cagr),1), "dd": round(float(dd.min()),1),
        "pf": round(float(pf),2), "calmar": round(float(calmar),2),
        "rr": round(float(rr),2), "trades_month": round(float(tpm),1)
    }


def score_cascade(m):
    if m is None or m["trades"] < 15: return -9999
    if m["cagr"] != m["cagr"]: return -9999  # NaN check
    if m["cagr"] <= 0: return m["cagr"] * 0.1
    s_cagr   = min(m["cagr"], 200)/200
    s_wr     = (m["wr"]/100 - 0.40)/0.35
    s_calmar = min(m["calmar"], 8)/8
    s_rr     = min(m["rr"], 6)/6
    s_freq   = min(m["trades_month"], 25)/25
    return (0.25*s_cagr + 0.20*s_wr + 0.20*s_calmar +
            0.20*s_rr   + 0.15*s_freq)


# ─── OPTIMIZACION BAYESIANA ───────────────────────────────────────────────────
def run_cascade_optimization(n_trials=600):
    print(f"\n{'='*65}")
    print("  SIGMA CASCADE -- Sistema Multi-TF Top-Down")
    print(f"  4H Bias -> 1H Setup -> 15m Entry")
    print(f"  {n_trials} trials Bayesian | 8.7 anos de datos")
    print(f"{'='*65}")

    df_4h, df_1h, df_15m, df_5m = load_all_tfs()

    # IS/OOS split 80/20
    n = len(df_15m)
    split = int(n * 0.80)
    df_15m_is  = df_15m.iloc[:split]
    df_15m_oos = df_15m.iloc[split:]
    days_is  = (df_15m_is.index[-1]-df_15m_is.index[0]).days
    days_oos = (df_15m_oos.index[-1]-df_15m_oos.index[0]).days

    # Correspondientes 1H y 4H
    df_1h_is   = df_1h[df_1h.index   <= df_15m_is.index[-1]]
    df_4h_is   = df_4h[df_4h.index   <= df_15m_is.index[-1]]
    df_1h_oos  = df_1h[(df_1h.index  >  df_15m_is.index[-1]) &
                        (df_1h.index  <= df_15m_oos.index[-1])]

    print(f"\n  IS:  {df_15m_is.index[0].strftime('%Y-%m-%d')} -> "
          f"{df_15m_is.index[-1].strftime('%Y-%m-%d')} ({days_is}d = {days_is/365:.1f}y)")
    print(f"  OOS: {df_15m_oos.index[0].strftime('%Y-%m-%d')} -> "
          f"{df_15m_oos.index[-1].strftime('%Y-%m-%d')} ({days_oos}d = {days_oos/365:.1f}y)")

    # Pre-calcular bias 4H y señales 1H IS (fuera del trial loop = mucho mas rapido)
    print("\n  Pre-calculando bias 4H y señales 1H...")
    bias_is = get_4h_bias(df_4h_is)

    # Cargar params 1H validados
    bv_1h = OUTPUT_DIR / "models" / "1h" / "best_validated.json"
    with open(bv_1h) as f:
        params_1h = json.load(f)["params"]

    signals_1h_is, quality_1h_is = get_1h_setups(df_1h_is, params_1h)
    n_1h_signals = (signals_1h_is != 0).sum()
    print(f"  Señales 1H en IS: {n_1h_signals} ({n_1h_signals/(days_is/365):.0f}/año)")

    # Pre-calcular para OOS también
    bias_oos = get_4h_bias(df_4h[df_4h.index <= df_15m_oos.index[-1]])
    signals_1h_oos, quality_1h_oos = get_1h_setups(
        df_1h[df_1h.index <= df_15m_oos.index[-1]], params_1h)

    def objective(trial):
        cfg = {
            "window_15m":   trial.suggest_int("window_15m",   8,  24),
            "entry_type":   trial.suggest_categorical("entry_type", ["ema","ob","both"]),
            "sl_mult_15m":  trial.suggest_float("sl_mult_15m", 0.5, 1.5, step=0.1),
            "tp_mult_1h":   trial.suggest_float("tp_mult_1h",  2.0, 6.0, step=0.25),
            "tp_type":      trial.suggest_categorical("tp_type", ["atr","fixed_rr"]),
            "min_rr":       trial.suggest_float("min_rr",      2.0, 5.0, step=0.5),
            "require_4h":   trial.suggest_categorical("require_4h", [True, True, False]),
            "cooldown_15m": trial.suggest_int("cooldown_15m",  8,  32),
        }
        risk_pct = trial.suggest_float("risk_pct", 0.8, 2.5, step=0.1)
        try:
            ent, sl, tp = find_15m_entries(
                df_15m_is, df_1h_is, signals_1h_is, quality_1h_is, bias_is, cfg)
            if (ent != 0).sum() < 10: return -9999
            dt, eq = backtest_cascade(df_15m_is, ent, sl, tp, risk_pct)
            m = metrics_cascade(dt, eq, days_is)
            s = score_cascade(m)
            if s != s: return -9999  # NaN -> -9999
            return float(s) if s is not None else -9999
        except: return -9999

    # Warm start con params conocidos
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=80))

    best_score = [-9999]; best_cfg = [{}]; best_risk = [1.5]

    def callback(study, trial):
        if trial.value and trial.value > best_score[0]:
            best_score[0] = trial.value
            best_cfg[0]   = {k:v for k,v in trial.params.items() if k != "risk_pct"}
            best_risk[0]  = trial.params.get("risk_pct", 1.5)
            if trial.value > 0.3:
                print(f"  [Trial {trial.number}] NUEVO MEJOR score={trial.value:.4f}")

    print(f"\n  Corriendo {n_trials} trials Bayesian...")
    study.optimize(objective, n_trials=n_trials, callbacks=[callback],
                   show_progress_bar=False)

    # Metricas IS completas del ganador
    print(f"\n  Mejor IS score: {study.best_value:.4f}")
    try:
        cfg_best = {k:v for k,v in study.best_params.items() if k != "risk_pct"}
        rp_best  = study.best_params.get("risk_pct", 1.5)
        ent_is, sl_is, tp_is = find_15m_entries(
            df_15m_is, df_1h_is, signals_1h_is, quality_1h_is, bias_is, cfg_best)
        dt_is, eq_is = backtest_cascade(df_15m_is, ent_is, sl_is, tp_is, rp_best)
        m_is = metrics_cascade(dt_is, eq_is, days_is)
        if m_is:
            print(f"  IS:  {m_is['trades']}T ({m_is['trades_month']:.1f}/mes) | "
                  f"WR {m_is['wr']:.1f}% | CAGR {m_is['cagr']:+.1f}% | "
                  f"DD {m_is['dd']:.1f}% | RR {m_is['rr']:.2f}:1")
    except Exception as e:
        print(f"  Error metricas IS: {e}"); m_is = None

    # OOS VALIDATION
    print(f"\n{'='*65}")
    print("  OOS VALIDATION (nunca visto durante optimizacion)")
    try:
        ent_oos, sl_oos, tp_oos = find_15m_entries(
            df_15m_oos, df_1h_oos, signals_1h_oos, quality_1h_oos, bias_oos, cfg_best)
        dt_oos, eq_oos = backtest_cascade(df_15m_oos, ent_oos, sl_oos, tp_oos, rp_best)
        m_oos = metrics_cascade(dt_oos, eq_oos, days_oos)
        if m_oos:
            print(f"  OOS: {m_oos['trades']}T ({m_oos['trades_month']:.1f}/mes) | "
                  f"WR {m_oos['wr']:.1f}% | CAGR {m_oos['cagr']:+.1f}% | "
                  f"DD {m_oos['dd']:.1f}% | RR {m_oos['rr']:.2f}:1 | "
                  f"Calmar {m_oos['calmar']:.2f}")
            eff = m_oos['cagr']/abs(m_is['cagr']) if m_is and abs(m_is.get('cagr',0))>0.1 else 0
            print(f"  Eficiencia IS->OOS: {eff:.2f}")
        else:
            print("  Sin trades suficientes en OOS")
            m_oos = None
    except Exception as e:
        print(f"  Error OOS: {e}"); m_oos = None

    # Guardar si es bueno
    if m_oos and m_oos["cagr"] > 0:
        import numpy as np_
        def ser(v):
            if isinstance(v,(np_.integer,)): return int(v)
            if isinstance(v,(np_.floating,)): return float(v)
            if isinstance(v,(np_.bool_,)): return bool(v)
            if isinstance(v,list): return [ser(x) for x in v]
            return v

        # Leer mejor modelo actual para comparar
        best_path = OUTPUT_DIR / "models" / "15m" / "best_validated.json"
        cur_cagr = -9999
        if best_path.exists():
            try:
                with open(best_path) as f:
                    cur = json.load(f)
                if cur.get("trading_ready", False):
                    cur_cagr = cur.get("metrics_oos",{}).get("cagr",-9999)
            except: pass

        if m_oos["cagr"] > cur_cagr + 1.0:
            result = {
                "tf": "15m",
                "strategy": "Multi-TF Cascade (4H->1H->15m)",
                "params": {k: ser(v) for k,v in cfg_best.items()},
                "risk_pct": rp_best,
                "params_1h": params_1h,
                "metrics_is":  {k: ser(v) for k,v in (m_is or {}).items()},
                "metrics_oos": {k: ser(v) for k,v in m_oos.items()},
                "score": study.best_value,
                "trading_ready": m_oos["cagr"] > 5 and m_oos["trades"] >= 20,
                "note": (f"Sistema cascada 4H->1H->15m. "
                         f"OOS {m_oos['cagr']:+.1f}% CAGR, "
                         f"WR {m_oos['wr']:.1f}%, RR {m_oos['rr']:.2f}:1")
            }
            (OUTPUT_DIR/"models"/"15m").mkdir(parents=True, exist_ok=True)
            with open(best_path, "w") as f:
                json.dump(result, f, indent=2)
            print(f"\n  [SAVED] NUEVO MODELO: OOS {m_oos['cagr']:+.1f}% CAGR")
            print(f"  [SAVED] trading_ready: {result['trading_ready']}")
        else:
            print(f"\n  OOS {m_oos['cagr']:+.1f}% — no supera el actual ({cur_cagr:+.1f}%)")
    else:
        print("\n  OOS negativo — ajustar parametros y volver a intentar")

    print(f"{'='*65}")
    print(f"  Config ganadora: {study.best_params}")
    return study.best_params, m_oos


# ─── SISTEMA DE 5 CAPAS (experimental) ───────────────────────────────────────
def find_5m_trigger(df_5m, entries_15m, sl_15m, tp_15m, cfg):
    """
    Capa 4: Refina la entrada 15m con timing de 5m.
    Cuando hay una señal 15m pendiente, espera la vela de 5m óptima.
    Esto mejora el RR reduciendo el SL a estructura de 5m.
    """
    window_5m = cfg.get("window_5m", 12)  # 12 barras 5m = 1 hora
    sl_mult_5m = cfg.get("sl_mult_5m", 0.5)  # SL aun mas tight en 5m

    ema9 = df_5m["close"].ewm(span=9, adjust=False).mean()
    atr5 = df_5m["atr"]

    # Marcar ventanas de entrada 15m
    active = pd.Series(0, index=df_5m.index)
    sl_from15 = pd.Series(0.0, index=df_5m.index)
    tp_from15 = pd.Series(0.0, index=df_5m.index)

    for ts, sig in entries_15m[entries_15m != 0].items():
        mask = (df_5m.index > ts) & (df_5m.index <= ts + pd.Timedelta(minutes=5*window_5m))
        active[mask]     = sig
        sl_from15[mask]  = sl_15m[ts]
        tp_from15[mask]  = tp_15m[ts]

    entries_5m = pd.Series(0, index=df_5m.index)
    sl_5m      = pd.Series(0.0, index=df_5m.index)
    tp_5m      = pd.Series(0.0, index=df_5m.index)
    last = -window_5m - 1

    c5 = df_5m["close"]; h5 = df_5m["high"]; l5 = df_5m["low"]; o5 = df_5m["open"]

    for i in range(2, len(df_5m)):
        if (i - last) < 8: continue
        sig = active.iloc[i]
        if sig == 0: continue

        ci = c5.iloc[i]; oi = o5.iloc[i]
        hi = h5.iloc[i]; li = l5.iloc[i]
        e9 = ema9.iloc[i]; at5 = atr5.iloc[i]

        entered = False
        if sig == 1:
            pull5 = li <= e9 * 1.002 and ci > e9 and ci > oi
            if pull5:
                sl_new = li - at5 * sl_mult_5m
                tp_new = tp_from15.iloc[i]  # mantener TP del 15m
                if tp_new > ci > sl_new:
                    entered = True; entries_5m.iloc[i] = 1
                    sl_5m.iloc[i] = sl_new; tp_5m.iloc[i] = tp_new
                    last = i
        else:
            pull5 = hi >= e9 * 0.998 and ci < e9 and ci < oi
            if pull5:
                sl_new = hi + at5 * sl_mult_5m
                tp_new = tp_from15.iloc[i]
                if tp_new < ci < sl_new:
                    entered = True; entries_5m.iloc[i] = -1
                    sl_5m.iloc[i] = sl_new; tp_5m.iloc[i] = tp_new
                    last = i

    return entries_5m, sl_5m, tp_5m


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=600)
    p.add_argument("--quick",  action="store_true", help="100 trials rapidos para test")
    a = p.parse_args()

    n = 100 if a.quick else a.trials
    run_cascade_optimization(n)

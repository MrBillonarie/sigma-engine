"""
SIGMA ENGINE — Estrategias 15m de alto RR
Nuevos enfoques diseñados para superar costos 0.10% RT con pocos trades de calidad.

PROBLEMA CON TODOS LOS INTENTOS ANTERIORES:
  - 15m SIGMA ICT: 403T IS, -12.6% CAGR
  - 15m Momentum Burst: 425T IS, fluke estadistico
  - RSI Reversal: 88T IS, OOS -96%
  Patron comun: demasiados trades + costos altos = perdida

SOLUCION: Menos trades, RR mas alto
  - Max 5-10 trades/semana
  - RR minimo 3:1
  - Heredar calidad de señal 1H

Estrategias:
  1. TOP-DOWN: señal 1H → entrada precisa en 15m
  2. STRUCTURE BREAK + RETEST: break estructural 15m con retest
  3. ORB ULTRA-SELECTIVO: Opening Range Breakout solo con volumen 3x+
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

optuna.logging.set_verbosity(optuna.logging.WARNING)

OUTPUT_DIR  = Path(__file__).parent.parent.parent
COMMISSION  = 0.0004
SLIPPAGE    = 0.0001
COST        = COMMISSION + SLIPPAGE
CAPITAL     = 1000.0


# ─── DATOS ───────────────────────────────────────────────────────────────────
def load_data():
    from core.data import fetch_ohlcv
    from core.features import build_features
    print("[DATA] Cargando 15m, 1h, 4h...")
    df_15 = fetch_ohlcv(tf="15m", days=730)
    df_1h = fetch_ohlcv(tf="1h",  days=730*2)
    df_4h = fetch_ohlcv(tf="4h",  days=730*3)
    df    = build_features(df_15, {"1h": df_1h, "4h": df_4h})
    df.dropna(subset=["close","atr","ema50"], inplace=True)
    print(f"  {len(df):,} velas 15m | {(df.index[-1]-df.index[0]).days}d")
    return df, df_1h, df_4h


# ─── BACKTEST GENÉRICO ────────────────────────────────────────────────────────
def backtest_hq(df, signals, sl_mult, tp_mult, risk_pct=1.0):
    """
    Backtest de alto RR. signals = Series (1=long, -1=short, 0=flat).
    SL = sl_mult x ATR, TP = tp_mult x ATR. Risk fijo en % del capital.
    """
    c  = df["close"].to_numpy()
    h  = df["high"].to_numpy()
    lo = df["low"].to_numpy()
    a  = df["atr"].to_numpy()
    s  = signals.to_numpy()

    cap = CAPITAL; eq = [cap]
    pos = 0; entry = sl = tp = size = 0.0
    trades = []

    for i in range(1, len(c)):
        pr = c[i]; atr = a[i-1]; sig = s[i-1]
        hi = h[i]; low = lo[i]

        if pos != 0:
            pnl = 0.0; closed = False
            if pos == 1:
                if low <= sl:
                    pnl = size*(sl-entry) - size*(entry+sl)*COST; closed = True
                elif hi >= tp:
                    pnl = size*(tp-entry) - size*(entry+tp)*COST; closed = True
            else:
                if hi >= sl:
                    pnl = size*(entry-sl) - size*(entry+sl)*COST; closed = True
                elif low <= tp:
                    pnl = size*(entry-tp) - size*(entry+tp)*COST; closed = True
            if not closed and sig == -pos:
                pnl = pos*size*(pr-entry) - size*(entry+pr)*COST; closed = True
            if closed:
                cap += pnl
                trades.append({"pnl": pnl, "won": pnl > 0})
                pos = 0

        if pos == 0 and sig != 0 and cap > 50:
            rsl  = atr * sl_mult
            if rsl <= 0: continue
            size = (cap * risk_pct/100) / rsl
            pos  = sig; entry = pr
            sl   = entry - rsl*pos
            tp   = entry + atr*tp_mult*pos

        eq.append(cap)

    df_t = pd.DataFrame(trades)
    eq_s = pd.Series(eq[:len(df)], index=df.index[:len(eq)])
    return df_t, eq_s


def score_hq(df_t, eq_s, days):
    """Score para estrategias de alto RR. Premia pocos trades pero de calidad."""
    if df_t.empty or len(df_t) < 10:
        return -9999
    w  = df_t[df_t["pnl"] > 0]
    l  = df_t[df_t["pnl"] <= 0]
    gp = w["pnl"].sum(); gl = abs(l["pnl"].sum())
    pf = gp/gl if gl > 0 else 999
    wr = len(w)/len(df_t)
    peak = eq_s.cummax()
    dd   = (eq_s - peak)/peak*100
    cagr = ((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1)) - 1)*100

    if cagr <= 0: return cagr * 0.1
    calmar = cagr / abs(dd.min()) if dd.min() < 0 else 0
    rr_real = w["pnl"].mean() / abs(l["pnl"].mean()) if not l.empty and not w.empty else 0

    # Bonus por RR alto (queremos estrategias de alto RR)
    rr_bonus = min(rr_real, 5) / 5 * 0.3

    return (0.30*min(calmar,5)/5 + 0.25*(wr-0.40)/0.40 +
            0.20*min(pf,4)/4   + 0.25*min(cagr,60)/60 + rr_bonus)


# ─── ESTRATEGIA 1: TOP-DOWN (1H → 15m) ───────────────────────────────────────
def sig_topdown(df_15m, df_1h, cfg):
    """
    Señal 1H crea una ventana de entrada en 15m.
    Dentro de esa ventana, busca un pullback a EMA20 o OB bounce en 15m.
    RR: SL en 15m (tight), TP a objetivo 1H (wide) → tipicamente 3-5:1.
    """
    from core.signals import get_signals

    # Cargar params del modelo 1H validado
    bv_path = OUTPUT_DIR / "models" / "1h" / "best_validated.json"
    with open(bv_path) as f:
        params_1h = json.load(f)["params"]

    from core.features import build_features
    df_1h_feat = build_features(df_1h, {})
    df_1h_feat.dropna(subset=["close","atr","ema50"], inplace=True)

    signals_1h, _ = get_signals(df_1h_feat, params_1h)

    window_bars = cfg.get("window_bars", 12)   # 3h en 15m
    entry_type  = cfg.get("entry_type", "ema") # "ema" o "ob"
    min_pull    = cfg.get("min_pull", 0.3)     # pullback minimo en ATR

    ema20 = df_15m["close"].ewm(span=20, adjust=False).mean()
    atr   = df_15m["atr"]

    # Marcar ventanas activas alineando 1H → 15m
    active = pd.Series(0, index=df_15m.index)
    for ts, sig in signals_1h[signals_1h != 0].items():
        mask = df_15m.index >= ts
        if not mask.any(): continue
        idxs = df_15m.index[mask][:window_bars]
        active[idxs] = sig

    # Buscar entrada dentro de ventana
    sig_out = pd.Series(0, index=df_15m.index)
    last_entry = -20
    cd = cfg.get("cooldown", 16)  # 4h cooldown entre trades

    close = df_15m["close"]
    open_ = df_15m["open"]
    low_s = df_15m["low"]
    high_s= df_15m["high"]

    for i in range(2, len(df_15m)):
        if (i - last_entry) < cd: continue
        w = active.iloc[i]
        if w == 0: continue

        c  = close.iloc[i]
        o  = open_.iloc[i]
        lo = low_s.iloc[i]
        hi = high_s.iloc[i]
        e20= ema20.iloc[i]
        at = atr.iloc[i]

        if w == 1:  # Ventana LONG activa
            if entry_type == "ema":
                # Pullback a EMA20 + vela alcista
                touched_ema = lo <= e20 * (1 + min_pull*0.001) and c > e20
                if touched_ema and c > o:
                    sig_out.iloc[i] = 1; last_entry = i
            else:  # ob bounce
                # Vela anterior bajista + vela actual alcista (bounce)
                prev_bear = close.iloc[i-1] < open_.iloc[i-1]
                if prev_bear and c > o and (c-o)/(hi-lo+1e-8) > 0.6:
                    sig_out.iloc[i] = 1; last_entry = i

        elif w == -1:  # Ventana SHORT activa
            if entry_type == "ema":
                touched_ema = hi >= e20 * (1 - min_pull*0.001) and c < e20
                if touched_ema and c < o:
                    sig_out.iloc[i] = -1; last_entry = i
            else:
                prev_bull = close.iloc[i-1] > open_.iloc[i-1]
                if prev_bull and c < o and (o-c)/(hi-lo+1e-8) > 0.6:
                    sig_out.iloc[i] = -1; last_entry = i

    return sig_out


# ─── ESTRATEGIA 2: STRUCTURE BREAK + RETEST ──────────────────────────────────
def sig_structure_break(df, cfg):
    """
    Break estructural en 15m con retest del nivel roto.
    Muy selectivo: solo entra cuando hay volumen elevado en el break
    y el precio vuelve a testear el nivel antes de continuar.

    Long: rompe maximo de N barras + retest + bounce
    Short: rompe minimo de N barras + retest + rebote bajista
    """
    swing_n  = cfg.get("swing_n",   10)   # barras para definir swing
    vol_mult = cfg.get("vol_mult",  1.5)  # volumen minimo en break
    rt_pct   = cfg.get("rt_pct",   0.3)  # cuanto puede alejarse antes del retest (ATR)
    cd       = cfg.get("cooldown",  16)

    c   = df["close"]; h = df["high"]; lo = df["low"]; o = df["open"]
    vol = df["volume"]; atr = df["atr"]
    vol_ma = vol.rolling(20).mean()

    # HTF alineacion
    htf_l = df.get("htf_long_1h",  pd.Series(True, index=df.index))
    htf_s = df.get("htf_short_1h", pd.Series(False, index=df.index))

    # Swing highs y lows
    swing_h = h.rolling(swing_n).max().shift(1)
    swing_l = lo.rolling(swing_n).min().shift(1)

    # Break de estructura (cierre limpio fuera del rango)
    break_up = (c > swing_h) & (vol > vol_ma * vol_mult) & htf_l
    break_dn = (c < swing_l) & (vol > vol_ma * vol_mult) & htf_s

    sig_out  = pd.Series(0, index=df.index)
    last_sig = -cd - 1

    # Estado de espera de retest
    waiting_l = False; break_level_l = 0.0
    waiting_s = False; break_level_s = 0.0
    wait_timeout = 0; max_wait = 20  # max barras esperando retest

    for i in range(swing_n + 2, len(df)):
        if (i - last_sig) < cd:
            waiting_l = waiting_s = False
            continue

        ci  = c.iloc[i]; hi_i = h.iloc[i]; lo_i = lo.iloc[i]
        oi  = o.iloc[i]; at   = atr.iloc[i]

        # Actualizar timeout
        if waiting_l or waiting_s:
            wait_timeout += 1
            if wait_timeout > max_wait:
                waiting_l = waiting_s = False

        # Nuevo break (resetea estado)
        if break_up.iloc[i]:
            waiting_l     = True
            break_level_l = c.iloc[i]
            wait_timeout  = 0
            waiting_s     = False

        if break_dn.iloc[i]:
            waiting_s     = True
            break_level_s = c.iloc[i]
            wait_timeout  = 0
            waiting_l     = False

        # Retest y entrada LONG
        if waiting_l:
            # Precio regresa al nivel del break (dentro de 1 ATR)
            retest_zone = abs(lo_i - break_level_l) < at * rt_pct
            bounce      = ci > oi and (ci - oi)/(hi_i - lo_i + 1e-8) > 0.5
            if retest_zone and bounce and ci > break_level_l:
                sig_out.iloc[i] = 1; last_sig = i
                waiting_l = False

        # Retest y entrada SHORT
        if waiting_s:
            retest_zone = abs(hi_i - break_level_s) < at * rt_pct
            bounce      = ci < oi and (oi - ci)/(hi_i - lo_i + 1e-8) > 0.5
            if retest_zone and bounce and ci < break_level_s:
                sig_out.iloc[i] = -1; last_sig = i
                waiting_s = False

    return sig_out


# ─── ESTRATEGIA 3: ORB ULTRA-SELECTIVO ───────────────────────────────────────
def sig_orb_selective(df, cfg):
    """
    Opening Range Breakout ultra-selectivo:
    Solo entra en los primeros 30 min de London (08:00) y NY (13:00 UTC)
    cuando el movimiento inicial es fuerte (volumen 3x+ y vela > 1.5 ATR).

    RR 4:1: SL = 1x ATR, TP = 4x ATR → solo necesita 22% WR para ser rentable.
    """
    sessions   = cfg.get("sessions",   [8, 13])   # horas UTC
    vol_mult   = cfg.get("vol_mult",   2.5)
    min_move   = cfg.get("min_move",   1.2)        # ATR minimo de la primera vela
    window     = cfg.get("window",     2)           # barras de 15m tras apertura
    cd_bars    = cfg.get("cooldown",   20)          # 5h cooldown
    htf_req    = cfg.get("htf_req",    True)

    h_idx = df.index.hour
    m_idx = df.index.minute
    dow   = df.index.dayofweek

    c = df["close"]; h = df["high"]; lo = df["low"]; o = df["open"]
    vol = df["volume"]; atr = df["atr"]
    vol_ma = vol.rolling(30).mean()

    htf_l = df.get("htf_long_1h",  pd.Series(True, index=df.index))
    htf_s = df.get("htf_short_1h", pd.Series(False, index=df.index))

    sig_out  = pd.Series(0, index=df.index)
    last_sig = -cd_bars - 1
    day_ok   = pd.Series(dow, index=df.index).isin([1,2,3])  # Mar-Jue

    for i in range(5, len(df)):
        if (i - last_sig) < cd_bars: continue
        if not day_ok.iloc[i]: continue

        hh = h_idx[i]; mm = m_idx[i]
        if hh not in sessions: continue
        if mm >= window * 15: continue

        ci = c.iloc[i]; oi = o.iloc[i]; hi = h.iloc[i]; li = lo.iloc[i]
        at = atr.iloc[i]
        vi = vol.iloc[i]; vma = vol_ma.iloc[i]

        # Filtros de calidad: volumen elevado + vela grande
        vol_ok  = vi > vma * vol_mult if vma > 0 else False
        move_ok = (hi - li) > at * min_move

        if not (vol_ok and move_ok): continue

        # Direccion: primera vela de la sesion
        is_bull = ci > oi and (ci-oi)/(hi-li+1e-8) > 0.55
        is_bear = ci < oi and (oi-ci)/(hi-li+1e-8) > 0.55

        if is_bull and (not htf_req or htf_l.iloc[i]):
            sig_out.iloc[i] = 1; last_sig = i
        elif is_bear and (not htf_req or htf_s.iloc[i]):
            sig_out.iloc[i] = -1; last_sig = i

    return sig_out


# ─── BUSQUEDA BAYESIANA ───────────────────────────────────────────────────────
def run_advanced_15m(n_trials_each=400):
    print(f"\n{'='*65}")
    print("  SIGMA ADVANCED 15M — 3 estrategias de alto RR")
    print(f"  {n_trials_each} trials x 3 estrategias")
    print(f"{'='*65}")

    df, df_1h, df_4h = load_data()

    n = len(df)
    split   = int(n * 0.80)
    df_is   = df.iloc[:split];  df_oos = df.iloc[split:]
    df_1h_is = df_1h[df_1h.index <= df_is.index[-1]]
    days_is  = (df_is.index[-1]-df_is.index[0]).days
    days_oos = (df_oos.index[-1]-df_oos.index[0]).days
    print(f"  IS: {df_is.index[0].strftime('%Y-%m-%d')} -> {df_is.index[-1].strftime('%Y-%m-%d')} ({days_is}d)")
    print(f"  OOS: {df_oos.index[0].strftime('%Y-%m-%d')} -> {df_oos.index[-1].strftime('%Y-%m-%d')} ({days_oos}d)\n")

    results = {}

    # ── ESTRATEGIA 1: TOP-DOWN ────────────────────────────────────────────────
    print("  [1] TOP-DOWN (1H signal -> 15m entry)")
    def obj_topdown(trial):
        cfg = {
            "window_bars": trial.suggest_int("window_bars", 8, 20),
            "entry_type":  trial.suggest_categorical("entry_type", ["ema", "ob"]),
            "min_pull":    trial.suggest_float("min_pull", 0.1, 0.6, step=0.1),
            "cooldown":    trial.suggest_int("cooldown", 8, 24),
        }
        sl = trial.suggest_float("sl", 1.0, 2.5, step=0.1)
        tp = trial.suggest_float("tp", 3.0, 6.0, step=0.5)
        try:
            sigs = sig_topdown(df_is, df_1h_is, cfg)
            if (sigs!=0).sum() < 8: return -9999
            dt, eq = backtest_hq(df_is, sigs, sl, tp)
            return score_hq(dt, eq, days_is)
        except: return -9999

    study1 = optuna.create_study(direction="maximize",
                                  sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=60))
    best1 = {"score": -9999, "cfg": {}, "sl": 1.5, "tp": 4.0}
    def cb1(study, trial):
        if trial.value and trial.value > best1["score"]:
            best1["score"] = trial.value
            best1["cfg"]   = {k:v for k,v in trial.params.items() if k not in("sl","tp")}
            best1["sl"]    = trial.params["sl"]
            best1["tp"]    = trial.params["tp"]
            if trial.value > 0.3:
                print(f"    [Trial {trial.number}] score={trial.value:.4f}")

    study1.optimize(obj_topdown, n_trials=n_trials_each, callbacks=[cb1], show_progress_bar=False)

    # IS metrics del ganador
    try:
        sigs1 = sig_topdown(df_is, df_1h_is, best1["cfg"])
        dt1, eq1 = backtest_hq(df_is, sigs1, best1["sl"], best1["tp"])
        w1 = dt1[dt1["pnl"]>0]; l1 = dt1[dt1["pnl"]<=0]
        rr1 = w1["pnl"].mean()/abs(l1["pnl"].mean()) if not l1.empty and not w1.empty else 0
        cagr1 = ((eq1.iloc[-1]/CAPITAL)**(365.25/max(days_is,1))-1)*100
        wr1 = len(w1)/len(dt1)*100 if len(dt1)>0 else 0
        print(f"    Mejor IS: {len(dt1)}T | WR {wr1:.1f}% | CAGR {cagr1:+.1f}% | RR {rr1:.2f}:1 | cfg={best1}")
        results["topdown"] = {"cfg": best1, "is_trades": len(dt1), "is_wr": wr1, "is_cagr": cagr1, "rr": rr1}
    except Exception as e:
        print(f"    Error metrics: {e}")

    # ── ESTRATEGIA 2: STRUCTURE BREAK + RETEST ───────────────────────────────
    print("\n  [2] STRUCTURE BREAK + RETEST")
    def obj_sbr(trial):
        cfg = {
            "swing_n":  trial.suggest_int("swing_n",   6, 20),
            "vol_mult": trial.suggest_float("vol_mult", 1.2, 3.0, step=0.2),
            "rt_pct":   trial.suggest_float("rt_pct",   0.2, 0.8, step=0.1),
            "cooldown": trial.suggest_int("cooldown",   12, 32),
        }
        sl = trial.suggest_float("sl", 1.0, 2.5, step=0.1)
        tp = trial.suggest_float("tp", 3.0, 7.0, step=0.5)
        try:
            sigs = sig_structure_break(df_is, cfg)
            if (sigs!=0).sum() < 8: return -9999
            dt, eq = backtest_hq(df_is, sigs, sl, tp)
            return score_hq(dt, eq, days_is)
        except: return -9999

    study2 = optuna.create_study(direction="maximize",
                                  sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=60))
    best2 = {"score": -9999, "cfg": {}, "sl": 1.5, "tp": 4.0}
    def cb2(study, trial):
        if trial.value and trial.value > best2["score"]:
            best2["score"] = trial.value
            best2["cfg"]   = {k:v for k,v in trial.params.items() if k not in("sl","tp")}
            best2["sl"]    = trial.params["sl"]
            best2["tp"]    = trial.params["tp"]
            if trial.value > 0.3:
                print(f"    [Trial {trial.number}] score={trial.value:.4f}")

    study2.optimize(obj_sbr, n_trials=n_trials_each, callbacks=[cb2], show_progress_bar=False)

    try:
        sigs2 = sig_structure_break(df_is, best2["cfg"])
        dt2, eq2 = backtest_hq(df_is, sigs2, best2["sl"], best2["tp"])
        w2 = dt2[dt2["pnl"]>0]; l2 = dt2[dt2["pnl"]<=0]
        rr2 = w2["pnl"].mean()/abs(l2["pnl"].mean()) if not l2.empty and not w2.empty else 0
        cagr2 = ((eq2.iloc[-1]/CAPITAL)**(365.25/max(days_is,1))-1)*100
        wr2 = len(w2)/len(dt2)*100 if len(dt2)>0 else 0
        print(f"    Mejor IS: {len(dt2)}T | WR {wr2:.1f}% | CAGR {cagr2:+.1f}% | RR {rr2:.2f}:1")
        results["sbr"] = {"cfg": best2, "is_trades": len(dt2), "is_wr": wr2, "is_cagr": cagr2, "rr": rr2}
    except Exception as e:
        print(f"    Error metrics: {e}")

    # ── ESTRATEGIA 3: ORB ULTRA-SELECTIVO ────────────────────────────────────
    print("\n  [3] ORB ULTRA-SELECTIVO")
    def obj_orb(trial):
        cfg = {
            "sessions":  trial.suggest_categorical("sessions", [[8,13],[8],[13],[1,8,13]]),
            "vol_mult":  trial.suggest_float("vol_mult",  1.8, 4.0, step=0.2),
            "min_move":  trial.suggest_float("min_move",  0.8, 2.0, step=0.1),
            "window":    trial.suggest_int("window",      1,   3),
            "cooldown":  trial.suggest_int("cooldown",    12,  28),
            "htf_req":   trial.suggest_categorical("htf_req", [True, True, False]),
        }
        sl = trial.suggest_float("sl", 0.8, 2.0, step=0.1)
        tp = trial.suggest_float("tp", 3.0, 7.0, step=0.5)
        try:
            sigs = sig_orb_selective(df_is, cfg)
            if (sigs!=0).sum() < 8: return -9999
            dt, eq = backtest_hq(df_is, sigs, sl, tp)
            return score_hq(dt, eq, days_is)
        except: return -9999

    study3 = optuna.create_study(direction="maximize",
                                  sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=60))
    best3 = {"score": -9999, "cfg": {}, "sl": 1.2, "tp": 4.5}
    def cb3(study, trial):
        if trial.value and trial.value > best3["score"]:
            best3["score"] = trial.value
            best3["cfg"]   = {k:v for k,v in trial.params.items() if k not in("sl","tp")}
            best3["sl"]    = trial.params["sl"]
            best3["tp"]    = trial.params["tp"]
            if trial.value > 0.3:
                print(f"    [Trial {trial.number}] score={trial.value:.4f}")

    study3.optimize(obj_orb, n_trials=n_trials_each, callbacks=[cb3], show_progress_bar=False)

    try:
        sigs3 = sig_orb_selective(df_is, best3["cfg"])
        dt3, eq3 = backtest_hq(df_is, sigs3, best3["sl"], best3["tp"])
        w3 = dt3[dt3["pnl"]>0]; l3 = dt3[dt3["pnl"]<=0]
        rr3 = w3["pnl"].mean()/abs(l3["pnl"].mean()) if not l3.empty and not w3.empty else 0
        cagr3 = ((eq3.iloc[-1]/CAPITAL)**(365.25/max(days_is,1))-1)*100
        wr3 = len(w3)/len(dt3)*100 if len(dt3)>0 else 0
        print(f"    Mejor IS: {len(dt3)}T | WR {wr3:.1f}% | CAGR {cagr3:+.1f}% | RR {rr3:.2f}:1")
        results["orb"] = {"cfg": best3, "is_trades": len(dt3), "is_wr": wr3, "is_cagr": cagr3, "rr": rr3}
    except Exception as e:
        print(f"    Error metrics: {e}")

    # ── OOS VALIDATION DE LAS 3 ───────────────────────────────────────────────
    print(f"\n{'='*65}")
    print("  OOS VALIDATION")
    print(f"{'='*65}")

    df_1h_oos = df_1h[df_1h.index > df_is.index[-1]]
    oos_results = {}

    strategies = [
        ("Top-Down",            sig_topdown,         best1, [df_oos, df_1h_oos]),
        ("Structure Break",     sig_structure_break, best2, [df_oos]),
        ("ORB Ultra-Selectivo", sig_orb_selective,   best3, [df_oos]),
    ]

    best_oos_cagr = -9999; best_name = ""; best_data = {}

    for name, fn, best, fn_args in strategies:
        try:
            sigs = fn(*fn_args, best["cfg"])
            dt, eq = backtest_hq(df_oos, sigs, best["sl"], best["tp"])
            if dt.empty or len(dt) < 5:
                print(f"  {name}: Sin trades OOS suficientes")
                continue
            w = dt[dt["pnl"]>0]; l = dt[dt["pnl"]<=0]
            rr  = w["pnl"].mean()/abs(l["pnl"].mean()) if not l.empty and not w.empty else 0
            cagr = ((eq.iloc[-1]/CAPITAL)**(365.25/max(days_oos,1))-1)*100
            wr   = len(w)/len(dt)*100
            dd   = ((eq - eq.cummax())/eq.cummax()*100).min()
            pf   = w["pnl"].sum()/abs(l["pnl"].sum()) if not l.empty else 999
            print(f"  {name}:")
            print(f"    {len(dt)}T | WR {wr:.1f}% | CAGR {cagr:+.1f}% | DD {dd:.1f}% | PF {pf:.2f} | RR {rr:.2f}:1")

            oos_results[name] = {"trades": len(dt), "wr": wr, "cagr": cagr, "dd": dd, "pf": pf, "rr": rr}
            if cagr > best_oos_cagr:
                best_oos_cagr = cagr; best_name = name
                best_data = {"name": name, "cfg": best, "oos": oos_results[name]}
        except Exception as e:
            print(f"  {name}: Error OOS — {e}")

    # ── GUARDAR MEJOR ─────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    if best_oos_cagr > 0:
        print(f"  GANADOR OOS: {best_name} — CAGR {best_oos_cagr:+.1f}%")

        # Leer modelo actual para comparar
        cur_path = OUTPUT_DIR / "models" / "15m" / "best_validated.json"
        cur_cagr = -9999
        if cur_path.exists():
            try:
                with open(cur_path) as f:
                    cur = json.load(f)
                cur_cagr = cur.get("metrics_oos", {}).get("cagr", -9999)
                if cur.get("trading_ready", True) == False:
                    cur_cagr = -9999  # forzar reemplazo si el actual no es confiable
            except: pass

        if best_oos_cagr > cur_cagr + 1.0:
            import numpy as np_
            def ser(v):
                if isinstance(v, (np_.integer,)): return int(v)
                if isinstance(v, (np_.floating,)): return float(v)
                if isinstance(v, (np_.bool_,)): return bool(v)
                if isinstance(v, list): return [ser(x) for x in v]
                return v

            result = {
                "tf": "15m", "strategy": best_name,
                "params": {k: ser(v) for k,v in best_data["cfg"].items()},
                "metrics_oos": {k: round(float(v),4) for k,v in best_data["oos"].items()},
                "trading_ready": best_oos_cagr > 5 and best_data["oos"]["trades"] >= 15,
                "note": f"OOS CAGR {best_oos_cagr:+.1f}% con {best_data['oos']['trades']} trades"
            }
            (OUTPUT_DIR/"models"/"15m").mkdir(parents=True, exist_ok=True)
            with open(cur_path, "w") as f:
                json.dump(result, f, indent=2)
            print(f"  [SAVED] models/15m/best_validated.json")
            print(f"  trading_ready: {result['trading_ready']}")
        else:
            print(f"  Sin mejora vs modelo actual ({cur_cagr:+.1f}%)")
    else:
        print("  Ningun modelo supera OOS positivo. Seguir buscando.")

    print(f"{'='*65}")
    return oos_results


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=400)
    a = p.parse_args()
    run_advanced_15m(a.trials)

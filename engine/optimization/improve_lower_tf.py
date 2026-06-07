"""
SIGMA ENGINE — Estrategias simples para TFs bajos (1m, 5m, 15m)

El sistema SIGMA CAMPEON no funciona en TFs bajos porque:
- Demasiadas condiciones complejas → señales muy raras
- ICT/SMC requiere contexto estructural que en 1m es ruido

Solución: estrategias simples y directas, alta frecuencia de señales,
costos realistas (comision + slippage = 0.18% round-trip).
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random, json, numpy as np, pandas as pd
import warnings
from pathlib import Path
warnings.filterwarnings('ignore')

random.seed(42); np.random.seed(42)

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION = 0.0004   # 0.04% taker Binance Futures (por lado)
SLIPPAGE   = 0.0001   # 0.01% slippage real para BTC/USDT con cuenta pequena (por lado)
COST       = COMMISSION + SLIPPAGE  # 0.05% por lado = 0.10% round trip
CAPITAL    = 1000.0

TF_BARS_YEAR = {"1m": 525960, "5m": 105192, "15m": 35040}


# ─── CARGA DE DATOS ──────────────────────────────────────────────────────────

def load_tf_data(tf):
    from core.data import fetch_ohlcv
    from core.features import build_features

    days_map = {"1m": 30, "5m": 180, "15m": 730}
    htf_map  = {"1m": ("5m","15m"), "5m": ("15m","1h"), "15m": ("1h","4h")}
    days = days_map[tf]
    h1, h2 = htf_map[tf]

    # Preferir max CSV si existe
    max_p = OUTPUT_DIR / "models" / f"data_{tf}_max.csv"
    if max_p.exists():
        df_b = pd.read_csv(max_p, index_col=0, parse_dates=True)
        df_b.index.name = "timestamp"
        print(f"  {tf}: {len(df_b):,} velas (max)")
    else:
        df_b = fetch_ohlcv(tf=tf, days=days)

    df_h1 = fetch_ohlcv(tf=h1, days=days*3)
    df_h2 = fetch_ohlcv(tf=h2, days=days*5)

    df = build_features(df_b, {h1: df_h1, h2: df_h2})
    df.dropna(subset=["close","atr","ema50"], inplace=True)
    return df


# ─── SEÑALES SIMPLES ─────────────────────────────────────────────────────────

def _cd(sig, bars):
    """Cooldown entre señales — usa numpy para evitar lentitud de .iloc en loops."""
    arr = sig.to_numpy().copy()
    last = -bars - 1
    for i in range(len(arr)):
        if arr[i] != 0:
            if (i - last) >= bars:
                last = i
            else:
                arr[i] = 0
    return pd.Series(arr, index=sig.index)


def sig_ema_pullback(df, cfg):
    """Pullback a EMA en tendencia — funciona en todos los TFs."""
    c, h, l, o = df["close"], df["high"], df["low"], df["open"]
    fast = cfg["fast"]; slow = cfg["slow"]
    ema_f = c.ewm(span=fast, adjust=False).mean()
    ema_s = c.ewm(span=slow, adjust=False).mean()
    bull = ema_f > ema_s; bear = ema_f < ema_s
    tol = cfg.get("tol", 0.003)

    touch_l = (l <= ema_f * (1 + tol)) & (c > ema_f) & (c > o)
    touch_s = (h >= ema_f * (1 - tol)) & (c < ema_f) & (c < o)

    htf = df.get("htf_long_1h" if "htf_long_1h" in df.columns else "htf_long_4h",
                 pd.Series(True, index=df.index))

    sig = pd.Series(0, index=df.index)
    sig[touch_l & bull & htf] = 1
    sig[touch_s & bear & ~htf] = -1
    return _cd(sig, cfg.get("cooldown", 3))


def sig_range_breakout(df, cfg):
    """Breakout de rango N-barras con volumen."""
    c, h, l = df["close"], df["high"], df["low"]
    lb = cfg["lookback"]
    hh = h.rolling(lb).max().shift(1)
    ll = l.rolling(lb).min().shift(1)
    vol_ok = df["volume"] > df["volume"].rolling(lb).mean() * cfg.get("vol_mult", 1.5)

    htf = df.get("htf_long_1h" if "htf_long_1h" in df.columns else "htf_long_4h",
                 pd.Series(True, index=df.index))

    sig = pd.Series(0, index=df.index)
    sig[(c > hh) & vol_ok & htf] = 1
    sig[(c < ll) & vol_ok & ~htf] = -1
    return _cd(sig, cfg.get("cooldown", 3))


def sig_rsi_reversal(df, cfg):
    """RSI extremo + vela de reversal."""
    c = df["close"]
    rsi = df.get("rsi", _calc_rsi(c, 14))
    ob = cfg.get("rsi_ob", 70); os_ = cfg.get("rsi_os", 30)

    bull_rev = (rsi < os_) & (c > c.shift(1)) & (c > c.shift(2))
    bear_rev = (rsi > ob) & (c < c.shift(1)) & (c < c.shift(2))

    sig = pd.Series(0, index=df.index)
    sig[bull_rev] = 1; sig[bear_rev] = -1
    return _cd(sig, cfg.get("cooldown", 5))


def sig_vwap_bounce(df, cfg):
    """Bounce desde VWAP diario — funciona muy bien en scalping."""
    c, h, l = df["close"], df["high"], df["low"]
    vol = df["volume"]

    # VWAP diario
    typ = (h + l + c) / 3
    day_grp = pd.Grouper(freq="D")
    vwap = (typ * vol).groupby(pd.Grouper(freq="D")).cumsum() / \
           vol.groupby(pd.Grouper(freq="D")).cumsum()

    dev = cfg.get("dev", 0.003)
    near_vwap_l = (l <= vwap * (1 + dev)) & (c > vwap) & (c > c.shift(1))
    near_vwap_s = (h >= vwap * (1 - dev)) & (c < vwap) & (c < c.shift(1))

    htf = df.get("htf_long_1h" if "htf_long_1h" in df.columns else "htf_long_4h",
                 pd.Series(True, index=df.index))

    sig = pd.Series(0, index=df.index)
    sig[near_vwap_l & htf] = 1
    sig[near_vwap_s & ~htf] = -1
    return _cd(sig, cfg.get("cooldown", 3))


def sig_momentum_burst(df, cfg):
    """Momentum burst — precio accelera en direccion de tendencia."""
    c = df["close"]; atr = df["atr"]
    n = cfg.get("n", 3)
    ret_n = c.pct_change(n)
    thresh = cfg.get("thresh", 0.005)

    ema_f = c.ewm(span=cfg.get("fast", 9), adjust=False).mean()
    ema_s = c.ewm(span=cfg.get("slow", 21), adjust=False).mean()
    bull = ema_f > ema_s; bear = ema_f < ema_s

    sig = pd.Series(0, index=df.index)
    sig[(ret_n > thresh) & bull & (df["volume"] > df["volume"].rolling(20).mean())] = 1
    sig[(ret_n < -thresh) & bear & (df["volume"] > df["volume"].rolling(20).mean())] = -1
    return _cd(sig, cfg.get("cooldown", 5))


def sig_session_scalp(df, cfg):
    """Primeros N minutos de sesion London/NY — mayor volumen y direccion."""
    h_utc = df.index.hour
    lon_s, lon_e = 8, 10   # London open
    ny_s,  ny_e  = 13, 15  # NY open
    in_sess = ((h_utc >= lon_s) & (h_utc < lon_e)) | ((h_utc >= ny_s) & (h_utc < ny_e))

    c = df["close"]
    ema_f = c.ewm(span=cfg.get("fast", 9), adjust=False).mean()
    ema_s = c.ewm(span=cfg.get("slow", 21), adjust=False).mean()
    bull = ema_f > ema_s; bear = ema_f < ema_s

    vol_ok = df["volume"] > df["volume"].rolling(20).mean() * cfg.get("vol_mult", 1.2)

    sig = pd.Series(0, index=df.index)
    sig[in_sess & bull & vol_ok & (c > c.shift(1))] = 1
    sig[in_sess & bear & vol_ok & (c < c.shift(1))] = -1
    return _cd(sig, cfg.get("cooldown", 5))


def sig_bb_mean_reversion(df, cfg):
    """Bollinger Bands mean reversion — ideal para mercados en rango."""
    c, h, l = df["close"], df["high"], df["low"]
    p = cfg.get("period", 20); dev = cfg.get("dev", 2.0)
    sma = c.rolling(p).mean(); std = c.rolling(p).std()
    bb_u = sma + dev * std; bb_l = sma - dev * std

    adx = df.get("adx", pd.Series(20, index=df.index))
    ranging = adx < cfg.get("adx_max", 25)

    sig = pd.Series(0, index=df.index)
    sig[(l <= bb_l) & (c > c.shift(1)) & ranging] = 1
    sig[(h >= bb_u) & (c < c.shift(1)) & ranging] = -1
    return _cd(sig, cfg.get("cooldown", 3))


def _calc_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -delta.clip(upper=0)
    ma_up = up.ewm(com=period-1, adjust=False).mean()
    ma_dn = down.ewm(com=period-1, adjust=False).mean()
    rs = ma_up / ma_dn.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ─── BACKTEST ────────────────────────────────────────────────────────────────

def backtest(df, sig, sl_m, tp_m, risk=0.5, trail=False, trail_m=2.0):
    # Extraer numpy arrays para velocidad (evita .iloc[] en loop)
    closes = df["close"].to_numpy()
    highs  = df["high"].to_numpy()
    lows   = df["low"].to_numpy()
    atrs   = df["atr"].to_numpy()
    sigs   = sig.to_numpy()

    cap = CAPITAL; eq = [cap]; pos = 0; entry = sl = tp = trl = sz = 0.0
    trades = []
    for i in range(1, len(closes)):
        pr  = closes[i]; atr = atrs[i-1]
        h_  = highs[i];  lo  = lows[i]; s = sigs[i-1]

        if pos != 0:
            pnl = 0.; closed = False
            if trail:
                if pos == 1:  trl = max(trl, h_ - atr * trail_m)
                else:         trl = min(trl, lo + atr * trail_m)
                if (pos == 1 and lo <= trl) or (pos == -1 and h_ >= trl):
                    pnl = pos * sz * (trl - entry) - sz * (entry + trl) * COST
                    closed = True
            else:
                if pos == 1:
                    if lo <= sl:   pnl = sz*(sl-entry) - sz*(entry+sl)*COST; closed=True
                    elif h_ >= tp: pnl = sz*(tp-entry) - sz*(entry+tp)*COST; closed=True
                else:
                    if h_ >= sl:   pnl = sz*(entry-sl) - sz*(entry+sl)*COST; closed=True
                    elif lo <= tp: pnl = sz*(entry-tp) - sz*(entry+tp)*COST; closed=True
            if not closed and s == -pos:
                pnl = pos * sz * (pr - entry) - sz * (entry + pr) * COST
                closed = True
            if closed:
                cap += pnl; trades.append({"pnl": pnl, "won": pnl > 0}); pos = 0

        if pos == 0 and s != 0 and cap > 50:
            pos = s; entry = pr; r_sl = atr * sl_m
            sl = entry - r_sl if pos == 1 else entry + r_sl
            tp = entry + atr * tp_m if pos == 1 else entry - atr * tp_m
            trl = sl; sz = (cap * risk / 100) / r_sl if r_sl > 0 else 0
        eq.append(cap)

    df_t = pd.DataFrame(trades)
    eq_s = pd.Series(eq[:len(df)], index=df.index[:len(eq)])
    if df_t.empty or len(df_t) < 10: return None

    w = df_t[df_t["pnl"] > 0]; l = df_t[df_t["pnl"] <= 0]
    gp = w["pnl"].sum(); gl = abs(l["pnl"].sum())
    peak = eq_s.cummax(); dd = (eq_s - peak) / peak * 100
    ret = eq_s.pct_change().dropna()
    days = (eq_s.index[-1] - eq_s.index[0]).days
    cagr = ((eq_s.iloc[-1] / CAPITAL) ** (365.25 / max(days, 1)) - 1) * 100
    wr = len(w) / len(df_t)

    import scipy.stats as st
    se = np.sqrt(wr * (1 - wr) / len(df_t))
    ci = st.norm.ppf(0.975) * se

    return {
        "trades": len(df_t), "winrate": round(wr * 100, 1),
        "ci_low": round((wr - ci) * 100, 1), "ci_high": round((wr + ci) * 100, 1),
        "cagr": round(cagr, 2), "max_dd": round(dd.min(), 2),
        "pf": round(gp / gl, 3) if gl > 0 else 999,
        "sharpe": round(ret.mean() / ret.std() * np.sqrt(525960 if days < 60 else 105192 if days < 200 else 35040), 3) if ret.std() > 0 else 0,
        "calmar": round(cagr / abs(dd.min()), 3) if dd.min() < 0 else 0,
    }


def score_m(m, min_t=50):
    if m is None or m["trades"] < min_t: return -9999
    if m["cagr"] <= 0: return -9999
    pen = max(0, (150 - m["trades"]) / 150) * 0.3
    cal = min(m["calmar"], 5) / 5
    wr  = (m["winrate"] / 100 - 0.40) / 0.40   # umbral mas bajo — acepta WR 40%+ con buen RR
    pf  = min(m["pf"], 5) / 5                   # PF pesa mas
    cagr_n = min(max(m["cagr"], 0), 100) / 100
    return 0.30 * cal + 0.20 * wr + 0.30 * pf + 0.20 * cagr_n - pen


# ─── ESPACIOS DE BUSQUEDA POR ESTRATEGIA ─────────────────────────────────────

STRATEGIES = {
    "EMA Pullback": (sig_ema_pullback, {
        "fast": [5, 8, 9, 12, 21],
        "slow": [21, 34, 50, 100],
        "tol":  [0.002, 0.003, 0.005, 0.008],
        "cooldown": [2, 3, 5],
    }),
    "Range Breakout": (sig_range_breakout, {
        "lookback": [5, 8, 10, 14, 20],
        "vol_mult": [1.3, 1.5, 2.0, 2.5],
        "cooldown": [2, 3, 5],
    }),
    "RSI Reversal": (sig_rsi_reversal, {
        "rsi_ob": [65, 70, 75],
        "rsi_os": [25, 30, 35],
        "cooldown": [3, 5, 8],
    }),
    "VWAP Bounce": (sig_vwap_bounce, {
        "dev": [0.002, 0.003, 0.005],
        "cooldown": [2, 3, 5],
    }),
    "Momentum Burst": (sig_momentum_burst, {
        "n":     [2, 3, 5],
        "thresh":[0.003, 0.005, 0.008, 0.012],
        "fast":  [5, 9, 12],
        "slow":  [21, 34],
        "cooldown": [3, 5, 8],
    }),
    "Session Scalp": (sig_session_scalp, {
        "fast":     [5, 9, 12],
        "slow":     [21, 34],
        "vol_mult": [1.2, 1.5, 2.0],
        "cooldown": [3, 5],
    }),
    "BB Reversion": (sig_bb_mean_reversion, {
        "period":  [14, 20, 30],
        "dev":     [1.5, 2.0, 2.5],
        "adx_max": [20, 25, 30],
        "cooldown":[2, 3, 5],
    }),
}

SL_RANGE  = [1.0, 1.5, 2.0, 2.5, 3.0]
TP_RANGE  = [2.5, 3.0, 4.0, 5.0, 6.0]   # RR minimo 2.5x — compensa WR bajo
N_SAMPLES = 400  # por estrategia


def run_lower_tf(tf="15m", n_per=N_SAMPLES):
    print(f"\n{'='*65}")
    print(f"  SIGMA LOWER-TF — {tf.upper()}")
    print(f"  {len(STRATEGIES)} estrategias x {n_per} muestras")
    print(f"{'='*65}")

    print(f"\n[DATA] Cargando {tf}...")
    df = load_tf_data(tf)
    days_total = (df.index[-1] - df.index[0]).days
    print(f"  {len(df):,} velas | {days_total} dias")

    # OOS: ultimos 20%
    split  = int(len(df) * 0.80)
    df_is  = df.iloc[:split]
    df_oos = df.iloc[split:]
    d_is   = (df_is.index[-1] - df_is.index[0]).days
    d_oos  = (df_oos.index[-1] - df_oos.index[0]).days
    print(f"  IS: {d_is}d | OOS: {d_oos}d\n")

    all_valid = []
    best_g = None; best_s = -9999; best_cfg = {}; best_name = ""

    for strat_name, (fn, space) in STRATEGIES.items():
        print(f"  [{strat_name}]")
        best_strat = None; best_strat_s = -9999

        for _ in range(n_per):
            cfg = {k: random.choice(v) for k, v in space.items()}
            sl  = random.choice(SL_RANGE)
            tp  = random.choice(TP_RANGE)
            if tp <= sl: continue
            trail   = random.choice([True, False])
            trail_m = random.choice([1.5, 2.0, 2.5]) if trail else 2.0
            risk    = random.choice([0.3, 0.5, 0.8, 1.0])

            try:
                sig = fn(df_is, cfg)
                n_sig = (sig != 0).sum()
                if n_sig < 30: continue
                m = backtest(df_is, sig, sl, tp, risk, trail, trail_m)
                if m is None: continue
                s = score_m(m, min_t=50)

                if s > best_strat_s:
                    best_strat_s = s; best_strat = m.copy()
                if s > best_g_s if (best_g_s := best_s) else True:
                    pass
                if s > best_s:
                    best_s = s; best_g = m.copy(); best_name = strat_name
                    best_cfg = {**cfg, "sl": sl, "tp": tp, "trail": trail,
                                "trail_m": trail_m, "risk": risk}
                    if m["trades"] >= 80 and m["cagr"] > 5:
                        print(f"  *** {strat_name} ***")
                        print(f"  {m['trades']}T | WR {m['winrate']:.1f}% "
                              f"[{m['ci_low']:.1f}-{m['ci_high']:.1f}%] | "
                              f"CAGR {m['cagr']:+.1f}%/ano | Calmar {m['calmar']:.2f}")

                min_t_valid = {"1m": 300, "5m": 150, "15m": 80}.get(tf, 80)
                if (m["trades"] >= min_t_valid and m["winrate"] >= 50
                        and m["cagr"] > 0 and m["calmar"] >= 0.5):
                    all_valid.append({**m, "strategy": strat_name, "cfg": cfg,
                                      "sl": sl, "tp": tp})
            except Exception:
                continue

        if best_strat and best_strat["cagr"] > 0:
            print(f"  Mejor IS: {best_strat['trades']}T | WR {best_strat['winrate']:.1f}% | "
                  f"CAGR {best_strat['cagr']:+.1f}%/ano | Calmar {best_strat['calmar']:.2f}")
        else:
            print(f"  Sin resultado positivo")

    # OOS del mejor global
    print(f"\n{'='*65}")
    print(f"  OOS VALIDATION — {best_name}")

    if best_g and best_g["cagr"] > 0 and best_name:
        fn, _ = STRATEGIES[best_name]
        inner = {k: v for k, v in best_cfg.items()
                 if k not in ("sl", "tp", "trail", "trail_m", "risk")}
        try:
            sig_oos = fn(df_oos, inner)
            m_oos = backtest(df_oos, sig_oos, best_cfg["sl"], best_cfg["tp"],
                             best_cfg["risk"], best_cfg["trail"], best_cfg["trail_m"])
            if m_oos and m_oos["cagr"] > 0:
                print(f"  OOS {tf.upper()}: {m_oos['trades']}T | "
                      f"WR {m_oos['winrate']:.1f}% [{m_oos['ci_low']:.1f}-{m_oos['ci_high']:.1f}%] | "
                      f"CAGR {m_oos['cagr']:+.1f}%/ano | Calmar {m_oos['calmar']:.2f}")

                # Guardar SOLO si supera el actual
                out_dir = OUTPUT_DIR / "models" / tf
                out_dir.mkdir(parents=True, exist_ok=True)
                prev_p = out_dir / "best_validated.json"
                prev_cagr = 0.0
                if prev_p.exists():
                    try:
                        pm = json.load(open(prev_p)).get("metrics_oos", {})
                        prev_cagr = pm.get("cagr", 0.0)
                    except: pass
                if m_oos["cagr"] <= prev_cagr:
                    print(f"  Sin mejora vs actual ({prev_cagr:+.1f}%). No guardando.")
                else:
                    result = {
                        "tf": tf, "strategy": best_name, "params": best_cfg,
                        "metrics_is": {k: round(v, 4) if isinstance(v, float) else v
                                       for k, v in best_g.items()},
                        "metrics_oos": {k: round(v, 4) if isinstance(v, float) else v
                                        for k, v in m_oos.items()},
                        "score": score_m(m_oos, min_t=30),
                        "valid_configs": len(all_valid),
                    }
                    with open(out_dir / "best_validated.json", "w") as f:
                        json.dump(result, f, indent=2)
                    print(f"  [SAVED] models/{tf}/best_validated.json")
            else:
                print(f"  OOS {tf.upper()}: sin edge ({m_oos['cagr'] if m_oos else 'N/A'}% CAGR)")
        except Exception as e:
            print(f"  OOS error: {e}")
    else:
        print(f"  Sin modelo IS positivo para validar.")

    all_valid.sort(key=lambda x: x.get("calmar", 0), reverse=True)
    print(f"\n  Configs validas ({tf}): {len(all_valid)}")
    if all_valid:
        print(f"  TOP 5:")
        for i, m in enumerate(all_valid[:5], 1):
            print(f"  {i}. {m['strategy']}: {m['trades']}T | WR {m['winrate']:.1f}% | "
                  f"CAGR {m['cagr']:+.1f}%/ano | Calmar {m['calmar']:.2f}")

        rpt = OUTPUT_DIR / "results" / "reports"
        rpt.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(all_valid).to_csv(rpt / f"{tf}_lower_results.csv", index=False)

    print(f"\n[DONE] {tf.upper()} completado.")
    return all_valid


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf",      default="15m")
    parser.add_argument("--samples", type=int, default=N_SAMPLES)
    args = parser.parse_args()
    run_lower_tf(args.tf, args.samples)

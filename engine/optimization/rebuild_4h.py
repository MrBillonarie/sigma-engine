"""
SIGMA ENGINE — Reconstruccion completa estrategia 4H

Abandona el sistema SIGMA (ICT/SMC) para 4H.
Nuevas estrategias especificas para BTC 4H Futures:

1. Funding Rate Reversion  — extremo funding -> contrarian
2. Weekly Pivot Breakout   — breakout de niveles semanales con volumen
3. Momentum Acumulado      — 3+ velas consecutivas + volumen creciente
4. Liquidity Sweep         — barre liquidez semanal y revierte
5. Trend Pullback Simple   — pullback a EMA20 en tendencia clara (sin filtros ICT)
6. Range Extremes          — compra en soporte / vende en resistencia del rango 4H

Target: 40-70 trades/año | OOS CAGR > 8% | WR > 50%
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, random, numpy as np, pandas as pd
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path
import requests

random.seed(77); np.random.seed(77)

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION = 0.0004; SLIPPAGE = 0.0001; COST = COMMISSION + SLIPPAGE
CAPITAL    = 1000.0


# ─── DATOS ────────────────────────────────────────────────────────────────────

def load_4h():
    """Carga maximo de historia 4H con indicadores basicos."""
    from core.data import fetch_ohlcv

    max_p = OUTPUT_DIR / "models" / "data_4h_max.csv"
    if max_p.exists():
        df = pd.read_csv(max_p, index_col=0, parse_dates=True)
        df.index.name = "timestamp"
        df = df.astype(float)
    else:
        df = fetch_ohlcv(tf="4h", days=1500)

    # Indicadores basicos
    df["atr"]    = _atr(df, 14)
    df["ema20"]  = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"]  = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["vol_ma"] = df["volume"].rolling(20).mean()
    df["rsi"]    = _rsi(df["close"], 14)
    df.dropna(inplace=True)
    print(f"  4H: {len(df):,} velas | {(df.index[-1]-df.index[0]).days} dias")
    return df


def load_funding():
    """Descarga funding rate historico de Binance."""
    try:
        print("  [FUNDING] Descargando...")
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        rows = []
        end = int(pd.Timestamp.now().timestamp() * 1000)
        for _ in range(60):
            params = {"symbol":"BTCUSDT","limit":1000,"endTime":end}
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            if not data: break
            rows.extend(data)
            end = data[0]["fundingTime"] - 1
            if len(data) < 1000: break
        df_f = pd.DataFrame(rows)
        df_f["time"] = pd.to_datetime(df_f["fundingTime"], unit="ms", utc=True).dt.tz_localize(None)
        df_f["rate"] = df_f["fundingRate"].astype(float)
        df_f = df_f.set_index("time")["rate"].sort_index()
        print(f"  [FUNDING] {len(df_f)} registros")
        return df_f
    except Exception as e:
        print(f"  [FUNDING] Error: {e} — usando proxy")
        return None


def _atr(df, n=14):
    h,l,c = df["high"],df["low"],df["close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(com=n-1, adjust=False).mean()


def _rsi(s, n=14):
    d = s.diff(); u=d.clip(lower=0); dn=-d.clip(upper=0)
    rs = u.ewm(com=n-1,adjust=False).mean() / dn.ewm(com=n-1,adjust=False).mean().replace(0,np.nan)
    return 100 - 100/(1+rs)


def _cd(sig, bars):
    """Cooldown numpy (rapido)."""
    arr = sig.to_numpy().copy(); last=-bars-1
    for i in range(len(arr)):
        if arr[i]!=0:
            if (i-last)>=bars: last=i
            else: arr[i]=0
    return pd.Series(arr, index=sig.index)


# ─── ESTRATEGIAS ──────────────────────────────────────────────────────────────

def sig_funding_reversion(df, df_f, cfg):
    """
    Funding Rate Reversion — la mas especifica de BTC Futures.
    Funding extremo positivo = todos long = reversal short (y viceversa).
    Edge: cuando todos estan de un lado, el mercado los exprime.
    """
    if df_f is None:
        return pd.Series(0, index=df.index)

    # Reindexar funding a 4H con forward fill
    f_4h = df_f.reindex(df.index, method="ffill").fillna(0)

    extreme_long  = f_4h >  cfg.get("thr_pos",  0.05) / 100  # >0.05% = longs pagando mucho
    extreme_short = f_4h < -cfg.get("thr_neg",  0.02) / 100  # <-0.02% = shorts pagando

    # Confirmacion de vela: reversal candle
    bull_candle = df["close"] > df["open"]
    bear_candle = df["close"] < df["open"]

    sig = pd.Series(0, index=df.index)
    sig[extreme_short & bull_candle] =  1  # funding negativo + vela verde = long
    sig[extreme_long  & bear_candle] = -1  # funding positivo + vela roja = short
    return _cd(sig, cfg.get("cooldown", 3))


def sig_weekly_pivot_extended(df, cfg):
    """
    Weekly Pivot EXTENDIDO — agrega R2/S2 y midpoints PP-R1/PP-S1.
    Mas señales = mas trades/año manteniendo la misma logica objetiva.
    """
    wkly = df.resample("W").agg({"high":"max","low":"min","close":"last"})
    wkly["pp"] = (wkly["high"]+wkly["low"]+wkly["close"])/3
    wkly["r1"] = 2*wkly["pp"]-wkly["low"]
    wkly["s1"] = 2*wkly["pp"]-wkly["high"]
    wkly["r2"] = wkly["pp"]+(wkly["high"]-wkly["low"])
    wkly["s2"] = wkly["pp"]-(wkly["high"]-wkly["low"])
    wkly["m1_r"] = (wkly["pp"]+wkly["r1"])/2  # midpoint PP-R1
    wkly["m1_s"] = (wkly["pp"]+wkly["s1"])/2  # midpoint PP-S1

    pp   = wkly["pp"].shift(1).reindex(df.index, method="ffill")
    r1   = wkly["r1"].shift(1).reindex(df.index, method="ffill")
    s1   = wkly["s1"].shift(1).reindex(df.index, method="ffill")
    r2   = wkly["r2"].shift(1).reindex(df.index, method="ffill")
    s2   = wkly["s2"].shift(1).reindex(df.index, method="ffill")
    m1r  = wkly["m1_r"].shift(1).reindex(df.index, method="ffill")
    m1s  = wkly["m1_s"].shift(1).reindex(df.index, method="ffill")

    vol_ok = df["volume"] > df["vol_ma"] * cfg.get("vol_mult", 1.3)
    bull   = df["ema50"] > df["ema200"]
    bear   = df["ema50"] < df["ema200"]
    tol    = cfg.get("tol", 0.002)

    # Breakouts
    break_r1  = (df["close"]>r1)&(df["close"].shift(1)<=r1)
    break_r2  = (df["close"]>r2)&(df["close"].shift(1)<=r2)
    break_s1  = (df["close"]<s1)&(df["close"].shift(1)>=s1)
    break_s2  = (df["close"]<s2)&(df["close"].shift(1)>=s2)
    # Bounces
    bounce_pp_l = (df["low"]<=pp*(1+tol))&(df["close"]>pp)&(df["close"]>df["open"])&bull
    bounce_pp_s = (df["high"]>=pp*(1-tol))&(df["close"]<pp)&(df["close"]<df["open"])&bear
    bounce_r1_l = (df["low"]<=r1*(1+tol))&(df["close"]>r1)&(df["close"]>df["open"])&bull
    bounce_s1_s = (df["high"]>=s1*(1-tol))&(df["close"]<s1)&(df["close"]<df["open"])&bear
    # Midpoints
    bounce_m1r_l= (df["low"]<=m1r*(1+tol))&(df["close"]>m1r)&bull
    bounce_m1s_s= (df["high"]>=m1s*(1-tol))&(df["close"]<m1s)&bear

    use_r2     = cfg.get("use_r2", True)
    use_bounce = cfg.get("use_bounce", True)
    use_mid    = cfg.get("use_mid", False)

    sig = pd.Series(0, index=df.index)
    sig[(break_r1|(bounce_pp_l if use_bounce else pd.Series(False,index=df.index))|(bounce_r1_l if use_bounce else pd.Series(False,index=df.index)))&vol_ok] = 1
    sig[(break_s1|(bounce_pp_s if use_bounce else pd.Series(False,index=df.index))|(bounce_s1_s if use_bounce else pd.Series(False,index=df.index)))&vol_ok] = -1
    if use_r2:
        sig[(break_r2)&vol_ok&bull] = 1
        sig[(break_s2)&vol_ok&bear] = -1
    if use_mid:
        sig[(bounce_m1r_l)&vol_ok] = 1
        sig[(bounce_m1s_s)&vol_ok] = -1
    return _cd(sig, cfg.get("cooldown", 4))


def sig_weekly_pivot(df, cfg):
    """
    Weekly Pivot Breakout — BTC respeta mucho estos niveles.
    PP = (H+L+C)/3 de la semana anterior.
    Breakout con volumen = entrada en esa direccion.
    """
    # Calcular pivots semanales
    wkly = df.resample("W").agg({"high":"max","low":"min","close":"last"})
    wkly["pp"] = (wkly["high"] + wkly["low"] + wkly["close"]) / 3
    wkly["r1"] = 2*wkly["pp"] - wkly["low"]
    wkly["s1"] = 2*wkly["pp"] - wkly["high"]
    wkly["r2"] = wkly["pp"] + (wkly["high"] - wkly["low"])
    wkly["s2"] = wkly["pp"] - (wkly["high"] - wkly["low"])

    # Reindexar a 4H con la semana ANTERIOR (shift 1)
    pp_4h = wkly["pp"].shift(1).reindex(df.index, method="ffill")
    r1_4h = wkly["r1"].shift(1).reindex(df.index, method="ffill")
    s1_4h = wkly["s1"].shift(1).reindex(df.index, method="ffill")
    r2_4h = wkly["r2"].shift(1).reindex(df.index, method="ffill")
    s2_4h = wkly["s2"].shift(1).reindex(df.index, method="ffill")

    vol_ok = df["volume"] > df["vol_ma"] * cfg.get("vol_mult", 1.3)
    bull   = df["ema50"] > df["ema200"]
    bear   = df["ema50"] < df["ema200"]

    # Breakout de R1 en uptrend | Breakdown de S1 en downtrend
    break_r1 = (df["close"] > r1_4h) & (df["close"].shift(1) <= r1_4h)
    break_s1 = (df["close"] < s1_4h) & (df["close"].shift(1) >= s1_4h)
    # Bounce desde PP
    bounce_pp_long  = (df["low"] <= pp_4h * 1.002) & (df["close"] > pp_4h) & bull
    bounce_pp_short = (df["high"] >= pp_4h * 0.998) & (df["close"] < pp_4h) & bear

    sig = pd.Series(0, index=df.index)
    sig[(break_r1 | bounce_pp_long)  & vol_ok] =  1
    sig[(break_s1 | bounce_pp_short) & vol_ok] = -1
    return _cd(sig, cfg.get("cooldown", 4))


def sig_momentum_accumulated(df, cfg):
    """
    Momentum Acumulado — 3+ velas consecutivas en misma direccion con volumen creciente.
    BTC en 4H tiene estas secuencias cuando hay momentum institucional.
    """
    n = cfg.get("n_bars", 3)
    c = df["close"]; v = df["volume"]

    # N velas consecutivas alcistas
    bull_seq = pd.Series(True, index=c.index)
    for i in range(1, n+1):
        bull_seq = bull_seq & (c > c.shift(i))
    # Volumen creciente
    vol_growing = v > v.shift(1)

    bear_seq = pd.Series(True, index=c.index)
    for i in range(1, n+1):
        bear_seq = bear_seq & (c < c.shift(i))

    htf_bull = df["ema50"] > df["ema200"]
    htf_bear = df["ema50"] < df["ema200"]

    sig = pd.Series(0, index=df.index)
    sig[bull_seq & vol_growing & htf_bull] =  1
    sig[bear_seq & vol_growing & htf_bear] = -1
    return _cd(sig, cfg.get("cooldown", 6))


def sig_liquidity_sweep(df, cfg):
    """
    Liquidity Sweep + Reversal — BTC barre stops de la semana anterior y revierte.
    Setup: precio supera el high/low semanal, pero cierra de vuelta abajo/arriba.
    Es una señal muy fuerte de institucion manipulando el mercado.
    """
    lb = cfg.get("lookback", 42)  # 42 barras x 4h = 1 semana
    wk_high = df["high"].rolling(lb).max().shift(1)
    wk_low  = df["low"].rolling(lb).min().shift(1)

    # Sweep alcista: sube sobre wk_high pero cierra debajo (fake breakout -> short)
    sweep_high = (df["high"] > wk_high) & (df["close"] < wk_high) & (df["close"] < df["open"])
    # Sweep bajista: baja bajo wk_low pero cierra arriba (fake breakdown -> long)
    sweep_low  = (df["low"]  < wk_low)  & (df["close"] > wk_low)  & (df["close"] > df["open"])

    # Confirmar con volumen alto (institucion activa)
    vol_spike = df["volume"] > df["vol_ma"] * cfg.get("vol_mult", 1.5)

    sig = pd.Series(0, index=df.index)
    sig[sweep_low  & vol_spike] =  1  # sweep bajo = reversal long
    sig[sweep_high & vol_spike] = -1  # sweep alto = reversal short
    return _cd(sig, cfg.get("cooldown", 4))


def sig_trend_pullback_clean(df, cfg):
    """
    Pullback limpio a EMA en tendencia — version sin filtros ICT.
    Simple y directo: tendencia clara + retrocede a EMA + rebota.
    """
    f = cfg.get("ema_fast", 20); s = cfg.get("ema_slow", 50)
    ema_f = df["close"].ewm(span=f, adjust=False).mean()
    ema_s = df["close"].ewm(span=s, adjust=False).mean()
    bull = (ema_f > ema_s) & (df["close"] > ema_f * 1.01)  # tendencia clara + precio sobre EMA
    bear = (ema_f < ema_s) & (df["close"] < ema_f * 0.99)

    tol = cfg.get("tol", 0.01)
    touch_l = (df["low"] <= ema_f*(1+tol)) & (df["close"] > ema_f) & (df["close"] > df["open"])
    touch_s = (df["high"] >= ema_f*(1-tol)) & (df["close"] < ema_f) & (df["close"] < df["open"])

    adx = df.get("adx", pd.Series(25, index=df.index))
    strong = adx > cfg.get("adx_min", 20) if "adx" in df.columns else pd.Series(True, index=df.index)

    sig = pd.Series(0, index=df.index)
    sig[touch_l & bull & strong] =  1
    sig[touch_s & bear & strong] = -1
    return _cd(sig, cfg.get("cooldown", 3))


def sig_range_extremes(df, cfg):
    """
    Range Extremes 4H — compra en soporte, vende en resistencia.
    Usa Bollinger Bands + RSI para identificar extremos del rango.
    """
    p = cfg.get("period", 30); d = cfg.get("dev", 2.2)
    sma = df["close"].rolling(p).mean(); std = df["close"].rolling(p).std()
    bb_u = sma + d*std; bb_l = sma - d*std

    # Solo en rangos (ADX bajo)
    adx = df.get("adx", pd.Series(20, index=df.index))
    ranging = adx < cfg.get("adx_max", 25) if "adx" in df.columns else pd.Series(True, index=df.index)

    # RSI oversold/overbought
    os_ = cfg.get("rsi_os", 32); ob = cfg.get("rsi_ob", 68)

    sig = pd.Series(0, index=df.index)
    sig[(df["low"] <= bb_l) & (df["rsi"] < os_) & (df["close"] > df["open"]) & ranging] =  1
    sig[(df["high"]>= bb_u) & (df["rsi"] > ob)  & (df["close"] < df["open"]) & ranging] = -1
    return _cd(sig, cfg.get("cooldown", 4))


# ─── BACKTEST ─────────────────────────────────────────────────────────────────

def backtest(df, sig, sl_m, tp_m, risk=1.0, trail=False, trail_m=2.0):
    closes=df["close"].to_numpy(); highs=df["high"].to_numpy()
    lows=df["low"].to_numpy();     atrs=df["atr"].to_numpy()
    sigs=sig.to_numpy()

    cap=CAPITAL; eq=[cap]; pos=0
    entry=sl=tp=trl=sz=0.0; trades=[]

    for i in range(1, len(closes)):
        pr=closes[i]; atr=atrs[i-1]; h_=highs[i]; lo=lows[i]; s=sigs[i-1]
        if pos!=0:
            pnl=0.; closed=False
            if trail:
                if pos==1: trl=max(trl, h_-atr*trail_m)
                else:      trl=min(trl, lo+atr*trail_m)
                if (pos==1 and lo<=trl) or (pos==-1 and h_>=trl):
                    pnl=pos*sz*(trl-entry)-sz*(entry+trl)*COST; closed=True
            else:
                if pos==1:
                    if lo<=sl: pnl=sz*(sl-entry)-sz*(entry+sl)*COST; closed=True
                    elif h_>=tp: pnl=sz*(tp-entry)-sz*(entry+tp)*COST; closed=True
                else:
                    if h_>=sl: pnl=sz*(entry-sl)-sz*(entry+sl)*COST; closed=True
                    elif lo<=tp: pnl=sz*(entry-tp)-sz*(entry+tp)*COST; closed=True
            if not closed and s==-pos:
                pnl=pos*sz*(pr-entry)-sz*(entry+pr)*COST; closed=True
            if closed:
                cap+=pnl; trades.append({"pnl":pnl,"won":pnl>0}); pos=0
        if pos==0 and s!=0 and cap>50:
            pos=s; entry=pr; r_sl=atr*sl_m
            sl=entry-r_sl if pos==1 else entry+r_sl
            tp=entry+atr*tp_m if pos==1 else entry-atr*tp_m
            trl=sl; sz=(cap*risk/100)/r_sl if r_sl>0 else 0
        eq.append(cap)

    df_t=pd.DataFrame(trades); eq_s=pd.Series(eq[:len(df)],index=df.index[:len(eq)])
    if df_t.empty or len(df_t)<5: return None
    w=df_t[df_t["pnl"]>0]; l=df_t[df_t["pnl"]<=0]
    gp=w["pnl"].sum(); gl=abs(l["pnl"].sum())
    peak=eq_s.cummax(); dd=(eq_s-peak)/peak*100
    ret=eq_s.pct_change().dropna()
    days=(eq_s.index[-1]-eq_s.index[0]).days
    cagr=((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1))-1)*100
    wr=len(w)/len(df_t)
    sh=ret.mean()/ret.std()*np.sqrt(2190) if ret.std()>0 else 0
    calmar=cagr/abs(dd.min()) if dd.min()<0 else 0
    return {"trades":len(df_t),"wr":round(wr*100,1),"cagr":round(cagr,2),
            "dd":round(dd.min(),2),"pf":round(gp/gl,3) if gl>0 else 999,
            "sharpe":round(sh,3),"calmar":round(calmar,3)}


def score(m, min_t=30):
    if m is None or m["trades"]<min_t or m["cagr"]<=0: return -9999
    pen = max(0,(60-m["trades"])/60)*0.2
    cal = min(m["calmar"],5)/5
    wr  = (m["wr"]/100-0.45)/0.40
    pf  = min(m["pf"],5)/5
    cagr_n = min(m["cagr"],80)/80
    return 0.30*cal + 0.25*wr + 0.25*pf + 0.20*cagr_n - pen


# ─── ESPACIOS DE BUSQUEDA ─────────────────────────────────────────────────────

STRATEGIES = {
    "Weekly Pivot Extended": (sig_weekly_pivot_extended, {
        "vol_mult": [1.0, 1.2, 1.5],
        "tol":      [0.001, 0.002, 0.003],
        "use_r2":   [True, False],
        "use_bounce":[True, False],
        "use_mid":  [True, False],
        "cooldown": [3, 4, 6],
    }),
    "Funding Reversion": (sig_funding_reversion, {
        "thr_pos":  [0.03, 0.05, 0.08, 0.10],
        "thr_neg":  [0.01, 0.02, 0.03],
        "cooldown": [2, 3, 4],
    }),
    "Weekly Pivot": (sig_weekly_pivot, {
        "vol_mult": [1.2, 1.5, 2.0],
        "cooldown": [3, 4, 6],
    }),
    "Momentum Acumulado": (sig_momentum_accumulated, {
        "n_bars":   [2, 3, 4],
        "cooldown": [4, 6, 8],
    }),
    "Liquidity Sweep": (sig_liquidity_sweep, {
        "lookback": [30, 42, 56],  # 5, 7, 9 dias
        "vol_mult": [1.3, 1.5, 2.0],
        "cooldown": [3, 4, 6],
    }),
    "Trend Pullback": (sig_trend_pullback_clean, {
        "ema_fast": [20, 34],
        "ema_slow": [50, 100],
        "tol":      [0.008, 0.012, 0.015],
        "adx_min":  [18, 22, 25],
        "cooldown": [2, 3],
    }),
    "Range Extremes": (sig_range_extremes, {
        "period":   [20, 30, 40],
        "dev":      [2.0, 2.2, 2.5],
        "rsi_os":   [28, 32, 36],
        "rsi_ob":   [64, 68, 72],
        "adx_max":  [20, 25, 30],
        "cooldown": [3, 4],
    }),
}

SL_RANGE   = [1.5, 2.0, 2.5, 3.0, 3.5]
TP_RANGE   = [3.0, 4.0, 5.0, 6.0, 8.0]
N_SAMPLES  = 400


def run(n_per=N_SAMPLES):
    print(f"\n{'='*65}")
    print(f"  SIGMA 4H — RECONSTRUCCION COMPLETA")
    print(f"  {len(STRATEGIES)} estrategias nuevas x {n_per} muestras")
    print(f"  Target: >8% CAGR OOS | >50% WR | 40-70T/año")
    print(f"{'='*65}")

    print("\n[DATA] Cargando datos...")
    df = load_4h()
    df_f = load_funding()

    split  = int(len(df)*0.80)
    df_is  = df.iloc[:split];   df_oos = df.iloc[split:]
    d_is   = (df_is.index[-1]-df_is.index[0]).days
    d_oos  = (df_oos.index[-1]-df_oos.index[0]).days
    print(f"  IS: {d_is}d | OOS: {d_oos}d\n")

    all_valid=[]; best_g=None; best_s=-9999; best_cfg={}; best_name=""

    for strat_name, (fn, space) in STRATEGIES.items():
        print(f"  [{strat_name}]")
        best_strat=None; best_strat_s=-9999

        for _ in range(n_per):
            cfg = {k:random.choice(v) for k,v in space.items()}
            sl  = random.choice(SL_RANGE)
            tp  = random.choice(TP_RANGE)
            if tp<=sl: continue
            trail   = random.choice([True, False])
            trail_m = random.choice([2.0, 2.5, 3.0]) if trail else 2.0
            risk    = random.choice([0.5, 0.8, 1.0, 1.5])

            try:
                sig = fn(df_is, df_f, cfg) if strat_name=="Funding Reversion" else fn(df_is, cfg)
                if (sig!=0).sum()<15: continue
                m = backtest(df_is, sig, sl, tp, risk, trail, trail_m)
                if m is None: continue
                s = score(m, min_t=30)

                if s > best_strat_s: best_strat_s=s; best_strat=m.copy()
                if s > best_s:
                    best_s=s; best_g=m.copy(); best_name=strat_name
                    best_cfg={**cfg,"sl":sl,"tp":tp,"trail":trail,"trail_m":trail_m,"risk":risk}
                    if m["trades"]>=40 and m["cagr"]>5:
                        print(f"  *** NUEVO MEJOR ({strat_name}) ***")
                        print(f"  {m['trades']}T | WR {m['wr']:.1f}% | "
                              f"CAGR {m['cagr']:+.1f}%/año | Calmar {m['calmar']:.2f} | PF {m['pf']:.2f}")

                if m["trades"]>=40 and m["wr"]>=50 and m["cagr"]>0 and m["calmar"]>=0.5:
                    all_valid.append({**m,"strategy":strat_name,"cfg":cfg,"sl":sl,"tp":tp})
            except:
                continue

        if best_strat and best_strat["cagr"]>0:
            print(f"  IS mejor: {best_strat['trades']}T | WR {best_strat['wr']:.1f}% | CAGR {best_strat['cagr']:+.1f}%")
        else:
            print(f"  Sin resultado positivo")

    # OOS validation
    print(f"\n{'='*65}")
    print(f"  OOS VALIDATION — {best_name}")

    if best_g and best_g["cagr"]>0 and best_name:
        fn,_ = STRATEGIES[best_name]
        inner = {k:v for k,v in best_cfg.items() if k not in ("sl","tp","trail","trail_m","risk")}
        try:
            sig_oos = fn(df_oos, df_f, inner) if best_name=="Funding Reversion" else fn(df_oos, inner)
            m_oos   = backtest(df_oos, sig_oos, best_cfg["sl"], best_cfg["tp"],
                               best_cfg["risk"], best_cfg["trail"], best_cfg["trail_m"])
            if m_oos:
                t_yr = round(m_oos["trades"] / d_oos * 365)
                print(f"  OOS 4H: {m_oos['trades']}T (~{t_yr}T/año) | WR {m_oos['wr']:.1f}% | "
                      f"CAGR {m_oos['cagr']:+.1f}%/año | Calmar {m_oos['calmar']:.2f}")

                # Guardar SOLO si supera el mejor actual
                prev_path = OUTPUT_DIR/"models"/"4h"/"best_validated.json"
                prev_cagr = 0.0
                if prev_path.exists():
                    try:
                        prev_d = json.load(open(prev_path))
                        pm = prev_d.get("metrics_oos", prev_d.get("metrics_is", {}))
                        prev_cagr = pm.get("cagr", 0.0)
                    except: pass

                if m_oos["cagr"] > prev_cagr:
                    print(f"  MEJORA: {prev_cagr:+.1f}% → {m_oos['cagr']:+.1f}% CAGR — GUARDANDO")
                    out_dir = OUTPUT_DIR/"models"/"4h"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    result = {"tf":"4h","strategy":best_name,"params":best_cfg,
                              "metrics_is": {k:round(v,4) if isinstance(v,float) else v
                                             for k,v in best_g.items()},
                              "metrics_oos":{k:round(v,4) if isinstance(v,float) else v
                                             for k,v in m_oos.items()},
                              "score":score(m_oos,min_t=15)}
                    with open(out_dir/"best_validated.json","w") as f:
                        json.dump(result, f, indent=2)
                    print(f"  [SAVED] models/4h/best_validated.json")
            else:
                print(f"  OOS: sin trades suficientes")
        except Exception as e:
            print(f"  OOS error: {e}")

    all_valid.sort(key=lambda x:x.get("calmar",0), reverse=True)
    print(f"\n  Configs validas (IS): {len(all_valid)}")
    if all_valid:
        print(f"  TOP 5:")
        for i,m in enumerate(all_valid[:5],1):
            print(f"  {i}. {m['strategy']}: {m['trades']}T | WR {m['wr']:.1f}% | "
                  f"CAGR {m['cagr']:+.1f}%/año | Calmar {m['calmar']:.2f}")

        pd.DataFrame(all_valid).to_csv(
            OUTPUT_DIR/"results"/"reports"/"4h_rebuild_results.csv", index=False)

    # Notificar
    try:
        import winsound, subprocess
        for _ in range(3): winsound.Beep(1200, 300)
        m = all_valid[0] if all_valid else {}
        msg = (f"4H RECONSTRUCCION COMPLETA\\n\\n"
               f"Configs validas: {len(all_valid)}\\n"
               f"MEJOR: {m.get('strategy','?')} | {m.get('trades',0)}T | "
               f"WR {m.get('wr',0):.1f}% | CAGR {m.get('cagr',0):+.1f}%")
        subprocess.Popen(["powershell","-WindowStyle","Hidden","-Command",
            f'Add-Type -AssemblyName PresentationFramework;'
            f'[System.Windows.MessageBox]::Show("{msg}","4H Rebuild","OK","Information")'])
    except: pass

    print(f"\n[DONE] 4H rebuild completado.")
    return all_valid


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=int, default=N_SAMPLES)
    a = p.parse_args()
    run(a.samples)

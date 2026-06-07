"""
SIGMA ENGINE — Reconstruccion 15m con estrategias objetivas

Igual que 4H con Weekly Pivots, el 15m necesita señales basadas
en niveles OBJETIVOS que no se fiten al dataset historico.

Estrategias para BTC 15m:
1. Daily Pivot Breakout   — PP/R1/S1 diarios (mismo concepto que 4H con semanales)
2. Session Open Momentum  — primeros 15m de London y NY con direccion clara
3. VWAP Intraday         — rebotes desde VWAP diario en sesiones
4. Hourly High/Low Break  — breakout de rango de la hora anterior
5. EMA Ribbon Pullback    — pullback a zona EMA en tendencia fuerte

Target: 80-120 trades/año | OOS CAGR > 10% | WR > 45%
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, random, numpy as np, pandas as pd
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

random.seed(42); np.random.seed(42)

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION = 0.0004; SLIPPAGE = 0.0001; COST = COMMISSION + SLIPPAGE
CAPITAL    = 1000.0


def load_15m():
    from core.data import fetch_ohlcv

    max_p = OUTPUT_DIR / "models" / "data_15m_max.csv"
    if max_p.exists():
        df = pd.read_csv(max_p, index_col=0, parse_dates=True)
        df.index.name = "timestamp"
    else:
        df = fetch_ohlcv(tf="15m", days=730)

    df["atr"]    = _atr(df, 14)
    df["ema20"]  = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"]  = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["vol_ma"] = df["volume"].rolling(20).mean()
    df["rsi"]    = _rsi(df["close"], 14)
    df.dropna(inplace=True)
    print(f"  15M: {len(df):,} velas | {(df.index[-1]-df.index[0]).days} dias")
    return df


def _atr(df, n=14):
    h,l,c = df["high"],df["low"],df["close"]
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(com=n-1,adjust=False).mean()


def _rsi(s, n=14):
    d=s.diff(); u=d.clip(lower=0); dn=-d.clip(upper=0)
    rs=u.ewm(com=n-1,adjust=False).mean()/dn.ewm(com=n-1,adjust=False).mean().replace(0,np.nan)
    return 100-100/(1+rs)


def _cd(sig, bars):
    arr=sig.to_numpy().copy(); last=-bars-1
    for i in range(len(arr)):
        if arr[i]!=0:
            if (i-last)>=bars: last=i
            else: arr[i]=0
    return pd.Series(arr, index=sig.index)


# ─── ESTRATEGIAS ──────────────────────────────────────────────────────────────

def sig_daily_pivot(df, cfg):
    """Daily Pivot Breakout — mismo enfoque que Weekly Pivot en 4H pero diario."""
    daily = df.resample("D").agg({"high":"max","low":"min","close":"last"})
    daily["pp"] = (daily["high"]+daily["low"]+daily["close"])/3
    daily["r1"] = 2*daily["pp"]-daily["low"]
    daily["s1"] = 2*daily["pp"]-daily["high"]
    daily["r2"] = daily["pp"]+(daily["high"]-daily["low"])
    daily["s2"] = daily["pp"]-(daily["high"]-daily["low"])

    pp = daily["pp"].shift(1).reindex(df.index, method="ffill")
    r1 = daily["r1"].shift(1).reindex(df.index, method="ffill")
    s1 = daily["s1"].shift(1).reindex(df.index, method="ffill")

    vol_ok = df["volume"] > df["vol_ma"] * cfg.get("vol_mult", 1.3)
    bull   = df["ema50"] > df["ema200"]
    bear   = df["ema50"] < df["ema200"]

    break_r1 = (df["close"] > r1) & (df["close"].shift(1) <= r1) & vol_ok
    break_s1 = (df["close"] < s1) & (df["close"].shift(1) >= s1) & vol_ok
    bounce_l = (df["low"] <= pp*1.001) & (df["close"] > pp) & (df["close"]>df["open"]) & bull
    bounce_s = (df["high"]>= pp*0.999) & (df["close"] < pp) & (df["close"]<df["open"]) & bear

    sig = pd.Series(0, index=df.index)
    sig[break_r1 | bounce_l] =  1
    sig[break_s1 | bounce_s] = -1
    return _cd(sig, cfg.get("cooldown", 4))


def sig_session_open(df, cfg):
    """Session Open Momentum — primeros N minutos de London y NY."""
    h_utc = df.index.hour
    m_utc = df.index.minute
    n     = cfg.get("n_bars", 4)  # primeras N velas de la sesion

    # London: 08:00-10:00 UTC | NY: 13:00-15:00 UTC
    lon_open = (h_utc == 8)  & (m_utc < n*15)
    ny_open  = (h_utc == 13) & (m_utc < n*15)
    in_open  = lon_open | ny_open

    # Direccion: vela de apertura + confirmacion siguiente
    bull_open = (df["close"] > df["open"]) & (df["close"] > df["close"].shift(1))
    bear_open = (df["close"] < df["open"]) & (df["close"] < df["close"].shift(1))

    vol_ok = df["volume"] > df["vol_ma"] * cfg.get("vol_mult", 1.2)
    htf    = df["ema50"] > df["ema200"]

    sig = pd.Series(0, index=df.index)
    sig[in_open & bull_open & vol_ok & htf]  =  1
    sig[in_open & bear_open & vol_ok & ~htf] = -1
    return _cd(sig, cfg.get("cooldown", 8))


def sig_vwap_intraday(df, cfg):
    """VWAP Intraday — rebote desde VWAP diario en sesion activa."""
    typ = (df["high"]+df["low"]+df["close"])/3
    vwap_num = (typ*df["volume"]).groupby(pd.Grouper(freq="D")).cumsum()
    vwap_den = df["volume"].groupby(pd.Grouper(freq="D")).cumsum()
    vwap = vwap_num / vwap_den.replace(0, np.nan)

    dev = cfg.get("dev", 0.003)
    h_utc = df.index.hour
    in_sess = ((h_utc>=8)&(h_utc<20)) | (h_utc>=22) | (h_utc<6)

    bull = df["ema20"] > df["ema50"]
    bear = df["ema20"] < df["ema50"]

    near_l = (df["low"] <= vwap*(1+dev)) & (df["close"] > vwap) & (df["close"]>df["open"])
    near_s = (df["high"]>= vwap*(1-dev)) & (df["close"] < vwap) & (df["close"]<df["open"])

    sig = pd.Series(0, index=df.index)
    sig[near_l & bull & in_sess] =  1
    sig[near_s & bear & in_sess] = -1
    return _cd(sig, cfg.get("cooldown", 4))


def sig_hourly_breakout(df, cfg):
    """Breakout del rango de la hora anterior — niveles de referencia frescos."""
    lb = cfg.get("lb", 4)  # 4 x 15m = 1 hora
    hh = df["high"].rolling(lb).max().shift(1)
    ll = df["low"].rolling(lb).min().shift(1)
    vol_ok = df["volume"] > df["vol_ma"] * cfg.get("vol_mult", 1.5)
    bull = df["ema20"] > df["ema50"]
    bear = df["ema20"] < df["ema50"]

    sig = pd.Series(0, index=df.index)
    sig[(df["close"]>hh) & vol_ok & bull] =  1
    sig[(df["close"]<ll) & vol_ok & bear] = -1
    return _cd(sig, cfg.get("cooldown", 4))


def sig_ema_ribbon(df, cfg):
    """EMA Ribbon Pullback — pullback a zona EMA en tendencia fuerte."""
    f=cfg.get("fast",8); m=cfg.get("mid",21); s=cfg.get("slow",55)
    ef=df["close"].ewm(span=f,adjust=False).mean()
    em=df["close"].ewm(span=m,adjust=False).mean()
    es=df["close"].ewm(span=s,adjust=False).mean()

    ribbon_bull = (ef>em) & (em>es) & (ef>es)
    ribbon_bear = (ef<em) & (em<es) & (ef<es)

    tol = cfg.get("tol", 0.003)
    touch_l = (df["low"]<=em*(1+tol)) & (df["close"]>em) & (df["close"]>df["open"])
    touch_s = (df["high"]>=em*(1-tol)) & (df["close"]<em) & (df["close"]<df["open"])

    adx = df.get("adx", pd.Series(25, index=df.index))
    strong = adx > cfg.get("adx_min", 20) if "adx" in df.columns else pd.Series(True, index=df.index)

    sig = pd.Series(0, index=df.index)
    sig[touch_l & ribbon_bull & strong] =  1
    sig[touch_s & ribbon_bear & strong] = -1
    return _cd(sig, cfg.get("cooldown", 3))


# ─── BACKTEST ─────────────────────────────────────────────────────────────────

def backtest(df, sig, sl_m, tp_m, risk=0.5):
    closes=df["close"].to_numpy(); highs=df["high"].to_numpy()
    lows=df["low"].to_numpy();     atrs=df["atr"].to_numpy()
    sigs=sig.to_numpy()

    cap=CAPITAL; eq=[cap]; pos=0; entry=sl=tp=sz=0.0; trades=[]
    for i in range(1,len(closes)):
        pr=closes[i]; atr=atrs[i-1]; h_=highs[i]; lo=lows[i]; s=sigs[i-1]
        if pos!=0:
            pnl=0.; closed=False
            if pos==1:
                if lo<=sl: pnl=sz*(sl-entry)-sz*(entry+sl)*COST; closed=True
                elif h_>=tp: pnl=sz*(tp-entry)-sz*(entry+tp)*COST; closed=True
            else:
                if h_>=sl: pnl=sz*(entry-sl)-sz*(entry+sl)*COST; closed=True
                elif lo<=tp: pnl=sz*(entry-tp)-sz*(entry+tp)*COST; closed=True
            if not closed and s==-pos:
                pnl=pos*sz*(pr-entry)-sz*(entry+pr)*COST; closed=True
            if closed: cap+=pnl; trades.append({"pnl":pnl,"won":pnl>0}); pos=0
        if pos==0 and s!=0 and cap>50:
            pos=s; entry=pr; rsl=atr*sl_m
            sl=entry-rsl if pos==1 else entry+rsl
            tp=entry+atr*tp_m if pos==1 else entry-atr*tp_m
            sz=(cap*risk/100)/rsl if rsl>0 else 0
        eq.append(cap)

    df_t=pd.DataFrame(trades); eq_s=pd.Series(eq[:len(df)],index=df.index[:len(eq)])
    if df_t.empty or len(df_t)<10: return None
    w=df_t[df_t["pnl"]>0]; l=df_t[df_t["pnl"]<=0]
    gp=w["pnl"].sum(); gl=abs(l["pnl"].sum())
    peak=eq_s.cummax(); dd=(eq_s-peak)/peak*100
    ret=eq_s.pct_change().dropna()
    days=(eq_s.index[-1]-eq_s.index[0]).days
    cagr=((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1))-1)*100
    wr=len(w)/len(df_t)
    sh=ret.mean()/ret.std()*np.sqrt(35040) if ret.std()>0 else 0
    calmar=cagr/abs(dd.min()) if dd.min()<0 else 0
    return {"trades":len(df_t),"wr":round(wr*100,1),"cagr":round(cagr,2),
            "dd":round(dd.min(),2),"pf":round(gp/gl,3) if gl>0 else 999,
            "sharpe":round(sh,3),"calmar":round(calmar,3)}


def score(m, min_t=60):
    if m is None or m["trades"]<min_t or m["cagr"]<=0: return -9999
    pen  = max(0,(120-m["trades"])/120)*0.2
    cal  = min(m["calmar"],5)/5
    wr   = (m["wr"]/100-0.42)/0.38
    pf   = min(m["pf"],4)/4
    cagr_n = min(m["cagr"],60)/60
    return 0.30*cal+0.25*wr+0.25*pf+0.20*cagr_n-pen


STRATEGIES = {
    "Daily Pivot":    (sig_daily_pivot, {
        "vol_mult": [1.2, 1.5, 2.0],
        "cooldown": [3, 4, 6],
    }),
    "Session Open":   (sig_session_open, {
        "n_bars":   [2, 3, 4],
        "vol_mult": [1.2, 1.5],
        "cooldown": [6, 8, 12],
    }),
    "VWAP Intraday":  (sig_vwap_intraday, {
        "dev":      [0.002, 0.003, 0.004],
        "cooldown": [3, 4, 6],
    }),
    "Hourly Breakout":(sig_hourly_breakout, {
        "lb":       [4, 8],
        "vol_mult": [1.3, 1.5, 2.0],
        "cooldown": [3, 4, 6],
    }),
    "EMA Ribbon":     (sig_ema_ribbon, {
        "fast":     [8, 12],
        "mid":      [21, 34],
        "slow":     [55, 89],
        "tol":      [0.002, 0.003, 0.005],
        "adx_min":  [18, 22, 25],
        "cooldown": [2, 3, 4],
    }),
}

SL_RANGE = [1.0, 1.5, 2.0, 2.5]
TP_RANGE = [2.0, 3.0, 4.0, 5.0]
N_SAMPLES = 500


def run(n_per=N_SAMPLES):
    print(f"\n{'='*65}")
    print(f"  SIGMA 15M — RECONSTRUCCION CON NIVELES OBJETIVOS")
    print(f"  {len(STRATEGIES)} estrategias x {n_per} muestras")
    print(f"  Target: >10% CAGR OOS | >45% WR | 80-120T/año")
    print(f"{'='*65}")

    print("\n[DATA] Cargando 15m...")
    df = load_15m()
    split  = int(len(df)*0.80)
    df_is  = df.iloc[:split]; df_oos = df.iloc[split:]
    d_is   = (df_is.index[-1]-df_is.index[0]).days
    d_oos  = (df_oos.index[-1]-df_oos.index[0]).days
    print(f"  IS: {d_is}d | OOS: {d_oos}d\n")

    all_valid=[]; best_g=None; best_s=-9999; best_cfg={}; best_name=""

    for name,(fn,space) in STRATEGIES.items():
        print(f"  [{name}]")
        best_st=None; best_st_s=-9999

        for _ in range(n_per):
            cfg={k:random.choice(v) for k,v in space.items()}
            sl=random.choice(SL_RANGE); tp=random.choice(TP_RANGE)
            if tp<=sl: continue
            risk=random.choice([0.3, 0.5, 0.8, 1.0])

            try:
                sig=fn(df_is,cfg)
                if (sig!=0).sum()<30: continue
                m=backtest(df_is,sig,sl,tp,risk)
                if m is None: continue
                s=score(m,min_t=60)
                if s>best_st_s: best_st_s=s; best_st=m.copy()
                if s>best_s:
                    best_s=s; best_g=m.copy(); best_name=name
                    best_cfg={**cfg,"sl":sl,"tp":tp,"risk":risk}
                    if m["trades"]>=80 and m["cagr"]>8:
                        print(f"  *** {name} ***")
                        print(f"  {m['trades']}T | WR {m['wr']:.1f}% | CAGR {m['cagr']:+.1f}% | Calmar {m['calmar']:.2f}")
                if m["trades"]>=80 and m["wr"]>=45 and m["cagr"]>0 and m["calmar"]>=0.5:
                    all_valid.append({**m,"strategy":name,"cfg":cfg,"sl":sl,"tp":tp})
            except: continue

        if best_st and best_st["cagr"]>0:
            print(f"  IS mejor: {best_st['trades']}T | WR {best_st['wr']:.1f}% | CAGR {best_st['cagr']:+.1f}%")
        else:
            print(f"  Sin resultado positivo")

    print(f"\n{'='*65}")
    print(f"  OOS VALIDATION — {best_name}")

    if best_g and best_g["cagr"]>0 and best_name:
        fn,_=STRATEGIES[best_name]
        inner={k:v for k,v in best_cfg.items() if k not in ("sl","tp","risk")}
        try:
            sig_oos=fn(df_oos,inner)
            m_oos=backtest(df_oos,sig_oos,best_cfg["sl"],best_cfg["tp"],best_cfg["risk"])
            if m_oos:
                t_yr=round(m_oos["trades"]/d_oos*365)
                print(f"  OOS 15M: {m_oos['trades']}T (~{t_yr}T/año) | WR {m_oos['wr']:.1f}% | "
                      f"CAGR {m_oos['cagr']:+.1f}%/año | Calmar {m_oos['calmar']:.2f}")
                print(f"  IS WR: {best_g['wr']:.1f}% → OOS WR: {m_oos['wr']:.1f}% "
                      f"({'consistente' if abs(best_g['wr']-m_oos['wr'])<8 else 'DIVERGE'})")

                if m_oos["cagr"]>0:
                    out_dir=OUTPUT_DIR/"models"/"15m"
                    out_dir.mkdir(parents=True,exist_ok=True)
                    result={"tf":"15m","strategy":best_name,"params":best_cfg,
                            "metrics_is":{k:round(v,4) if isinstance(v,float) else v
                                          for k,v in best_g.items()},
                            "metrics_oos":{k:round(v,4) if isinstance(v,float) else v
                                           for k,v in m_oos.items()},
                            "score":score(m_oos,min_t=40)}
                    with open(out_dir/"best_validated.json","w") as f:
                        json.dump(result,f,indent=2)
                    print(f"  [SAVED] models/15m/best_validated.json")
            else:
                print(f"  OOS sin trades suficientes")
        except Exception as e:
            print(f"  OOS error: {e}")

    all_valid.sort(key=lambda x:x.get("calmar",0),reverse=True)
    print(f"\n  Configs validas IS: {len(all_valid)}")
    if all_valid:
        print(f"  TOP 5:")
        for i,m in enumerate(all_valid[:5],1):
            print(f"  {i}. {m['strategy']}: {m['trades']}T | WR {m['wr']:.1f}% | "
                  f"CAGR {m['cagr']:+.1f}% | Calmar {m['calmar']:.2f}")

    print(f"\n[DONE] 15M rebuild completado.")
    return all_valid


if __name__ == "__main__":
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--samples",type=int,default=N_SAMPLES)
    a=p.parse_args()
    run(a.samples)

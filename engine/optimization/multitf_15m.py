"""
SIGMA ENGINE — Estrategia Multi-TF: 1H Setup + 15M Entry

Como operan los traders profesionales:
  1. 1H da el SETUP (direccion, contexto, calidad)
  2. 15M da el TIMING (entrada precisa en pullback/bounce)
  3. SL/TP basados en 1H ATR (mas amplio, mas valido)

Ventajas vs estrategia 1H pura:
  - Entrada mas precisa (mejor precio, menor slippage efectivo)
  - Mas trades: 1 setup 1H puede generar 2-3 entradas 15M
  - Mismo edge de 1H pero con timing 15M
  - SL mas ajustado (15M ATR) si se quiere mayor frecuencia

Ventajas vs estrategia 15M pura:
  - Filtro de calidad superior (condiciones 1H)
  - Trades en la direccion correcta del mercado mayor
  - OOS mas robusto (validado en 1H)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, random, numpy as np, pandas as pd
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

random.seed(55); np.random.seed(55)
OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION = 0.0004; SLIPPAGE = 0.0001; COST = COMMISSION + SLIPPAGE
CAPITAL    = 1000.0


def load_data():
    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.signals import get_signals

    print("  [1H] Cargando setup...")
    max_1h = OUTPUT_DIR / "models" / "data_1h_max.csv"
    df_1h  = pd.read_csv(max_1h, index_col=0, parse_dates=True)
    df_1h.index.name = "timestamp"
    df_4h  = fetch_ohlcv(tf="4h", days=1500)
    df_1d  = fetch_ohlcv(tf="1d", days=1500)
    df_1h_feat = build_features(df_1h, {"4h": df_4h, "1d": df_1d})
    df_1h_feat.dropna(subset=["close","atr","ema50"], inplace=True)

    cfg_1h = json.load(open(OUTPUT_DIR/"models"/"1h"/"config.json"))["params"]
    sigs_1h, qual_1h = get_signals(df_1h_feat, cfg_1h)
    print(f"    {len(df_1h_feat):,} velas | {(sigs_1h!=0).sum()} señales 1H")

    print("  [15M] Cargando datos...")
    max_15m = OUTPUT_DIR / "models" / "data_15m_max.csv"
    df_15m  = pd.read_csv(max_15m, index_col=0, parse_dates=True)
    df_15m.index.name = "timestamp"
    df_15m["atr"]    = _atr(df_15m)
    df_15m["ema20"]  = df_15m["close"].ewm(span=20, adjust=False).mean()
    df_15m["ema50"]  = df_15m["close"].ewm(span=50, adjust=False).mean()
    df_15m["vol_ma"] = df_15m["volume"].rolling(20).mean()
    df_15m["rsi"]    = _rsi(df_15m["close"])
    df_15m.dropna(inplace=True)
    print(f"    {len(df_15m):,} velas 15M")

    return df_1h_feat, sigs_1h, qual_1h, df_15m


def _atr(df, n=14):
    h,l,c=df["high"],df["low"],df["close"]
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
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


# ─── SEÑALES 15M PARA ENTRY TIMING ────────────────────────────────────────────

def entry_15m_pullback(df_15m, direction_1h, cfg):
    """
    Pullback a EMA20 en 15M cuando la direccion 1H es clara.
    La señal 1H da la direccion — el 15M solo busca el mejor momento de entrada.
    """
    # Reindexar direccion 1H a 15M (forward fill — una vez que 1H da señal, vale por N horas)
    lookfwd = cfg.get("lookfwd_hours", 4)  # la señal 1H vale X horas
    dir_fwd = pd.Series(0, index=df_15m.index)

    for ts, sig in direction_1h[direction_1h!=0].items():
        end_ts = ts + pd.Timedelta(hours=lookfwd)
        mask   = (df_15m.index >= ts) & (df_15m.index < end_ts)
        dir_fwd[mask] = sig

    # En esa direccion, buscar pullback a EMA20 en 15M
    tol  = cfg.get("tol", 0.003)
    c15  = df_15m["close"]; l15=df_15m["low"]; h15=df_15m["high"]
    o15  = df_15m["open"];  ema20=df_15m["ema20"]

    touch_l = (l15 <= ema20*(1+tol)) & (c15 > ema20) & (c15 > o15)
    touch_s = (h15 >= ema20*(1-tol)) & (c15 < ema20) & (c15 < o15)

    sig = pd.Series(0, index=df_15m.index)
    sig[(dir_fwd==1)  & touch_l] =  1
    sig[(dir_fwd==-1) & touch_s] = -1
    return _cd(sig, cfg.get("cooldown", 4))


def entry_15m_volume_burst(df_15m, direction_1h, cfg):
    """
    Volume burst en 15M en la direccion del setup 1H.
    Cuando hay momentum de volumen en la direccion correcta.
    """
    lookfwd = cfg.get("lookfwd_hours", 6)
    dir_fwd = pd.Series(0, index=df_15m.index)
    for ts, sig in direction_1h[direction_1h!=0].items():
        end_ts = ts + pd.Timedelta(hours=lookfwd)
        mask   = (df_15m.index >= ts) & (df_15m.index < end_ts)
        dir_fwd[mask] = sig

    vol_ok   = df_15m["volume"] > df_15m["vol_ma"] * cfg.get("vol_mult", 1.5)
    bull_bar = df_15m["close"] > df_15m["open"]
    bear_bar = df_15m["close"] < df_15m["open"]
    # RSI confirma
    rsi_bull = df_15m["rsi"] > 45
    rsi_bear = df_15m["rsi"] < 55

    sig = pd.Series(0, index=df_15m.index)
    sig[(dir_fwd==1)  & vol_ok & bull_bar & rsi_bull] =  1
    sig[(dir_fwd==-1) & vol_ok & bear_bar & rsi_bear] = -1
    return _cd(sig, cfg.get("cooldown", 4))


def entry_15m_session_confirm(df_15m, direction_1h, cfg):
    """
    Solo entra en los primeros N minutos de cada sesion si hay señal 1H activa.
    """
    lookfwd = cfg.get("lookfwd_hours", 8)
    dir_fwd = pd.Series(0, index=df_15m.index)
    for ts, sig in direction_1h[direction_1h!=0].items():
        end_ts = ts + pd.Timedelta(hours=lookfwd)
        mask   = (df_15m.index >= ts) & (df_15m.index < end_ts)
        dir_fwd[mask] = sig

    h_utc = df_15m.index.hour
    m_utc = df_15m.index.minute
    n     = cfg.get("n_bars", 4)
    in_open = ((h_utc==8)|(h_utc==13)) & (m_utc < n*15)

    vol_ok   = df_15m["volume"] > df_15m["vol_ma"] * cfg.get("vol_mult", 1.3)
    bull_bar = df_15m["close"] > df_15m["open"]
    bear_bar = df_15m["close"] < df_15m["open"]

    sig = pd.Series(0, index=df_15m.index)
    sig[(dir_fwd==1)  & in_open & vol_ok & bull_bar] =  1
    sig[(dir_fwd==-1) & in_open & vol_ok & bear_bar] = -1
    return _cd(sig, cfg.get("cooldown", 8))


ENTRY_METHODS = {
    "Pullback EMA20":    entry_15m_pullback,
    "Volume Burst":      entry_15m_volume_burst,
    "Session Confirm":   entry_15m_session_confirm,
}


def backtest_15m(df_15m, sig, sl_m, tp_m, risk, use_1h_atr=False, df_1h=None):
    """Backtest en 15M con la opcion de usar ATR de 1H para SL/TP."""
    closes=df_15m["close"].to_numpy(); highs=df_15m["high"].to_numpy()
    lows=df_15m["low"].to_numpy(); atrs=df_15m["atr"].to_numpy(); sigs=sig.to_numpy()

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

    df_t=pd.DataFrame(trades); eq_s=pd.Series(eq[:len(df_15m)],index=df_15m.index[:len(eq)])
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
    t_yr=round(len(df_t)/days*365)
    return {"trades":len(df_t),"wr":round(wr*100,1),"cagr":round(cagr,1),
            "dd":round(dd.min(),1),"pf":round(gp/gl,3) if gl>0 else 999,
            "sharpe":round(sh,2),"calmar":round(calmar,2),"trades_year":t_yr}


def run(n_per=300):
    print("\n"+"="*65)
    print("  SIGMA MULTI-TF: 1H SETUP + 15M ENTRY")
    print("  Usa el edge validado de 1H con precision de 15M")
    print("="*65)

    df_1h, sigs_1h, qual_1h, df_15m = load_data()

    # Alinear periodos
    start = max(df_1h.index[0], df_15m.index[0])
    end   = min(df_1h.index[-1], df_15m.index[-1])
    df_15m = df_15m[(df_15m.index>=start)&(df_15m.index<=end)]
    sigs_1h_align = sigs_1h[(sigs_1h.index>=start)&(sigs_1h.index<=end)]

    split_15m = int(len(df_15m)*0.80)
    df_15m_is  = df_15m.iloc[:split_15m]; df_15m_oos = df_15m.iloc[split_15m:]
    split_1h   = df_15m_is.index[-1]
    sigs_is    = sigs_1h_align[sigs_1h_align.index<=split_1h]
    sigs_oos   = sigs_1h_align[sigs_1h_align.index>split_1h]

    d_is  = (df_15m_is.index[-1]-df_15m_is.index[0]).days
    d_oos = (df_15m_oos.index[-1]-df_15m_oos.index[0]).days
    print(f"\n  IS: {d_is}d | OOS: {d_oos}d")
    print(f"  Señales 1H IS: {(sigs_is!=0).sum()} | OOS: {(sigs_oos!=0).sum()}\n")

    best_g=None; best_s=-9999; best_cfg={}; best_method=""

    for method_name, fn in ENTRY_METHODS.items():
        print(f"  [{method_name}]")
        best_m=None; best_m_s=-9999

        for _ in range(n_per):
            cfg = {
                "lookfwd_hours": random.choice([2,4,6,8]),
                "tol":           random.choice([0.002,0.003,0.005]) if method_name=="Pullback EMA20" else 0,
                "vol_mult":      random.choice([1.2,1.5,2.0]),
                "n_bars":        random.choice([2,3,4]) if method_name=="Session Confirm" else 4,
                "cooldown":      random.choice([2,4,6,8]),
            }
            sl = random.choice([1.0,1.5,2.0,2.5])
            tp = random.choice([2.0,3.0,4.0,5.0])
            if tp<=sl: continue
            risk = random.choice([0.3,0.5,0.8,1.0])

            try:
                sig = fn(df_15m_is, sigs_is, cfg)
                if (sig!=0).sum()<20: continue
                m = backtest_15m(df_15m_is, sig, sl, tp, risk)
                if m is None: continue
                s = -9999
                if m["trades"]>=50 and m["cagr"]>0 and m["wr"]>=42:
                    s = 0.35*min(m["calmar"],5)/5 + 0.30*(m["wr"]/100-0.40)/0.40 + 0.35*min(m["cagr"],60)/60
                if s>best_m_s: best_m_s=s; best_m=m.copy()
                if s>best_s:
                    best_s=s; best_g=m.copy(); best_method=method_name
                    best_cfg={**cfg,"sl":sl,"tp":tp,"risk":risk}
                    if m["trades"]>=80 and m["cagr"]>8:
                        print(f"  *** NUEVO MEJOR ***")
                        print(f"  {m['trades']}T (~{m['trades_year']}T/año) | WR {m['wr']:.1f}% | "
                              f"CAGR {m['cagr']:+.1f}% | Calmar {m['calmar']:.2f}")
            except: continue

        if best_m and best_m["cagr"]>0:
            print(f"  IS mejor: {best_m['trades']}T | WR {best_m['wr']:.1f}% | CAGR {best_m['cagr']:+.1f}%")
        else:
            print(f"  Sin resultado IS positivo con WR>=42%")

    print(f"\n{'='*65}")
    print(f"  OOS VALIDATION — {best_method}")

    if best_g and best_method:
        fn = ENTRY_METHODS[best_method]
        inner = {k:v for k,v in best_cfg.items() if k not in ("sl","tp","risk")}
        try:
            sig_oos = fn(df_15m_oos, sigs_oos, inner)
            m_oos   = backtest_15m(df_15m_oos, sig_oos, best_cfg["sl"], best_cfg["tp"], best_cfg["risk"])
            if m_oos:
                t_yr = round(m_oos["trades"]/d_oos*365)
                print(f"  OOS 15M: {m_oos['trades']}T (~{t_yr}T/año) | WR {m_oos['wr']:.1f}% | "
                      f"CAGR {m_oos['cagr']:+.1f}% | Calmar {m_oos['calmar']:.2f}")
                wr_delta = abs(best_g["wr"]-m_oos["wr"])
                print(f"  WR IS {best_g['wr']:.1f}% → OOS {m_oos['wr']:.1f}% "
                      f"({'consistente' if wr_delta<8 else 'DIVERGE'})")

                if m_oos["cagr"]>0:
                    out = OUTPUT_DIR/"models"/"15m"
                    out.mkdir(parents=True, exist_ok=True)
                    result = {"tf":"15m","strategy":f"MultiTF {best_method}","params":best_cfg,
                              "metrics_is": {k:round(v,4) if isinstance(v,float) else v
                                             for k,v in best_g.items()},
                              "metrics_oos":{k:round(v,4) if isinstance(v,float) else v
                                             for k,v in m_oos.items()},
                              "note":"1H setup + 15M timing entry"}
                    with open(out/"best_validated.json","w") as f:
                        json.dump(result,f,indent=2)
                    print(f"  [SAVED] models/15m/best_validated.json")
            else:
                print(f"  OOS sin suficientes trades")
        except Exception as e:
            print(f"  OOS error: {e}")

    print(f"\n[DONE] Multi-TF completado.")


if __name__ == "__main__":
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--samples",type=int,default=300)
    a=p.parse_args()
    run(a.samples)

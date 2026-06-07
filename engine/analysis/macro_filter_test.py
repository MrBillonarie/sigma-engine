"""
SIGMA — Test filtro macro (EMA200 diario)

Solo operar cuando BTC esta en tendencia macro clara:
  BULL: close_diario > EMA200_diario AND EMA50 > EMA200
  BEAR: close_diario < EMA200_diario AND EMA50 < EMA200
  RANGO: ni bull ni bear -> no operar
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json, numpy as np, pandas as pd
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent.parent
CAPITAL=1000.0; COMMISSION=0.0004; SLIPPAGE=0.0001; COST=COMMISSION+SLIPPAGE


def load():
    from core.data import fetch_ohlcv
    from core.features import build_features
    max_p = OUTPUT_DIR/"models"/"data_1h_max.csv"
    df_b  = pd.read_csv(max_p, index_col=0, parse_dates=True); df_b.index.name="timestamp"
    df_4h = fetch_ohlcv(tf="4h", days=1500)
    df_1d = fetch_ohlcv(tf="1d", days=1500)
    df    = build_features(df_b, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close","atr","ema50"], inplace=True)
    return df, df_1d


def build_macro(df, df_1d):
    e200 = df_1d["close"].ewm(span=200, adjust=False).mean()
    e50  = df_1d["close"].rolling(50).mean()
    bull = (df_1d["close"] > e200) & (e50 > e200)
    bear = (df_1d["close"] < e200) & (e50 < e200)
    bull_1h = bull.reindex(df.index, method="ffill").fillna(False)
    bear_1h = bear.reindex(df.index, method="ffill").fillna(False)
    return bull_1h, bear_1h


def bt(df_w, sig_w, q_w, cfg, allow_long=None, allow_short=None):
    closes=df_w["close"].to_numpy(); highs=df_w["high"].to_numpy()
    lows=df_w["low"].to_numpy();     atrs=df_w["atr"].to_numpy()
    sigs=sig_w.to_numpy()
    quals=q_w.map({"ELITE_ICT":3,"ELITE":2,"EXECUTE":1}).fillna(0).astype(int).to_numpy() if hasattr(q_w,"map") else q_w.to_numpy()
    al = allow_long.to_numpy()  if allow_long  is not None else np.ones(len(sigs),bool)
    as_ = allow_short.to_numpy() if allow_short is not None else np.ones(len(sigs),bool)

    e_sl=cfg.get("elite_sl_mult",2.4); e_tp=cfg.get("elite_tp_mult",2.0)
    x_sl=cfg.get("exec_sl_mult", 1.9); x_tp=cfg.get("exec_tp_mult", 3.5)
    risk=cfg.get("risk_pct",1.5);      q65=cfg.get("qty_tp1",0.65)

    cap=CAPITAL; trades=[]; pos=0
    entry=sl=tp1=tp2=sz=sz2=0.0; tp1_done=False
    for i in range(1, len(closes)):
        pr=closes[i]; atr=atrs[i-1]; h_=highs[i]; lo_=lows[i]
        s=sigs[i-1]; q=quals[i-1]
        if s==1  and not al[i-1]:  s=0
        if s==-1 and not as_[i-1]: s=0
        if pos!=0:
            closed=False; pnl=0.0
            if pos==1:
                if lo_<=sl: pnl=(sz+sz2)*(sl-entry)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif h_>=tp1 and not tp1_done:
                    p1=sz*(tp1-entry)-sz*(entry+tp1)*COST; cap+=p1; trades.append(p1>0); sz=0; tp1_done=True
                elif h_>=tp2: pnl=sz2*(tp2-entry)-sz2*(entry+tp2)*COST; closed=True
            else:
                if h_>=sl: pnl=(sz+sz2)*(entry-sl)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif lo_<=tp1 and not tp1_done:
                    p1=sz*(entry-tp1)-sz*(entry+tp1)*COST; cap+=p1; trades.append(p1>0); sz=0; tp1_done=True
                elif lo_<=tp2: pnl=sz2*(entry-tp2)-sz2*(entry+tp2)*COST; closed=True
            if not closed and s==-pos:
                rem=sz+sz2; pnl=pos*rem*(pr-entry)-rem*(entry+pr)*COST; closed=True
            if closed: cap+=pnl; trades.append(pnl>0); pos=0; tp1_done=False
        if pos==0 and s!=0 and cap>50:
            is_el=q>=2; sl_m=e_sl if is_el else x_sl; tp_m=e_tp if is_el else x_tp
            pos=s; entry=pr; rsl=atr*sl_m
            sl=entry-rsl if pos==1 else entry+rsl
            tp1=entry+atr*tp_m if pos==1 else entry-atr*tp_m
            tp2=entry+atr*tp_m*1.5 if pos==1 else entry-atr*tp_m*1.5
            tsz=(cap*risk/100)/rsl if rsl>0 else 0; sz=tsz*q65; sz2=tsz*(1-q65); tp1_done=False
    if len(trades)<3: return None
    wins=sum(trades); n=len(trades); wr=wins/n
    days=(df_w.index[-1]-df_w.index[0]).days
    cagr=((cap/CAPITAL)**(365.25/max(days,1))-1)*100
    pct_long  = al.mean()*100  if allow_long  is not None else 100
    pct_short = as_.mean()*100 if allow_short is not None else 100
    return {"T":n,"WR":round(wr*100,1),"CAGR":round(cagr,1),
            "cap":round(cap,2),"pct_active":round((pct_long+pct_short)/2,0)}


def run():
    print("\n"+"="*65)
    print("  SIGMA — FILTRO MACRO EMA200 DIARIO")
    print("="*65)

    df, df_1d = load()
    cfg = json.load(open(OUTPUT_DIR/"models"/"1h"/"config.json"))["params"]
    from core.signals import get_signals
    signals, quality = get_signals(df, cfg)

    bull_1h, bear_1h = build_macro(df, df_1d)
    pct_bull = bull_1h.mean()*100; pct_bear = bear_1h.mean()*100
    print(f"\n  Macro BULL: {pct_bull:.0f}% del tiempo | BEAR: {pct_bear:.0f}% | RANGO: {100-pct_bull-pct_bear:.0f}%")

    split   = int(len(df)*0.80)
    df_is   = df.iloc[:split];   sig_is=signals.iloc[:split]; q_is=quality.iloc[:split]
    bull_is = bull_1h.iloc[:split]; bear_is=bear_1h.iloc[:split]
    df_oos  = df.iloc[split:];   sig_oos=signals.iloc[split:]; q_oos=quality.iloc[split:]
    bull_oos= bull_1h.iloc[split:]; bear_oos=bear_1h.iloc[split:]

    scenarios = [
        ("Sin filtro macro",         None,     None),
        ("Solo BULL (L+S en bull)",  bull_1h,  bull_1h),
        ("Solo BEAR (L+S en bear)",  bear_1h,  bear_1h),
        ("Directional (L=bull,S=bear)", bull_1h, bear_1h),
        ("L en bull, no shorts",     bull_1h,  pd.Series(False,index=df.index)),
    ]

    hdr = f"  {'Escenario':<30} {'T':>5} {'WR':>6} {'CAGR':>8} {'%Activo':>9}"
    for period_name, df_p, sig_p, q_p, b, br in [
        ("FULL 3 AÑOS",  df,     signals,  quality,  bull_1h, bear_1h),
        ("OOS (20%)",    df_oos, sig_oos,  q_oos,    bull_oos,bear_oos),
    ]:
        print(f"\n  --- {period_name} ---")
        print(hdr)
        print("  "+"-"*62)
        for name, al_full, as_full in scenarios:
            al = al_full.reindex(df_p.index).fillna(False) if al_full is not None else None
            as_ = as_full.reindex(df_p.index).fillna(False) if as_full is not None else None
            r = bt(df_p, sig_p, q_p, cfg, al, as_)
            if r:
                print(f"  {name:<30} {r['T']:>5} {r['WR']:>5.1f}% {r['CAGR']:>+7.1f}% {r['pct_active']:>8.0f}%")

    print("\n"+"="*65)
    print("  CONCLUSION:")
    r_base = bt(df_oos, sig_oos, q_oos, cfg, None, None)
    r_dir  = bt(df_oos, sig_oos, q_oos, cfg,
                bull_oos, bear_oos)
    if r_dir and r_base:
        delta = r_dir["CAGR"] - r_base["CAGR"]
        print(f"  Directional vs baseline OOS: {delta:+.1f}% CAGR")
        if delta > 5:
            print("  -> IMPLEMENTAR filtro directional en Pine Script.")
        elif delta > 0:
            print("  -> Mejora marginal. Incluir como mejora opcional.")
        else:
            print("  -> El filtro macro no mejora el OOS. Señales ya incorporan trend.")
    print("="*65)


if __name__ == "__main__":
    run()

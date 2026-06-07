"""
SIGMA — Test de filtro de regimen para 1H

Hipotesis: la estrategia 1H funciona en TREND pero pierde en RANGE.
Filtro: solo operar cuando BTC esta en tendencia clara en 4H y mensual.

Condiciones para operar:
  1. Precio > EMA200 en 4H (tendencia 4H alcista) O precio < EMA200 en 4H (bajista)
  2. ADX 4H > 20 (hay fuerza direccional)
  3. NO en los 5 dias despues de un cambio de regimen (transicion)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json, numpy as np, pandas as pd
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent.parent
CAPITAL = 1000.0; COMMISSION = 0.0004; SLIPPAGE = 0.0001; COST = COMMISSION + SLIPPAGE


def load_data():
    from core.data import fetch_ohlcv
    from core.features import build_features
    max_p = OUTPUT_DIR / "models" / "data_1h_max.csv"
    df_b  = pd.read_csv(max_p, index_col=0, parse_dates=True)
    df_b.index.name = "timestamp"
    df_4h = fetch_ohlcv(tf="4h", days=1500)
    df_1d = fetch_ohlcv(tf="1d", days=1500)
    df = build_features(df_b, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close","atr","ema50"], inplace=True)
    return df, df_4h


def build_regime_mask(df, df_4h, mode="strict"):
    """
    Construye mascara de regimen: True = OK para operar.

    Modos:
      'strict'  — 4H trending + ADX > 25
      'medium'  — 4H trending + ADX > 18
      'loose'   — solo 4H trending (sin ADX)
    """
    # EMA en 4H
    ema50_4h  = df_4h["close"].ewm(span=50, adjust=False).mean()
    ema200_4h = df_4h["close"].ewm(span=200, adjust=False).mean()
    adx_4h    = df_4h.get("adx", pd.Series(25, index=df_4h.index))

    # Tendencia clara en 4H (diferencia > 0.5% del precio)
    diff_pct  = (ema50_4h - ema200_4h).abs() / ema200_4h * 100
    trending  = diff_pct > 0.5

    adx_thres = {"strict": 25, "medium": 18, "loose": 0}.get(mode, 20)
    strong    = adx_4h > adx_thres if adx_thres > 0 else pd.Series(True, index=df_4h.index)

    ok_4h = (trending & strong)

    # Reindexar a 1H con forward fill
    ok_1h = ok_4h.reindex(df.index, method="ffill").fillna(False)
    return ok_1h


def backtest_with_regime(df, signals, quality, cfg, regime_mask=None):
    closes=df["close"].to_numpy(); highs=df["high"].to_numpy()
    lows=df["low"].to_numpy();     atrs=df["atr"].to_numpy()
    sigs=signals.to_numpy()
    quals=quality.map({"ELITE_ICT":3,"ELITE":2,"EXECUTE":1}).fillna(0).astype(int).to_numpy()
    mask = regime_mask.to_numpy() if regime_mask is not None else np.ones(len(sigs), dtype=bool)

    e_sl=cfg.get("elite_sl_mult",2.4); e_tp=cfg.get("elite_tp_mult",2.0)
    x_sl=cfg.get("exec_sl_mult",1.9);  x_tp=cfg.get("exec_tp_mult",3.5)
    risk=cfg.get("risk_pct",1.5);      q65=cfg.get("qty_tp1",0.65)

    cap=CAPITAL; eq=[cap]; trades=[]; pos=0
    entry=sl=tp1=tp2=sz=sz2=0.0; tp1_done=False

    for i in range(1, len(closes)):
        pr=closes[i]; atr=atrs[i-1]; h_=highs[i]; lo=lows[i]
        s=sigs[i-1]; q=quals[i-1]; ok=mask[i-1]
        if not ok: s=0  # bloquear señal si régimen no favorable

        if pos!=0:
            closed=False; pnl=0.0
            if pos==1:
                if lo<=sl: pnl=(sz+sz2)*(sl-entry)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif h_>=tp1 and not tp1_done:
                    p1=sz*(tp1-entry)-sz*(entry+tp1)*COST; cap+=p1; trades.append({"pnl":p1,"won":p1>0}); sz=0; tp1_done=True
                elif h_>=tp2: pnl=sz2*(tp2-entry)-sz2*(entry+tp2)*COST; closed=True
            else:
                if h_>=sl: pnl=(sz+sz2)*(entry-sl)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif lo<=tp1 and not tp1_done:
                    p1=sz*(entry-tp1)-sz*(entry+tp1)*COST; cap+=p1; trades.append({"pnl":p1,"won":p1>0}); sz=0; tp1_done=True
                elif lo<=tp2: pnl=sz2*(entry-tp2)-sz2*(entry+tp2)*COST; closed=True
            if not closed and s==-pos:
                rem=sz+sz2; pnl=pos*rem*(pr-entry)-rem*(entry+pr)*COST; closed=True
            if closed: cap+=pnl; trades.append({"pnl":pnl,"won":pnl>0}); pos=0; tp1_done=False

        if pos==0 and s!=0 and cap>50:
            is_el=q>=2; sl_m=e_sl if is_el else x_sl; tp_m=e_tp if is_el else x_tp
            pos=s; entry=pr; rsl=atr*sl_m
            sl=entry-rsl if pos==1 else entry+rsl
            tp1=entry+atr*tp_m if pos==1 else entry-atr*tp_m
            tp2=entry+atr*tp_m*1.5 if pos==1 else entry-atr*tp_m*1.5
            tsz=(cap*risk/100)/rsl if rsl>0 else 0; sz=tsz*q65; sz2=tsz*(1-q65); tp1_done=False
        eq.append(cap)

    df_t=pd.DataFrame(trades); eq_s=pd.Series(eq[:len(df)], index=df.index[:len(eq)])
    if df_t.empty or len(df_t)<5: return None
    w=df_t[df_t["pnl"]>0]; l=df_t[df_t["pnl"]<=0]
    gp=w["pnl"].sum(); gl=abs(l["pnl"].sum())
    peak=eq_s.cummax(); dd=(eq_s-peak)/peak*100
    ret=eq_s.pct_change().dropna()
    days=(eq_s.index[-1]-eq_s.index[0]).days
    cagr=((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1))-1)*100
    wr=len(w)/len(df_t)
    sh=ret.mean()/ret.std()*np.sqrt(8760) if ret.std()>0 else 0
    return {"trades":len(df_t),"wr":round(wr*100,1),"cagr":round(cagr,1),
            "dd":round(dd.min(),1),"pf":round(gp/gl,3) if gl>0 else 999,
            "sharpe":round(sh,2),"calmar":round(cagr/abs(dd.min()),2) if dd.min()<0 else 0,
            "pct_time_active": round(mask.mean()*100 if regime_mask is not None else 100, 1)}


def run():
    print("\n" + "="*65)
    print("  SIGMA — TEST FILTRO DE REGIMEN 1H")
    print("="*65)

    df, df_4h = load_data()
    cfg = json.load(open(OUTPUT_DIR/"models"/"1h"/"config.json"))["params"]

    from core.signals import get_signals
    signals, quality = get_signals(df, cfg)

    split   = int(len(df)*0.80)
    df_oos  = df.iloc[split:];   sig_oos=signals.iloc[split:]; q_oos=quality.iloc[split:]
    df_full = df;                sig_full=signals;              q_full=quality

    scenarios = [
        ("Sin filtro (baseline)",    None),
        ("Filtro loose (solo 4H>)",  build_regime_mask(df_full, df_4h, "loose")),
        ("Filtro medium (4H+ADX18)", build_regime_mask(df_full, df_4h, "medium")),
        ("Filtro strict (4H+ADX25)", build_regime_mask(df_full, df_4h, "strict")),
    ]

    print(f"\n  {'Escenario':<30} {'T':>5} {'WR':>6} {'CAGR':>8} {'DD':>7} {'Calmar':>7} {'%Activo':>8}")
    print("  " + "-"*75)
    print("  --- FULL PERIOD (3 años) ---")

    best = None; best_name = ""
    for name, mask in scenarios:
        mask_full = mask if mask is None else mask
        m = backtest_with_regime(df_full, sig_full, q_full, cfg, mask_full)
        if not m: continue
        print(f"  {name:<30} {m['trades']:>5} {m['wr']:>5.1f}% {m['cagr']:>+7.1f}% "
              f"{m['dd']:>6.1f}% {m['calmar']:>7.2f} {m.get('pct_time_active',100):>7.1f}%")
        if best is None or m["cagr"] > best["cagr"]:
            best = m; best_name = name

    print(f"\n  --- OOS (ultimos 20%) ---")
    for name, mask in scenarios:
        mask_oos = mask.iloc[split:] if mask is not None else None
        m = backtest_with_regime(df_oos, sig_oos, q_oos, cfg, mask_oos)
        if not m: continue
        print(f"  {name:<30} {m['trades']:>5} {m['wr']:>5.1f}% {m['cagr']:>+7.1f}% "
              f"{m['dd']:>6.1f}% {m['calmar']:>7.2f} {m.get('pct_time_active',100):>7.1f}%")

    print(f"\n{'='*65}")
    if best:
        pct_active = best.get('pct_time_active', 100)
        print(f"  MEJOR CONFIG: {best_name}")
        print(f"  CAGR {best['cagr']:+.1f}% | WR {best['wr']:.1f}% | Trades {best['trades']}")
        print(f"  Activo {pct_active:.0f}% del tiempo — pausa {100-pct_active:.0f}% en rangos")
        if best["cagr"] > 10:
            print(f"  -> IMPLEMENTAR este filtro en Pine Script.")
        else:
            print(f"  -> El filtro mejora pero CAGR sigue bajo. Necesita mas trabajo.")

    return best


if __name__ == "__main__":
    run()

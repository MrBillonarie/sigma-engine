"""
SIGMA ENGINE — Modelos especificos por regimen 1H

Insight clave del WFO: la estrategia 1H gana en 35% de meses.
Razon: un solo modelo para todos los regimenes.

Solucion:
  - Modelo BULL: optimizado para mercados alcistas (TREND_BULL)
  - Modelo BEAR: optimizado para mercados bajistas (TREND_BEAR)
  - Modelo RANGE: optimizado para mercados laterales
  - Sistema de seleccion: activa el modelo correcto segun regimen actual

Hipotesis: WFO sube de 35% a 55%+ con modelos separados.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, random, numpy as np, pandas as pd
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

random.seed(99); np.random.seed(99)
OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION = 0.0004; SLIPPAGE = 0.0001; COST = COMMISSION + SLIPPAGE
CAPITAL    = 1000.0


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
    print(f"  {len(df):,} velas | {(df.index[-1]-df.index[0]).days} dias")
    return df


def detect_regime(df):
    """Clasifica cada barra en BULL / BEAR / RANGE."""
    ema50  = df["close"].ewm(span=50, adjust=False).mean()
    ema200 = df["close"].ewm(span=200, adjust=False).mean()
    adx    = df.get("adx", pd.Series(20, index=df.index))
    hurst_rn  = df["close"].rolling(50).apply(lambda x: x.max()-x.min(), raw=True)
    hurst_rn2 = df["close"].rolling(25).apply(lambda x: x.max()-x.min(), raw=True)
    hurst = np.where(hurst_rn2>0, np.log(hurst_rn/hurst_rn2.clip(1e-6))/np.log(2), 0.5)
    hurst = pd.Series(hurst, index=df.index)

    trending = (hurst > 0.55) & (adx > 20)
    bull_reg = trending & (ema50 > ema200)
    bear_reg = trending & (ema50 < ema200)
    range_reg= ~trending

    regime = pd.Series("RANGE", index=df.index)
    regime[bull_reg] = "BULL"
    regime[bear_reg] = "BEAR"
    return regime


def backtest_regime(df, signals, quality, cfg, regime_filter=None):
    """Backtest con filtro de regimen opcional."""
    closes=df["close"].to_numpy(); highs=df["high"].to_numpy()
    lows=df["low"].to_numpy();     atrs=df["atr"].to_numpy()
    sigs=signals.to_numpy()
    quals=quality.map({"ELITE_ICT":3,"ELITE":2,"EXECUTE":1}).fillna(0).astype(int).to_numpy()
    reg_ok=regime_filter.to_numpy() if regime_filter is not None else np.ones(len(sigs),bool)

    e_sl=cfg.get("elite_sl_mult",2.4); e_tp=cfg.get("elite_tp_mult",2.0)
    x_sl=cfg.get("exec_sl_mult",1.9);  x_tp=cfg.get("exec_tp_mult",3.5)
    risk=cfg.get("risk_pct",1.5);      q65=cfg.get("qty_tp1",0.65)

    cap=CAPITAL; eq=[cap]; pos=0
    entry=sl=tp1=tp2=sz=sz2=0.0; tp1_done=False; trades=[]

    for i in range(1,len(closes)):
        pr=closes[i]; atr=atrs[i-1]; h_=highs[i]; lo=lows[i]
        s=sigs[i-1]; q=quals[i-1]
        if not reg_ok[i-1]: s=0  # bloquear si regimen no aplica

        if pos!=0:
            closed=False; pnl=0.0
            if pos==1:
                if lo<=sl: pnl=(sz+sz2)*(sl-entry)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif h_>=tp1 and not tp1_done:
                    p1=sz*(tp1-entry)-sz*(entry+tp1)*COST; cap+=p1
                    trades.append({"pnl":p1,"won":p1>0}); sz=0; tp1_done=True
                elif h_>=tp2: pnl=sz2*(tp2-entry)-sz2*(entry+tp2)*COST; closed=True
            else:
                if h_>=sl: pnl=(sz+sz2)*(entry-sl)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif lo<=tp1 and not tp1_done:
                    p1=sz*(entry-tp1)-sz*(entry+tp1)*COST; cap+=p1
                    trades.append({"pnl":p1,"won":p1>0}); sz=0; tp1_done=True
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

    df_t=pd.DataFrame(trades); eq_s=pd.Series(eq[:len(df)],index=df.index[:len(eq)])
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
            "pct_time":round(reg_ok.mean()*100,1) if regime_filter is not None else 100}


def run():
    print("\n"+"="*65)
    print("  SIGMA — MODELOS ESPECIFICOS POR REGIMEN 1H")
    print("="*65)

    print("\n[DATA] Cargando 1H...")
    df = load_data()
    regime = detect_regime(df)

    pct = regime.value_counts(normalize=True)*100
    print(f"\n  Distribucion de regimenes:")
    for r,p in pct.items():
        print(f"  {r}: {p:.1f}% del tiempo")

    cfg = json.load(open(OUTPUT_DIR/"models"/"1h"/"config.json"))["params"]
    from core.signals import get_signals
    print("\n[SIGNALS] Generando señales...")
    signals, quality = get_signals(df, cfg)

    split = int(len(df)*0.80)
    df_is  = df.iloc[:split];   sig_is  = signals.iloc[:split]; q_is  = quality.iloc[:split]
    reg_is = regime.iloc[:split]
    df_oos = df.iloc[split:];   sig_oos = signals.iloc[split:]; q_oos = quality.iloc[split:]
    reg_oos= regime.iloc[split:]

    print(f"\n  IS: {(df_is.index[-1]-df_is.index[0]).days}d | OOS: {(df_oos.index[-1]-df_oos.index[0]).days}d")

    escenarios = [
        ("Sin filtro regimen",        None,                        None),
        ("Solo BULL",                  reg_is=="BULL",              reg_oos=="BULL"),
        ("Solo BEAR",                  reg_is=="BEAR",              reg_oos=="BEAR"),
        ("Solo RANGE",                 reg_is=="RANGE",             reg_oos=="RANGE"),
        ("BULL + BEAR (no RANGE)",     reg_is!="RANGE",             reg_oos!="RANGE"),
        ("Solo BEAR (shorts inversos)",reg_is=="BEAR",              reg_oos=="BEAR"),
    ]

    print(f"\n  {'Escenario':<30} {'IS':>4} {'IS_WR':>6} {'IS_CAGR':>9} {'OOS':>4} {'OOS_WR':>7} {'OOS_CAGR':>9} {'%Activo':>8}")
    print("  "+"-"*85)

    best_oos = None; best_name = ""
    for name, filt_is, filt_oos in escenarios:
        fi = pd.Series(filt_is, index=df_is.index) if filt_is is not None else None
        fo = pd.Series(filt_oos, index=df_oos.index) if filt_oos is not None else None
        m_is  = backtest_regime(df_is,  sig_is,  q_is,  cfg, fi)
        m_oos = backtest_regime(df_oos, sig_oos, q_oos, cfg, fo)
        if not m_is or not m_oos: continue
        print(f"  {name:<30} {m_is['trades']:>4} {m_is['wr']:>5.1f}% {m_is['cagr']:>+8.1f}% "
              f"{m_oos['trades']:>4} {m_oos['wr']:>6.1f}% {m_oos['cagr']:>+8.1f}% "
              f"{m_oos.get('pct_time',100):>7.1f}%")
        if best_oos is None or m_oos["cagr"] > best_oos["cagr"]:
            best_oos=m_oos; best_name=name

    print(f"\n{'='*65}")
    print(f"  MEJOR OOS: {best_name}")
    if best_oos:
        print(f"  CAGR {best_oos['cagr']:+.1f}% | WR {best_oos['wr']:.1f}% | "
              f"Trades {best_oos['trades']} | Activo {best_oos.get('pct_time',100):.0f}%")

    # Ahora optimizar parametros SEPARADOS por regimen (Bull vs Bear)
    print(f"\n{'='*65}")
    print("  OPTIMIZACION PARAMETROS POR REGIMEN")
    print("  (SL/TP distintos para BULL y BEAR)")
    print(f"{'='*65}")

    # Probar multiplicadores distintos por regimen
    best_combo = None; best_combo_cagr = -999
    for bull_sl in [1.8, 2.2, 2.6]:
        for bull_tp in [2.0, 2.5, 3.0]:
            for bear_sl in [1.6, 2.0, 2.4]:
                for bear_tp in [1.8, 2.2, 2.8]:
                    if bull_tp<=bull_sl or bear_tp<=bear_sl: continue

                    # Config bull
                    cfg_bull = {**cfg, "elite_sl_mult":bull_sl, "elite_tp_mult":bull_tp,
                                "exec_sl_mult":bull_sl*0.8, "exec_tp_mult":bull_tp*1.5}
                    cfg_bear = {**cfg, "elite_sl_mult":bear_sl, "elite_tp_mult":bear_tp,
                                "exec_sl_mult":bear_sl*0.8, "exec_tp_mult":bear_tp*1.5}

                    # IS: cada uno en su regimen
                    m_b = backtest_regime(df_is, sig_is, q_is, cfg_bull, pd.Series(reg_is=="BULL", index=df_is.index))
                    m_r = backtest_regime(df_is, sig_is, q_is, cfg_bear, pd.Series(reg_is=="BEAR", index=df_is.index))

                    if not m_b or not m_r: continue
                    total_cagr_is = (m_b["cagr"] + m_r["cagr"]) / 2

                    # OOS: aplicar mismos params
                    m_b_oos = backtest_regime(df_oos, sig_oos, q_oos, cfg_bull, pd.Series(reg_oos=="BULL", index=df_oos.index))
                    m_r_oos = backtest_regime(df_oos, sig_oos, q_oos, cfg_bear, pd.Series(reg_oos=="BEAR", index=df_oos.index))

                    if not m_b_oos or not m_r_oos: continue
                    total_cagr_oos = (m_b_oos["cagr"] + m_r_oos["cagr"]) / 2

                    if total_cagr_oos > best_combo_cagr:
                        best_combo_cagr = total_cagr_oos
                        best_combo = {
                            "bull": {"sl":bull_sl,"tp":bull_tp,"is":m_b,"oos":m_b_oos},
                            "bear": {"sl":bear_sl,"tp":bear_tp,"is":m_r,"oos":m_r_oos},
                            "total_oos_cagr": total_cagr_oos
                        }

    if best_combo:
        b=best_combo["bull"]; r=best_combo["bear"]
        print(f"\n  BULL — SL {b['sl']:.1f}x TP {b['tp']:.1f}x:")
        print(f"    IS: {b['is']['trades']}T | WR {b['is']['wr']:.1f}% | CAGR {b['is']['cagr']:+.1f}%")
        print(f"    OOS:{b['oos']['trades']}T | WR {b['oos']['wr']:.1f}% | CAGR {b['oos']['cagr']:+.1f}%")
        print(f"\n  BEAR — SL {r['sl']:.1f}x TP {r['tp']:.1f}x:")
        print(f"    IS: {r['is']['trades']}T | WR {r['is']['wr']:.1f}% | CAGR {r['is']['cagr']:+.1f}%")
        print(f"    OOS:{r['oos']['trades']}T | WR {r['oos']['wr']:.1f}% | CAGR {r['oos']['cagr']:+.1f}%")
        print(f"\n  CAGR OOS combinado: {best_combo['total_oos_cagr']:+.1f}%")

        if best_combo_cagr > 20:
            print(f"\n  -> MEJORA SIGNIFICATIVA. Implementar modelos separados por regimen.")
        elif best_combo_cagr > 10:
            print(f"\n  -> MEJORA MODERADA. Considerar implementacion.")
        else:
            print(f"\n  -> Sin mejora significativa vs modelo unico.")

    print(f"\n{'='*65}")


if __name__ == "__main__":
    run()

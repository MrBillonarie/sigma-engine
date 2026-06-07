"""
SIGMA — Validacion filtro 4H sobre senales 1H

Idea: solo entrar en 1H cuando 4H confirma la misma direccion.
Hipotesis: sube WR de 53% a 58-62%, reduce trades de 58 a ~40,
           pero el Calmar y Sharpe mejoran significativamente.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json, numpy as np, pandas as pd
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION = 0.0004; SLIPPAGE = 0.0001; COST = COMMISSION + SLIPPAGE
CAPITAL = 1000.0

def load_data():
    from core.data import fetch_ohlcv
    from core.features import build_features
    print("[DATA] Cargando 1H con contexto 4H y 1D...")
    max_1h = OUTPUT_DIR / "models" / "data_1h_max.csv"
    df_b   = pd.read_csv(max_1h, index_col=0, parse_dates=True) if max_1h.exists() else fetch_ohlcv(tf="1h", days=1095)
    df_b.index.name = "timestamp"
    df_4h  = fetch_ohlcv(tf="4h", days=1500)
    df_1d  = fetch_ohlcv(tf="1d", days=1500)
    df = build_features(df_b, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close","atr","ema50"], inplace=True)
    print(f"  {len(df):,} velas | {(df.index[-1]-df.index[0]).days} dias")
    return df

def get_4h_trend(df):
    """Tendencia 4H calculada directamente en 1H — EMA 50/200 en 4H."""
    from core.data import fetch_ohlcv
    df_4h = fetch_ohlcv(tf="4h", days=1500)
    ema50_4h  = df_4h["close"].ewm(span=50,  adjust=False).mean()
    ema200_4h = df_4h["close"].ewm(span=200, adjust=False).mean()
    trend_4h  = (ema50_4h > ema200_4h).astype(int)  # 1=bull, 0=bear
    # Reindexar a 1H (forward fill)
    trend_1h = trend_4h.reindex(df.index, method="ffill")
    return trend_1h

def backtest_with_filter(df, signals, quality, cfg, htf_filter=None):
    """Backtest con filtro 4H opcional."""
    closes = df["close"].to_numpy()
    highs  = df["high"].to_numpy()
    lows   = df["low"].to_numpy()
    atrs   = df["atr"].to_numpy()
    sigs   = signals.to_numpy()
    quals  = quality.to_numpy() if hasattr(quality,"to_numpy") else np.zeros(len(sigs))
    htf    = htf_filter.to_numpy() if htf_filter is not None else np.ones(len(sigs))

    e_sl = cfg.get("elite_sl_mult", 2.4); e_tp = cfg.get("elite_tp_mult", 2.0)
    x_sl = cfg.get("exec_sl_mult",  1.9); x_tp = cfg.get("exec_tp_mult",  3.5)
    risk = cfg.get("risk_pct", 1.5);      q65  = cfg.get("qty_tp1", 0.65)

    cap = CAPITAL; eq = [cap]; pos = 0
    entry = sl = tp1 = tp2 = sz = sz2 = 0.0
    trades = []; tp1_done = False

    for i in range(1, len(closes)):
        pr=closes[i]; atr=atrs[i-1]; h_=highs[i]; lo=lows[i]
        s=sigs[i-1]; q=quals[i-1]; h4=htf[i-1]

        # Aplicar filtro 4H
        if htf_filter is not None:
            if s == 1 and h4 != 1:  s = 0   # no long si 4H es bajista
            if s ==-1 and h4 != 0:  s = 0   # no short si 4H es alcista

        if pos != 0:
            closed=False; pnl=0.0
            if pos == 1:
                if lo <= sl:   pnl=(sz+sz2)*(sl-entry)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif h_>=tp1 and not tp1_done:
                    p1=sz*(tp1-entry)-sz*(entry+tp1)*COST
                    cap+=p1; trades.append({"pnl":p1,"won":p1>0}); sz=0; tp1_done=True
                elif h_>=tp2:  pnl=sz2*(tp2-entry)-sz2*(entry+tp2)*COST; closed=True
            else:
                if h_>=sl:     pnl=(sz+sz2)*(entry-sl)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif lo<=tp1 and not tp1_done:
                    p1=sz*(entry-tp1)-sz*(entry+tp1)*COST
                    cap+=p1; trades.append({"pnl":p1,"won":p1>0}); sz=0; tp1_done=True
                elif lo<=tp2:  pnl=sz2*(entry-tp2)-sz2*(entry+tp2)*COST; closed=True
            if not closed and s==-pos:
                rem=sz+sz2; pnl=pos*rem*(pr-entry)-rem*(entry+pr)*COST; closed=True
            if closed:
                cap+=pnl; trades.append({"pnl":pnl,"won":pnl>0}); pos=0

        if pos==0 and s!=0 and cap>50:
            is_el = q>=2
            sl_m=e_sl if is_el else x_sl; tp_m=e_tp if is_el else x_tp
            pos=s; entry=pr; rsl=atr*sl_m
            sl=entry-rsl if pos==1 else entry+rsl
            tp1=entry+atr*tp_m if pos==1 else entry-atr*tp_m
            tp2=entry+atr*tp_m*1.5 if pos==1 else entry-atr*tp_m*1.5
            tsz=(cap*risk/100)/rsl if rsl>0 else 0; sz=tsz*q65; sz2=tsz*(1-q65)
            tp1_done=False
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
    calmar=cagr/abs(dd.min()) if dd.min()<0 else 0
    return {"trades":len(df_t),"wr":round(wr*100,1),"cagr":round(cagr,1),
            "dd":round(dd.min(),1),"pf":round(gp/gl,3) if gl>0 else 999,
            "sharpe":round(sh,2),"calmar":round(calmar,2),"final":round(eq_s.iloc[-1],2)}

def run():
    print("\n"+"="*65)
    print("  SIGMA — FILTRO 4H SOBRE SENALES 1H")
    print("="*65)

    df = load_data()
    cfg = json.load(open(OUTPUT_DIR/"models"/"1h"/"config.json"))["params"]

    from core.signals import get_signals
    signals, quality = get_signals(df, cfg)
    quality = quality.map({"ELITE_ICT":3,"ELITE":2,"EXECUTE":1}).fillna(0).astype(int)

    # Obtener tendencia 4H
    from core.data import fetch_ohlcv
    df_4h    = fetch_ohlcv(tf="4h", days=1500)
    ema50_4h = df_4h["close"].ewm(span=50,  adjust=False).mean()
    ema200_4h= df_4h["close"].ewm(span=200, adjust=False).mean()
    trend_4h = (ema50_4h > ema200_4h).astype(int)
    trend_1h = trend_4h.reindex(df.index, method="ffill").fillna(1)

    # Split IS/OOS
    split   = int(len(df)*0.80)
    df_is   = df.iloc[:split];   sig_is  = signals.iloc[:split]; q_is = quality.iloc[:split]; t_is = trend_1h.iloc[:split]
    df_oos  = df.iloc[split:];   sig_oos = signals.iloc[split:]; q_oos= quality.iloc[split:]; t_oos= trend_1h.iloc[split:]

    print(f"\n  IS: {(df_is.index[-1]-df_is.index[0]).days}d | OOS: {(df_oos.index[-1]-df_oos.index[0]).days}d")

    escenarios = [
        ("Baseline 1H solo",         None,   None),
        ("1H + filtro 4H bull/bear",  t_is,   t_oos),
    ]

    print(f"\n  {'Escenario':<30} {'Trades':>7} {'WR':>6} {'CAGR':>8} {'DD':>7} {'Calmar':>7} {'Sharpe':>7}")
    print("  " + "-"*70)

    best_oos = None; best_name = ""
    for name, tf_is, tf_oos in escenarios:
        m_is  = backtest_with_filter(df_is,  sig_is,  q_is,  cfg, tf_is)
        m_oos = backtest_with_filter(df_oos, sig_oos, q_oos, cfg, tf_oos)

        def fmt(m, label):
            if not m: return f"  {label:<30} {'N/A':>62}"
            return (f"  {label:<30} {m['trades']:>7} {m['wr']:>5.1f}% "
                    f"{m['cagr']:>+7.1f}% {m['dd']:>6.1f}% "
                    f"{m['calmar']:>7.2f} {m['sharpe']:>7.2f}")
        print(fmt(m_is,  f"{name} IS"))
        print(fmt(m_oos, f"{name} OOS"))
        print()

        if m_oos and (best_oos is None or m_oos["cagr"] > best_oos["cagr"]):
            best_oos = m_oos; best_name = name

    print("="*65)
    if best_oos:
        print(f"  MEJOR OOS: {best_name}")
        print(f"  CAGR {best_oos['cagr']:+.1f}% | WR {best_oos['wr']:.1f}% | "
              f"Trades {best_oos['trades']} | Calmar {best_oos['calmar']:.2f}")
        if best_name != "Baseline 1H solo":
            print(f"  -> El filtro 4H MEJORA el resultado. Actualizar Pine Script.")
        else:
            print(f"  -> El filtro 4H no mejora. Mantener 1H solo.")

    return best_oos

if __name__ == "__main__":
    run()

"""
SIGMA ENGINE — Walk-Forward Optimization Real

En vez del IS/OOS estático (80/20 fijo), usa ventanas rodantes:
  - Train: 6 meses
  - Test:  1 mes
  - Step:  1 mes
  - Repite hasta cubrir todo el historial

Si el modelo gana en >60% de las ventanas → edge robusto.
Si gana en <50% → overfit, no deploy.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path
from datetime import timedelta

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION = 0.0004; SLIPPAGE = 0.0001; COST = COMMISSION + SLIPPAGE
CAPITAL    = 1000.0


def load_data(tf="1h"):
    from core.data import fetch_ohlcv
    from core.features import build_features

    max_p = OUTPUT_DIR / "models" / f"data_{tf}_max.csv"
    if max_p.exists():
        df_b = pd.read_csv(max_p, index_col=0, parse_dates=True)
        df_b.index.name = "timestamp"
    else:
        df_b = fetch_ohlcv(tf=tf, days=1095)

    df_4h = fetch_ohlcv(tf="4h", days=1500)
    df_1d = fetch_ohlcv(tf="1d", days=1500)
    df = build_features(df_b, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close", "atr", "ema50"], inplace=True)
    return df


def backtest_window(df_w, signals_w, quality_w, cfg):
    """Backtest rápido para una ventana."""
    closes = df_w["close"].to_numpy(); highs = df_w["high"].to_numpy()
    lows   = df_w["low"].to_numpy();   atrs  = df_w["atr"].to_numpy()
    sigs   = signals_w.to_numpy()
    quals  = quality_w.map({"ELITE_ICT":3,"ELITE":2,"EXECUTE":1}).fillna(0).astype(int).to_numpy()

    e_sl = cfg.get("elite_sl_mult", 2.4); e_tp = cfg.get("elite_tp_mult", 2.0)
    x_sl = cfg.get("exec_sl_mult",  1.9); x_tp = cfg.get("exec_tp_mult",  3.5)
    risk = cfg.get("risk_pct", 1.5);      q65  = cfg.get("qty_tp1", 0.65)

    cap = CAPITAL; trades = []; pos = 0
    entry = sl = tp1 = tp2 = sz = sz2 = 0.0; tp1_done = False

    for i in range(1, len(closes)):
        pr=closes[i]; atr=atrs[i-1]; h_=highs[i]; lo=lows[i]; s=sigs[i-1]; q=quals[i-1]
        if pos != 0:
            closed=False; pnl=0.0
            if pos==1:
                if lo<=sl: pnl=(sz+sz2)*(sl-entry)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif h_>=tp1 and not tp1_done:
                    p1=sz*(tp1-entry)-sz*(entry+tp1)*COST; cap+=p1; trades.append(p1>0); sz=0; tp1_done=True
                elif h_>=tp2: pnl=sz2*(tp2-entry)-sz2*(entry+tp2)*COST; closed=True
            else:
                if h_>=sl: pnl=(sz+sz2)*(entry-sl)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif lo<=tp1 and not tp1_done:
                    p1=sz*(entry-tp1)-sz*(entry+tp1)*COST; cap+=p1; trades.append(p1>0); sz=0; tp1_done=True
                elif lo<=tp2: pnl=sz2*(entry-tp2)-sz2*(entry+tp2)*COST; closed=True
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

    if len(trades) < 3: return None
    wins=sum(trades); n=len(trades); wr=wins/n
    days=(df_w.index[-1]-df_w.index[0]).days
    cagr=((cap/CAPITAL)**(365.25/max(days,1))-1)*100
    return {"trades":n, "wr":round(wr*100,1), "cagr":round(cagr,1), "positive": cagr > 0}


def run_wfo(tf="1h", train_months=6, test_months=1):
    print(f"\n{'='*65}")
    print(f"  WALK-FORWARD OPTIMIZATION — {tf.upper()}")
    print(f"  Train: {train_months}m | Test: {test_months}m | Step: {test_months}m")
    print(f"{'='*65}")

    print("\n[DATA] Cargando datos...")
    df = load_data(tf)
    days_total = (df.index[-1] - df.index[0]).days
    print(f"  {len(df):,} velas | {days_total} dias")

    cfg_path = OUTPUT_DIR / "models" / tf / "config.json"
    with open(cfg_path) as f:
        cfg = json.load(f)["params"]

    from core.signals import get_signals
    print("[SIGNALS] Generando señales completas...")
    signals, quality = get_signals(df, cfg)

    # Construir ventanas
    start = df.index[0]
    end   = df.index[-1]
    train_delta = timedelta(days=train_months * 30)
    test_delta  = timedelta(days=test_months  * 30)

    windows = []
    cur = start
    while cur + train_delta + test_delta <= end:
        train_end  = cur + train_delta
        test_end   = cur + train_delta + test_delta
        windows.append((cur, train_end, test_end))
        cur += test_delta

    print(f"  {len(windows)} ventanas de test ({test_months}m cada una)\n")

    results = []
    print(f"  {'Ventana':<20} {'IS CAGR':>9} {'OOS CAGR':>9} {'OOS WR':>7} {'OOS T':>6} {'OK':>4}")
    print("  " + "-"*58)

    for i, (tr_s, tr_e, te_e) in enumerate(windows):
        df_tr = df[(df.index >= tr_s) & (df.index < tr_e)]
        df_te = df[(df.index >= tr_e) & (df.index < te_e)]
        if len(df_tr) < 100 or len(df_te) < 20: continue

        sig_tr = signals[(signals.index >= tr_s) & (signals.index < tr_e)]
        q_tr   = quality[(quality.index >= tr_s) & (quality.index < tr_e)]
        sig_te = signals[(signals.index >= tr_e) & (signals.index < te_e)]
        q_te   = quality[(quality.index >= tr_e) & (quality.index < te_e)]

        m_is  = backtest_window(df_tr, sig_tr, q_tr, cfg)
        m_oos = backtest_window(df_te, sig_te, q_te, cfg)

        if not m_oos: continue

        ok = "✓" if m_oos["positive"] else "✗"
        label = f"{tr_e.strftime('%Y-%m')}"
        is_cagr = f"{m_is['cagr']:+.0f}%" if m_is else "N/A"
        print(f"  {label:<20} {is_cagr:>9} {m_oos['cagr']:>+8.1f}% {m_oos['wr']:>6.1f}% {m_oos['trades']:>6} {ok:>4}")
        results.append(m_oos)

    if not results:
        print("  Sin resultados suficientes.")
        return

    # Resumen estadístico
    positive   = [r for r in results if r["positive"]]
    win_rate_w = len(positive) / len(results) * 100
    avg_cagr   = np.mean([r["cagr"] for r in results])
    std_cagr   = np.std([r["cagr"] for r in results])
    avg_wr     = np.mean([r["wr"] for r in results])
    total_t    = sum(r["trades"] for r in results)

    print(f"\n{'='*65}")
    print(f"  RESUMEN WFO — {len(results)} ventanas")
    print(f"  Ventanas positivas : {len(positive)}/{len(results)} ({win_rate_w:.0f}%)")
    print(f"  CAGR promedio OOS  : {avg_cagr:+.1f}% ± {std_cagr:.1f}%")
    print(f"  WR promedio OOS    : {avg_wr:.1f}%")
    print(f"  Trades totales OOS : {total_t}")

    if win_rate_w >= 65:
        veredicto = "ROBUSTO — Edge genuino. OK para produccion."
    elif win_rate_w >= 50:
        veredicto = "MARGINAL — Edge moderado. Monitorear en papel."
    else:
        veredicto = "FRAGIL — Overfit probable. No deployar."

    print(f"\n  VEREDICTO: {veredicto}")
    print(f"{'='*65}")

    # Guardar
    # Convertir bool numpy a bool Python para JSON
    for r in results:
        r["positive"] = bool(r["positive"])
    out = {"tf": tf, "windows": len(results), "win_rate_pct": round(win_rate_w,1),
           "avg_cagr": round(float(avg_cagr),2), "std_cagr": round(float(std_cagr),2),
           "avg_wr": round(float(avg_wr),1), "veredicto": veredicto,
           "detail": results}
    rpt = OUTPUT_DIR / "results" / "reports" / f"wfo_{tf}.json"
    rpt.parent.mkdir(parents=True, exist_ok=True)
    with open(rpt, "w") as f: json.dump(out, f, indent=2)
    print(f"  [SAVED] {rpt}")
    return out


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--tf",    default="1h")
    p.add_argument("--train", type=int, default=6)
    p.add_argument("--test",  type=int, default=1)
    a = p.parse_args()
    run_wfo(a.tf, a.train, a.test)

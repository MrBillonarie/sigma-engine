"""
SIGMA ENGINE — Monte Carlo sobre trades reales

Toma los trades del backtest y los permuta 10,000 veces.
Responde: "En el peor caso realista, ¿cuánto puedo perder?"

Output:
  - Distribución de CAGR (percentil 5, 25, 50, 75, 95)
  - Distribución de MaxDD
  - Probabilidad de ruina (<-50% capital)
  - Probability of profit
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent.parent
CAPITAL    = 1000.0
N_SIMS     = 10000


def load_trades_from_backtest(tf="1h"):
    """Extrae los PnL de los trades del mejor backtest guardado."""
    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.signals import get_signals

    max_p = OUTPUT_DIR / "models" / f"data_{tf}_max.csv"
    if max_p.exists():
        df_b = pd.read_csv(max_p, index_col=0, parse_dates=True)
        df_b.index.name = "timestamp"
    else:
        df_b = fetch_ohlcv(tf=tf, days=1095)

    df_4h = fetch_ohlcv(tf="4h", days=1500)
    df_1d = fetch_ohlcv(tf="1d", days=1500)
    df = build_features(df_b, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close","atr","ema50"], inplace=True)

    cfg = json.load(open(OUTPUT_DIR/"models"/tf/"config.json"))["params"]
    signals, quality = get_signals(df, cfg)

    # Backtest completo para extraer trades
    # Inline backtest (evita import circular)
    quality_num = quality.map({"ELITE_ICT":3,"ELITE":2,"EXECUTE":1}).fillna(0).astype(int)

    COMMISSION = 0.0004; SLIPPAGE = 0.0001; COST = COMMISSION + SLIPPAGE
    closes=df["close"].to_numpy(); highs=df["high"].to_numpy()
    lows=df["low"].to_numpy();     atrs=df["atr"].to_numpy()
    sigs=signals.to_numpy(); quals=quality_num.to_numpy()
    e_sl=cfg.get("elite_sl_mult",2.4); e_tp=cfg.get("elite_tp_mult",2.0)
    x_sl=cfg.get("exec_sl_mult",1.9);  x_tp=cfg.get("exec_tp_mult",3.5)
    risk=cfg.get("risk_pct",1.5);      q65=cfg.get("qty_tp1",0.65)

    cap=CAPITAL; pnls=[]; pos=0; entry=sl=tp1=tp2=sz=sz2=0.0; tp1_done=False
    for i in range(1,len(closes)):
        pr=closes[i]; atr=atrs[i-1]; h_=highs[i]; lo=lows[i]; s=sigs[i-1]; q=quals[i-1]
        if pos!=0:
            closed=False; pnl=0.0
            if pos==1:
                if lo<=sl: pnl=(sz+sz2)*(sl-entry)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif h_>=tp1 and not tp1_done:
                    p1=sz*(tp1-entry)-sz*(entry+tp1)*COST; cap+=p1; pnls.append(p1); sz=0; tp1_done=True
                elif h_>=tp2: pnl=sz2*(tp2-entry)-sz2*(entry+tp2)*COST; closed=True
            else:
                if h_>=sl: pnl=(sz+sz2)*(entry-sl)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif lo<=tp1 and not tp1_done:
                    p1=sz*(entry-tp1)-sz*(entry+tp1)*COST; cap+=p1; pnls.append(p1); sz=0; tp1_done=True
                elif lo<=tp2: pnl=sz2*(entry-tp2)-sz2*(entry+tp2)*COST; closed=True
            if not closed and s==-pos:
                rem=sz+sz2; pnl=pos*rem*(pr-entry)-rem*(entry+pr)*COST; closed=True
            if closed: cap+=pnl; pnls.append(pnl); pos=0; tp1_done=False
        if pos==0 and s!=0 and cap>50:
            is_el=q>=2; sl_m=e_sl if is_el else x_sl; tp_m=e_tp if is_el else x_tp
            pos=s; entry=pr; rsl=atr*sl_m
            sl=entry-rsl if pos==1 else entry+rsl
            tp1=entry+atr*tp_m if pos==1 else entry-atr*tp_m
            tp2=entry+atr*tp_m*1.5 if pos==1 else entry-atr*tp_m*1.5
            tsz=(cap*risk/100)/rsl if rsl>0 else 0; sz=tsz*q65; sz2=tsz*(1-q65); tp1_done=False
    return np.array(pnls), (df.index[-1]-df.index[0]).days


def simulate(pnls, days, n_sims=N_SIMS):
    """Permuta los trades N veces y calcula distribuciones."""
    results = []
    for _ in range(n_sims):
        perm  = np.random.permutation(pnls)
        equity= np.concatenate([[CAPITAL], CAPITAL + np.cumsum(perm)])
        peak  = np.maximum.accumulate(equity)
        dd    = (equity - peak) / peak * 100
        final = equity[-1]
        cagr  = ((final/CAPITAL)**(365.25/max(days,1))-1)*100
        results.append({"cagr": cagr, "max_dd": dd.min(), "final": final})
    return pd.DataFrame(results)


def run_mc(tf="1h"):
    print(f"\n{'='*65}")
    print(f"  MONTE CARLO — {tf.upper()} | {N_SIMS:,} simulaciones")
    print(f"{'='*65}")

    print("\n[DATA] Extrayendo trades del backtest...")
    pnls, days = load_trades_from_backtest(tf)
    wins = (pnls > 0).sum(); n = len(pnls)
    print(f"  {n} trades | WR {wins/n*100:.1f}% | PnL total: ${pnls.sum():+.2f}")

    print(f"[MC] Corriendo {N_SIMS:,} permutaciones...")
    df_sim = simulate(pnls, days)

    # Estadísticas
    cagr_p  = np.percentile(df_sim["cagr"],    [5, 25, 50, 75, 95])
    dd_p    = np.percentile(df_sim["max_dd"],   [5, 25, 50, 75, 95])
    p_profit= (df_sim["cagr"] > 0).mean() * 100
    p_ruin  = (df_sim["max_dd"] < -40).mean() * 100
    p_great = (df_sim["cagr"] > 20).mean() * 100

    print(f"\n  DISTRIBUCION CAGR ({N_SIMS:,} sims):")
    print(f"  Peor  5%  : {cagr_p[0]:+.1f}%")
    print(f"  Peor 25%  : {cagr_p[1]:+.1f}%")
    print(f"  Mediana   : {cagr_p[2]:+.1f}%")
    print(f"  Mejor 75% : {cagr_p[3]:+.1f}%")
    print(f"  Mejor 95% : {cagr_p[4]:+.1f}%")

    print(f"\n  DISTRIBUCION MAX DRAWDOWN:")
    print(f"  Peor  5%  : {dd_p[0]:.1f}%")
    print(f"  Peor 25%  : {dd_p[1]:.1f}%")
    print(f"  Mediana   : {dd_p[2]:.1f}%")
    print(f"  Mejor 75% : {dd_p[3]:.1f}%")
    print(f"  Mejor 95% : {dd_p[4]:.1f}%")

    print(f"\n  PROBABILIDADES:")
    print(f"  P(CAGR > 0%)   : {p_profit:.1f}%")
    print(f"  P(CAGR > 20%)  : {p_great:.1f}%")
    print(f"  P(DD < -40%)   : {p_ruin:.1f}%  ← riesgo de ruina")

    print(f"\n{'='*65}")
    if p_ruin < 2 and p_profit > 70:
        veredicto = "SEGURO para deploy. Riesgo de ruina bajo."
    elif p_ruin < 10 and p_profit > 55:
        veredicto = "ACEPTABLE. Monitorear con circuit breaker en -20%."
    else:
        veredicto = "RIESGOSO. Reducir size o mejorar estrategia."
    print(f"  VEREDICTO: {veredicto}")
    print(f"{'='*65}")

    # Guardar
    out = {
        "tf": tf, "n_trades": n, "n_sims": N_SIMS,
        "cagr_p5": round(cagr_p[0],1), "cagr_p50": round(cagr_p[2],1), "cagr_p95": round(cagr_p[4],1),
        "dd_p5":   round(dd_p[0],1),   "dd_p50":   round(dd_p[2],1),   "dd_p95":   round(dd_p[4],1),
        "p_profit": round(p_profit,1),  "p_ruin":   round(p_ruin,1),
        "veredicto": veredicto
    }
    rpt = OUTPUT_DIR / "results" / "reports" / f"monte_carlo_{tf}.json"
    rpt.parent.mkdir(parents=True, exist_ok=True)
    with open(rpt, "w") as f: json.dump(out, f, indent=2)
    print(f"  [SAVED] {rpt}")
    return out


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--tf", default="1h")
    a = p.parse_args()
    run_mc(a.tf)

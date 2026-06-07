"""
SIGMA ENGINE — Paper Trading Tracker

Registra trades del Pine Script (via Make.com o manual),
compara vs backtest esperado y alerta si hay degradacion.

USO:
  python engine/live/paper_trading.py --log          # ver trades registrados
  python engine/live/paper_trading.py --add          # agregar trade manualmente
  python engine/live/paper_trading.py --report       # reporte completo
  python engine/live/paper_trading.py --monitor      # monitorear en vivo
"""

import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

OUTPUT_DIR  = Path(__file__).parent.parent.parent
LOG_PATH    = OUTPUT_DIR / "results" / "reports" / "paper_trades.csv"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

BACKTEST = {
    "1h":  {"cagr": 37.2, "winrate": 53.4, "pf": 1.67, "dd": -13.4, "trades_month": 4.8},
    "4h":  {"cagr":  4.4, "winrate": 50.0, "pf": 1.51, "dd":  -3.0, "trades_month": 2.5},
    "15m": {"cagr":  0.5, "winrate": 37.2, "pf": 1.01, "dd": -10.9, "trades_month": 33.0},
}

COLS = ["fecha", "tf", "direccion", "calidad", "regimen", "sesion",
        "entrada", "sl", "tp1", "tp2", "salida", "resultado",
        "pnl_usd", "pnl_pct", "rr_real", "duracion_h", "notas"]


def load_trades():
    if not LOG_PATH.exists():
        return pd.DataFrame(columns=COLS)
    return pd.read_csv(LOG_PATH, parse_dates=["fecha"])


def save_trades(df):
    df.to_csv(LOG_PATH, index=False)


def add_trade_interactive():
    print("\n=== AGREGAR TRADE DE PAPER TRADING ===")
    t = {}
    t["fecha"]      = input("Fecha (YYYY-MM-DD HH:MM, enter=ahora): ").strip() or datetime.now().strftime("%Y-%m-%d %H:%M")
    t["tf"]         = input("TF (1h/4h/15m): ").strip().lower()
    t["direccion"]  = input("LONG o SHORT: ").strip().upper()
    t["calidad"]    = input("Calidad (ELITE_ICT/ELITE/EXECUTE): ").strip() or "EXECUTE"
    t["regimen"]    = input("Regimen (TREND_BULL/TREND_BEAR/RANGE): ").strip() or "TRANSITION"
    t["sesion"]     = input("Sesion (LONDON/NY_AM/ASIA): ").strip() or "LONDON"
    t["entrada"]    = float(input("Precio entrada: ").strip())
    t["sl"]         = float(input("Stop Loss: ").strip())
    t["tp1"]        = float(input("TP1: ").strip())
    t["tp2"]        = float(input("TP2 (enter=skip): ").strip() or "0") or None
    t["salida"]     = float(input("Precio salida (enter=abierto): ").strip() or "0") or None
    t["resultado"]  = input("WIN/LOSS/OPEN: ").strip().upper() or "OPEN"
    t["pnl_usd"]    = float(input("PnL USD (enter=0): ").strip() or "0")
    t["pnl_pct"]    = float(input("PnL % (enter=0): ").strip() or "0")
    t["rr_real"]    = float(input("RR real (enter=0): ").strip() or "0")
    t["duracion_h"] = float(input("Duracion en horas (enter=0): ").strip() or "0")
    t["notas"]      = input("Notas: ").strip()

    df = load_trades()
    df = pd.concat([df, pd.DataFrame([t])], ignore_index=True)
    save_trades(df)
    print(f"\n[OK] Trade guardado. Total: {len(df)} trades.")


def calc_metrics(df):
    if df.empty: return {}
    closed = df[df["resultado"].isin(["WIN","LOSS"])]
    if closed.empty: return {"trades": 0}
    wins   = closed[closed["resultado"] == "WIN"]
    losses = closed[closed["resultado"] == "LOSS"]
    wr     = len(wins) / len(closed) * 100
    gp     = wins["pnl_usd"].sum()
    gl     = abs(losses["pnl_usd"].sum())
    pf     = gp/gl if gl>0 else 999
    pnl    = closed["pnl_usd"].sum()
    return {
        "trades":  len(closed),
        "open":    len(df[df["resultado"]=="OPEN"]),
        "winrate": round(wr, 1),
        "pf":      round(pf, 3),
        "pnl_usd": round(pnl, 2),
        "wins":    len(wins),
        "losses":  len(losses),
    }


def degradation_check(live, expected_tf):
    if not live or live["trades"] < 5:
        return None, "Insuficientes trades para evaluar"

    bt    = BACKTEST.get(expected_tf, {})
    bt_wr = bt.get("winrate", 50)
    live_wr = live["winrate"]
    delta_wr = live_wr - bt_wr

    if live_wr < bt_wr - 15:
        return "CRITICO", f"WR live {live_wr:.1f}% vs backtest {bt_wr:.1f}% (delta {delta_wr:+.1f}%)"
    elif live_wr < bt_wr - 8:
        return "WARN",    f"WR live {live_wr:.1f}% vs backtest {bt_wr:.1f}% (delta {delta_wr:+.1f}%)"
    else:
        return "OK",      f"WR live {live_wr:.1f}% vs backtest {bt_wr:.1f}% (delta {delta_wr:+.1f}%)"


def print_report():
    df = load_trades()
    print("\n" + "="*65)
    print("  SIGMA PAPER TRADING — REPORTE")
    print("="*65)

    if df.empty:
        print("  Sin trades registrados aun.")
        print("  Agrega trades con: python engine/live/paper_trading.py --add")
        return

    for tf in ["1h", "4h", "15m"]:
        subset = df[df["tf"] == tf]
        if subset.empty: continue
        m = calc_metrics(subset)
        bt = BACKTEST.get(tf, {})
        level, msg = degradation_check(m, tf)

        print(f"\n  [{tf.upper()}]")
        print(f"  Trades cerrados : {m.get('trades',0)} | Abiertos: {m.get('open',0)}")
        print(f"  WR live         : {m.get('winrate',0):.1f}% vs backtest {bt.get('winrate',0):.1f}%")
        print(f"  PF live         : {m.get('pf',0):.2f} vs backtest {bt.get('pf',0):.2f}")
        print(f"  PnL total       : ${m.get('pnl_usd',0):+.2f}")
        if level:
            status_icon = "[OK]" if level=="OK" else "[!]" if level=="WARN" else "[!!]"
            print(f"  Estado          : {status_icon} {msg}")

    print(f"\n  Ultimos 5 trades:")
    last5 = df.tail(5)[["fecha","tf","direccion","resultado","pnl_usd","rr_real"]]
    print(last5.to_string(index=False))
    print("\n" + "="*65)


def print_log():
    df = load_trades()
    if df.empty:
        print("Sin trades registrados.")
        return
    print(f"\nTotal trades: {len(df)}")
    print(df[["fecha","tf","direccion","calidad","resultado","pnl_usd","rr_real"]].to_string(index=False))


def monitor_loop():
    import time
    print("Monitoreando paper trades... Ctrl+C para salir.")
    print("Actualiza cada 5 minutos. Agrega trades con --add\n")
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print_report()
        print(f"\n  Ultima actualizacion: {datetime.now().strftime('%H:%M:%S')}")
        time.sleep(300)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SIGMA Paper Trading Tracker")
    parser.add_argument("--log",     action="store_true", help="Ver todos los trades")
    parser.add_argument("--add",     action="store_true", help="Agregar trade")
    parser.add_argument("--report",  action="store_true", help="Reporte completo")
    parser.add_argument("--monitor", action="store_true", help="Monitor en vivo")
    args = parser.parse_args()

    if args.add:
        add_trade_interactive()
    elif args.log:
        print_log()
    elif args.monitor:
        monitor_loop()
    else:
        print_report()

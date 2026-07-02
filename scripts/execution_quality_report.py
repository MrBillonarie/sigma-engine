#!/usr/bin/env python3
"""Reporte semanal de calidad de ejecución — domingo 18:00 Chile via cron.

Compara el slippage REAL (señal→fill, de execution_quality.jsonl que escribe
live_executor en cada entrada) contra el asumido en backtest/paper (1bp por
lado). Si el real supera sostenidamente al asumido, el backtest está
sobreestimando el edge — este reporte existe para saberlo antes de que lo
revele el PF live.

Sin datos nuevos en la semana → no envía nada (anti alert-fatigue).
NUNCA importa web_server.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHILE = timezone(timedelta(hours=-4))
LEDGER = Path('/opt/sigma/results/reports/execution_quality.jsonl')
ASSUMED_BPS_PER_SIDE = 1.0  # PAPER_SLIPPAGE_BPS en web_server

def main():
    if not LEDGER.exists():
        return
    cutoff = datetime.now(CHILE) - timedelta(days=7)
    week, hist = [], []
    for line in LEDGER.read_text(encoding='utf-8').splitlines():
        try:
            r = json.loads(line)
            ts = datetime.strptime(r['ts'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=CHILE)
        except Exception:
            continue
        hist.append(r)
        if ts >= cutoff:
            week.append(r)
    if not week:
        return

    def stats(rows):
        vals = sorted(r['adverse_bps'] for r in rows)
        n = len(vals)
        return n, sum(vals) / n, vals[int(n * 0.9)] if n >= 3 else vals[-1]

    por_sym = {}
    for r in week:
        por_sym.setdefault(r['sym'], []).append(r)

    n_w, mean_w, p90_w = stats(week)
    n_h, mean_h, _ = stats(hist)
    veredicto = ('✅ dentro de lo asumido' if mean_w <= ASSUMED_BPS_PER_SIDE * 1.5 else
                 '⚠️ SOBRE lo asumido — el backtest puede estar sobreestimando el edge'
                 if mean_w <= ASSUMED_BPS_PER_SIDE * 4 else
                 '🚨 MUY sobre lo asumido — revisar antes de confiar en el PF de backtest')

    lineas = [f"📏 <b>CALIDAD DE EJECUCIÓN</b> — semana al {datetime.now(CHILE):%d-%m}",
              "",
              f"Slippage real señal→fill vs asumido ({ASSUMED_BPS_PER_SIDE:.0f}bp/lado):",
              f"• Semana: {mean_w:+.1f}bp promedio, p90 {p90_w:+.1f}bp (n={n_w})",
              f"• Histórico: {mean_h:+.1f}bp promedio (n={n_h})",
              f"• Veredicto: {veredicto}",
              ""]
    for sym, rows in sorted(por_sym.items()):
        n, m, p90 = stats(rows)
        lineas.append(f"  {sym}: {m:+.1f}bp prom, p90 {p90:+.1f}bp (n={n})")
    lineas.append("")
    lineas.append("(positivo = adverso: compramos más caro / vendimos más barato que la señal)")

    sys.path.insert(0, '/opt/sigma/engine/live')
    import telegram_notifier
    telegram_notifier.send('\n'.join(lineas))

if __name__ == '__main__':
    main()

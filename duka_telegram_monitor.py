#!/usr/bin/env python3
"""
duka_telegram_monitor.py
Vigila /opt/sigma/results/reports/dukascopy_backfill_master.log y avisa por
Telegram cada vez que un commodity termina su backfill, mas un resumen final.
Corre desacoplado del proceso de backfill (solo lee el log, no lo toca) --
asi no hay riesgo de interferir con el for-loop que ya esta corriendo.
"""
import sys, time, json
from pathlib import Path

sys.path.insert(0, '/opt/sigma/engine/live')
import telegram_notifier as tn

STATE_PATH = Path('/opt/sigma/results/dukascopy_telegram_notified.json')
SYMS = ['PL', 'WTI', 'HG', 'NG']
MIN_ROWS = 50000  # mismo umbral que _has_15m_data en commodities/pipeline.py


def load_notified():
    if STATE_PATH.exists():
        try:
            return set(json.loads(STATE_PATH.read_text()))
        except Exception:
            return set()
    return set()


def save_notified(s):
    STATE_PATH.write_text(json.dumps(sorted(s)))


def count_rows(sym):
    try:
        with open(f'/opt/sigma/models/data_{sym}_15m_max.csv') as f:
            return sum(1 for _ in f) - 1
    except Exception:
        return None


def main():
    notified = load_notified()

    if 'inicio' not in notified:
        tn.send("🔄 <b>Backfill historico Dukascopy iniciado</b>\n"
                "Descargando historia profunda 15m para WTI/HG/NG/PL (hoy solo "
                "tenian ~2 anios via yfinance). Orden: PL -> WTI -> HG -> NG.\n"
                "Te aviso cuando termine cada uno.")
        notified.add('inicio')
        save_notified(notified)

    def sym_truly_done(sym):
        # señal real de finalizacion -- el propio script solo escribe esta linea
        # cuando termino el trabajo de verdad (no cuando una invocacion duplicada
        # se salta por el lock de /tmp/duka_lock_{sym})
        p = Path(f'/opt/sigma/results/reports/dukascopy_fetch_{sym}.log')
        try:
            return f'DONE {sym} --' in p.read_text(errors='ignore')
        except Exception:
            return False

    while True:
        for sym in SYMS:
            key = f'terminado_{sym}'
            if key not in notified and sym_truly_done(sym):
                rows = count_rows(sym)
                if rows is not None:
                    flag = '✅ supera el piso de 50k' if rows >= MIN_ROWS else '⚠️ quedo bajo 50k (limite de la fuente)'
                    status = f"{rows:,} velas 15m -- {flag}"
                else:
                    status = "no se pudo leer el CSV final"
                tn.send(f"✅ <b>{sym} backfill terminado</b>\n{status}")
                notified.add(key)
                save_notified(notified)

        if all(f'terminado_{s}' in notified for s in SYMS) and 'fin_total' not in notified:
            lines = []
            for sym in SYMS:
                rows = count_rows(sym)
                if rows is not None:
                    mark = '✅' if rows >= MIN_ROWS else '⚠️'
                    lines.append(f"{sym}: {rows:,} velas {mark}")
                else:
                    lines.append(f"{sym}: error leyendo CSV")
            tn.send("🎉 <b>Backfill historico Dukascopy COMPLETO</b>\n" + "\n".join(lines))
            notified.add('fin_total')
            save_notified(notified)
            break

        time.sleep(300)


if __name__ == '__main__':
    main()

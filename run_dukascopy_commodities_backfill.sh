#!/bin/bash
cd /opt/sigma
LOG=/opt/sigma/results/reports/dukascopy_backfill_master.log
echo "[$(date)] INICIO backfill secuencial PL/WTI/HG/NG" >> $LOG
for sym in PL WTI HG NG; do
    echo "[$(date)] === Empezando $sym ===" >> $LOG
    /opt/sigma_env/bin/python /opt/sigma/fetch_dukascopy_commodity.py $sym >> $LOG 2>&1
    echo "[$(date)] === Terminado $sym ===" >> $LOG
done
echo "[$(date)] FIN backfill secuencial -- los 4 completados" >> $LOG

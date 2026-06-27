#!/bin/bash
LOG_HG=/opt/sigma/results/reports/dukascopy_fetch_HG.log
LOG_NG=/opt/sigma/results/reports/dukascopy_fetch_NG.log
while ! grep -q 'DONE HG --' $LOG_HG 2>/dev/null; do
    sleep 60
done
rm -f /tmp/duka_lock_NG
/opt/sigma_env/bin/python /opt/sigma/fetch_dukascopy_commodity.py NG >> $LOG_NG 2>&1

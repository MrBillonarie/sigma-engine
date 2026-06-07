#!/bin/bash
echo '=== SIGMA VPS STATUS ==='
echo ''
echo 'Servicios:'
systemctl is-active sigma-trainer sigma-web 2>/dev/null
echo ''
echo 'Procesos Python:'
ps aux | grep python | grep -v grep | wc -l
echo ''
echo 'DB Backtests:'
sqlite3 /opt/sigma/models/sigma.db 'SELECT tf, COUNT(*) as n FROM runs GROUP BY tf ORDER BY n DESC' 2>/dev/null
echo ''
echo 'Trainer log (ultimas 10):'
tail -10 /opt/sigma/results/reports/trainer.log

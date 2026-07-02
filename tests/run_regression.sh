#!/bin/bash
# Suite de regresion de bugs historicos — diario 07:00 Chile.
# Si falla: un bug viejo VOLVIO. Alerta inmediata a Telegram.
LOG=/opt/sigma/results/reports/regression_tests.log
echo "=== $(date '+%Y-%m-%d %H:%M') ===" >> $LOG
if ! /opt/sigma_env/bin/python /opt/sigma/tests/test_regression_bugs.py >> $LOG 2>&1; then
    FALLAS=$(tail -40 $LOG | grep -E '^(FAIL|ERROR):' | head -5)
    /opt/sigma_env/bin/python - << PYEOF
import sys
sys.path.insert(0, '/opt/sigma/engine/live')
import telegram_notifier
telegram_notifier.send('''🧪🚨 <b>REGRESION DETECTADA</b>
La suite de bugs historicos FALLO — un bug viejo volvio.
$FALLAS
Log: results/reports/regression_tests.log''')
PYEOF
fi

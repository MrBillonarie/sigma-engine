#!/bin/bash
# Rota backups — mantiene ultimos 7 dias de codigo, 7 de modelos, 14 de trades
BACKUP_DIR=/opt/sigma/backups
LOG=/opt/sigma/results/reports/backup_rotation.log
DATE=$(date '+%Y-%m-%d %H:%M')

# Modelos (7 dias)
find $BACKUP_DIR -name "models_*.tar.gz"   -mtime +7  -type f -delete 2>/dev/null
# Code (7 dias)
find $BACKUP_DIR -name "code_*.tar.gz"     -mtime +7  -type f -delete 2>/dev/null
# Trade state (14 dias)
find $BACKUP_DIR -name "trade_state_*.json" -mtime +14 -type f -delete 2>/dev/null
# Crontab (7 dias)
find $BACKUP_DIR -name "crontab_*.txt"     -mtime +7  -type f -delete 2>/dev/null

REMAINING=$(ls $BACKUP_DIR | wc -l)
echo "[$DATE] Rotacion completada — $REMAINING archivos en $BACKUP_DIR" >> $LOG

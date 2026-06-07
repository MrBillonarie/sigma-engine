#!/bin/bash
# Backup nocturno de modelos SIGMA — mantiene ultimos 7 dias
set -e
BACKUP_DIR=/opt/sigma/backups
DATE=$(date +%Y-%m-%d)
SRC=/opt/sigma/models

mkdir -p "$BACKUP_DIR"

# Crear tar.gz solo de modelos activos (no archive)
tar --warning=no-file-changed -czf "$BACKUP_DIR/models_$DATE.tar.gz" -C /opt/sigma --exclude=models/archive* models/ || if [ $? -eq 1 ]; then echo "[$(date)] WARN: archivos cambiaron durante backup (no critico)"; else exit 1; fi

# Borrar backups con mas de 7 dias
find "$BACKUP_DIR" -name "models_*.tar.gz" -mtime +7 -delete

# Tambien backup de trade_state.json
cp /opt/sigma/results/trade_state.json "$BACKUP_DIR/trade_state_$DATE.json" 2>/dev/null || true
find "$BACKUP_DIR" -name "trade_state_*.json" -mtime +7 -delete

echo "[$(date)] Backup OK: models_$DATE.tar.gz ($(du -h $BACKUP_DIR/models_$DATE.tar.gz | cut -f1))"

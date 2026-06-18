#!/bin/bash
# SIGMA ENGINE — Backup nocturno de codigo
# 04:30 diario: git commit + push vps-snapshot a GitHub + code tar.gz local
LOG=/opt/sigma/results/reports/nightly_backup.log
DATE=$(date '+%Y-%m-%d %H:%M')
BACKUP_DIR=/opt/sigma/backups
CODE_TAR="$BACKUP_DIR/code_$(date +%Y-%m-%d).tar.gz"

echo "[$DATE] === Inicio backup nocturno ===" >> $LOG

# ── Git commit en rama vps-snapshot ─────────────────────────────────────────
cd /opt/sigma
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ "$BRANCH" != "vps-snapshot" ]; then
    git checkout vps-snapshot >> $LOG 2>&1
fi

# 2026-06-17: git add -A puede fallar por carrera con archivos que se modifican
# mientras se leen (modelos/trade_state.json en escritura activa por el motor
# corriendo en vivo) -- "fatal: confused by unstable object source data". Sin
# reintento, esto dejaba el backup de GitHub desactualizado en silencio (4 dias).
ADD_OK=0
for i in 1 2 3; do
    if git add -A >> $LOG 2>&1; then
        ADD_OK=1
        break
    fi
    echo "[$DATE] git add -A fallo (intento $i/3) -- reintentando en 5s" >> $LOG
    sleep 5
done

if [ "$ADD_OK" -eq 0 ]; then
    echo "[$DATE] git add -A fallo 3/3 -- backup de hoy incompleto, se reintenta manana" >> $LOG
else
    CHANGED=$(git diff --cached --name-only | wc -l)
    if [ "$CHANGED" -gt 0 ]; then
        git commit -m "Auto backup $(date '+%Y-%m-%d %H:%M') — $CHANGED archivos" >> $LOG 2>&1
        echo "[$DATE] Git: $CHANGED archivos commiteados" >> $LOG
    else
        echo "[$DATE] Git: sin cambios nuevos" >> $LOG
    fi
fi

# ── Push a GitHub (offsite real) ─────────────────────────────────────────────
git push origin vps-snapshot >> $LOG 2>&1 && \
    echo "[$DATE] GitHub push OK (vps-snapshot)" >> $LOG || \
    echo "[$DATE] GitHub push FALLO" >> $LOG

# ── Code tar.gz local (recovery rapido) ──────────────────────────────────────
mkdir -p $BACKUP_DIR
tar czf "$CODE_TAR" \
    --exclude='/opt/sigma/models' \
    --exclude='/opt/sigma/backups' \
    --exclude='/opt/sigma/.git' \
    --exclude='/opt/sigma/archive' \
    --exclude='/opt/sigma/results/reports' \
    --exclude='/opt/sigma/ibkr/gateway' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    /opt/sigma >> $LOG 2>&1
echo "[$DATE] Code tar.gz: $(du -sh $CODE_TAR | cut -f1)" >> $LOG

# ── Dump crontab ──────────────────────────────────────────────────────────────
crontab -l > "$BACKUP_DIR/crontab_$(date +%Y-%m-%d).txt" 2>/dev/null

echo "[$DATE] === Backup completado ===" >> $LOG

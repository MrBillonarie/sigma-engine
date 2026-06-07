#!/bin/bash
# Queue nocturno de pushes Optuna sobre slots con champion grade B.
# Espera al push actual (LTC 4h) y despues corre los 3 restantes en serie.

set -u

PYBIN=/opt/sigma_env/bin/python
SCRIPT=/opt/sigma/scripts/push_grade_a.py
LOG_DIR=/opt/sigma/results/reports
TRIALS=600
QUEUE_LOG="$LOG_DIR/push_queue_overnight_$(date +%Y%m%d_%H%M%S).log"

# Helper: send telegram via champion_watcher
notify_tg() {
    local msg="$1"
    /opt/sigma_env/bin/python -c "
import sys
sys.path.insert(0, '/opt/sigma')
from champion_watcher import send_telegram
send_telegram('''$msg''')
" >> "$QUEUE_LOG" 2>&1
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$QUEUE_LOG"
}

log "================================================"
log "QUEUE OVERNIGHT — push grade A para 3 candidatos"
log "================================================"
log "Cola: SOL 15m → BNB 1h → LTC 15m"
log "Trials por slot: $TRIALS"
log "Log: $QUEUE_LOG"

# 1) Esperar a que termine el push actual de LTC 4h
log ""
log "STEP 0 — Esperando al push activo de LTC 4h..."
while pgrep -f "push_grade_a.py LTC 4h" > /dev/null; do
    sleep 60
done
log "STEP 0 OK — LTC 4h push completo, arrancando queue"

# Push 1: SOL 15m
log ""
log "STEP 1 — SOL 15m regime_adaptive (score 0.4429, +0.1071 a A)"
SLOT_LOG="$LOG_DIR/push_grade_a_SOL_15m_$(date +%Y%m%d_%H%M%S).log"
"$PYBIN" "$SCRIPT" SOL 15m $TRIALS > "$SLOT_LOG" 2>&1
log "STEP 1 OK — log: $SLOT_LOG"

# Push 2: BNB 1h
log ""
log "STEP 2 — BNB 1h mfi_reversal (score 0.4367, +0.1133 a A)"
SLOT_LOG="$LOG_DIR/push_grade_a_BNB_1h_$(date +%Y%m%d_%H%M%S).log"
"$PYBIN" "$SCRIPT" BNB 1h $TRIALS > "$SLOT_LOG" 2>&1
log "STEP 2 OK — log: $SLOT_LOG"

# Push 3: LTC 15m
log ""
log "STEP 3 — LTC 15m macd_zero_cross_down (score 0.4115, +0.1385 a A)"
SLOT_LOG="$LOG_DIR/push_grade_a_LTC_15m_$(date +%Y%m%d_%H%M%S).log"
"$PYBIN" "$SCRIPT" LTC 15m $TRIALS > "$SLOT_LOG" 2>&1
log "STEP 3 OK — log: $SLOT_LOG"

# Re-generar Pine + verify
log ""
log "STEP 4 — Re-generar Pine y verify"
cd /opt/sigma
"$PYBIN" engine/live/generate_pine_params.py >> "$QUEUE_LOG" 2>&1
"$PYBIN" verify_pine_sync.py >> "$QUEUE_LOG" 2>&1

log ""
log "================================================"
log "QUEUE OVERNIGHT — COMPLETO"
log "================================================"

# Final Telegram notif
notify_tg "🌙 <b>Push overnight terminado</b>%0AQueue de 3 slots con champion grade B cerró exitosamente. Revisar dashboard y #estrategias-algo en Discord para ver si alguno cruzó a grade A. Log completo en $QUEUE_LOG"

#!/bin/bash
# Espera que el pipeline complete el primer ciclo (todos los 17 slots sin modelo)
# y luego reactiva sigma-trainer
sleep 5400  # 90 minutos
systemctl start sigma-trainer
echo "[Fri May  8 17:06:58 HSP 2026] sigma-trainer reactivado tras aceleracion" >> /opt/sigma/results/reports/master_pipeline.log

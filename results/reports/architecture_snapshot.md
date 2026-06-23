# SIGMA — Snapshot de Arquitectura
> Generado automaticamente: 2026-06-23 00:00:00
> Este archivo se regenera cada dia. No editar a mano.

## Servicios systemd (sigma-*)

- `sigma-architecture-map.service` — start SIGMA Architecture Map - mapa vivo de servicios/crons vs manifiesto
- `sigma-backup.service` — SIGMA Backup diario
- `sigma-champion-watcher.service` — SIGMA Champion Watcher Daemon - reactivo a cambios en models/
- `sigma-commodities.service` — SIGMA Motor 2 - Commodities Pipeline (XAU/XAG 4h/1h/15m/5m)
- `sigma-dashboard-guardian.service` — SIGMA Dashboard Guardian - protege dashboard.py de overwrites
- `sigma-data-integrity.service` — SIGMA Data Integrity - valida frescura de datos OHLCV/derivados
- `sigma-frontend.service` — SIGMA Web - Next.js Frontend (squantdesk.com)
- `sigma-marketing.service` — SIGMA Marketing Engine — auto-publisher
- `sigma-milestone.service` — SIGMA Telegram Milestone Monitor
- `sigma-nextjs.service` — SIGMA Web Next.js App
- `sigma-paper-trader.service` — SIGMA Paper Trader Daemon
- `sigma-pipeline.service` — SIGMA Master Pipeline - 5 activos x 4 TFs continuo
- `sigma-telegram.service` — SIGMA Telegram Notifier
- `sigma-trainer.service` — SIGMA Trainer
- `sigma-watchdog.service` — SIGMA Watchdog
- `sigma-web.service` — SIGMA Web Dashboard

## Timers systemd (sigma-*)

- `sigma-architecture-map.timer` — Run SIGMA Architecture Map daily
- `sigma-backup.timer` — Run SIGMA Backup daily at 04:00
- `sigma-dashboard-guardian.timer` — Run Dashboard Guardian every 2 minutes
- `sigma-data-integrity.timer` — Run SIGMA Data Integrity every 30 minutes
- `sigma-milestone.timer` — Run SIGMA Telegram Milestone Monitor every 10 minutes
- `sigma-watchdog.timer` — Run SIGMA Watchdog every 5 minutes

## Crontab (54 líneas activas)

- `0 2 * * 0 cd /opt/sigma && /opt/sigma_env/bin/python engine/optimization/wft_all_models.py >> results/reports/wft_weekly.log 2>&1`
- `0 2 1 * * cd /opt/sigma && /opt/sigma_env/bin/python -u run_mc_all.py >> results/reports/mc_monthly.log 2>&1`
- `0 3 * * 0 cd /opt/sigma && /opt/sigma_env/bin/python engine/analysis/monte_carlo_v2.py >> results/reports/mc.log 2>&1`
- `0 3 * * 1 cd /opt/sigma && /opt/sigma_env/bin/python -u engine/optimization/walk_forward_v2.py --tf 1h --trials 60 > results/reports/wft_eth.log 2>&1`
- `0 * * * * cd /opt/sigma && /opt/sigma_env/bin/python sync_models.py >> results/reports/sync.log 2>&1`
- `*/15 * * * * systemctl is-active sigma-pipeline || systemctl start sigma-pipeline`
- `*/2 * * * * curl -sf --max-time 10 http://localhost:8080/api/stats > /dev/null 2>&1 || (sleep 30 && curl -sf --max-time 10 http://localhost:8080/api/stats > /de`
- `30 0 * * * cd /opt/sigma && /opt/sigma_env/bin/python update_data.py >> results/reports/update_data.log 2>&1`
- `30 3 * * 0 cd /opt/sigma && /opt/sigma_env/bin/python engine/live/decay_monitor.py --days 90 >> results/reports/decay_cron.log 2>&1`
- `0 */6 * * * cd /opt/sigma && /opt/sigma_env/bin/python engine/live/champion_elector.py --notify >> results/reports/champion_elector_cron.log 2>&1`
- `CRON_TZ=America/Santiago`
- `0 9 * * *  /opt/sigma_env/bin/python /opt/sigma/morning_news.py  >> /opt/sigma/results/reports/tg_news.log 2>&1`
- `0 21 * * * /opt/sigma_env/bin/python /opt/sigma/evening_news.py >> /opt/sigma/results/reports/tg_news.log 2>&1`
- `*/5 * * * * /opt/sigma_env/bin/python /opt/sigma/engine/data/lsr_fetcher.py 1 >> /opt/sigma/results/reports/lsr_fetcher.log 2>&1`
- `*/15 * * * * /opt/sigma_env/bin/python /opt/sigma/engine/data/oi_fetcher.py 1 >> /opt/sigma/results/reports/oi_fetcher.log 2>&1`
- `0 */6 * * *  /opt/sigma_env/bin/python /opt/sigma/engine/data/fng_fetcher.py 2 >> /opt/sigma/results/reports/fng_fetcher.log 2>&1`
- `*/5 * * * * /opt/sigma_env/bin/python /opt/sigma/scripts/funding_emergency_dispatcher.py >> /var/log/funding_emergency.log 2>&1`
- `0 5 * * * /opt/sigma/scripts/rotate_backups.sh >> /opt/sigma/results/reports/backup_rotation.log 2>&1`
- `0 6 * * * cd /opt/sigma && /opt/sigma_env/bin/python verify_pine_sync.py >> /opt/sigma/results/reports/pine_sync.log 2>&1`
- `*/30 * * * * cd /opt/sigma && /opt/sigma_env/bin/python engine/live/generate_pine_params.py >> /opt/sigma/results/reports/pine_regen.log 2>&1`
- `*/30 * * * * cd /opt/sigma && /opt/sigma_env/bin/python bayesian_tracker.py >> results/reports/bayesian_tracker.log 2>&1`
- `*/15 * * * * /opt/sigma_env/bin/python /opt/sigma/gap_auto_launcher.py >> /opt/sigma/results/reports/gap_auto_launcher.log 2>&1`
- `30 * * * * /opt/sigma_env/bin/python /opt/sigma/scripts/adaptive_push_launcher.py`
- `0 * * * * /opt/sigma_env/bin/python /opt/sigma/ibkr/ibkr_fetcher.py >> /opt/sigma/ibkr/logs/cron.log 2>&1`
- `0 1 * * * /opt/sigma_env/bin/python /opt/sigma/update_xau_data.py >> /opt/sigma/results/reports/xau_update.log 2>&1`
- `30 4 * * * /opt/sigma/scripts/nightly_backup.sh`
- `0 1 * * * /opt/sigma_env/bin/python /opt/sigma/decay_detector.py >> /opt/sigma/logs/decay_detector.log 2>&1`
- `0 * * * * /opt/sigma_env/bin/python /opt/sigma/utils/performance_tracker.py >> /opt/sigma/results/reports/performance_tracker.log 2>&1`
- `5 * * * * /opt/sigma_env/bin/python /opt/sigma/utils/portfolio_risk.py >> /opt/sigma/results/reports/portfolio_risk.log 2>&1`
- `10 * * * * /opt/sigma_env/bin/python /opt/sigma/utils/risk_budget.py >> /opt/sigma/results/reports/risk_budget.log 2>&1`
- `*/30 * * * * cd /opt/sigma && /opt/sigma_env/bin/python engine/commodities/fetcher.py >> results/reports/commodities_update.log 2>&1  # cada 30 min - Motor 2 fr`
- `*/15 * * * * systemctl is-active sigma-commodities || systemctl start sigma-commodities`
- `0 6 * * * /opt/sigma_env/bin/python /opt/sigma/ibkr/yfinance_fetcher.py >> /opt/sigma/ibkr/logs/yfinance_fetcher.log 2>&1`
- `30 21 * * * SEND_TELEGRAM=1 /opt/sigma_env/bin/python /opt/sigma/daily_hf_report.py >> /opt/sigma/results/reports/hf_report.log 2>&1`
- `0 */6 * * * /opt/sigma_env/bin/python /opt/sigma/utils/hrp_portfolio.py >> /opt/sigma/logs/hrp.log 2>&1`
- `*/15 * * * * /opt/sigma_env/bin/python /opt/sigma/engine/live/regime_multi.py >> /opt/sigma/logs/regime.log 2>&1`
- `0 11 * * 0 SEND_TELEGRAM=1 /opt/sigma_env/bin/python /opt/sigma/engine/live/trade_researcher.py >> /opt/sigma/logs/researcher.log 2>&1`
- `*/30 * * * * /opt/sigma_env/bin/python /opt/sigma/engine/live/exposure_guardian.py >> /opt/sigma/logs/exposure.log 2>&1`
- `*/30 * * * * /opt/sigma_env/bin/python /opt/sigma/engine/commodities/m2_validator.py >> /opt/sigma/logs/m2_validator.log 2>&1`
- `0 2 * * * /opt/sigma_env/bin/python /opt/sigma/engine/live/slippage_calibrator.py >> /opt/sigma/logs/slippage.log 2>&1`
- `*/30 * * * * /opt/sigma_env/bin/python /opt/sigma/engine/live/vol_targeting.py >> /opt/sigma/logs/vol_target.log 2>&1`
- `30 3 * * 0 sqlite3 /opt/sigma/models/optuna_studies.db 'PRAGMA wal_checkpoint(TRUNCATE);' >> /opt/sigma/logs/vacuum.log 2>&1`
- `0 6 * * * /opt/sigma_env/bin/python3 /opt/sigma/engine/live/regime_memory.py >> /opt/sigma/results/reports/regime_memory.log 2>&1`
- `0 7 * * 1 /opt/sigma_env/bin/python3 /opt/sigma/engine/live/lifespan_tracker.py >> /opt/sigma/results/reports/lifespan.log 2>&1`
- `0 */6 * * * /opt/sigma_env/bin/python /opt/sigma/scripts/freeze_champion.py auto >> /opt/sigma/results/reports/freeze_monitor.log 2>&1`
- `30 14 * * 3,4 /opt/sigma_env/bin/python /opt/sigma/engine/commodities/eia_fetcher.py >> /opt/sigma/results/reports/eia_fetcher.log 2>&1`
- `30 1 * * * /opt/sigma_env/bin/python3 /opt/sigma/update_eia_data.py >> /opt/sigma/results/reports/eia_update.log 2>&1`
- `*/30 * * * * cd /opt/sigma && /opt/sigma_env/bin/python champion_watcher.py --seed-only >> results/reports/champion_watcher.log 2>&1`
- `10 7 * * * curl -s -H "Authorization: Bearer 0d45863ded29be15ffcac419c1a05b5dc20fa2cd13fc21754a2078caa10df0ac" http://127.0.0.1:3000/api/cron/macro-calendar >> `
- `0 4 * * 0 cd /opt/sigma && /opt/sigma_env/bin/python engine/live/adversarial_validator.py >> results/reports/adversarial_validator_cron.log 2>&1`
- `0 5 * * * cd /opt/sigma && /opt/sigma_env/bin/python engine/live/treasury_manager.py >> results/reports/treasury_manager_cron.log 2>&1`
- `*/15 * * * * systemctl is-active sigma-trainer || systemctl start sigma-trainer # Watchdog sigma-trainer cada 15min`
- `0 9 * * 1 cd /opt/sigma && /opt/sigma_env/bin/python scripts/reto_digest_cron.py >> results/reports/reto_digest.log 2>&1`
- `0 */6 * * * cd /opt/sigma && /opt/sigma_env/bin/python selection_bias_report.py >> /opt/sigma/results/reports/selection_bias.log 2>&1`

## Manifiesto (baseline conocido)

- Servicios conocidos: 16
- Timers conocidos: 6
- Cron lines conocidas: 53
- Baseline creado: 2026-06-19 16:43:36
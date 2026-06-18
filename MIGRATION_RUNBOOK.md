# Runbook de migración a VPS nuevo

Generado 2026-06-17. Ver memoria `project_vps_migration_prep_2026_06_17` para el contexto completo.

**Regla de oro:** nunca debe haber dos instancias de `sigma-web` (ni de ningún servicio que abra trades) corriendo al mismo tiempo contra la misma cuenta de Binance. Las órdenes SL/TP reales viven en Binance, no en ningún VPS — mientras NINGÚN motor esté corriendo, la posición sigue protegida.

## Fase 0 — Antes de tocar nada
- [ ] VPS nuevo elegido y provisionado (Ubuntu 24.04, recomendado 16 vCPU dedicados — ver memoria para comparación de proveedores)
- [ ] Agregar la IP nueva al whitelist de la API key de Binance (sin quitar la vieja todavía)
- [ ] Confirmar que `bash /opt/sigma/scripts/nightly_backup.sh` corrió sin errores hoy (backup fresco en GitHub `vps-snapshot`)

## Fase 1 — Preparar el VPS nuevo (cero riesgo, el viejo sigue operando)
```bash
# En el VPS nuevo
apt update && apt install -y python3-venv python3-pip nginx certbot python3-certbot-nginx sqlite3 git nodejs npm
python3 -m venv /opt/sigma_env
```

## Fase 2 — Transferir datos
```bash
# Desde el VPS VIEJO, hacia el nuevo (repetible, solo copia deltas)
rsync -avz --progress -e ssh /opt/sigma/ root@<IP_NUEVA>:/opt/sigma/

# Secrets (NO estan en git) -- copiar a mano por canal seguro
scp /opt/sigma/engine/config/secrets.json root@<IP_NUEVA>:/opt/sigma/engine/config/secrets.json
scp /opt/sigma/utils/secrets.py root@<IP_NUEVA>:/opt/sigma/utils/secrets.py

# En el VPS nuevo
/opt/sigma_env/bin/pip install -r /opt/sigma/requirements.txt
cp /opt/sigma/backups/sigma-*.service /etc/systemd/system/
systemctl daemon-reload
crontab /opt/sigma/backups/crontab_<FECHA-MAS-RECIENTE>.txt
```
**No activar (`enable`/`start`) los servicios todavía.**

Actualizar 3 referencias de IP hardcodeada en el VPS nuevo (antes de arrancar):
- `web_server.py` línea ~9545 (solo print informativo)
- `notifier.py` líneas 10 y 36 (`API`, `BASE_URL` — esta sí se usa en una llamada real)
- `ibkr/sigma_2fa_server.py` línea ~123 (solo print informativo)

Configurar nginx (copiar `/etc/nginx/sites-available/{squantdesk,motor.squantdesk.com}` del viejo al nuevo) — certbot se corre después de mover el DNS (Fase 3).

## Fase 3 — Corte (con la posición real abierta)
```bash
# 1. En el VPS VIEJO: parar todo en este orden
systemctl stop sigma-web sigma-pipeline sigma-trainer sigma-paper-trader \
              sigma-telegram sigma-commodities sigma-champion-watcher sigma-nextjs

# 2. rsync final incremental (rapido, solo deltas, captura trade_state.json actual)
rsync -avz --progress -e ssh /opt/sigma/ root@<IP_NUEVA>:/opt/sigma/

# 3. En el VPS NUEVO: arrancar todo
systemctl enable --now sigma-web sigma-pipeline sigma-trainer sigma-paper-trader \
                       sigma-telegram sigma-commodities sigma-champion-watcher sigma-nextjs
```

### Verificación inmediata post-corte (checklist probado en producción 2026-06-17)
```bash
systemctl is-active sigma-web sigma-pipeline sigma-trainer sigma-paper-trader \
                     sigma-telegram sigma-commodities sigma-champion-watcher sigma-nextjs

journalctl -u sigma-web --since "5 minutes ago" --no-pager | grep -iE 'error|traceback|exception'

# Debe decir "RECONCILE OK: <sym>/<tf> tiene SL activo" (no "ALERT...emergencia")
tail -5 /opt/sigma/results/reports/executor.log

# /api/trades del nuevo VPS debe coincidir con la posicion real en Binance
curl -s http://127.0.0.1:8080/api/trades | python3 -m json.tool
```
Cross-check directo contra Binance (usar `engine.live.live_executor._get_exchange()`):
```python
from engine.live.live_executor import _get_exchange, _binance_symbol
ex = _get_exchange()
print(ex.fetch_positions())
symbol = _binance_symbol('LTC')  # o el symbol que corresponda
print(ex.fapiPrivateGetOpenAlgoOrders({'symbol': ex.market(symbol)['id']}))  # debe mostrar SL+TP
```

- [ ] Cambiar DNS A de `squantdesk.com` y `motor.squantdesk.com` a la IP nueva
- [ ] `certbot --nginx` en el VPS nuevo para emitir certificados (DNS ya debe resolver ahí)

## Fase 4 — Enfriamiento
- [ ] VPS viejo apagado pero NO destruido por 24-48h (rollback)
- [ ] Monitorear load promedio, latencia, que el cron corra sin error
- [ ] Recién después: quitar la IP vieja del whitelist de Binance y destruir el VPS viejo

## Servicios a migrar (15 confirmados)
sigma-web, sigma-pipeline, sigma-trainer, sigma-paper-trader, sigma-telegram, sigma-commodities,
sigma-champion-watcher, sigma-nextjs, sigma-discord, sigma-frontend, sigma-backup, sigma-watchdog,
sigma-milestone, sigma-dashboard-guardian, sigma-marketing
(+ IB Gateway vía IBC — no es servicio systemd, requiere re-autenticación 2FA manual en el VPS nuevo)

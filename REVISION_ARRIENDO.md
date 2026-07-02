# Revisión trimestral de arriendo de subsistemas

Instituida: 2026-07-02. Recordatorio automático via Telegram el día 1 de ene/abr/jul/oct.

**El principio:** cada subsistema (servicio, cron, motor, canal, integración) debe poder
responder una pregunta por trimestre: **¿qué decisión o dinero cambió gracias a ti?**
El que no responde, se ARCHIVA (no se borra — coherente con feedback_dont_delete_data):
servicio detenido + disabled, cron comentado, código queda en git.

**Por qué existe:** la superficie del sistema solo crece (11 servicios, 3 motores,
~30 crons a la fecha), y cada pieza extra es algo que todas las auditorías futuras
deben revisar para siempre. Un sistema que aspira a correr solo tiene que ser podable
por diseño. Las auditorías de jun-jul 2026 gastaron la mayor parte del tiempo en
piezas que nadie usaba (marketing sin API keys, Discord con token muerto, IBKR
read-only sin consumidor).

**El ritual (30 min, 4 veces al año):**
1. Listar: `systemctl list-units 'sigma-*'` + `crontab -l` + motores + canales.
2. Por cada uno: ¿qué decisión o dinero cambió este trimestre? (una línea)
3. Los que no respondan → archivar en ese momento, no "el próximo mes".
4. Registrar el resultado en el diario de decisiones (qué se archivó y por qué).

**Candidatos ya conocidos para la primera revisión (2026-10-01):**
- sigma-marketing (sin API keys desde su creación — ¿se activó?)
- Bot Discord (token revocado desde 06-15 — ¿se regeneró?)
- IBKR read-only (¿alguien consumió /api/ibkr_positions?)
- Motores/TFs sin champion PASS_LIVE ni señal en 90 días

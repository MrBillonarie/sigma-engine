# SIGMA ENGINE — Protocolos de Pre-compromiso

Creado: 2026-07-02. Complementa `RISK_POLICY.md` (que describe los mecanismos automáticos).

**Qué es esto:** decisiones tomadas EN FRÍO, antes de que ocurra el evento que las gatilla.
El historial del proyecto muestra que los peores errores en trading se cometen ajustando
reglas bajo presión — tras un drawdown, con posiciones abiertas, o con una meta de fecha
encima (ver reto $1000: se detectó inalcanzable y la decisión correcta fue NO subir el
riesgo). Este documento existe para que, cuando llegue la presión, la decisión ya esté tomada.

**Regla cero:** nada de este documento se renegocia con posiciones abiertas ni en drawdown.
Cambios solo en frío, flat, y quedan registrados en el diario de decisiones con fecha y motivo.

---

## 1. Protocolo de cambio de régimen (BEAR → BULL/RANGE)

**Contexto:** las ~32 trades que abrieron el gate LIVE (2026-06-17) fueron 100% en régimen
BEAR. Esto se reconoció públicamente en el anuncio. La hipótesis "el edge sobrevive fuera
del régimen donde nació" está SIN PROBAR. Cuando el régimen semanal gire (señal: EMA200
semanal, `engine/live/regime_multi.py`), aplica automáticamente lo siguiente:

1. **Primeras 2 semanas en el régimen nuevo:** Kelly global ×0.5 sobre lo que ya calcule
   la cadena de multiplicadores (§1 de RISK_POLICY). Máximo 2 slots LIVE simultáneos (en
   vez de 4).
2. **Checkpoint a los 10 trades cerrados en el régimen nuevo:**
   - WR ≥ 45% y PF ≥ 1.0 → se levanta la restricción gradualmente (Kelly ×0.75 dos semanas
     más, luego normal).
   - WR < 45% o PF < 1.0 → **LIVE OFF para los modelos sin evidencia en ese régimen**;
     siguen en paper hasta acumular 20 trades paper con PF ≥ 1.2 en el régimen nuevo.
3. Las excepciones nombradas con evidencia retroactiva (ej: ETH/4h tma_bands, testeado
   long-en-BEAR con n=88) mantienen su estatus — la excepción es por modelo y por evidencia,
   nunca global.

## 2. Criterio de despido de champions (evidencia LIVE manda)

La entrada de un champion es exigente (robustness gate, walk-forward); la salida hoy es
vaga. Se corrige con esto:

- **Por modelo, con ≥10 trades LIVE cerrados:** si su WR live está más de 20 puntos
  absolutos bajo su WR de backtest, O su PF live < 0.8 → **democión a PAPER_ONLY**
  (Kelly multiplier 0.0, mismo mecanismo del §3 de RISK_POLICY). Re-promoción solo
  pasando el gate completo de nuevo, no por decisión manual.
- **Por modelo, con <10 trades:** no se despide por resultados (ruido), solo por bugs
  demostrados en su lógica (precedente: los 26 sig_*_short invalidados 2026-05-14).
- El `decay_monitor.py` semanal ya mide esto; este protocolo fija la CONSECUENCIA.
- **Prohibido** despedir o perdonar un champion mientras tenga posición abierta.

## 3. Kill criteria del sistema completo

Sin esto, la hipótesis "SIGMA tiene edge" es infalsificable. Condiciones bajo las cuales
se apaga LIVE por completo (LIVE_MODE=False, posiciones cerradas ordenadamente, se vuelve
a paper):

- **Por evidencia:** tras ≥60 trades LIVE cerrados cruzando ≥2 regímenes, si el PF del
  portafolio < 1.0 → el sistema NO tiene edge demostrado. LIVE OFF, post-mortem escrito,
  mínimo 30 días de paper + gate de 30 trades desde cero antes de reactivar.
- **Por capital:** equity real de Binance < 70% del capital de referencia (hoy: $550.51
  inicial → piso $385; recalcular piso solo hacia ARRIBA cuando se deposite capital nuevo,
  nunca hacia abajo). LIVE OFF inmediato sin discusión.
- **Por inestabilidad:** circuit breaker disparado 3 veces dentro de 30 días → LIVE OFF
  + revisión de causa raíz. Un CB que dispara repetido no es mala suerte, es señal.
- **Reactivación:** exige causa raíz identificada, corregida y testeada. "Ya pasó" no es
  causa raíz. El sistema no tiene derecho a una segunda hipótesis con el mismo capital
  sin explicar la primera.

## 4. Runbook: operador indisponible (factor bus = 1)

**Diseño actual (verificado 2026-07-02):** el sistema sobrevive solo. SL y TP viven como
órdenes reales (algo orders) EN Binance, no en el VPS — si el VPS muere, cada posición
sigue protegida por el exchange. `reconcile()` verifica cada 5 min que cada posición LIVE
tenga su SL. Circuit breaker y watchdogs corren sin intervención.

**Si el operador está indisponible >72h:** no se requiere ninguna acción para que el
capital sobreviva. Lo que NO pasa solo: depósitos, retiros, decisiones de este documento.

**Acceso de emergencia:**
- Colega: llave SSH propia (`sigma_vps_colega`) al VPS.
- Cierre total de emergencia (flat-all) SIN necesidad de VPS ni terminal: entrar a la app
  de Binance → Futures → cerrar todas las posiciones a mercado. Las cuentas que copian
  cierran proporcionalmente solas (mecánica del copy trading de Binance). Esto es siempre
  suficiente: no hay estado en el VPS cuya pérdida deje dinero en riesgo.
- El bookkeeping local (trade_state.json) quedará desincronizado tras un flat-all manual;
  eso es cosmético y se corrige después — el dinero primero.

## 5. Qué NO autoriza este documento

- No autoriza subir Kelly, leverage ni slots por encima de RISK_POLICY bajo ninguna
  circunstancia "excepcional".
- No autoriza reabrir LIVE tras un kill sin el proceso completo del §3.
- No autoriza agregar activos al universo (regla vigente: BTC/ETH/SOL/BNB/LTC + commodities
  + stocks M3; cerrado).

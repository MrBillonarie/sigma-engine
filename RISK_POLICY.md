# SIGMA ENGINE — Política de Riesgo e Inversión

Última actualización: 2026-06-18. Este documento describe las reglas que gobiernan cómo SIGMA ENGINE asigna capital, valida estrategias, y se protege a sí mismo. Cada regla cita el archivo/función real que la implementa — si el código cambia y este documento no, el código manda y este documento queda desactualizado (revisar antes de confiar en él para una decisión importante).

## 1. Tamaño de posición

Cada trade real usa una cadena de 6 multiplicadores sobre un Kelly base, nunca un % fijo. Implementado en `web_server.py` (~L4673-4715):

```
eff_risk_pct = base_risk(HRP) × dd_kelly_mult × stress_mult × exposure_mult × regime_mult × vol_mult
             (tope duro: min(..., 5.0) aquí, y MAX_KELLY_PCT=6.0% en live_executor.py)
```

| Multiplicador | Qué hace | Archivo |
|---|---|---|
| `base_risk` (HRP) | Peso por slot vía Hierarchical Risk Parity, no flat 5% | `utils/hrp_portfolio.py` |
| `dd_kelly_mult` | Reduce Kelly si el portafolio está en drawdown (curva continua, ver §5) | `engine/live/exposure_guardian.py` |
| `stress_mult` | Reduce si los últimos 5 trades muestran deterioro de WR + sequía de victorias | `web_server.py` (`_stress_mult`, ~L4680) |
| `exposure_mult` | Guardian de exposición total del portafolio | `engine/live/exposure_guardian.py` → `_read_exposure_gate()` |
| `regime_mult` | Ajusta por régimen (BULL/BEAR/RANGE) del activo específico | `engine/live/regime_multi.py` |
| `vol_mult` | Volatility targeting (objetivo 30% anual, ver §5) | `utils/risk_budget.py` |

El tamaño final en dólares: `size_usd = equity_real_binance × eff_risk_pct/100`, ejecutado en `engine/live/live_executor.py::execute_entry()`. **Usa el balance REAL de Binance, no el equity de paper trading** (son dos libros contables separados).

## 2. Asignación de capital entre slots

`utils/hrp_portfolio.py` (cron cada 6h) calcula pesos vía Hierarchical Risk Parity (López de Prado): clustering jerárquico por correlación entre slots, escalado min-max a Kelly **[1.5%, 8.0%]** — los slots más correlacionados entre sí reciben menos peso conjunto, no hay flat 5% para todos. Output: `results/reports/kelly_weights.json`, consumido directo por la cadena de §1.

Límite adicional independiente: `utils/quant.py::position_correlation_gate` bloquea una nueva posición si ya hay ≥2 posiciones abiertas en el mismo cluster correlacionado (clusters: {BTC,ETH,LTC}=1, {SOL,BNB}=2) en la misma dirección. Wireado en `web_server.py:9457`.

## 3. Promoción/democión de estrategias

`utils/robustness.py::robustness_score()` clasifica cada modelo entrenado en **PASS_LIVE / PAPER_ONLY / BLOCKED** según:
- Consistencia IS vs OOS (in-sample vs out-of-sample)
- Drawdown máximo
- Tasa de aprobación walk-forward
- Cantidad de trades
- Gate de sanidad (2026-06-18): CAGR >800% o WR>95% con ≥20 trades se considera imposible/sospechoso → BLOCKED o degradado a PAPER_ONLY

Solo `PASS_LIVE` puede operar con dinero real (Kelly multiplier 1.0); `PAPER_ONLY`/`BLOCKED` tienen multiplier 0.0 — pueden ser "champion" de su slot pero nunca generan una orden real.

`engine/live/champion_elector.py` (cron cada 6h) re-evalúa qué estrategia es campeona de cada slot y notifica por Telegram en cada cambio.

**Importante:** el champion gate exige que la señal venga específicamente de la estrategia campeona del slot — si otra estrategia no-campeona señala (incluso con mejor grade/WR aparente), el sistema la bloquea (`[CHAMPION_GATE] ... sin signal activa - abortando`, `web_server.py` ~L5026-5044). El campo `is_champion` en `/api/signals` (agregado 2026-06-18) hace esto explícito para evitar confusión.

## 4. Gates para pasar de paper a real

Dos checklists independientes, ambos deben cumplirse:

**A. Gate de 30 trades** (`utils/performance_tracker.py::LIVE_GATE_CRITERIA`):
| Criterio | Umbral |
|---|---|
| Trades | ≥ 30 |
| Días corriendo | ≥ 21 |
| WR portafolio | ≥ 55% |
| Max DD | ≥ -15% |
| Profit factor | ≥ 1.2 |
| Equity mínimo | ≥ $9,000 (paper) |
| Estrategias batiendo backtest (CI) | ≥ 1 |

**B. Scorecard de 100 puntos** (`engine/live/live_checklist.py`): 5 categorías — Performance paper (25pts), Calidad de modelos (20pts), Sistemas de riesgo (20pts), Infraestructura técnica (20pts), API/conectividad (15pts). Mínimo 80/100 para activar `LIVE_EXECUTION`. Verificado en `/api/trades` → `live_readiness.score`.

Estado actual (2026-06-18): 100/100, LIVE activo desde 2026-06-17.

## 5. Triggers de emergencia

**Circuit breaker** (`web_server.py` ~L1695-1730): se activa si CUALQUIERA de estos dos ocurre:
- CUSUM estadístico: en una ventana de los últimos 15 trades cerrados (mínimo 10), el z-score de win-rate observado vs esperado (baseline 65%) cae por debajo de **-2.0**. Si hay menos de 10 trades cerrados, fallback simple: 3 pérdidas consecutivas.
- Drawdown desde el último pico de equity **< -8%**.

Al activarse, bloquea nuevas entradas hasta reset manual o automático (ver `state['circuit_breaker']`).

**Exposure Guardian** (`engine/live/exposure_guardian.py`): curva continua de reducción de Kelly según drawdown actual — no es un escalón abrupto:
| DD desde pico | Kelly multiplier |
|---|---|
| 0% | 1.00 |
| -5% | 0.85 |
| -10% | 0.65 |
| -15% | 0.45 |
| -20% | 0.25 |
| -25% o peor | 0.10 (modo supervivencia) |

**Volatility targeting** (`utils/risk_budget.py`): objetivo de volatilidad anualizada 30%. Si supera 50% → sugiere reducir Kelly; si cae bajo 15% → sugiere subirlo (el sistema solo sugiere, no auto-ajusta este multiplicador específico).

## 6. Reporting

| Métrica | Dónde se calcula | Dónde se ve |
|---|---|---|
| Sharpe (con CI, Lo 2002) | `utils/quant.py::sharpe_with_ci` | Telegram diario (`daily_hf_report.py`, 21:30 Chile), `bayesian_edges.json` |
| Sortino (downside-only) | `utils/quant.py::sortino_with_ci` (agregado 2026-06-18) | Telegram diario |
| VaR 95%/99%, CVaR | `utils/portfolio_risk.py` (cron hourly) | `/api/portfolio_risk`, Telegram diario |
| Stress test (shock BTC -10/-20/-30%) | `utils/portfolio_risk.py::stress_scenarios()` (agregado 2026-06-18) | `/api/portfolio_risk` campo `stress_test`, Telegram diario. **Nota:** no tiene tarjeta propia en el dashboard Next.js todavía — se evaluó pero no había un patrón de tarjeta de VaR existente para replicar (gap real encontrado durante la implementación, ver pendientes). |
| Concentración (HHI) | `utils/portfolio_risk.py` | Telegram diario, `/api/portfolio_risk` |
| Bayesian edge confirmado | `utils/quant.py::bayesian_edge` vía `bayesian_tracker.py` | Telegram/Discord pulse, `/api/decisions` |
| Decision Stream (auditoría completa) | `utils/decisions.py` (append-only JSONL) | `/api/decisions`, squantdesk.com/decisiones |

## Pendientes conocidos (no bloqueantes)
- El stress test (§6) no tiene visualización propia en el dashboard web — hoy solo vía API cruda y Telegram. Si se quiere una tarjeta dedicada, requiere diseñar el componente desde cero (no hay un patrón de "tarjeta de riesgo" existente en `(dashboard)/portafolio/page.tsx` para replicar, a diferencia de lo asumido inicialmente).
- `utils/quant.py::decay_signal` está implementado pero no se usa en producción — `engine/live/decay_monitor.py` tiene su propia lógica separada (pandas/ccxt) para lo mismo. No es un bug, es redundancia menor.

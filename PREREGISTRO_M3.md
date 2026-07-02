# PRE-REGISTRO — Experimento Motor 3 (primer LONG live del sistema)

Escrito: 2026-07-02, ANTES de la primera señal live de M3. El commit de git de este
archivo es el timestamp que prueba que las expectativas se fijaron antes de ver resultados.

## Por qué se pre-registra

La primera señal M3 junta TRES novedades simultáneas: primera dirección LONG live del
sistema (todo el track record es short), primera clase de activo nueva (stocks vía
perpetuos de Binance), y régimen sin validación live para stocks. La trampa clásica es
interpretar después: si gana, "el sistema generaliza"; si pierde, "era esperable, n=1".
Este documento fija el criterio ANTES.

## Expectativas (OOS de los champions armados, 2026-07-02)

| Slot | Estrategia | WR OOS | PF OOS | CAGR | DD | n OOS |
|---|---|---|---|---|---|---|
| TSLA/1h | trend_strength | 50.0% | 1.64 | 37.8% | -17.0% | 20 |
| JPM/1h | tma_bands | 54.5% | 1.46 | 27.7% | -15.0% | 22 |
| NVDA/1h | obv_divergence | 65.5% | 1.25 | 22.3% | -24.4% | 29 |
| AAPL/1d | tema_cross | 71.8% | 1.33 | 7.2% | -26.3% | 110 |
| NVDA/1d | dmi_trend | 72.5% | 1.36 | 13.1% | -18.9% | 109 |
| AAPL/4h | lower_lows | 61.1% | 1.29 | 12.5% | -17.2% | 18 |

Expectativa agregada honesta: WR ~60±8%, PF ~1.3-1.5. Con decay live esperado (visto en
M1: backtest 66% → live 59%), un WR live de ~52-58% NO es señal de falla.

## Criterios pre-comprometidos

- **n < 10 trades M3 cerrados:** no se concluye NADA, ni a favor ni en contra. Solo se
  actúa por catástrofe: PF < 0.5 o pérdida acumulada del bucket M3 > 15% de su capital
  asignado → M3 vuelve a paper (los slots crypto/commodities no se tocan).
- **Checkpoint n = 25:** PF ≥ 1.2 → M3 validado provisionalmente, sigue normal.
  PF entre 0.8 y 1.2 → sigue pero sin subir Kelly ni slots. PF < 0.8 → M3 a paper,
  post-mortem escrito.
- **Prohibido:** ajustar parámetros, cambiar champions M3 o "darle una ayudita" al motor
  entre el trade 1 y el 25, salvo bug demostrado en código (no en resultados).
- **Prohibido:** citar públicamente los primeros trades M3 como prueba de que "el sistema
  generaliza a stocks" antes del checkpoint n=25, ni ocultarlos si van mal
  (feedback_dont_hide_real_data).

## Riesgos conocidos que el backtest NO captura

1. Perpetuos de stocks en Binance son un producto nuevo: liquidez, spread y funding sin
   historial largo. El slippage real puede diferir del asumido (1bp/lado) — el ledger de
   calidad de ejecución (execution_quality.jsonl) lo medirá desde el trade 1.
2. Los datos de entrenamiento son del subyacente (yfinance), no del perpetuo — puede
   haber divergencia de precio subyacente vs perp (basis) que las señales no conocen.
3. Horario: stocks tienen sesiones; los perpetuos cotizan 24/7. El comportamiento
   fuera de horario de mercado del subyacente es terra incognita para los modelos.

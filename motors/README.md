# SIGMA Multi-Motor Architecture

## Vision

5 motores cuant independientes operando distintas clases de activos, con un
meta-allocator que rota capital entre ellos basándose en performance.

```
                          META-ALLOCATOR
                                |
        +-----------+-----------+-----------+-----------+
        |           |           |           |           |
   CRYPTO        STOCKS-US  COMMODITIES   LATAM      FOREX
   (active)     (skeleton)  (skeleton)  (skeleton)  (skeleton)
```

## Estados de un motor

| Estado     | Significado                                              |
|------------|----------------------------------------------------------|
| SKELETON   | Estructura creada, sin datos ni backtest                |
| RESEARCH   | Datos cargados, backtest corriendo, validation activa   |
| PAPER      | Paper trading activo, no entra todavía al allocator     |
| ACTIVE     | Operando capital real, entra a la rotación              |
| DEPRECATED | Disabled por bad performance o cambio de regime         |

## Estructura de carpetas

```
/opt/sigma/
├── motors/
│   ├── crypto/         (actual SIGMA - vive en root por ahora)
│   ├── stocks_us/      (SKELETON)
│   ├── commodities/    (SKELETON)
│   ├── latam/          (SKELETON)
│   └── forex/          (SKELETON, last priority)
├── meta_allocator/
│   ├── config/allocator.json
│   ├── allocator.py    (rota capital basado en perf)
│   └── state/current_allocation.json
└── sigma_core/         (codigo compartido: strategies, backtest, validation, score)
```

## Roadmap

1. **Mes 1-3**: Validar SIGMA Crypto live, mientras se construye core compartido
2. **Mes 4-6**: SIGMA Stocks US (5 ETFs) → SKELETON → PAPER
3. **Mes 7-9**: SIGMA Commodities → SKELETON → PAPER
4. **Mes 10-12**: SIGMA LATAM ETFs → SKELETON → PAPER
5. **Mes 13-15**: SIGMA Forex → SKELETON → PAPER
6. **Mes 16+**: Meta-allocator opera con 2+ motores ACTIVE

## CPU planning

Cada motor a full demanda ~load 8-10. Para 5 motores:
- CPU upgrade necesario (planificado para 2026-05-25, ahora ya pasó - revisar)
- O cada motor liviano (40 strats x 4 TFs en lugar de 121 x 6)
- O motores en servidores separados

## Cómo agregar un motor nuevo

1. `mkdir /opt/sigma/motors/<nombre>/{config,data,results,logs}`
2. Crear `config/motor.json` con universo, asset_class, status: SKELETON
3. Crear `universe.py` con la lista de tickers
4. Crear `data_fetcher.py` (puede copiar el stub)
5. Cuando hayas validado, status: PAPER → ACTIVE

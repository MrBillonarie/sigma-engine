# SIGMA Multi-Motor Architecture

## Vision

Motores cuant independientes operando distintas clases de activos, con un
meta-allocator que rota capital entre ellos basandose en performance.
Organizados por BROKER/venue de ejecucion (no por "quien lo investiga"),
porque el costo real de un motor nuevo es la integracion de ejecucion, no
el universo de tickers.

```
                          META-ALLOCATOR
                                |
        +-----------+-----------------------------------+
        |           |                                   |
     CRYPTO    COMMODITIES                    IBKR (1 cuenta, 1 gateway)
   (Binance)   (Binance perps)         +-----------+-----------+
    ACTIVE        ACTIVE               |           |           |
                                   stocks_us   futures_idx    forex
                                  (equities+ETF  (CME micro   (IDEALPRO,
                                   +bonos ETF)    + bonos)     ultimo)
                                   SKELETON      SKELETON     SKELETON
```

PUPrime (CFD/forex broker) quedo descartado: es exposicion sintetica
contra el book del broker, no el activo real -- choca con la filosofia
de self-custody del proyecto. Todo lo que no es Binance vive en una sola
cuenta IBKR.

## Regla: solo ETFs, nunca acciones individuales

NVDA/AAPL y cualquier accion individual quedan fuera a proposito.
Idiosyncratic risk de una sola empresa no encaja con el modelo
sistematico/cross-asset de SIGMA. Si en el futuro se quiere exposicion a
"tech" o a una accion puntual, usar el ETF del sector, no el ticker.

## Estados de un motor

| Estado     | Significado                                              |
|------------|----------------------------------------------------------|
| SKELETON   | Estructura creada, sin datos ni backtest                |
| RESEARCH   | Datos cargados, backtest corriendo, validation activa   |
| PAPER      | Paper trading activo, no entra todavia al allocator     |
| ACTIVE     | Operando capital real, entra a la rotacion               |
| DEPRECATED | Disabled por bad performance o cambio de regime          |

## Estructura de carpetas

```
/opt/sigma/
├── motors/
│   ├── _shared/
│   │   └── ibkr_gateway.py   (auth/sesion compartida por los 3 sub-universos IBKR)
│   ├── stocks_us/   (SKELETON -- equities+ETF+bonos-ETF, broker ibkr)
│   ├── futures_idx/ (SKELETON -- CME micro futures indices+bonos, broker ibkr)
│   ├── forex/       (SKELETON -- IDEALPRO, broker ibkr, ULTIMO en activarse)
│   ├── commodities/ (SKELETON huerfano -- NO confundir con M2 real, que vive en
│   │                  /opt/sigma/engine/commodities/pipeline.py vía sigma-commodities.service.
│   │                  Pendiente: limpiar o repurposear esta carpeta.)
│   └── latam/       (SKELETON -- fuera de scope de la ronda actual)
├── meta_allocator/
│   ├── config/allocator.json
│   ├── allocator.py    (rota capital basado en perf)
│   └── state/current_allocation.json
└── sigma_core/         (codigo compartido: strategies, backtest, validation, score)
```

## Gate de autenticacion IBKR (lo mas critico antes de tocar capital real)

IBKR retail no tiene API key estatica como Binance: exige 2FA por celular
para autenticar y la sesion expira en <24h. OAuth (que evitaria esto) es
solo institucional. Antes de que CUALQUIER sub-universo IBKR pase de
SKELETON a PAPER, validar que `_shared/ibkr_gateway.py` (via IBeam/ibind)
aguanta semanas sin intervencion manual en la cuenta paper de IBKR. Detalle
completo en el docstring de ese archivo.

## Roadmap dentro del motor IBKR

1. Construir `_shared/ibkr_gateway.py` real (ib_async + IBeam/ibind) y
   validarlo en cuenta paper IBKR durante semanas, sin tocar estrategia
2. `stocks_us` (equities+ETF+bonos): SKELETON -> RESEARCH -> PAPER -> ACTIVE
3. `futures_idx` (CME micro, fase 1 US-only): idem
4. `forex` (IDEALPRO): idem, siempre el ultimo
5. Fase 2 (cuando haya mas capital y margen multi-moneda): futuros
   internacionales (DAX/FTSE/Nikkei/IBOVESPA) dentro de futures_idx

## Como agregar un sub-universo nuevo dentro de un broker existente

1. `mkdir /opt/sigma/motors/<nombre>/{config,data,results,logs}`
2. Crear `config/motor.json` con universo, asset_class, broker, status: SKELETON
3. Si el broker ya tiene `_shared/<broker>_gateway.py`, apuntar `auth_shared_module` ahi -- no duplicar auth
4. Crear `universe.py` con la lista de tickers
5. Crear `data_fetcher.py` (puede copiar el stub)
6. Cuando hayas validado (incluyendo el gate de auth si aplica), status: PAPER -> ACTIVE

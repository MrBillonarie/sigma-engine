#!/usr/bin/env python3
"""
SIGMA ENGINE — Live Executor v1.1
Abstraccion paper/live. Misma interfaz, diferente backend.

LIVE_MODE = False  (default) -> paper trading
LIVE_MODE = True              -> Binance Futures real

Para activar live trading:
1. Agregar API keys en /opt/sigma/engine/config/secrets.json:
   {"BINANCE_API_KEY": "...", "BINANCE_API_SECRET": "..."}
2. Verificar Gate >= 85 en el dashboard
3. Cambiar LIVE_MODE = True
4. Dejar DRY_RUN = True para tests sin dinero real
5. Reiniciar sigma-web

Fixes v1.1 (2026-06-17):
- FIX B1: Emergency close si entry OK pero SL falla
- FIX M2: amount_to_precision() para respetar stepSize de Binance
- FIX M3: Safety checks fail-safe (except->return False, no pass)
- FIX M4: newClientOrderId para idempotencia (evita doble ejecucion)

Fixes v1.2 (2026-06-21):
- FIX M5: piso de MIN_NOTIONAL_USD -- evita rechazo -4164 de Binance cuando
  equity*kelly cae bajo el minimo de orden (visto en SOL/15m con equity bajo)
"""
import json, os, time
from pathlib import Path
from datetime import datetime, timezone, timedelta

# -- CONFIG -------------------------------------------------------------------
LIVE_MODE = True   # <- False = paper | True = Binance live
DRY_RUN   = False    # <- True = loguea ordenes pero no ejecuta (solo con LIVE_MODE=True)

# Limites de seguridad
MAX_KELLY_PCT       = 6.0    # maximo 6% del capital por trade (sizing normal)
MAX_KELLY_HARD_CAP  = 15.0   # techo ABSOLUTO incluso forzando el minimo de notional del
                              # exchange -- nunca arriesgar mas que esto en un solo trade,
                              # pase lo que pase con el minimo que exija Binance
MAX_LEVERAGE        = 5      # maximo 5x
MAX_OPEN_SLOTS      = 3      # maximo 3 posiciones simultaneas
MIN_GATE_SCORE      = 85     # gate minimo para operar live
MIN_NOTIONAL_USD    = 5.5    # piso de Binance es 5.0 USDT -- margen por slippage de fetch_ticker a fill

CHILE        = timezone(timedelta(hours=-4))
BASE         = Path('/opt/sigma')
SECRETS_PATH = BASE / 'engine' / 'config' / 'secrets.json'
LOG_PATH     = BASE / 'results' / 'reports' / 'executor.log'

_exchange = None

# -- Logging ------------------------------------------------------------------
def _log(msg):
    cl   = datetime.now(CHILE)
    line = f"[{cl:%H:%M:%S}] [EXECUTOR] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except:
        pass

# -- Exchange (Binance Futures) ------------------------------------------------
def _get_exchange():
    global _exchange
    if _exchange:
        return _exchange
    try:
        import ccxt
        secrets = {}
        if SECRETS_PATH.exists():
            secrets = json.loads(SECRETS_PATH.read_text())
        _exchange = ccxt.binance({
            'apiKey':  secrets.get('BINANCE_API_KEY', ''),
            'secret':  secrets.get('BINANCE_API_SECRET', ''),
            'options': {'defaultType': 'future'},
            'timeout': 15000,
        })
        if DRY_RUN:
            _exchange.set_sandbox_mode(True)
        # Pre-carga markets para amount_to_precision()
        _exchange.load_markets()
        return _exchange
    except Exception as e:
        _log(f"Error init exchange: {e}")
        return None

def _get_equity():
    """Retorna equity disponible en USDT."""
    try:
        ex  = _get_exchange()
        bal = ex.fetch_balance()
        return float(bal.get('USDT', {}).get('free', 0))
    except Exception as e:
        _log(f"Error fetch balance: {e}")
        return 0

# Algunos commodities cotizan en Binance Futures con un ticker distinto al
# usado internamente en SIGMA (HG/NG/WTI/PL son los tickers COMEX/NYMEX o
# nombre largo; Binance usa COPPER/NATGAS/CL/XPT). XAU/XAG si calzan 1:1.
# Confirmado 2026-06-23: los 6 commodities de SIGMA ya tienen perpetuo USDT
# real en Binance (CL=WTI lanzado 2026-04-01 con 100x leverage).
_SYM_TICKER_MAP = {'HG': 'COPPER', 'NG': 'NATGAS', 'WTI': 'CL', 'PL': 'XPT'}

def _binance_symbol(sym):
    sym = _SYM_TICKER_MAP.get(sym, sym)
    return sym.replace('USDT', '') + '/USDT:USDT'

# -- Seguridad pre-trade ------------------------------------------------------
def _safety_checks(kelly_pct: float) -> tuple:
    """Verifica condiciones antes de ejecutar en live. Retorna (ok, reason).
    FIX M3: fail-safe — si el web server no responde, BLOQUEAMOS (no pass).
    """
    # Gate score
    try:
        import urllib.request
        r  = urllib.request.urlopen('http://127.0.0.1:8080/api/trades', timeout=5)
        td = json.loads(r.read())
        score = td.get('live_readiness', {}).get('score', 0)
        if score < MIN_GATE_SCORE:
            return False, f"Gate {score}/100 < minimo {MIN_GATE_SCORE}"
    except Exception as e:
        # FIX M3: fail-safe — si no podemos verificar, bloqueamos
        return False, f"Safety check unavailable (gate): {e}"

    # Circuit breaker
    try:
        r  = urllib.request.urlopen('http://127.0.0.1:8080/api/signals', timeout=5)
        sd = json.loads(r.read())
        if sd.get('circuit_breaker'):
            return False, "Circuit breaker activo"
    except Exception as e:
        # FIX M3: fail-safe
        return False, f"Safety check unavailable (CB): {e}"

    # Kelly cap
    if kelly_pct > MAX_KELLY_PCT:
        return False, f"Kelly {kelly_pct}% > maximo {MAX_KELLY_PCT}%"

    # Pausa manual
    if (BASE / 'results' / 'pausa.flag').exists():
        return False, "Sistema en pausa manual"

    return True, "OK"

# -- Emergency close ----------------------------------------------------------
def _emergency_close(ex, symbol, contracts, side, reason):
    """Cierra posicion de emergencia. Usar cuando entry OK pero SL falla."""
    _log(f"EMERGENCY CLOSE {symbol} {contracts} {side} — {reason}")
    try:
        close_side = 'sell' if side == 'buy' else 'buy'
        ex.create_market_order(symbol, close_side, contracts,
                               params={'reduceOnly': True})
        _log("EMERGENCY CLOSE OK")
        try:
            mkt_id = ex.market(symbol)['id']
            ex.fapiPrivateDeleteAlgoOpenOrders({'symbol': mkt_id})
        except Exception as _ce:
            _log(f"WARNING cancel algo orders post-emergency-close failed: {_ce}")
        # Alerta Telegram (usa el notifier real, no claves de secrets.json que no existen)
        try:
            import sys as _tsys
            if "/opt/sigma/engine/live" not in _tsys.path:
                _tsys.path.insert(0, "/opt/sigma/engine/live")
            import telegram_notifier as _tn
            _tn.send(f"🚨 <b>ALERTA CRITICA</b>\nEmergency close ejecutado en {symbol}.\nRazon: {reason}")
        except Exception as te:
            _log(f"Telegram alert failed: {te}")
    except Exception as e:
        _log(f"EMERGENCY CLOSE FAILED: {e} — POSICION ABIERTA SIN SL!")

# -- Reconcile ------------------------------------------------------------------
def reconcile():
    """Verifica que cada posicion LIVE abierta tenga su Stop Loss activo en Binance.
    Se corre al iniciar el proceso (web_server import) para detectar el caso en que
    un kill/crash interrumpio execute_entry() entre el fill de entrada y la
    colocacion del SL. Fail-safe: si no se puede verificar, solo alerta -- nunca
    cierra una posicion sin confirmar primero que de verdad le falta el SL.
    """
    if not LIVE_MODE:
        return
    try:
        # Lee trade_state.json directamente -- NUNCA "from web_server import ...",
        # eso ejecuta todo web_server.py de nuevo (sin guard __main__) y arranca
        # un segundo servidor + segundo set de hilos de trading duplicado.
        trade_state_path = BASE / 'results' / 'trade_state.json'
        if not trade_state_path.exists():
            _log("RECONCILE: trade_state.json no existe aun, nada que verificar")
            return
        state = json.loads(trade_state_path.read_text())
    except Exception as e:
        _log(f"RECONCILE: no se pudo leer trade_state: {e}")
        return

    live_open = [
        (k, tr) for k, tr in state.get('open', {}).items()
        if tr.get('status') == 'open' and tr.get('mode') == 'LIVE'
    ]
    if not live_open:
        _log("RECONCILE: sin posiciones LIVE abiertas, nada que verificar")
        return

    ex = _get_exchange()
    if not ex:
        _log("RECONCILE: no se pudo conectar a Binance, abortando check (fail-safe: solo alerta)")
        return

    try:
        positions = ex.fetch_positions()
    except Exception as e:
        _log(f"RECONCILE: error fetch_positions: {e}")
        return

    for key, tr in live_open:
        sym = tr.get('sym', ''); tf = tr.get('tf', '')
        symbol = _binance_symbol(sym)
        try:
            bnc_pos = next(
                (p for p in positions
                 if p.get('symbol') == symbol and abs(float(p.get('contracts', 0) or 0)) > 0),
                None
            )
            if not bnc_pos:
                _log(f"RECONCILE: {sym}/{tf} marcado LIVE abierto pero sin posicion en Binance "
                     f"-- pudo cerrarse mientras el proceso estaba caido. Revisar manualmente.")
                continue

            open_orders = ex.fetch_open_orders(symbol)
            has_sl = any('stop' in str(o.get('info', {}).get('type', o.get('type', ''))).lower()
                         for o in open_orders)
            if not has_sl:
                # 2026-06-17: Binance trata STOP_MARKET/TAKE_PROFIT_MARKET reduceOnly como
                # "algo orders" (endpoint separado) -- fetch_open_orders normal nunca las ve,
                # causaba falso-positivo "sin SL" + emergency close indebido sobre posiciones
                # que SI estaban protegidas. Confirmado con trade real (LTC/4h, 2026-06-17).
                try:
                    mkt_id = ex.market(symbol)['id']
                    algo_orders = ex.fapiPrivateGetOpenAlgoOrders({'symbol': mkt_id})
                    has_sl = any('STOP' in str(o.get('orderType', '')).upper() for o in algo_orders)
                except Exception as e:
                    _log(f"RECONCILE: error fetch_open_algo_orders {sym}/{tf}: {e}")

            if has_sl:
                _log(f"RECONCILE OK: {sym}/{tf} tiene SL activo")
                continue

            # Posicion confirmada SIN SL -- emergency close inmediato
            _log(f"RECONCILE ALERT: {sym}/{tf} posicion SIN STOP LOSS -- cerrando de emergencia")
            pos_side  = bnc_pos.get('side', 'long')
            entry_side = 'buy' if pos_side == 'long' else 'sell'
            contracts  = abs(float(bnc_pos.get('contracts', 0) or 0))
            _emergency_close(ex, symbol, contracts, entry_side,
                             "RECONCILE: SL faltante al reiniciar (posible kill durante execute_entry)")
        except Exception as e:
            _log(f"RECONCILE: error verificando {sym}/{tf}: {e}")

# -- Bookkeeping (via API local, NUNCA import web_server) ---------------------
def _record_trade(endpoint, payload):
    """Registra open/close en trade_state.json via la API HTTP local.
    CRITICO: jamas 'from web_server import open_trade/close_trade' -- el archivo
    no tiene guard __main__, importarlo duplica TODO el motor (server + hilos
    de trading) en un proceso paralelo. La API HTTP corre dentro del proceso
    real, sin ese riesgo. Localhost sin proxy headers pasa el auth gate."""
    import urllib.request, json as _json
    data = _json.dumps(payload).encode()
    req = urllib.request.Request(f'http://127.0.0.1:8080{endpoint}', data=data,
                                 headers={'Content-Type': 'application/json'}, method='POST')
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return _json.loads(r.read()).get('trade')
        except Exception as e:
            _log(f"_record_trade {endpoint} intento {attempt} fallo: {e}")
            time.sleep(1)
    _log(f"_record_trade {endpoint} FALLO 2/2 -- payload={payload} -- registrar manualmente si la posicion es real")
    return None


def _api_open_trade(sym, tf, direction, price, sl, tp, strategy, paper, grade, wr, cagr, kelly_pct, contracts=None):
    payload = {
        'sym': sym, 'tf': tf, 'direction': direction,
        'entry': price, 'sl': sl, 'tp': tp, 'strategy': strategy,
        'paper': paper, 'grade': grade, 'wr': wr, 'cagr': cagr, 'kelly_pct': kelly_pct,
    }
    if contracts is not None:
        payload['contracts'] = contracts
    return _record_trade('/api/trades/open', payload)


def _api_close_trade(sym, tf, exit_price, reason, contracts=None, real_equity=None):
    payload = {'sym': sym, 'tf': tf, 'exit_price': exit_price, 'reason': reason}
    if contracts is not None:
        payload['contracts'] = contracts
    if real_equity is not None:
        payload['real_equity'] = real_equity
    return _record_trade('/api/trades/close', payload)


# -- Entry --------------------------------------------------------------------
def execute_entry(sym, tf, direction, price, sl, tp,
                  strategy='', grade='B', wr=50.0, cagr=0.0,
                  kelly_pct=3.3, paper=True, ai_reason='', **kwargs):
    """
    Abre una posicion. Interfaz unica para paper y live.
    """
    mode = "PAPER" if (not LIVE_MODE or paper) else ("DRY_RUN" if DRY_RUN else "LIVE")
    _log(f"[{mode}] ENTRY {direction.upper()} {sym} {tf} @ {price:.4f} "
         f"SL:{sl:.4f} TP:{tp:.4f} Kelly:{kelly_pct}% Grade:{grade} {ai_reason}")

    # -- Paper mode -----------------------------------------------------------
    if not LIVE_MODE or paper:
        return _api_open_trade(sym, tf, direction, price, sl, tp, strategy,
                               True, grade, wr, cagr, kelly_pct)

    # -- Live mode ------------------------------------------------------------
    ok, reason = _safety_checks(kelly_pct)
    if not ok:
        _log(f"BLOCKED — {reason}")
        return False

    ex = _get_exchange()
    if not ex:
        return False

    symbol = _binance_symbol(sym)
    equity = _get_equity()
    if equity <= 0:
        _log("Balance USDT = 0"); return False

    # Calcular tamano con amount_to_precision (FIX M2: respeta stepSize de Binance)
    try:
        size_usd  = equity * min(kelly_pct / 100, MAX_KELLY_PCT / 100)
        cur_price = ex.fetch_ticker(symbol)['last']
        # FIX M5: piso de notional -- evita el rechazo -4164 de Binance cuando
        # equity*kelly cae bajo el minimo de la orden. El minimo NO es igual para
        # todos los simbolos (BTC=$50, ETH/LTC=$20, SOL/BNB=$5) -- se lee del
        # propio mercado en vez de asumir un valor fijo, con 5% de margen propio
        # encima del piso real (slippage de precio + perdida por redondeo de step).
        market       = ex.market(symbol)
        step         = market.get('precision', {}).get('amount') or 0.0
        min_amount   = market.get('limits', {}).get('amount', {}).get('min') or 0.0
        min_notional = market.get('limits', {}).get('cost', {}).get('min') or MIN_NOTIONAL_USD
        min_notional = max(min_notional, MIN_NOTIONAL_USD)
        target_usd    = max(size_usd, min_notional * 1.05)
        max_size_usd  = equity * MAX_KELLY_PCT / 100
        hard_cap_usd  = equity * MAX_KELLY_HARD_CAP / 100
        if target_usd > max_size_usd:
            # FIX M6: el minimo de notional manda sobre el tope normal de Kelly --
            # se usa el minimo INDISPENSABLE para que el exchange acepte la orden,
            # nunca mas que eso, y nunca por encima del techo absoluto de seguridad.
            if target_usd > hard_cap_usd:
                _log(f"Equity insuficiente (${equity:.2f}) -- ni forzando el Kelly al "
                     f"techo absoluto ({MAX_KELLY_HARD_CAP}%) se alcanza el minimo de "
                     f"Binance para {symbol} (${min_notional}). Trade no ejecutable en LIVE.")
                return False
            _log(f"Kelly {kelly_pct}% -> size ${size_usd:.2f} bajo minimo Binance para "
                 f"{symbol} (${min_notional}) -- FORZANDO tamano minimo ${target_usd:.2f} "
                 f"(kelly efectivo {target_usd / equity * 100:.2f}%, excede el tope normal "
                 f"{MAX_KELLY_PCT}% pero es lo minimo para que Binance acepte la orden)")
        elif target_usd > size_usd:
            _log(f"Kelly {kelly_pct}% -> size ${size_usd:.2f} bajo minimo Binance "
                 f"para {symbol} (${min_notional}) -- elevando a ${target_usd:.2f} "
                 f"(kelly efectivo {target_usd / equity * 100:.2f}%)")
        raw_qty = target_usd / cur_price
        if step > 0:
            # redondeo ARRIBA al step -- amount_to_precision trunca hacia abajo y
            # puede dejar el notional final por debajo del minimo real
            import math as _math
            raw_qty = _math.ceil(raw_qty / step) * step
        raw_qty   = max(raw_qty, min_amount)
        contracts = float(ex.amount_to_precision(symbol, raw_qty))
        if contracts <= 0:
            _log("Tamano de contrato = 0"); return False
        if contracts * cur_price < min_notional:
            contracts = float(ex.amount_to_precision(symbol, raw_qty + step))
    except Exception as e:
        _log(f"Error calculando size: {e}"); return False

    side    = 'buy' if direction == 'long' else 'sell'
    # FIX M4: newClientOrderId para idempotencia
    trade_ts  = int(time.time())
    entry_cid = f"sigma_{sym}_{tf}_{trade_ts}_entry"
    sl_cid    = f"sigma_{sym}_{tf}_{trade_ts}_sl"
    tp_cid    = f"sigma_{sym}_{tf}_{trade_ts}_tp"

    if DRY_RUN:
        _log(f"DRY_RUN — {side.upper()} {contracts} {symbol} "
             f"equity=${equity:.0f} size=${size_usd:.0f}")
        return True

    # Leverage
    try:
        ex.set_leverage(MAX_LEVERAGE, symbol)
    except Exception as e:
        _log(f"Leverage warning: {e}")  # no fatal, puede ya estar seteado

    # FIX B1: Separar entry de SL/TP — emergency close si SL falla post-fill
    entry_filled = False
    fill = cur_price
    oid  = '?'

    try:
        order = ex.create_market_order(symbol, side, contracts,
                                       params={'newClientOrderId': entry_cid})
        fill  = order.get('average') or cur_price
        oid   = order.get('id', '?')
        _log(f"ENTRY OK id={oid} fill={fill:.4f} contracts={contracts}")
        entry_filled = True
    except Exception as e:
        _log(f"ERROR entry order: {e}")
        return False

    # SL — si falla, emergency close
    sl_ok = False
    try:
        sl_side = 'sell' if direction == 'long' else 'buy'
        ex.create_order(symbol, 'stop_market', sl_side, contracts,
                        params={'stopPrice': round(sl, 4), 'reduceOnly': True,
                                'newClientOrderId': sl_cid})
        sl_ok = True
        _log(f"SL OK @ {sl:.4f}")
    except Exception as e:
        _log(f"ERROR SL placement: {e}")
        _emergency_close(ex, symbol, contracts, side, f"SL placement failed: {e}")
        return False

    # TP — si falla, solo loguear (SL ya esta activo, posicion protegida)
    try:
        tp_side = 'sell' if direction == 'long' else 'buy'
        ex.create_order(symbol, 'take_profit_market', tp_side, contracts,
                        params={'stopPrice': round(tp, 4), 'reduceOnly': True,
                                'newClientOrderId': tp_cid})
        _log(f"TP OK @ {tp:.4f}")
    except Exception as e:
        _log(f"WARNING TP placement failed: {e} — SL activo, posicion protegida")

    # Registrar la posicion real en trade_state.json para tracking/reconcile.
    # contracts real (no el kelly/equity simulado) -- el dashboard lo usa para
    # mostrar notional/margen reales en vez del notional fantasma del paper.
    _rec = _api_open_trade(sym, tf, direction, fill, sl, tp, strategy,
                           False, grade, wr, cagr, kelly_pct, contracts=contracts)
    if _rec is None:
        _log(f"ALERTA: posicion LIVE {sym}/{tf} abierta en Binance pero el registro en "
             f"trade_state.json FALLO -- revisar manualmente, reconcile() no la vera")
    return True

# -- Exit ---------------------------------------------------------------------
def update_live_sl(sym, tf, new_sl, direction):
    """Reemplaza la orden STOP_MARKET real en Binance por una nueva en new_sl.
    Usado por smart_exit.check_trailing() cuando mueve el SL a break-even --
    antes ese movimiento solo se guardaba en trade_state.json, nunca en la
    orden real, asi que la posicion quedaba protegida por el SL viejo (mas
    lejano) mientras el bot creia que estaba protegida por el nuevo (mas
    cerca) -- causa raiz del cierre fantasma de BTC 2026-06-23.

    Solo toca la orden STOP_MARKET (deja el TAKE_PROFIT_MARKET intacto).
    Si no encuentra una STOP_MARKET real para el symbol (trade es PAPER, o
    ya se cerro), no hace nada y retorna False -- check_auto_close/
    _execute_close ya cubren ese caso por su lado."""
    ex = _get_exchange()
    if not ex:
        return False
    symbol = _binance_symbol(sym)

    try:
        mkt_id = ex.market(symbol)['id']
    except Exception as e:
        _log(f"ERROR update_live_sl market lookup {symbol}: {e}")
        return False

    try:
        orders = ex.fapiPrivateGetOpenAlgoOrders()
    except Exception as e:
        _log(f"WARNING update_live_sl: no se pudo listar ordenes {symbol}: {e}")
        return False

    sl_orders = [o for o in orders if o.get('symbol') == mkt_id and o.get('orderType') == 'STOP_MARKET']
    if not sl_orders:
        return False

    positions = ex.fetch_positions([symbol])
    pos = next((p for p in positions if float(p.get('contracts', 0) or 0) > 0), None)
    if not pos:
        return False
    contracts  = float(pos['contracts'])
    close_side = 'sell' if direction == 'long' else 'buy'

    for o in sl_orders:
        try:
            ex.fapiPrivateDeleteAlgoOrder({'algoId': o['algoId']})
        except Exception as e:
            _log(f"WARNING update_live_sl: cancel fallo algoId={o.get('algoId')}: {e}")

    try:
        ts = int(time.time())
        ex.create_order(symbol, 'stop_market', close_side, contracts,
                        params={'stopPrice': round(new_sl, 4), 'reduceOnly': True,
                                'newClientOrderId': f"sigma_{sym}_{tf}_{ts}_sl_be"})
        _log(f"SL movido a break-even @ {new_sl:.4f} ({symbol})")
        return True
    except Exception as e:
        _log(f"ERROR update_live_sl placement {symbol}: {e}")
        return False


def close_live_position(sym, tf, reason='MANUAL'):
    """Cierra de verdad la posicion en Binance (cancela ordenes resting, market-close,
    lee balance real). Retorna {'exit_price','contracts','real_equity'} o None si no
    hay posicion real (ya cerro por su propia orden SL/TP resting, o nunca abrio).

    A proposito NO hace el bookkeeping (no llama _api_close_trade / HTTP) -- pensado
    para llamarse IN-PROCESS desde web_server.py mientras _TRADE_LOCK puede estar
    tomado por el caller (check_auto_close). Un loopback HTTP ahi adentro causaria
    deadlock (el request handler necesitaria el mismo RLock que el thread ya tiene).
    El caller hace el bookkeeping llamando close_trade() directo con estos datos.

    Bug que motivo esto (2026-06-23): execute_exit() existia pero nunca se llamaba
    desde ningun lado -- los cierres LIVE eran puro bookkeeping local, sin verificar
    contra Binance. Una posicion BTC quedo registrada como cerrada por SL_HIT sin que
    Binance ejecutara ningun cierre real -- quedo abierta y duplicada por semanas."""
    ex = _get_exchange()
    if not ex:
        return None
    symbol = _binance_symbol(sym)

    # Cancelar SL/TP pendientes -- cancel_all_orders normal NO cubre algo/conditional
    # orders (STOP_MARKET/TAKE_PROFIT_MARKET con reduceOnly viven en otro endpoint desde
    # 2026; confirmado con trade real 2026-06-17). Sin esto quedan huerfanas y podrian
    # interactuar con la siguiente posicion que abra en el mismo symbol.
    try:
        ex.cancel_all_orders(symbol)
    except Exception:
        pass
    try:
        mkt_id = ex.market(symbol)['id']
        ex.fapiPrivateDeleteAlgoOpenOrders({'symbol': mkt_id})
    except Exception as e:
        _log(f"WARNING cancel algo orders failed {symbol}: {e}")

    positions = ex.fetch_positions([symbol])
    pos = next((p for p in positions if float(p.get('contracts', 0)) > 0), None)
    if not pos:
        _log(f"No hay posicion abierta en {symbol}")
        return None

    contracts = float(pos['contracts'])
    side      = 'sell' if pos['side'] == 'long' else 'buy'

    if DRY_RUN:
        _log(f"DRY_RUN — CLOSE {side.upper()} {contracts} {symbol}")
        return {'exit_price': float(pos.get('markPrice') or 0), 'contracts': contracts, 'real_equity': None}

    close_ts = int(time.time())
    order      = ex.create_market_order(symbol, side, contracts,
                                         params={'reduceOnly': True,
                                                 'newClientOrderId': f"sigma_{sym}_{tf}_{close_ts}_close"})
    exit_price = order.get('average', 0)
    _log(f"CLOSED @ {exit_price:.4f}")

    # Balance real post-cierre -- usado para registrar pnl/equity reales,
    # no la formula de paper trading (que asume equity simulada de $10k).
    real_equity = None
    try:
        bal = ex.fetch_balance()
        real_equity = float(bal.get('USDT', {}).get('total', 0) or 0)
    except Exception as e:
        _log(f"WARNING: no se pudo leer balance real post-close: {e}")

    return {'exit_price': exit_price, 'contracts': contracts, 'real_equity': real_equity}


def execute_exit(sym, tf, reason='MANUAL'):
    """
    Cierra una posicion. Interfaz unica para paper y live. Hace tambien el
    bookkeeping via API HTTP (_api_close_trade) -- pensado para llamarse desde
    FUERA del proceso web_server.py (scripts externos, comandos manuales).

    Si se llama desde DENTRO de web_server.py mientras _TRADE_LOCK puede estar
    tomado (p.ej. check_auto_close), usar close_live_position() en su lugar
    para evitar deadlock por el loopback HTTP -- ver docstring ahi.
    """
    _log(f"[{'PAPER' if not LIVE_MODE else 'LIVE'}] EXIT {sym} {tf} [{reason}]")

    # Paper
    if not LIVE_MODE:
        try:
            import ccxt
            ex  = ccxt.binance({'options': {'defaultType': 'future'}, 'timeout': 10000})
            prc = ex.fetch_ticker(_binance_symbol(sym))['last']
        except:
            prc = 0
        return _api_close_trade(sym, tf, prc, reason)

    # Live
    try:
        _r = close_live_position(sym, tf, reason)
        if not _r:
            return False

        _rec = _api_close_trade(sym, tf, _r['exit_price'], reason,
                                contracts=_r['contracts'], real_equity=_r['real_equity'])
        if _rec is None:
            _log(f"ALERTA: posicion LIVE {sym}/{tf} cerrada en Binance pero el registro en "
                 f"trade_state.json FALLO -- revisar manualmente")
        return True

    except Exception as e:
        _log(f"ERROR live exit: {e}")
        return False

# -- Status -------------------------------------------------------------------
def status():
    return {
        'live_mode':   LIVE_MODE,
        'dry_run':     DRY_RUN,
        'max_kelly':   MAX_KELLY_PCT,
        'max_leverage':MAX_LEVERAGE,
        'max_slots':   MAX_OPEN_SLOTS,
        'min_gate':    MIN_GATE_SCORE,
        'secrets_ok':  SECRETS_PATH.exists(),
        'version':     'v1.1',
    }



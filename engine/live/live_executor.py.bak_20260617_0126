#!/usr/bin/env python3
"""
SIGMA ENGINE — Live Executor v1.0
Abstraccion paper/live. Misma interfaz, diferente backend.

LIVE_MODE = False  (default) → paper trading
LIVE_MODE = True              → Binance Futures real

Para activar live trading:
1. Agregar API keys en /opt/sigma/engine/config/secrets.json:
   {"BINANCE_API_KEY": "...", "BINANCE_API_SECRET": "..."}
2. Verificar Gate >= 85 en el dashboard
3. Cambiar LIVE_MODE = True
4. Dejar DRY_RUN = True para tests sin dinero real
5. Reiniciar sigma-web
"""
import json, os, time
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── CONFIG ────────────────────────────────────────────────────────────────────
LIVE_MODE = False   # ← False = paper | True = Binance live
DRY_RUN   = True    # ← True = loguea ordenes pero no ejecuta (solo con LIVE_MODE=True)

# Limites de seguridad
MAX_KELLY_PCT   = 6.0    # maximo 6% del capital por trade
MAX_LEVERAGE    = 5      # maximo 5x (conservador para empezar)
MAX_OPEN_SLOTS  = 3      # maximo 3 posiciones simultaneas (subido 2026-05-12 — trades quedaban stuck bloqueando slots)
MIN_GATE_SCORE  = 85     # gate minimo para operar live

CHILE        = timezone(timedelta(hours=-4))
BASE         = Path('/opt/sigma')
SECRETS_PATH = BASE / 'engine' / 'config' / 'secrets.json'
LOG_PATH     = BASE / 'results' / 'reports' / 'executor.log'

_exchange = None

# ── Logging ───────────────────────────────────────────────────────────────────
def _log(msg):
    cl   = datetime.now(CHILE)
    line = f"[{cl:%H:%M:%S}] [EXECUTOR] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except:
        pass

# ── Exchange (Binance Futures) ────────────────────────────────────────────────
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

def _binance_symbol(sym):
    return sym.replace('USDT', '') + '/USDT:USDT'

# ── Seguridad pre-trade ────────────────────────────────────────────────────────
def _safety_checks(kelly_pct: float) -> tuple:
    """Verifica condiciones antes de ejecutar en live. Retorna (ok, reason)."""
    # Gate score
    try:
        import urllib.request
        r  = urllib.request.urlopen('http://127.0.0.1:8080/api/trades', timeout=5)
        td = json.loads(r.read())
        score = td.get('live_readiness', {}).get('score', 0)
        if score < MIN_GATE_SCORE:
            return False, f"Gate {score}/100 < minimo {MIN_GATE_SCORE}"
    except:
        pass

    # Circuit breaker
    try:
        r  = urllib.request.urlopen('http://127.0.0.1:8080/api/signals', timeout=5)
        sd = json.loads(r.read())
        if sd.get('circuit_breaker'):
            return False, "Circuit breaker activo"
    except:
        pass

    # Kelly cap
    if kelly_pct > MAX_KELLY_PCT:
        return False, f"Kelly {kelly_pct}% > maximo {MAX_KELLY_PCT}%"

    # Pausa manual
    if (BASE / 'results' / 'pausa.flag').exists():
        return False, "Sistema en pausa manual"

    return True, "OK"

# ── Entry ──────────────────────────────────────────────────────────────────────
def execute_entry(sym, tf, direction, price, sl, tp,
                  strategy='', grade='B', wr=50.0, cagr=0.0,
                  kelly_pct=3.3, paper=True, ai_reason='', **kwargs):
    """
    Abre una posicion. Interfaz unica para paper y live.
    """
    mode = "PAPER" if (not LIVE_MODE or paper) else ("DRY_RUN" if DRY_RUN else "LIVE")
    _log(f"[{mode}] ENTRY {direction.upper()} {sym} {tf} @ {price:.4f} "
         f"SL:{sl:.4f} TP:{tp:.4f} Kelly:{kelly_pct}% Grade:{grade} {ai_reason}")

    # ── Paper mode ─────────────────────────────────────────────────────────────
    if not LIVE_MODE or paper:
        import sys
        sys.path.insert(0, str(BASE))
        from web_server import open_trade
        return open_trade(sym, tf, direction, price, sl, tp,
                         strategy=strategy, paper=True,
                         grade=grade, wr=wr, cagr=cagr, kelly_pct=kelly_pct)

    # ── Live mode ──────────────────────────────────────────────────────────────
    ok, reason = _safety_checks(kelly_pct)
    if not ok:
        _log(f"BLOCKED — {reason}")
        return False

    try:
        ex     = _get_exchange()
        if not ex:
            return False

        symbol = _binance_symbol(sym)
        equity = _get_equity()
        if equity <= 0:
            _log("Balance USDT = 0"); return False

        # Calcular tamano en contratos
        size_usd  = equity * min(kelly_pct / 100, MAX_KELLY_PCT / 100)
        cur_price = ex.fetch_ticker(symbol)['last']
        contracts = round(size_usd / cur_price, 3)
        if contracts <= 0:
            _log("Tamano de contrato = 0"); return False

        side = 'buy' if direction == 'long' else 'sell'

        if DRY_RUN:
            _log(f"DRY_RUN — {side.upper()} {contracts} {symbol} "
                 f"equity=${equity:.0f} size=${size_usd:.0f}")
            return True

        # Leverage
        ex.set_leverage(MAX_LEVERAGE, symbol)

        # Orden de mercado
        order = ex.create_market_order(symbol, side, contracts)
        fill  = order.get('average') or cur_price
        oid   = order.get('id', '?')
        _log(f"ORDER OK id={oid} fill={fill:.4f} contracts={contracts}")

        # Stop Loss
        sl_side = 'sell' if direction == 'long' else 'buy'
        ex.create_order(symbol, 'stop_market', sl_side, contracts,
                        params={'stopPrice': round(sl, 4), 'reduceOnly': True})

        # Take Profit
        tp_side = 'sell' if direction == 'long' else 'buy'
        ex.create_order(symbol, 'take_profit_market', tp_side, contracts,
                        params={'stopPrice': round(tp, 4), 'reduceOnly': True})

        # Registrar en paper state para tracking
        import sys
        sys.path.insert(0, str(BASE))
        from web_server import open_trade
        open_trade(sym, tf, direction, fill, sl, tp,
                  strategy=strategy, paper=False,
                  grade=grade, wr=wr, cagr=cagr, kelly_pct=kelly_pct)
        return True

    except Exception as e:
        _log(f"ERROR live entry: {e}")
        return False

# ── Exit ───────────────────────────────────────────────────────────────────────
def execute_exit(sym, tf, reason='MANUAL'):
    """
    Cierra una posicion. Interfaz unica para paper y live.
    """
    _log(f"[{'PAPER' if not LIVE_MODE else 'LIVE'}] EXIT {sym} {tf} [{reason}]")

    # Paper
    if not LIVE_MODE:
        import sys
        sys.path.insert(0, str(BASE))
        from web_server import close_trade
        try:
            import ccxt
            ex  = ccxt.binance({'options': {'defaultType': 'future'}, 'timeout': 10000})
            prc = ex.fetch_ticker(_binance_symbol(sym))['last']
        except:
            prc = 0
        return close_trade(sym, tf, prc, reason)

    # Live
    try:
        ex     = _get_exchange()
        if not ex: return False
        symbol = _binance_symbol(sym)

        # Cancelar SL/TP pendientes
        try:
            ex.cancel_all_orders(symbol)
        except:
            pass

        # Obtener posicion
        positions = ex.fetch_positions([symbol])
        pos = next((p for p in positions if float(p.get('contracts', 0)) > 0), None)
        if not pos:
            _log(f"No hay posicion abierta en {symbol}"); return False

        contracts = float(pos['contracts'])
        side      = 'sell' if pos['side'] == 'long' else 'buy'

        if DRY_RUN:
            _log(f"DRY_RUN — CLOSE {side.upper()} {contracts} {symbol}")
            return True

        order      = ex.create_market_order(symbol, side, contracts,
                                             params={'reduceOnly': True})
        exit_price = order.get('average', 0)
        _log(f"CLOSED @ {exit_price:.4f}")

        import sys
        sys.path.insert(0, str(BASE))
        from web_server import close_trade
        close_trade(sym, tf, exit_price, reason)
        return True

    except Exception as e:
        _log(f"ERROR live exit: {e}")
        return False

# ── Status ─────────────────────────────────────────────────────────────────────
def status():
    return {
        'live_mode':   LIVE_MODE,
        'dry_run':     DRY_RUN,
        'max_kelly':   MAX_KELLY_PCT,
        'max_leverage':MAX_LEVERAGE,
        'max_slots':   MAX_OPEN_SLOTS,
        'min_gate':    MIN_GATE_SCORE,
        'secrets_ok':  SECRETS_PATH.exists(),
    }

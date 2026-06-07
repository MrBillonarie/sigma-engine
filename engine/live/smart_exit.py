#!/usr/bin/env python3
"""
SIGMA ENGINE — Smart Exit Monitor v1.0
Mejora las salidas mas alla de SL/TP fijo:
1. Trailing stop: mueve SL a break-even cuando trade llega al 60% del TP
2. Limite de tiempo: cierra posiciones abiertas > 96 horas
3. Regimen adverso: cierra si el regimen cambia fuerte contra la posicion
"""
import json, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE  = Path('/opt/sigma')
CHILE = timezone(timedelta(hours=-4))

TRAILING_TRIGGER  = 0.60   # activar BE cuando precio llega al 60% del camino al TP
# Timeout adaptativo por TF — antes era 96h fijo, que dejaba trades 1H stuck 4 días
MAX_TRADE_HOURS_BY_TF = {
    '5m':  6,     # 5 minutos: max 6h
    '15m': 12,    # 15 minutos: max 12h
    '1h':  48,    # 1 hora: max 48h (= 48 barras)
    '4h':  96,    # 4 horas: max 96h (= 24 barras)
    '1d':  240,   # diario: max 10 días
}
MAX_TRADE_HOURS   = 96     # fallback por compatibilidad
BE_BUFFER         = 0.001  # 0.1% buffer sobre entry para el SL en BE

def _now():
    return datetime.now(CHILE)

def _load_state():
    try:
        return json.loads((BASE / 'results/trade_state.json').read_text())
    except:
        return {'open': {}, 'history': []}

def _save_state(state):
    (BASE / 'results/trade_state.json').write_text(
        json.dumps(state, indent=2), encoding='utf-8')

def _hours_open(trade):
    try:
        opened = datetime.fromisoformat(trade.get('opened_at', ''))
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=CHILE)
        return (_now() - opened).total_seconds() / 3600
    except:
        return 0

def check_trailing(key, trade, price):
    """Mueve SL a BE cuando el trade llega al TRAILING_TRIGGER del TP. Retorna True si modifico."""
    entry = trade.get('entry', 0)
    sl    = trade.get('sl', 0)
    tp    = trade.get('tp', 0)
    direc = trade.get('direction', 'long')
    if not entry or not sl or not tp or trade.get('be_set'):
        return False
    dist = abs(tp - entry)
    if dist == 0:
        return False
    if direc == 'long':
        progress = (price - entry) / dist
        if progress >= TRAILING_TRIGGER and sl < entry:
            trade['sl']     = round(entry * (1 + BE_BUFFER), 4)
            trade['be_set'] = True
            print(f"[SMART EXIT] {key} trailing BE → SL={trade['sl']:.4f}", flush=True)
            return True
    else:
        progress = (entry - price) / dist
        if progress >= TRAILING_TRIGGER and sl > entry:
            trade['sl']     = round(entry * (1 - BE_BUFFER), 4)
            trade['be_set'] = True
            print(f"[SMART EXIT] {key} trailing BE → SL={trade['sl']:.4f}", flush=True)
            return True
    return False

def check_time_limit(key, trade):
    """Retorna True si el trade excede el max hours según su TF.
    Adaptativo: 1H trades max 48h, 4H max 96h, etc. Antes era 96h para todo (demasiado)."""
    tf = trade.get('tf', '').lower()
    max_h = MAX_TRADE_HOURS_BY_TF.get(tf, MAX_TRADE_HOURS)
    h = _hours_open(trade)
    if h >= max_h:
        print(f"[SMART EXIT] {key} time limit {h:.0f}h >= {max_h}h (TF {tf})", flush=True)
        return True
    return False

def check_regime_exit(key, trade, regime):
    """Retorna True si el regimen es fuertemente adverso a la posicion."""
    direc = trade.get('direction', 'long')
    h     = _hours_open(trade)
    if h < 2:
        return False  # dar al menos 2h antes de salir por regimen
    if regime == 'BEAR' and direc == 'long':
        print(f"[SMART EXIT] {key} regimen BEAR vs LONG", flush=True)
        return True
    if regime == 'BULL' and direc == 'short':
        print(f"[SMART EXIT] {key} regimen BULL vs SHORT", flush=True)
        return True
    return False

def run_smart_exit(current_prices: dict, regime: str, close_fn):
    """
    Punto de entrada principal.
    current_prices: {sym: float}
    regime: 'BULL' | 'BEAR' | 'RANGE'
    close_fn: close_trade(sym, tf, price, reason)
    """
    state    = _load_state()
    open_t   = state.get('open', {})
    modified = False

    for key, trade in list(open_t.items()):
        if trade.get('status') != 'open':
            continue
        sym   = trade.get('sym', '')
        tf    = trade.get('tf', '')
        price = current_prices.get(sym, 0)
        if not price:
            continue

        # 1. Trailing stop (modifica SL en state)
        if check_trailing(key, trade, price):
            modified = True

        # 2. Tiempo limite
        if check_time_limit(key, trade):
            close_fn(sym, tf, price, 'TIME_LIMIT')
            continue

        # 3. Regimen adverso (solo si BEAR o BULL fuerte, no RANGE)
        if regime in ('BULL', 'BEAR') and check_regime_exit(key, trade, regime):
            close_fn(sym, tf, price, 'REGIME_EXIT')

    if modified:
        _save_state(state)

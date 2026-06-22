#!/usr/bin/env python3
"""
SIGMA Treasury Manager — ESQUELETO. Calcula cuanto correspondería retirar a
cold storage segun WITHDRAW_PCT/WITHDRAW_EVERY_DAYS, pero NO ejecuta ningun
retiro real. La direccion de destino (COLD_STORAGE_ADDRESS) se deja vacia a
proposito -- nunca se pide ni se escribe desde una sesion de IA. El usuario la
completa directo en el VPS cuando este listo.

Mientras la direccion este vacia, este script SOLO loguea y alerta el monto
calculado. No existe ninguna funcion de retiro real en este archivo.

Cron: diario, solo calculo + log + alerta (sin systemd persistente).
"""
# --- SIGMA secrets loader (audit 2026-05-13) ---
import sys as _sigma_sys
if "/opt/sigma" not in _sigma_sys.path:
    _sigma_sys.path.insert(0, "/opt/sigma")
from utils.secrets import get_tg_token as _sigma_get_tg_token
# --- end SIGMA secrets loader ---

import json, time
from datetime import datetime
from pathlib import Path

BASE          = Path('/opt/sigma')
CONFIG_PATH   = BASE / 'engine' / 'config' / 'treasury_config.json'
SECRETS_PATH  = BASE / 'engine' / 'config' / 'secrets.json'
STATE_PATH    = BASE / 'results' / 'reports' / 'treasury_state.json'
WITHDRAW_LOG  = BASE / 'results' / 'reports' / 'withdrawal_log.jsonl'
LOG_PATH      = BASE / 'results' / 'reports' / 'treasury_manager.log'
CHAT_ID       = '-1003787411069'

DEFAULT_CONFIG = {
    "_comment": (
        "Esqueleto de tesoreria/autocustodia. WITHDRAW_PCT y WITHDRAW_EVERY_DAYS "
        "en 0 = inactivo (no calcula nada). COLD_STORAGE_ADDRESS vacio a proposito "
        "-- completar manualmente en el VPS, nunca pegar la direccion en un chat de IA. "
        "Este script NUNCA ejecuta un retiro real, solo calcula y alerta."
    ),
    "WITHDRAW_PCT": 0.0,
    "WITHDRAW_EVERY_DAYS": 0,
    "COLD_STORAGE_ADDRESS": "",
}


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f'[{ts}] {msg}\n')


def tg(text, silent=True):
    try:
        import urllib.request, urllib.parse
        token = _sigma_get_tg_token()
        url = f'https://api.telegram.org/bot{token}/sendMessage'
        data = urllib.parse.urlencode({
            'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML',
            'disable_notification': str(silent).lower(),
        }).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        log(f'TG fail: {e}')


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding='utf-8')
        log('Config inicial creada (vacia, a completar por el usuario en el VPS)')
        return dict(DEFAULT_CONFIG)
    try:
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception as e:
        log(f'ERROR leyendo config: {e}')
        return dict(DEFAULT_CONFIG)


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {'baseline_equity': None, 'baseline_at': None, 'last_withdraw_check': 0}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')


def get_equity():
    """Equity real en USDT via ccxt -- mismo patron que live_executor._get_equity()."""
    try:
        import ccxt
        secrets = json.loads(SECRETS_PATH.read_text()) if SECRETS_PATH.exists() else {}
        ex = ccxt.binance({
            'apiKey':  secrets.get('BINANCE_API_KEY', ''),
            'secret':  secrets.get('BINANCE_API_SECRET', ''),
            'options': {'defaultType': 'future'},
            'timeout': 15000,
        })
        bal = ex.fetch_balance()
        usdt = bal.get('USDT', {}) or {}
        return float(usdt.get('total', usdt.get('free', 0)) or 0)
    except Exception as e:
        log(f'ERROR fetch_balance: {e}')
        return None


def append_withdrawal_log(entry):
    WITHDRAW_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(WITHDRAW_LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def main():
    config = load_config()
    pct = float(config.get('WITHDRAW_PCT', 0) or 0)
    every_days = int(config.get('WITHDRAW_EVERY_DAYS', 0) or 0)
    address = (config.get('COLD_STORAGE_ADDRESS') or '').strip()

    if pct <= 0 or every_days <= 0:
        log('INACTIVO -- WITHDRAW_PCT o WITHDRAW_EVERY_DAYS en 0, nada que calcular')
        return

    equity = get_equity()
    if equity is None:
        log('No se pudo leer equity, abortando este ciclo')
        return

    state = load_state()
    now = time.time()

    if state.get('baseline_equity') is None:
        state['baseline_equity'] = equity
        state['baseline_at'] = now
        save_state(state)
        log(f'Baseline de equity establecido: ${equity:.2f}')
        return

    days_since_check = (now - state.get('last_withdraw_check', state['baseline_at'])) / 86400.0
    if days_since_check < every_days:
        log(f'OK equity=${equity:.2f} -- faltan {every_days - days_since_check:.1f} dias para el proximo ciclo')
        return

    profit = max(0.0, equity - state['baseline_equity'])
    amount_to_withdraw = round(profit * pct, 2)

    entry = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'equity': round(equity, 2),
        'baseline_equity': round(state['baseline_equity'], 2),
        'profit_since_baseline': round(profit, 2),
        'withdraw_pct_config': pct,
        'amount_calculated': amount_to_withdraw,
        'status': 'PENDING_CONFIG' if not address else 'PENDING_MANUAL_REVIEW',
    }
    append_withdrawal_log(entry)
    log(f'CALCULO: profit=${profit:.2f} monto_a_retirar=${amount_to_withdraw:.2f} status={entry["status"]}')

    if not address:
        tg(
            f'🏦 <b>Tesorería — retiro calculado, falta configurar destino</b>\n\n'
            f'Profit desde el último checkpoint: <b>${profit:.2f}</b>\n'
            f'Monto que correspondería retirar ({pct*100:.0f}%): <b>${amount_to_withdraw:.2f}</b>\n\n'
            f'<i>COLD_STORAGE_ADDRESS está vacío en treasury_config.json — complétalo directo en el VPS '
            f'para que esto deje de ser solo un cálculo.</i>',
            silent=True,
        )
    else:
        tg(
            f'🏦 <b>Tesorería — retiro listo para revisión manual</b>\n\n'
            f'Monto calculado: <b>${amount_to_withdraw:.2f}</b>\n\n'
            f'<i>Este script todavía no ejecuta retiros automáticos — esa función no está implementada. '
            f'Revisar manualmente y ejecutar desde Binance.</i>',
            silent=False,
        )

    state['baseline_equity'] = equity
    state['last_withdraw_check'] = now
    save_state(state)


if __name__ == '__main__':
    main()

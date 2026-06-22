#!/usr/bin/env python3
"""
SIGMA Adversarial Validator — intenta romper a los champions ya validados,
separado del pipeline que los entrena (mismo principio de robustness.py: no
relajar, pero aplicado por un proceso distinto al que construye el modelo).

v1: perturbacion de slippage -- re-evalua los trades cerrados de cada champion
asumiendo 2x el slippage ya calibrado (slippage_model.json) y mide que
porcentaje de trades ganadores se volverian perdedores.

v2 (no implementado todavia): perturbacion de entrada (delay de 1 vela) --
requiere cruzar con OHLCV crudo por timestamp, mas trabajo, queda pendiente.

No bloquea ni reemplaza utils/robustness.py. Solo agrega un flag informativo
'adversarial_flag' (PASS/CAUTION) por champion, mismo principio que los
red-flags del dashboard: informacion, no filtro automatico.

Cron: semanal (no systemd timer, sigue el patron de los crons semanales
existentes como wft_all_models.py).
"""
# --- SIGMA secrets loader (audit 2026-05-13) ---
import sys as _sigma_sys
if "/opt/sigma" not in _sigma_sys.path:
    _sigma_sys.path.insert(0, "/opt/sigma")
from utils.secrets import get_tg_token as _sigma_get_tg_token
# --- end SIGMA secrets loader ---

import json
from datetime import datetime
from pathlib import Path

BASE        = Path('/opt/sigma')
LOG_PATH    = BASE / 'results' / 'reports' / 'adversarial_validator.log'
OUT_PATH    = BASE / 'results' / 'reports' / 'adversarial_flags.json'
CHAT_ID     = '-1003787411069'

MIN_TRADES_TO_EVALUATE = 5      # menos que esto, no hay base para estresar
CAUTION_FLIP_THRESHOLD = 0.30   # si >=30% de los ganadores se vuelven perdedores bajo estres -> CAUTION


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


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default if default is not None else {}


def stress_test_champion(trades, slippage_mult):
    """Aplica slippage_mult al costo de slippage ya asumido en cada trade y
    cuenta cuantos trades ganadores se vuelven perdedores."""
    winners = [t for t in trades if (t.get('pnl_pct_raw', t.get('pnl_pct', 0)) or 0) > 0]
    if not winners:
        return None

    flipped = 0
    for t in winners:
        pnl_raw = t.get('pnl_pct_raw', t.get('pnl_pct', 0)) or 0
        sl_dist = abs(t.get('sl_dist_pct_at_open', 0) or 0)
        extra_cost = (slippage_mult - 1.0) * sl_dist * 0.1  # fraccion conservadora del SL distance como cost adicional
        stressed_pnl = pnl_raw - extra_cost
        if stressed_pnl <= 0:
            flipped += 1

    flip_rate = flipped / len(winners)
    return {
        'n_winners': len(winners),
        'n_flipped': flipped,
        'flip_rate': round(flip_rate, 3),
    }


def main():
    snapshot = load_json(BASE / 'results' / 'reports' / 'port_snapshot.json', {})
    champions = snapshot.get('champions', {})  # {"SYM|TF": "strategy|type"}
    trade_state = load_json(BASE / 'results' / 'trade_state.json', {})
    history = trade_state.get('history', [])
    slippage_model = load_json(BASE / 'results' / 'reports' / 'slippage_model.json', {})
    global_mult = slippage_model.get('global_mult', 1.5)
    per_asset = slippage_model.get('per_asset', {})

    flags = {}
    caution_list = []
    skipped = 0

    for slot_key, champ_val in champions.items():
        try:
            sym, tf = slot_key.split('|')
            strategy, _ = champ_val.split('|')
        except ValueError:
            continue

        mult = per_asset.get(sym, global_mult)
        trades = [t for t in history if t.get('sym') == sym and t.get('strategy') == strategy]

        if len(trades) < MIN_TRADES_TO_EVALUATE:
            skipped += 1
            continue

        result = stress_test_champion(trades, mult)
        if result is None:
            skipped += 1
            continue

        flag = 'CAUTION' if result['flip_rate'] >= CAUTION_FLIP_THRESHOLD else 'PASS'
        flags[slot_key] = {
            'strategy': strategy,
            'slippage_mult_used': mult,
            **result,
            'adversarial_flag': flag,
        }
        if flag == 'CAUTION':
            caution_list.append((slot_key, strategy, result['flip_rate']))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        'computed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'flags': flags,
        'skipped_low_sample': skipped,
        'note': 'v1: solo perturbacion de slippage (2x calibrado). Entry-delay queda para v2.',
    }, indent=2, ensure_ascii=False), encoding='utf-8')

    log(f'STATUS evaluados={len(flags)} skipped_low_sample={skipped} caution={len(caution_list)}')

    if caution_list:
        parts = ['🧪 <b>Adversarial Validator — champions sensibles a slippage</b>', '']
        for slot_key, strategy, flip_rate in caution_list:
            parts.append(f'  • <code>{slot_key}</code> ({strategy}): {flip_rate*100:.0f}% de ganadores se vuelven perdedores bajo 2x slippage')
        parts.append('\nNo bloquea nada -- es información adicional, igual que los red-flags del dashboard.')
        tg('\n'.join(parts), silent=True)


if __name__ == '__main__':
    main()

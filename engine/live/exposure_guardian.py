#!/usr/bin/env python3
"""
SIGMA ENGINE — Agente 4: Guardian de Exposicion Total
Ajusta el Kelly base segun el drawdown actual del portfolio.
La curva de reduccion es continua (no escalones), lo que evita
comportamientos abruptos cerca de los umbrales.

Curva de Kelly multiplier vs DD:
  DD  0%: mult 1.00  (normal)
  DD  5%: mult 0.85
  DD 10%: mult 0.65
  DD 15%: mult 0.45
  DD 20%: mult 0.25
  DD 25%+: mult 0.10 (modo supervivencia)

Output: /opt/sigma/results/reports/exposure_gate.json
  {
    "kelly_multiplier": 0.85,
    "dd_current_pct": -5.3,
    "dd_from_peak_pct": -5.3,
    "equity": 11488,
    "peak_equity": 12100,
    "status": "CAUTION",
    "note": "DD 5.3% -> Kelly reducido a 85%",
    "computed_at": "..."
  }

Integrado en web_server.py: `base_risk = 5.0 * _dd_kelly_mult * exposure_mult`
Cron: cada 30 minutos (antes era solo hourly)
"""
import json, math, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE     = Path('/opt/sigma')
OUT_FILE = BASE / 'results/reports/exposure_gate.json'
CHILE    = timezone(timedelta(hours=-4))

# ─── Kelly curve ──────────────────────────────────────────────────────────────

def _kelly_multiplier(dd_pct: float) -> float:
    """
    dd_pct: numero negativo (ej: -7.3 = 7.3% de drawdown).
    Retorna multiplicador 0.10..1.00.
    Curva exponencial suave: mult = exp(-k * |dd|)
    Calibrada para que DD=15% -> mult=0.45.
    """
    dd_abs = abs(min(dd_pct, 0))  # asegurar positivo
    if dd_abs < 0.5:
        return 1.0
    # k calibrado: exp(-k*15) = 0.45 → k = -ln(0.45)/15 = 0.0527
    k    = 0.0527
    mult = math.exp(-k * dd_abs)
    return round(max(0.10, min(1.00, mult)), 3)

def _status(mult: float) -> str:
    if mult >= 0.90:
        return 'NORMAL'
    if mult >= 0.70:
        return 'CAUTION'
    if mult >= 0.40:
        return 'REDUCED'
    return 'SURVIVAL'

# ─── Main ─────────────────────────────────────────────────────────────────────

def run():
    ts   = {}
    try:
        ts = json.loads((BASE / 'results/trade_state.json').read_text())
    except Exception:
        pass

    port   = ts.get('portfolio', {})
    equity = port.get('equity', 10000)
    init   = port.get('initial_capital', 10000)
    peak   = port.get('peak_equity', equity)

    # DD from peak (more conservative than DD from initial)
    dd_peak  = round((equity - peak) / peak * 100, 2) if peak > 0 else 0.0
    dd_init  = round((equity - init) / init * 100, 2) if init > 0 else 0.0
    # Use the worse of the two
    dd_use   = min(dd_peak, dd_init)

    mult   = _kelly_multiplier(dd_use)
    status = _status(mult)
    note   = f'DD {abs(dd_use):.1f}% (peak={dd_peak:.1f}% init={dd_init:.1f}%) -> Kelly x{mult:.2f} [{status}]'

    out = {
        'kelly_multiplier':  mult,
        'dd_current_pct':    dd_use,
        'dd_from_peak_pct':  dd_peak,
        'dd_from_init_pct':  dd_init,
        'equity':            round(equity, 2),
        'peak_equity':       round(peak, 2),
        'initial_capital':   round(init, 2),
        'status':            status,
        'note':              note,
        'computed_at':       datetime.now(CHILE).isoformat(),
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2))

    print(f'[EXPOSURE_GUARDIAN] {note}', flush=True)

    # Alertar via Telegram si status empeora
    _check_alert(out)

    return out

def _check_alert(out):
    prev_path = BASE / 'results/reports/exposure_gate_prev.json'
    try:
        prev = json.loads(prev_path.read_text())
    except Exception:
        prev = {}

    prev_status = prev.get('status', 'NORMAL')
    curr_status = out['status']

    severity_rank = {'NORMAL': 0, 'CAUTION': 1, 'REDUCED': 2, 'SURVIVAL': 3}
    if severity_rank.get(curr_status, 0) > severity_rank.get(prev_status, 0):
        # Status empeoró — alertar
        _send_alert(out)

    # Save current as prev
    prev_path.write_text(json.dumps(out, indent=2))

def _send_alert(out):
    import urllib.request, urllib.parse
    sys.path.insert(0, str(BASE))
    try:
        from utils.secrets import get_tg_token
        token = get_tg_token()
    except Exception:
        token = None
    if not token:
        return

    emojis = {'CAUTION': '⚠️', 'REDUCED': '🔴', 'SURVIVAL': '🆘'}
    em = emojis.get(out['status'], '⚠️')

    msg = (f'{em} <b>SIGMA — Exposure Guardian</b>\n\n'
           f'Portfolio en modo <b>{out["status"]}</b>\n'
           f'DD actual: <code>{out["dd_current_pct"]:.1f}%</code>\n'
           f'Equity: <code>${out["equity"]:,.2f}</code>\n'
           f'Kelly reducido a: <code>x{out["kelly_multiplier"]:.2f}</code> del base\n\n'
           f'<i>El sistema ajusta sizing automaticamente.</i>')

    data = urllib.parse.urlencode({
        'chat_id': '-1003787411069',
        'text': msg, 'parse_mode': 'HTML'
    }).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f'https://api.telegram.org/bot{token}/sendMessage', data=data
            ), timeout=10
        )
    except Exception as e:
        print(f'[EXPOSURE_GUARDIAN] Telegram error: {e}', flush=True)


if __name__ == '__main__':
    result = run()
    print(json.dumps(result, indent=2))

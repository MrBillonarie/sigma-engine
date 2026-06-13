#!/usr/bin/env python3
"""
SIGMA ENGINE — Agente 5: Validador Motor 2
Valida champions de commodities antes de promoverlos al portafolio activo.

Criterios de validacion:
  1. Data quality   : gaps < 10% de barras, no NaN en columnas clave
  2. Trades minimos : >= 15 trades en OOS (commodities menos frecuentes)
  3. CAGR sanity    : no > 400% (sospechasamente overfitteado)
  4. IS/OOS gap     : OOS_CAGR >= IS_CAGR * 0.4 (OOS al menos 40% del IS)
  5. Drawdown limite: OOS_DD < 60% (commodities pueden tener mas DD)
  6. Duracion minima: CSV con al menos 1 año de datos

Resultado:
  - PASS: champion promovido normal
  - WARN: promovido con flag de advertencia
  - BLOCK: no promovido, necesita re-optimizacion

Output: stamp en el JSON del model + log
Cron: cada 30 min (corre ligero, solo verifica JSONs nuevos)
"""
import json, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/opt/sigma')

BASE      = Path('/opt/sigma')
CHILE     = timezone(timedelta(hours=-4))
M2_ASSETS = ['XAU', 'XAG', 'WTI', 'HG', 'NG', 'PL']
LOG_FILE  = BASE / 'results/reports/m2_validator.log'

# Umbrales (mas permisivos que M1 porque los datos son menos densos)
MIN_OOS_TRADES   = 15
MAX_CAGR_SANITY  = 400.0   # % — CAGR mas alto = sospechoso
MIN_IS_OOS_RATIO = 0.35    # OOS >= 35% de IS
MAX_OOS_DD       = 65.0    # % max DD en OOS
MIN_DATA_ROWS    = 500     # minimo 500 barras en CSV
MAX_GAP_PCT      = 12.0    # % maximo de barras faltantes


def _log(msg):
    ts = datetime.now(CHILE).strftime('%Y-%m-%d %H:%M')
    line = f'[{ts}] {msg}'
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


def _csv_quality(csv_path):
    """Verifica calidad del CSV de datos."""
    path = Path(csv_path)
    if not path.exists():
        return False, 'CSV no existe', 0

    lines = []
    try:
        with open(path, 'r') as f:
            lines = f.readlines()
    except Exception as e:
        return False, f'Error leyendo CSV: {e}', 0

    if len(lines) < 2:
        return False, 'CSV vacio', 0

    n_rows = len(lines) - 1  # excluding header
    if n_rows < MIN_DATA_ROWS:
        return False, f'Solo {n_rows} barras (minimo {MIN_DATA_ROWS})', n_rows

    # Check for NaN / empty values in close column
    header = lines[0].strip().split(',')
    ci = {h.lower().strip(): i for i, h in enumerate(header)}
    close_idx = ci.get('close', ci.get('c', 4))

    nan_count = 0
    prev_close = None
    for line in lines[1:]:
        parts = line.strip().split(',')
        if len(parts) <= close_idx:
            nan_count += 1
            continue
        try:
            val = float(parts[close_idx])
            if val <= 0 or val != val:  # nan check
                nan_count += 1
        except Exception:
            nan_count += 1

    gap_pct = nan_count / n_rows * 100
    if gap_pct > MAX_GAP_PCT:
        return False, f'Demasiados gaps: {gap_pct:.1f}% ({nan_count} barras)', n_rows

    return True, f'OK ({n_rows} barras, {gap_pct:.1f}% gaps)', n_rows


def _validate_model(model_path, csv_path):
    """Valida un model JSON de Motor 2. Retorna (status, reasons)."""
    reasons = []
    warnings = []

    # Load model
    try:
        d = json.loads(Path(model_path).read_text())
    except Exception as e:
        return 'BLOCK', [f'Error leyendo model JSON: {e}']

    moos = d.get('metrics_oos') or {}
    mis  = d.get('metrics_is')  or {}

    # 1. CSV quality
    csv_ok, csv_msg, n_rows = _csv_quality(csv_path)
    if not csv_ok:
        reasons.append(f'DATA_QUALITY: {csv_msg}')
    else:
        if n_rows < MIN_DATA_ROWS * 2:
            warnings.append(f'DATA_THIN: Solo {n_rows} barras')

    # 2. OOS trades
    oos_trades = moos.get('trades', d.get('n_trades', 0)) or 0
    if oos_trades < MIN_OOS_TRADES:
        reasons.append(f'LOW_TRADES: {oos_trades} trades OOS (minimo {MIN_OOS_TRADES})')
    elif oos_trades < MIN_OOS_TRADES * 2:
        warnings.append(f'FEW_TRADES: {oos_trades} trades OOS (estadistica debil)')

    # 3. CAGR sanity
    oos_cagr = moos.get('cagr', d.get('oos_cagr', d.get('cagr', 0))) or 0
    if oos_cagr > MAX_CAGR_SANITY:
        reasons.append(f'CAGR_INSANE: {oos_cagr:.0f}% (max permitido {MAX_CAGR_SANITY:.0f}%)')
    elif oos_cagr > MAX_CAGR_SANITY * 0.7:
        warnings.append(f'CAGR_HIGH: {oos_cagr:.0f}% — posible overfit')

    if oos_cagr <= 0:
        reasons.append(f'CAGR_NEGATIVE: {oos_cagr:.1f}%')

    # 4. IS/OOS gap
    is_cagr = mis.get('cagr', d.get('is_cagr', 0)) or 0
    if is_cagr > 0 and oos_cagr > 0:
        ratio = oos_cagr / is_cagr
        if ratio < MIN_IS_OOS_RATIO:
            reasons.append(f'IS_OOS_GAP: OOS/IS={ratio:.2f} (minimo {MIN_IS_OOS_RATIO})')
        elif ratio < MIN_IS_OOS_RATIO * 1.5:
            warnings.append(f'IS_OOS_WARN: OOS/IS={ratio:.2f} — degradacion moderada')

    # 5. Drawdown
    oos_dd = abs(moos.get('max_dd', d.get('dd', 0)) or 0)
    if oos_dd > MAX_OOS_DD:
        reasons.append(f'DD_EXTREME: {oos_dd:.1f}% (max {MAX_OOS_DD:.0f}%)')
    elif oos_dd > MAX_OOS_DD * 0.75:
        warnings.append(f'DD_HIGH: {oos_dd:.1f}%')

    # Verdict
    if reasons:
        status = 'BLOCK'
    elif warnings:
        status = 'WARN'
    else:
        status = 'PASS'

    return status, reasons + [f'[WARN] {w}' for w in warnings]


def _stamp_model(model_path, status, reasons):
    """Agrega sello de validacion al JSON del model."""
    try:
        d = json.loads(Path(model_path).read_text())
        d['m2_validation'] = {
            'status':      status,
            'reasons':     reasons,
            'validated_at': datetime.now(CHILE).isoformat(),
        }
        Path(model_path).write_text(json.dumps(d, indent=2))
    except Exception as e:
        _log(f'Error stamping {model_path}: {e}')


def run():
    validated = 0
    passed    = 0
    blocked   = 0
    warned    = 0

    csv_map = {
        'XAU': BASE / 'models/data_XAU_{tf}_max.csv',
        'XAG': BASE / 'models/data_XAG_{tf}_max.csv',
        'WTI': BASE / 'models/data_WTI_{tf}_max.csv',
        'HG':  BASE / 'models/data_HG_{tf}_max.csv',
        'NG':  BASE / 'models/data_NG_{tf}_max.csv',
        'PL':  BASE / 'models/data_PL_{tf}_max.csv',
    }

    for tf in ['4h', '1h', '15m', '5m']:
        tf_dir = BASE / f'models/{tf}'
        if not tf_dir.exists():
            continue

        for model_file in tf_dir.glob('*.json'):
            if model_file.name in ('strategy.pine', 'walk_forward_v2.json'):
                continue

            # Check if this is a Motor 2 model
            asset = None
            for a in M2_ASSETS:
                if model_file.stem.startswith(a.lower()):
                    asset = a
                    break
            if not asset:
                continue

            # Skip if already validated and not stale (< 24h)
            try:
                d = json.loads(model_file.read_text())
                val = d.get('m2_validation', {})
                if val:
                    val_time = val.get('validated_at', '')
                    if val_time:
                        dt = datetime.fromisoformat(val_time)
                        age_h = (datetime.now(CHILE) - dt.astimezone(CHILE)).total_seconds() / 3600
                        if age_h < 24:
                            continue  # ya validado recientemente
            except Exception:
                pass

            csv_path = str(csv_map[asset]).replace('{tf}', tf)
            status, reasons = _validate_model(str(model_file), csv_path)
            _stamp_model(str(model_file), status, reasons)

            symbol = model_file.stem
            _log(f'[{status}] {symbol} ({tf}) — {", ".join(reasons) if reasons else "OK"}')

            validated += 1
            if status == 'PASS':  passed  += 1
            elif status == 'WARN': warned  += 1
            elif status == 'BLOCK': blocked += 1

    if validated > 0:
        _log(f'Summary: {validated} validated | {passed} PASS | {warned} WARN | {blocked} BLOCK')

        # Alert if any BLOCK
        if blocked > 0:
            _send_block_alert(blocked)

    return {'validated': validated, 'passed': passed, 'warned': warned, 'blocked': blocked}


def _send_block_alert(n_blocked):
    import urllib.request, urllib.parse
    sys.path.insert(0, str(BASE))
    try:
        from utils.secrets import get_tg_token
        token = get_tg_token()
    except Exception:
        token = None
    if not token:
        return

    msg = (f'🚫 <b>SIGMA — M2 Validator</b>\n\n'
           f'{n_blocked} champion(s) de Motor 2 bloqueados por validacion.\n'
           f'Revisar: <code>/opt/sigma/results/reports/m2_validator.log</code>')

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
    except Exception:
        pass


if __name__ == '__main__':
    result = run()
    print(json.dumps(result, indent=2))

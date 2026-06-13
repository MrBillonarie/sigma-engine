#!/usr/bin/env python3
"""
SIGMA ENGINE - Agente 7: Volatility Targeting
Ajusta sizing para mantener volatilidad de portafolio ~12% anual.
vol > target -> reduce Kelly. vol < target -> sube (max x1.3).
Output: /opt/sigma/results/reports/vol_target.json
Cron: cada 30min
"""
import json, math, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE       = Path('/opt/sigma')
OUT        = BASE / 'results/reports/vol_target.json'
CHILE      = timezone(timedelta(hours=-4))
VOL_TARGET = 12.0
WINDOW     = 20
MIN_TRADES = 8

def run():
    ts   = json.loads((BASE / 'results/trade_state.json').read_text())
    hist = ts.get('history', [])

    if len(hist) < MIN_TRADES:
        out = {'vol_mult': 1.0, 'realized_vol_annual': None,
               'note': f'Insuficientes trades ({len(hist)} < {MIN_TRADES})',
               'computed_at': datetime.now(CHILE).isoformat()}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(out, indent=2))
        print('[VOL_TARGET] mult=1.0 (pocos trades)', flush=True)
        return out

    recent = hist[-WINDOW:]
    pnls   = [float(t.get('pnl_pct', 0) or 0) for t in recent]
    mean   = sum(pnls) / len(pnls)
    std    = math.sqrt(sum((p - mean)**2 for p in pnls) / len(pnls))

    dates = []
    for t in hist:
        try:
            dates.append(datetime.fromisoformat(str(t.get('opened_at','')).replace('Z','+00:00')))
        except:
            pass
    tpy = len(hist) / max((max(dates)-min(dates)).total_seconds()/86400, 1) * 365 if len(dates) >= 2 else 250

    vol_annual = std * math.sqrt(tpy)
    if vol_annual > 0.5:
        mult = round(max(0.5, min(1.3, VOL_TARGET / vol_annual)), 3)
    else:
        mult = 1.0

    out = {
        'computed_at':         datetime.now(CHILE).isoformat(),
        'vol_mult':            mult,
        'realized_vol_annual': round(vol_annual, 2),
        'vol_target_annual':   VOL_TARGET,
        'trades_per_year_est': round(tpy, 1),
        'std_per_trade':       round(std, 3),
        'window':              len(recent),
        'note': f'vol={vol_annual:.1f}% anual -> x{mult} (target={VOL_TARGET}%)',
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print(f'[VOL_TARGET] vol={vol_annual:.1f}% | target={VOL_TARGET}% | mult={mult}', flush=True)
    return out

if __name__ == '__main__': run()

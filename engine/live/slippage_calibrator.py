#!/usr/bin/env python3
"""
SIGMA ENGINE - Agente 6: Slippage Calibrator
Calibra el factor de slippage real vs teorico desde los trades live.
Output: /opt/sigma/results/reports/slippage_model.json
Cron: diario 02:00
"""
import json, sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone, timedelta

BASE    = Path('/opt/sigma')
OUT     = BASE / 'results/reports/slippage_model.json'
CHILE   = timezone(timedelta(hours=-4))
DEFAULT = 1.5

def run():
    ts   = json.loads((BASE / 'results/trade_state.json').read_text())
    hist = ts.get('history', [])
    sl_hits = [t for t in hist if t.get('reason') == 'SL_HIT']
    mults, by_asset = [], defaultdict(list)

    for t in sl_hits:
        sl_th  = abs(t.get('sl_dist_pct_at_open', 0) or 0)
        actual = abs(t.get('pnl_pct_raw', t.get('pnl_pct', 0)) or 0)  # raw precio, no formula Kelly
        if sl_th > 0.1 and actual > 0:
            m = actual / sl_th
            mults.append(m)
            by_asset[t.get('sym','BTC').split('/')[0]].append(m)

    global_mult = round(sum(mults)/len(mults), 3) if len(mults) >= 3 else DEFAULT
    per_asset   = {a: round(sum(v)/len(v), 3) for a,v in by_asset.items() if len(v) >= 2}

    out = {
        'computed_at':  datetime.now(CHILE).isoformat(),
        'n_sl_hits':    len(sl_hits),
        'n_calibrated': len(mults),
        'global_mult':  global_mult,
        'per_asset':    per_asset,
        'note': f'Default {DEFAULT}x cuando <3 datos. Basado en {len(mults)} SL hits.',
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print(f'[SLIPPAGE] global={global_mult:.2f}x | {len(mults)}/{len(sl_hits)} calibrados', flush=True)
    for a,m in sorted(per_asset.items()):
        print(f'  {a}: {m:.2f}x', flush=True)
    return out

if __name__ == '__main__': run()

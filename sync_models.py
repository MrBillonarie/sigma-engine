#!/usr/bin/env python3
"""
Genera un archivo sync_results.json con los mejores modelos encontrados.
El PC puede descargar este archivo para actualizar sus modelos.
"""
import json, os
from pathlib import Path
from datetime import datetime

BASE = Path('/opt/sigma/models')
OUT  = Path('/opt/sigma/results/reports/sync_results.json')

results = {
    'timestamp': datetime.now().isoformat(),
    'models': {}
}

for tf in ['1h', '4h', '15m', '5m']:
    tf_dir = BASE / tf
    if not tf_dir.exists():
        continue
    results['models'][tf] = []
    for f in sorted(tf_dir.glob('*.json')):
        try:
            d = json.load(open(f))
            m_oos = d.get('metrics_oos')
            if not m_oos:
                continue
            cagr = m_oos.get('cagr', 0)
            if cagr > 0:
                results['models'][tf].append({
                    'file': f.name,
                    'strategy': d.get('strategy', '?'),
                    'cagr_oos': cagr,
                    'wr_oos': m_oos.get('wr', m_oos.get('winrate', 0)),
                    'dd_oos': m_oos.get('dd', m_oos.get('max_dd', 0)),
                    'trades_oos': m_oos.get('trades', 0),
                })
        except:
            pass
    results['models'][tf].sort(key=lambda x: x['cagr_oos'], reverse=True)

with open(OUT, 'w') as f:
    json.dump(results, f, indent=2)
print(f'Sync results saved: {OUT}')
for tf, models in results['models'].items():
    for m in models[:2]:
        print(f'  {tf}: {m["strategy"]} OOS {m["cagr_oos"]:+.1f}% ({m["trades_oos"]}T)')

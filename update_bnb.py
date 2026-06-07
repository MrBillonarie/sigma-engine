
import json
from pathlib import Path
p = Path('/opt/sigma/models/4h/bnb_breakout.json')
d = json.loads(p.read_text())
val = d.get('validation', {})
val['walk_forward'] = {
    'windows': 95, 'positive': 23, 'pct_positive': 24.2,
    'avg_cagr_positive': 64.2, 'passed': False,
    'note': 'Solo 24% ventanas positivas. Usar solo con filtro BULL confirmado.'
}
val['confidence'] = 'MEDIA'
val['wft_note'] = 'MC=98% ALTA pero WFT=24%. Muy dependiente del regimen BULL.'
d['validation'] = val
p.write_text(json.dumps(d, indent=2, default=str))
print('BNB 4H: confidence bajada a MEDIA (WFT 24%)')

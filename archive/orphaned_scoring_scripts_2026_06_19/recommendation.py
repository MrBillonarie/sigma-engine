#!/usr/bin/env python3
"""SIGMA — Tabla de recomendaciones operativas con reglas claras."""
import json
from pathlib import Path
from datetime import datetime

BASE   = Path('/opt/sigma')
MODELS = BASE / 'models'

SKIP = {'config.json','adaptive_params.json','walk_forward_v2.json',
        'current_params.json','regime_params.json','config_aggressive.json',
        'new_strategy.json','conservative.json'}

GRADES = [(0.70,'A+'),(0.55,'A'),(0.40,'B'),(0.25,'C')]
def grade(s):
    for t,g in GRADES:
        if s>=t: return g
    return 'D'

def score(m):
    t=m.get('trades',0); ty=m.get('trades_year',0)
    wr=m.get('wr',0); cagr=m.get('cagr',0)
    dd=m.get('dd',0); pf=m.get('pf',1)
    if t<10 or cagr<=0: return -9999
    if ty<=0 and t>0: ty=t*(365/600)
    if ty<3: return -9999
    if wr<=0 and cagr>0: wr=50
    return round(
        min(ty/12,1)*0.20 + min(cagr,60)/60*0.40 +
        max(wr/100-.5,0)/.20*0.20 +
        min(cagr/abs(dd) if dd<0 else 0,5)/5*.15 +
        min(pf,3)/3*.05, 4)

def recommend(m):
    """
    Reglas de recomendación para operar a 2x con 2 slots máx.
    Retorna: (decision, motivo)
    ACTIVAR / CONDICIONAL / NO_ACTIVAR
    """
    g    = m['grade']
    dd2x = m['dd'] * 2
    wr   = m['wr']
    t    = m['trades']
    ty   = m['trades_yr']

    # ── Rechazos automáticos ──────────────────────────────────────────────────
    if g == 'D':
        return 'NO_ACTIVAR', 'Grade D — sin edge suficiente'
    if t < 10:
        return 'NO_ACTIVAR', f'Solo {t} trades OOS — muestra insuficiente'
    if wr > 0 and wr < 42:
        return 'NO_ACTIVAR', f'WR {wr:.0f}% muy bajo — demasiados perdedores'
    if dd2x < -70:
        return 'NO_ACTIVAR', f'DD a 2x = {dd2x:.0f}% — riesgo liquidación'

    # ── Condicionales ────────────────────────────────────────────────────────
    if g == 'C':
        return 'CONDICIONAL', 'Grade C — solo si no hay A+/A disponible y régimen perfecto'
    if dd2x < -45:
        return 'CONDICIONAL', f'DD a 2x = {dd2x:.0f}% — usar solo en bull fuerte o bajar a 1x'
    if ty > 0 and ty < 6:
        return 'CONDICIONAL', f'Solo {ty:.0f} trades/año — señales raras, slippage real mayor'
    if wr > 0 and wr < 50:
        return 'CONDICIONAL', f'WR {wr:.0f}% — rentable pero depende de RR alto, monitorear'

    # ── Activar ──────────────────────────────────────────────────────────────
    return 'ACTIVAR', f'Grade {g} | WR {wr:.0f}% | DD2x {dd2x:.0f}% | {ty:.0f} trades/año'

def regime_ok(mtype, regime):
    if regime == 'BULL'  and mtype == 'short':   return False
    if regime == 'BEAR'  and mtype == 'long':    return False
    return True

# ─────────────────────────────────────────────────────────────────────────────
models_raw = []
for tf_dir in sorted(MODELS.iterdir()):
    if not tf_dir.is_dir() or tf_dir.name == 'archive': continue
    for jf in sorted(tf_dir.glob('*.json')):
        if jf.name in SKIP: continue
        try:
            d  = json.loads(jf.read_text(encoding='utf-8'))
            m  = d.get('metrics_oos', {})
            if not m or m.get('cagr', 0) <= 0: continue
            sym = d.get('symbol','').replace('/USDT','') or jf.stem.split('_')[0].upper()
            st  = d.get('strategy', jf.stem)
            is_short = any(x in st for x in ['short','breakdown'])
            is_adapt = 'adaptive' in st
            s  = score(m)
            models_raw.append({
                'sym': sym, 'tf': tf_dir.name, 'strategy': st,
                'cagr': m.get('cagr',0), 'dd': m.get('dd',0),
                'wr': m.get('wr',0), 'pf': m.get('pf',0),
                'trades': m.get('trades',0),
                'trades_yr': m.get('trades_year',0),
                'score': s, 'grade': grade(s),
                'type': 'adaptive' if is_adapt else ('short' if is_short else 'long'),
            })
        except: continue

models_raw.sort(key=lambda x: x['score'], reverse=True)

# Obtener régimen actual
try:
    import urllib.request, json as _j
    r = urllib.request.urlopen('http://localhost:8080/api/regime', timeout=3)
    regime_data = _j.loads(r.read())
    bull = sum(1 for v in regime_data.values() if v.get('regime')=='BULL')
    bear = sum(1 for v in regime_data.values() if v.get('regime')=='BEAR')
    rng  = sum(1 for v in regime_data.values() if v.get('regime')=='RANGE')
    regime_now = 'BEAR' if bear >= 3 else ('BULL' if bull >= 3 else 'RANGE')
    regime_detail = ' | '.join(f'{k}:{v.get("regime","?")}' for k,v in regime_data.items())
except:
    regime_now    = 'DESCONOCIDO'
    regime_detail = '(sin conexión a API)'

ICONS = {'ACTIVAR': '✅', 'CONDICIONAL': '⚠️ ', 'NO_ACTIVAR': '❌'}

print(f'\n{"="*105}')
print(f'  SIGMA — TABLA DE RECOMENDACIONES OPERATIVAS  |  2x leverage  |  2 slots máx')
print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
print(f'  Régimen actual: {regime_now}  ({regime_detail})')
print('='*105)

# ── TABLA PRINCIPAL ───────────────────────────────────────────────────────────
print(f'\n  {"Activo":<7} {"TF":<5} {"Strategy":<22} {"Tipo":<8} {"Gr":<3} '
      f'{"CAGR":>7} {"WR":>6} {"DD 2x":>7} {"Decisión":<12} Motivo')
print('  ' + '─'*100)

activar     = []
condicional = []
no_activar  = []

for m in models_raw:
    dec, motivo = recommend(m)
    reg_ok = regime_ok(m['type'], regime_now)
    dd2x   = m['dd'] * 2

    # Si el régimen no está ok, baja la decisión
    if dec == 'ACTIVAR' and not reg_ok:
        dec_show = '⚠️  ESPERAR'
        motivo_show = f'Régimen {regime_now} no compatible con {m["type"].upper()}'
    elif dec == 'ACTIVAR':
        dec_show = f'{ICONS[dec]} ACTIVAR'
        activar.append(m)
        motivo_show = motivo
    elif dec == 'CONDICIONAL':
        dec_show = f'{ICONS[dec]} CONDIC.'
        condicional.append(m)
        motivo_show = motivo
    else:
        dec_show = f'{ICONS[dec]} NO'
        no_activar.append(m)
        motivo_show = motivo

    print(f'  {m["sym"]:<7} {m["tf"]:<5} {m["strategy"]:<22} '
          f'{m["type"].upper():<8} {m["grade"]:<3} '
          f'{m["cagr"]:>+6.1f}% {m["wr"]:>5.0f}% {dd2x:>6.0f}%  '
          f'{dec_show:<14} {motivo_show}')

# ── SLOTS RECOMENDADOS HOY ────────────────────────────────────────────────────
print(f'\n\n  SLOTS PARA OPERAR HOY — Régimen: {regime_now}')
print('  ' + '─'*70)

activos_ok = [m for m in activar if regime_ok(m['type'], regime_now)]
seen = set()
slots = []
for m in activos_ok:
    if m['sym'] not in seen:
        slots.append(m)
        seen.add(m['sym'])
    if len(slots) == 2: break

if slots:
    total_cagr_2x = sum(m['cagr']*2 - 9.5 for m in slots) / len(slots)
    total_dd_2x   = sum(m['dd']*2 for m in slots) / len(slots) * 0.9
    for i, m in enumerate(slots, 1):
        c2 = round(m['cagr']*2 - 9.5, 1)
        d2 = round(m['dd']*2, 1)
        print(f'  Slot {i}: {m["sym"]} {m["tf"]} {m["strategy"]} [{m["grade"]}]'
              f'  →  2x CAGR: {c2:+.1f}%   DD: {d2:.1f}%')
    print(f'\n  Portafolio combinado:  CAGR ~{total_cagr_2x:+.1f}%   DD ~{total_dd_2x:.1f}%')
    y1 = round(1000*(1+total_cagr_2x/100))
    y2 = round(1000*(1+total_cagr_2x/100)**2)
    y3 = round(1000*(1+total_cagr_2x/100)**3)
    print(f'  Proyección 1000 USDT:  1 año → {y1:,}  |  2 años → {y2:,}  |  3 años → {y3:,} USDT')
else:
    print(f'  ⚠️  Sin modelos ACTIVAR compatibles con régimen {regime_now}')
    print(f'  → Esperar cambio de régimen o que el pipeline encuentre modelo short validado.')
    adapt = [m for m in activar if m['type']=='adaptive']
    if adapt:
        m = adapt[0]
        c2 = round(m['cagr']*2, 1)
        print(f'  → Único disponible: {m["sym"]} {m["tf"]} {m["strategy"]} 2x CAGR: {c2:+.1f}%')

# ── RESUMEN ───────────────────────────────────────────────────────────────────
print(f'\n\n  RESUMEN')
print('  ' + '─'*50)
print(f'  ✅ ACTIVAR:      {len(activar):>2} modelos — listos para operar con 2x')
print(f'  ⚠️  CONDICIONAL: {len(condicional):>2} modelos — solo bajo condiciones específicas')
print(f'  ❌ NO ACTIVAR:  {len(no_activar):>2} modelos — descartar hasta nuevo modelo')
print(f'\n  REGLA DE ORO:')
print(f'  Solo entrar si: Grade A+ o A  +  DD2x < 40%  +  Régimen OK  +  Slot disponible')
print('='*105 + '\n')

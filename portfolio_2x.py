#!/usr/bin/env python3
"""
SIGMA — Hipótesis portafolio 2x, 2 slots máx, selección por score+régimen.
"""
import json
from pathlib import Path
from datetime import datetime

BASE   = Path('/opt/sigma')
MODELS = BASE / 'models'

FUNDING_ANNUAL = 9.5   # % anual promedio (posición base)
EXTRA_FUNDING  = 9.5   # % extra por capital prestado en 2x
LEVERAGE       = 2

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

def load_models():
    out = []
    for tf_dir in sorted(MODELS.iterdir()):
        if not tf_dir.is_dir() or tf_dir.name=='archive': continue
        for jf in sorted(tf_dir.glob('*.json')):
            if jf.name in SKIP: continue
            try:
                d = json.loads(jf.read_text(encoding='utf-8'))
                m = d.get('metrics_oos',{})
                if not m or m.get('cagr',0)<=0: continue
                sym = d.get('symbol','').replace('/USDT','') or jf.stem.split('_')[0].upper()
                st  = d.get('strategy', jf.stem)
                is_short = any(x in st for x in ['short','breakdown'])
                is_adapt = 'adaptive' in st
                s = score(m)
                if s<=0: continue
                out.append({
                    'sym': sym, 'tf': tf_dir.name, 'strategy': st,
                    'cagr': m.get('cagr',0), 'dd': m.get('dd',0),
                    'wr': m.get('wr',0), 'pf': m.get('pf',0),
                    'trades_yr': m.get('trades_year',0),
                    'score': s, 'grade': grade(s),
                    'type': 'adaptive' if is_adapt else ('short' if is_short else 'long'),
                })
            except: continue
    return sorted(out, key=lambda x: x['score'], reverse=True)

def cagr_2x(cagr_base, dd_base, mtype):
    extra = EXTRA_FUNDING if mtype=='long' else 0   # shorts reciben funding
    c = round(cagr_base * LEVERAGE - extra, 1)
    d = round(dd_base   * LEVERAGE, 1)
    return c, d

def select_slots(models, regime, n_slots=2):
    """
    Selección de n_slots según régimen.
    BULL  → longs + adaptive
    BEAR  → shorts + adaptive
    RANGE → todos, preferir adaptive primero
    """
    eligible = []
    for m in models:
        if regime == 'BULL'  and m['type'] == 'short':  continue
        if regime == 'BEAR'  and m['type'] == 'long':   continue
        eligible.append(m)

    # Evitar 2 posiciones del mismo activo
    selected, seen_sym = [], set()
    for m in eligible:
        if m['sym'] in seen_sym: continue
        selected.append(m)
        seen_sym.add(m['sym'])
        if len(selected) == n_slots: break
    return selected

def project_portfolio(slots, leverage=2):
    if not slots: return 0, 0, 0, 0, 0
    cagrs, dds = [], []
    for m in slots:
        c, d = cagr_2x(m['cagr'], m['dd'], m['type'])
        cagrs.append(c); dds.append(d)
    # Capital dividido en partes iguales por slot
    weight  = 1.0 / len(slots)
    c_port  = round(sum(cagrs) * weight, 1)
    # DD conjunto con correlación alta → no se diversifica mucho
    d_port  = round(sum(dds) * weight * 0.90, 1)
    cap1 = round(1000 * (1 + c_port/100))
    cap2 = round(1000 * (1 + c_port/100)**2)
    cap3 = round(1000 * (1 + c_port/100)**3)
    return c_port, d_port, cap1, cap2, cap3

# ─────────────────────────────────────────────────────────────────────────────
models = load_models()

print(f'\n{"="*90}')
print(f'  SIGMA — HIPÓTESIS PORTAFOLIO 2x | 2 SLOTS MÁX | SELECCIÓN POR SCORE+RÉGIMEN')
print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")} | {len(models)} modelos válidos')
print(f'  Supuestos: 2x leverage | funding anual {FUNDING_ANNUAL}% base + {EXTRA_FUNDING}% extra (capital prestado)')
print('='*90)

# ── TABLA 1: Todos los modelos con 1x vs 2x ──────────────────────────────────
print(f'\n  {"#":<3} {"Activo":<7} {"TF":<5} {"Strategy":<22} {"Tipo":<9} {"Gr":<3} '
      f'{"1x":>8} {"1x DD":>7} │ {"2x":>8} {"2x DD":>7}')
print('  ' + '─'*82)

for i, m in enumerate(models[:15], 1):
    c2, d2 = cagr_2x(m['cagr'], m['dd'], m['type'])
    tip = {'long':'LONG','short':'SHORT','adaptive':'ADAPT'}[m['type']]
    warn = ' ⚠' if d2 < -70 else ''
    print(f'  {i:<3} {m["sym"]:<7} {m["tf"]:<5} {m["strategy"]:<22} {tip:<9} {m["grade"]:<3} '
          f'{m["cagr"]:>+7.1f}% {m["dd"]:>6.1f}% │ {c2:>+7.1f}% {d2:>6.1f}%{warn}')

# ── TABLA 2: Selección por régimen ───────────────────────────────────────────
print(f'\n\n  SLOTS SELECCIONADOS POR RÉGIMEN (score más alto, sin repetir activo)')
print(f'  {"Régimen":<8} {"Slot 1":<30} {"Slot 2":<30} {"CAGR 2x":>9} {"DD 2x":>8} '
      f'{"1 año":>10} {"2 años":>10} {"3 años":>10}')
print('  ' + '─'*108)

for regime in ['BULL', 'RANGE', 'BEAR']:
    slots = select_slots(models, regime, n_slots=2)
    c, d, y1, y2, y3 = project_portfolio(slots, leverage=2)
    def slot_str(m):
        return f'{m["sym"]} {m["tf"]} {m["strategy"][:12]} [{m["grade"]}]'
    s1 = slot_str(slots[0]) if len(slots) > 0 else '—'
    s2 = slot_str(slots[1]) if len(slots) > 1 else '(sin modelo)'
    warn = ' ⚠' if d < -60 else ''
    print(f'  {regime:<8} {s1:<30} {s2:<30} {c:>+8.1f}% {d:>7.1f}%{warn} '
          f'{y1:>8,} USDT {y2:>8,} USDT {y3:>8,} USDT')

# ── TABLA 3: Regla de selección explicada ────────────────────────────────────
print(f'\n\n  REGLA DE SELECCIÓN — cómo elegir cuál de las 5 estrategias entra')
print('  ' + '─'*70)
print(f'''
  1. FILTRO RÉGIMEN (obligatorio)
     BULL  → solo LONG + ADAPTIVE
     BEAR  → solo SHORT + ADAPTIVE
     RANGE → todos, ADAPTIVE tiene prioridad

  2. ORDEN POR SCORE (ponderado)
     Score = 40% CAGR + 20% WR + 20% frecuencia + 15% Calmar + 5% PF
     El score más alto entra primero al slot disponible.

  3. DIVERSIFICACIÓN MÍNIMA
     Si los 2 mejores son el mismo activo (ej: SOL 4H y SOL 1H)
     → tomar el 1ro y bajar al 3ro de la lista.

  4. SLOT LIBERADO
     Cuando cierra un trade → el slot se abre.
     Entra el siguiente en la cola por score (ya filtrado por régimen).

  EJEMPLO HOY (régimen BEAR en 4/5 activos):
''')

slots_hoy = select_slots(models, 'BEAR', n_slots=2)
if slots_hoy:
    for i, m in enumerate(slots_hoy, 1):
        c2, d2 = cagr_2x(m['cagr'], m['dd'], m['type'])
        print(f'  Slot {i}: {m["sym"]} {m["tf"]} {m["strategy"]} '
              f'[{m["grade"]}] → 2x CAGR proyectado: {c2:+.1f}%  DD: {d2:.1f}%')
else:
    print('  No hay modelos SHORT o ADAPTIVE validados todavía.')
    print('  → En régimen BEAR actual el sistema ESPERA (no opera) hasta tener modelo short.')
    print('  → Única excepción: SOL 15m regime_adaptive opera en cualquier régimen.')
    # Mostrar adaptive disponible
    adaptives = [m for m in models if m['type']=='adaptive']
    for m in adaptives[:2]:
        c2, d2 = cagr_2x(m['cagr'], m['dd'], m['type'])
        print(f'    → {m["sym"]} {m["tf"]} {m["strategy"]} [{m["grade"]}] 2x: {c2:+.1f}% DD:{d2:.1f}%')

print(f'\n  RESUMEN EJECUTIVO 2x:')
c_bull, d_bull, y1b, y2b, y3b = project_portfolio(select_slots(models,'BULL'), 2)
c_range,d_range,y1r,y2r,y3r  = project_portfolio(select_slots(models,'RANGE'),2)
c_bear, d_bear, y1br,y2br,y3br= project_portfolio(select_slots(models,'BEAR'), 2)
print(f'  BULL  2x: {c_bull:+.1f}% CAGR | 3 años → {y3b:,} USDT por cada 1,000 invertidos')
print(f'  RANGE 2x: {c_range:+.1f}% CAGR | 3 años → {y3r:,} USDT por cada 1,000 invertidos')
print(f'  BEAR  2x: {c_bear:+.1f}% CAGR | 3 años → {y3br:,} USDT por cada 1,000 invertidos')
print('='*90 + '\n')

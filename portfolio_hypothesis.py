#!/usr/bin/env python3
"""
SIGMA Portfolio Hypothesis — simulación de apalancamiento + portafolio 2 trades máx.
Lee los modelos actuales y proyecta retornos bajo distintos escenarios.
"""
import json, sys
from pathlib import Path

BASE   = Path(__file__).parent
MODELS = BASE / 'models'

# Funding rate anual promedio histórico BTC/ETH/alts (en % por posición)
FUNDING_ANNUAL_PCT = 9.5   # 0.0087% cada 8h × 3 × 365
COMMISSION_RT      = 0.12  # 0.06% × 2 lados (comisión + slippage)

SKIP = {'config.json','adaptive_params.json','walk_forward_v2.json',
        'current_params.json','regime_params.json','config_aggressive.json',
        'new_strategy.json','conservative.json','adaptive_params.json'}

GRADES = [(0.70,'A+'),(0.55,'A'),(0.40,'B'),(0.25,'C'),(0,'D')]
def grade(s):
    for t,g in GRADES:
        if s>=t: return g
    return '—'

def score(m):
    if not m: return -9999
    t  = m.get('trades',0); ty = m.get('trades_year',0)
    wr = m.get('wr',0); cagr=m.get('cagr',0)
    dd = m.get('dd',0); pf=m.get('pf',1)
    if t<10 or cagr<=0: return -9999
    if ty<=0 and t>0: ty=t*(365/600)
    if ty<3: return -9999
    if wr<=0 and cagr>0: wr=50
    return round(
        min(ty/12,1)*0.20 + min(cagr,60)/60*0.40 +
        max(wr/100-0.5,0)/0.20*0.20 +
        min(cagr/abs(dd) if dd<0 else 0,5)/5*0.15 +
        min(pf,3)/3*0.05, 4)

def cagr_leveraged(cagr_base, dd_base, leverage, direction='long'):
    """
    Proyecta CAGR y DD con apalancamiento.
    - CAGR escala linealmente
    - Extra funding por capital prestado (solo para longs en bull; shorts reciben)
    - DD se amplifica
    - Liquidación teórica si DD × leverage > 90%
    """
    # Funding extra por capital prestado = funding_rate × (leverage - 1)
    # Longs pagan, shorts reciben → promedio conservador: longs pagan, shorts neutro
    extra_funding = FUNDING_ANNUAL_PCT * (leverage - 1) if direction == 'long' else 0
    cagr_lev = cagr_base * leverage - extra_funding
    dd_lev   = dd_base * leverage
    ruin     = dd_lev < -85  # riesgo de liquidación
    return round(cagr_lev, 1), round(dd_lev, 1), ruin


def load_models():
    models = []
    for tf_dir in sorted(MODELS.iterdir()):
        if not tf_dir.is_dir() or tf_dir.name == 'archive': continue
        for jf in sorted(tf_dir.glob('*.json')):
            if jf.name in SKIP: continue
            try:
                d = json.loads(jf.read_text(encoding='utf-8'))
                m = d.get('metrics_oos', {})
                if not m or m.get('cagr', 0) <= 0: continue
                sym = d.get('symbol','').replace('/USDT','') or jf.stem.split('_')[0].upper()
                s   = score(m)
                if s <= 0: continue
                models.append({
                    'sym':      sym,
                    'tf':       tf_dir.name,
                    'strategy': d.get('strategy', jf.stem),
                    'cagr':     m.get('cagr', 0),
                    'dd':       m.get('dd', 0),
                    'wr':       m.get('wr', 0),
                    'pf':       m.get('pf', 0),
                    'trades_yr':m.get('trades_year', 0),
                    'score':    s,
                    'grade':    grade(s),
                    'direction': 'short' if any(x in d.get('strategy','') for x in ['short','breakdown']) else 'long',
                })
            except: continue
    return sorted(models, key=lambda x: x['score'], reverse=True)


def print_table(models):
    leverages = [1, 2, 3, 5]

    print(f'\n{"="*100}')
    print(f'  SIGMA PORTFOLIO HYPOTHESIS — {len(models)} modelos válidos')
    print(f'  Supuestos: funding anual {FUNDING_ANNUAL_PCT}% (longs), slippage+comisión {COMMISSION_RT}% round-trip')
    print('='*100)

    # ── TABLA 1: Por modelo con leverage ─────────────────────────────────────
    print(f'\n  {"#":<3} {"Activo":<8} {"TF":<5} {"Strategy":<20} {"Gr":<3} '
          f'{"1x CAGR":>8} {"1x DD":>7} | '
          f'{"2x CAGR":>8} {"2x DD":>7} | '
          f'{"3x CAGR":>8} {"3x DD":>7} | '
          f'{"5x CAGR":>8} {"5x DD":>7} {"Liq?":>5}')
    print('  ' + '-'*98)

    for i, m in enumerate(models[:15], 1):
        row = f'  {i:<3} {m["sym"]:<8} {m["tf"]:<5} {m["strategy"]:<20} {m["grade"]:<3} '
        row += f'{m["cagr"]:>+7.1f}% {m["dd"]:>6.1f}% | '
        for lev in [2, 3, 5]:
            c_l, d_l, ruin = cagr_leveraged(m['cagr'], m['dd'], lev, m['direction'])
            liq = '⚠' if ruin else ''
            if lev == 5:
                row += f'{c_l:>+7.1f}% {d_l:>6.1f}% {liq:>5}'
            else:
                row += f'{c_l:>+7.1f}% {d_l:>6.1f}% | '
        print(row)

    # ── TABLA 2: Portafolio 2 trades máx ────────────────────────────────────
    print(f'\n\n  PORTAFOLIO — máximo 2 trades simultáneos')
    print(f'  Lógica: 50% capital por posición | correlación assets ~0.80 (alta)')
    print(f'  Capital: 1000 USDT (referencia)')
    print()
    print(f'  {"Escenario":<35} {"CAGR portf":>11} {"DD portf":>10} {"Capital 1y":>12} {"Capital 2y":>12} {"Capital 3y":>12}')
    print('  ' + '-'*95)

    top2 = models[:2]
    if len(top2) < 2:
        print('  (menos de 2 modelos válidos)')
        return

    m1, m2 = top2[0], top2[1]

    for lev in leverages:
        c1, d1, _ = cagr_leveraged(m1['cagr'], m1['dd'], lev, m1['direction'])
        c2, d2, _ = cagr_leveraged(m2['cagr'], m2['dd'], lev, m2['direction'])

        # Portafolio: 50/50, compounding
        # CAGR portfolio = prom simple de los dos (con correlación alta)
        # DD portfolio: con correlación 0.80, DD_port ≈ DD_avg × 0.9
        cagr_port = round((c1 + c2) / 2, 1)
        dd_port   = round((d1 + d2) / 2 * 0.90, 1)

        # Capital proyectado (compounding)
        cap_1y = round(1000 * (1 + cagr_port/100) ** 1)
        cap_2y = round(1000 * (1 + cagr_port/100) ** 2)
        cap_3y = round(1000 * (1 + cagr_port/100) ** 3)
        ruin   = dd_port < -85

        name = (f'{m1["sym"]}+{m2["sym"]} {lev}x'
                f'{" [⚠ LIQIDACIÓN]" if ruin else ""}')
        print(f'  {name:<35} {cagr_port:>+10.1f}% {dd_port:>9.1f}%'
              f'  {cap_1y:>10,} USDT  {cap_2y:>10,} USDT  {cap_3y:>10,} USDT')

    # Sin apalancamiento pero con más modelos
    print()
    print(f'  {"--- variando nº de mejores modelos (sin apalancamiento) ---":<70}')
    for n in [1, 2, 3, 5]:
        top_n = models[:n]
        if len(top_n) < n: continue
        avg_cagr = sum(m['cagr'] for m in top_n) / n
        avg_dd   = sum(m['dd']   for m in top_n) / n * (0.85 if n > 1 else 1.0)
        # Con 2 max simultáneos: si hay 5 modelos pero solo 2 activos a la vez
        # el capital se divide entre los activos simultáneos (no entre todos)
        # Solo los 2 mejores están activos en promedio
        effective_cagr = avg_cagr if n <= 2 else (avg_cagr * n / 2)  # escala por slots
        cap_1y = round(1000 * (1 + effective_cagr/100))
        cap_2y = round(1000 * (1 + effective_cagr/100) ** 2)
        cap_3y = round(1000 * (1 + effective_cagr/100) ** 3)
        syms = '+'.join(m['sym'] for m in top_n)
        name = f'Top {n} modelos 1x ({syms})'
        print(f'  {name:<35} {effective_cagr:>+10.1f}% {avg_dd:>9.1f}%'
              f'  {cap_1y:>10,} USDT  {cap_2y:>10,} USDT  {cap_3y:>10,} USDT')

    # ── Nota de riesgo ───────────────────────────────────────────────────────
    print(f'\n  NOTAS IMPORTANTES:')
    print(f'  • CAGR proyectado = CAGR_base × leverage − funding_anual × (leverage−1)')
    print(f'  • Funding asumido: {FUNDING_ANNUAL_PCT}% anual (promedio histórico BTC). En bull puede ser 3x más.')
    print(f'  • DD con 3x-5x puede superar el margin → liquidación. VER columna Liq.')
    print(f'  • Correlación alta (0.80) entre activos: 2 posiciones pueden caer juntas.')
    print(f'  • Recomendación: 2x máximo. 3x solo con modelos grado A+ y DD < −15%.')
    print('='*100 + '\n')


if __name__ == '__main__':
    models = load_models()
    if not models:
        # Intentar con path del VPS
        MODELS = Path('/opt/sigma/models')
        models = load_models()
    if not models:
        print('Sin modelos válidos encontrados.')
        sys.exit(1)
    print_table(models)

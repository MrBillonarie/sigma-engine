"""
SIGMA CORRELATION CHECK — Analiza solapamiento entre estrategias
Cuando corres 4H + 1H juntos, pueden señalar al mismo tiempo
creando concentracion de riesgo no visible en backtest individual.

Calcula:
  - % de trades que se solapan temporalmente
  - Correlacion entre equity curves
  - Capital en riesgo maximo simultaneo
  - Recomendacion de sizing para portfolio
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import numpy as np
import pandas as pd
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent.parent


def run_correlation_analysis():
    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.signals import get_signals
    from core.backtest import run_backtest, calc_metrics

    print('\n' + '='*65)
    print('  SIGMA CORRELATION CHECK — Analisis multi-estrategia')
    print('='*65)

    # Cargar datos comunes
    print('[DATA] Cargando...')
    df_4h = fetch_ohlcv(tf='4h', days=3200)
    df_1h = fetch_ohlcv(tf='1h', days=3200)
    df_1d = fetch_ohlcv(tf='1d', days=3200)
    df_4h_f = build_features(df_4h, {'1d': df_1d})
    df_1h_f = build_features(df_1h, {'4h': df_4h, '1d': df_1d})

    strategies = {}

    # 4H Aggressive
    path_4h = OUTPUT_DIR / 'models' / '4h' / 'best_validated.json'
    if path_4h.exists():
        with open(path_4h) as f: d = json.load(f)
        p = d.get('params', {})
        if p:
            sig, q = get_signals(df_4h_f, p)
            tr, eq = run_backtest(df_4h_f, sig, q, p)
            strategies['4H_Aggressive'] = {'signals': sig, 'equity': eq, 'params': p}
            print(f'  4H Aggressive: {(sig!=0).sum()} señales')

    # 1H Breakout
    path_1h = OUTPUT_DIR / 'models' / '1h' / 'best_bull_breakout.json'
    if path_1h.exists():
        with open(path_1h) as f: d = json.load(f)
        p = d.get('params', {})
        if p:
            try:
                sig, q = get_signals(df_1h_f, p)
                tr, eq = run_backtest(df_1h_f, sig, q, p)
                strategies['1H_Breakout'] = {'signals': sig, 'equity': eq, 'params': p}
                print(f'  1H Breakout: {(sig!=0).sum()} señales')
            except Exception as e:
                print(f'  1H Breakout: error {e}')

    if len(strategies) < 2:
        print('  Menos de 2 estrategias disponibles para comparar')
        return

    names = list(strategies.keys())
    print(f'\n  Comparando: {" vs ".join(names)}')

    # Calcular correlacion de equity curves
    equities = {}
    for name, s in strategies.items():
        eq = s['equity']
        # Resamplear a frecuencia diaria para comparar
        eq_daily = eq.resample('D').last().ffill()
        equities[name] = eq_daily

    # Alinear fechas
    common_idx = equities[names[0]].index.intersection(equities[names[1]].index)
    if len(common_idx) > 30:
        eq1 = equities[names[0]].reindex(common_idx)
        eq2 = equities[names[1]].reindex(common_idx)

        ret1 = eq1.pct_change().dropna()
        ret2 = eq2.pct_change().dropna()

        if len(ret1) > 0 and len(ret2) > 0:
            corr = ret1.corr(ret2)
            print(f'\n  Correlacion de retornos diarios: {corr:.3f}')
            if abs(corr) < 0.3:
                print(f'  -> BAJA correlacion: excelente diversificacion')
            elif abs(corr) < 0.6:
                print(f'  -> MEDIA correlacion: diversificacion moderada')
            else:
                print(f'  -> ALTA correlacion: poco beneficio de diversificacion')

    # Solapamiento de señales (trades activos al mismo tiempo)
    sig_4h = strategies[names[0]]['signals']
    sig_1h = strategies[names[1]]['signals']

    # Resamplear 4H a 1H para comparar
    sig_4h_1h = sig_4h.resample('H').last().reindex(sig_1h.index, method='ffill').fillna(0)

    active_4h = sig_4h_1h != 0
    active_1h = sig_1h != 0
    both_active = active_4h & active_1h

    pct_overlap = both_active.sum() / max(active_1h.sum(), 1) * 100

    print(f'\n  Solapamiento temporal de señales:')
    print(f'  {names[0]}: {active_4h.sum()} horas activo')
    print(f'  {names[1]}: {active_1h.sum()} horas activo')
    print(f'  Ambos activos: {both_active.sum()} horas ({pct_overlap:.1f}% del tiempo)')

    if pct_overlap > 30:
        print(f'  -> ALTO solapamiento: reducir size de ambas cuando coincidan')
        overlap_action = 'Cuando ambas estrategias esten activas simultaneamente, reducir size de cada una al 60%'
    elif pct_overlap > 15:
        print(f'  -> MODERADO solapamiento: acceptable')
        overlap_action = 'Solapamiento moderado. Reducir size a 80% cuando coincidan.'
    else:
        print(f'  -> BAJO solapamiento: excelente, pueden correr independientemente')
        overlap_action = 'Bajo solapamiento. Pueden correr con size completo independientemente.'

    # Calculo de sizing optimo para portfolio
    risk_4h = strategies[names[0]]['params'].get('risk_pct', 3.3)
    risk_1h = strategies[names[1]]['params'].get('risk_pct', 3.3)

    max_simultaneous_risk = risk_4h + risk_1h
    print(f'\n  Sizing del portfolio:')
    print(f'  Risk {names[0]}: {risk_4h:.1f}% por trade')
    print(f'  Risk {names[1]}: {risk_1h:.1f}% por trade')
    print(f'  Riesgo max simultaneo: {max_simultaneous_risk:.1f}%')

    if max_simultaneous_risk > 10:
        print(f'  -> REDUCIR: riesgo total demasiado alto')
        recommended_4h = round(risk_4h * 8 / max_simultaneous_risk, 1)
        recommended_1h = round(risk_1h * 8 / max_simultaneous_risk, 1)
    else:
        recommended_4h = risk_4h
        recommended_1h = risk_1h

    print(f'\n  Sizing RECOMENDADO para portfolio:')
    print(f'  {names[0]}: {recommended_4h:.1f}% por trade')
    print(f'  {names[1]}: {recommended_1h:.1f}% por trade')
    print(f'  Max riesgo combinado: {recommended_4h + recommended_1h:.1f}%')
    print(f'  {overlap_action}')

    # Guardar reporte
    result = {
        'timestamp': str(pd.Timestamp.now()),
        'strategies': names,
        'overlap_pct': round(pct_overlap, 1),
        'overlap_action': overlap_action,
        'sizing': {
            names[0]: recommended_4h,
            names[1]: recommended_1h,
        },
        'max_combined_risk': round(recommended_4h + recommended_1h, 1),
    }
    out = OUTPUT_DIR / 'results' / 'reports' / 'correlation_report.json'
    with open(out, 'w') as f:
        json.dump(result, f, indent=2, default=str)
    print(f'\n  [SAVED] {out.name}')
    print('='*65)
    return result


if __name__ == '__main__':
    run_correlation_analysis()

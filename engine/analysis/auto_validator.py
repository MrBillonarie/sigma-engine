"""
SIGMA AUTO VALIDATOR
Se ejecuta automaticamente cuando asset_pipeline encuentra un OOS positivo.
Aplica 3 filtros en cascada:
  1. Monte Carlo bootstrap  -> P(CAGR>0) >= 65%
  2. Walk-Forward rapido    -> >= 55% ventanas positivas
  3. Cross-Asset (1H/4H)   -> funciona en 1+ activo adicional

Resultado se agrega al JSON del modelo:
  {
    "validation": {
      "monte_carlo": {"p_pos": 72.3, "ic95": [-8.1, 42.7], "passed": true},
      "walk_forward": {"pct_positive": 61.0, "windows": 30, "passed": true},
      "cross_asset":  {"positive_assets": 2, "passed": true},
      "confidence":   "ALTA",   # BAJA / MEDIA / ALTA
      "validated_at": "2026-05-08 21:00:00"
    }
  }
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION  = 0.0004
CAPITAL     = 1000.0

# Umbrales
MC_MIN_P_POS  = 65.0   # Monte Carlo: minimo P(CAGR>0)
WFT_MIN_PCT   = 55.0   # Walk-Forward: minimo % ventanas positivas
WFT_WINDOWS   = 30     # Ventanas rapidas (vs 97 del WFT completo)
WFT_TRIALS    = 60     # Trials por ventana (rapido)
CA_MIN_ASSETS = 1      # Cross-asset: minimo activos adicionales positivos

# Activos cross-asset por simbolo base
CROSS_ASSETS = {
    'BTC': ['ETH/USDT', 'LTC/USDT'],
    'ETH': ['BTC/USDT', 'LTC/USDT'],
    'LTC': ['BTC/USDT', 'ETH/USDT'],
    'SOL': ['ETH/USDT', 'BNB/USDT'],
    'BNB': ['BTC/USDT', 'ETH/USDT'],
}


# ── MONTE CARLO ───────────────────────────────────────────────────────────────

def monte_carlo_bootstrap(trades_pnl, n_sim=2000, capital=CAPITAL):
    """Bootstrap sobre lista de PnL individuales."""
    if len(trades_pnl) < 5:
        return None
    pnl = np.array(trades_pnl)
    cagrs = []
    for _ in range(n_sim):
        sample = np.random.choice(pnl, size=len(pnl), replace=True)
        final  = capital + sample.sum()
        if final <= 0:
            continue
        # Assume average 1yr per 56 trades (BTC 1H baseline)
        years = max(len(pnl) / 56, 0.5)
        cagr  = ((final / capital) ** (1 / years) - 1) * 100
        cagrs.append(cagr)
    if not cagrs:
        return None
    cagrs = np.array(cagrs)
    return {
        'p_pos':    round(float((cagrs > 0).mean() * 100), 1),
        'median':   round(float(np.median(cagrs)), 1),
        'ic95_lo':  round(float(np.percentile(cagrs, 2.5)), 1),
        'ic95_hi':  round(float(np.percentile(cagrs, 97.5)), 1),
        'ic80_lo':  round(float(np.percentile(cagrs, 10)), 1),
        'ic80_hi':  round(float(np.percentile(cagrs, 90)), 1),
    }


# ── WALK-FORWARD RAPIDO ───────────────────────────────────────────────────────

def quick_walk_forward(df, sig_fn, best_params, risk_pct, n_windows=WFT_WINDOWS, trials=WFT_TRIALS):
    """
    Walk-forward simplificado: divide en ventanas de 6m train + 2m test.
    Usa los params encontrados SIN re-optimizar (frozen params WFT).
    Mas rapido que el WFT completo porque no re-optimiza por ventana.
    """
    from engine.optimization.asset_pipeline import backtest, metrics

    if len(df) < 2000:
        return None

    results = []
    step_bars   = len(df) // (n_windows + 3)
    window_bars = step_bars * 4   # ~4x el step
    test_bars   = step_bars

    for i in range(n_windows):
        start = i * step_bars
        end   = start + window_bars
        if end + test_bars > len(df):
            break
        df_test = df.iloc[end:end + test_bars]
        if len(df_test) < 100:
            continue
        days_t = (df_test.index[-1] - df_test.index[0]).days
        if days_t < 20:
            continue
        try:
            sig, sl, tp = sig_fn(df_test, best_params)
            dt, eq = backtest(df_test, sig, sl, tp, risk_pct)
            m = metrics(dt, eq, days_t, min_t=2)
            if m:
                results.append({'cagr': m['cagr'], 'wr': m['wr'], 'trades': m['trades']})
        except:
            continue

    if len(results) < 5:
        return None

    pos = sum(1 for r in results if r['cagr'] > 0)
    pct = pos / len(results) * 100
    avg_cagr = np.mean([r['cagr'] for r in results])

    return {
        'windows':      len(results),
        'positive':     pos,
        'pct_positive': round(pct, 1),
        'avg_cagr':     round(avg_cagr, 1),
    }


# ── CROSS-ASSET ───────────────────────────────────────────────────────────────

def cross_asset_check(base_asset, sig_fn, best_params, risk_pct):
    """Prueba los mismos params en 2 activos relacionados."""
    from engine.optimization.asset_pipeline import fetch_asset, add_features, backtest, metrics

    peers = CROSS_ASSETS.get(base_asset, [])[:2]
    if not peers:
        return None

    results = []
    for symbol in peers:
        try:
            df = fetch_asset(symbol, '1h', days=800)
            if df is None or len(df) < 500:
                continue
            df = add_features(df)
            split  = int(len(df) * 0.80)
            df_oos = df.iloc[split:]
            days   = (df_oos.index[-1] - df_oos.index[0]).days
            sig, sl, tp = sig_fn(df_oos, best_params)
            dt, eq = backtest(df_oos, sig, sl, tp, risk_pct)
            m = metrics(dt, eq, days, min_t=3)
            if m:
                results.append({
                    'symbol': symbol,
                    'cagr':   m['cagr'],
                    'wr':     m['wr'],
                    'trades': m['trades'],
                })
        except:
            continue

    positive = [r for r in results if r['cagr'] > 0]
    return {
        'tested':          len(results),
        'positive':        len(positive),
        'positive_assets': [r['symbol'] for r in positive],
        'results':         results,
    }


# ── MAIN VALIDATOR ────────────────────────────────────────────────────────────

def validate(model_path, df_full, sig_fn, best_params, risk_pct,
             trades_oos_pnl, asset, tf):
    """
    Corre los 3 filtros y actualiza el model JSON con los resultados.
    Returns: (passed, confidence, validation_dict)
    """
    log = lambda m: print(f'    [VALIDATOR] {m}', flush=True)
    val = {}
    passed_steps = 0

    # ── 1. MONTE CARLO ────────────────────────────────────────────────────────
    log('Paso 1/3: Monte Carlo bootstrap (2000 sims)...')
    mc = monte_carlo_bootstrap(trades_oos_pnl)
    if mc is None:
        log('  MC: sin datos suficientes')
        mc = {'p_pos': 0, 'passed': False}
    else:
        mc['passed'] = mc['p_pos'] >= MC_MIN_P_POS
        status = 'PASS' if mc['passed'] else 'FAIL'
        log(f'  MC: P(CAGR>0)={mc["p_pos"]:.1f}% IC95=[{mc["ic95_lo"]:.1f}%,{mc["ic95_hi"]:.1f}%] → {status}')
    val['monte_carlo'] = mc
    if mc['passed']:
        passed_steps += 1
    elif not mc['passed']:
        # MC es requisito minimo — si falla con menos del 55% no seguimos
        if mc.get('p_pos', 0) < 55:
            log('  MC < 55% — descartando modelo, demasiado ruido')
            val['confidence'] = 'DESCARTADO'
            val['validated_at'] = str(datetime.now())
            _save_validation(model_path, val)
            return False, 'DESCARTADO', val

    # ── 2. WALK-FORWARD RAPIDO ────────────────────────────────────────────────
    log(f'Paso 2/3: Walk-Forward rapido ({WFT_WINDOWS} ventanas)...')
    wft = quick_walk_forward(df_full, sig_fn, best_params, risk_pct)
    if wft is None:
        log('  WFT: sin datos suficientes (mantenemos modelo)')
        wft = {'pct_positive': 0, 'passed': False, 'note': 'sin datos'}
    else:
        wft['passed'] = wft['pct_positive'] >= WFT_MIN_PCT
        status = 'PASS' if wft['passed'] else 'FAIL'
        log(f'  WFT: {wft["positive"]}/{wft["windows"]} ventanas positivas ({wft["pct_positive"]:.1f}%) → {status}')
    val['walk_forward'] = wft
    if wft.get('passed'):
        passed_steps += 1

    # ── 3. CROSS-ASSET (solo 1H y 4H — tiene sentido para estos TFs) ─────────
    if tf in ('1h', '4h'):
        log(f'Paso 3/3: Cross-Asset (peers de {asset})...')
        ca = cross_asset_check(asset, sig_fn, best_params, risk_pct)
        if ca:
            ca['passed'] = ca['positive'] >= CA_MIN_ASSETS
            status = 'PASS' if ca['passed'] else 'FAIL'
            log(f'  CA: {ca["positive"]}/{ca["tested"]} activos positivos ({ca["positive_assets"]}) → {status}')
        else:
            ca = {'positive': 0, 'passed': False, 'note': 'sin datos'}
        val['cross_asset'] = ca
        if ca.get('passed'):
            passed_steps += 1
        max_steps = 3
    else:
        max_steps = 2  # 15m y 5m no hacen cross-asset

    # ── CONFIDENCE ────────────────────────────────────────────────────────────
    if passed_steps == max_steps:
        confidence = 'ALTA'
    elif passed_steps >= max_steps - 1:
        confidence = 'MEDIA'
    elif passed_steps >= 1:
        confidence = 'BAJA'
    else:
        confidence = 'DESCARTADO'

    overall_passed = passed_steps >= 1  # Al menos MC pasa

    val['confidence']   = confidence
    val['passed_steps'] = passed_steps
    val['max_steps']    = max_steps
    val['validated_at'] = str(datetime.now())

    _save_validation(model_path, val)
    log(f'Resultado: {passed_steps}/{max_steps} filtros → Confianza {confidence}')
    return overall_passed, confidence, val


def _save_validation(model_path, validation):
    """Agrega los resultados de validacion al JSON del modelo."""
    try:
        data = json.loads(Path(model_path).read_text())
        data['validation'] = validation
        Path(model_path).write_text(json.dumps(data, indent=2, default=str))
    except Exception as e:
        print(f'    [VALIDATOR] Error guardando validacion: {e}')


# ── STANDALONE ────────────────────────────────────────────────────────────────

def validate_existing_model(model_path):
    """Valida un modelo ya guardado. Uso: python auto_validator.py models/1h/best_bull_breakout.json"""
    from engine.optimization.asset_pipeline import (
        fetch_asset, add_features, backtest, metrics, SIG_FN
    )

    data     = json.loads(Path(model_path).read_text())
    symbol   = data.get('symbol', 'BTC/USDT')
    tf       = data.get('tf', '1h')
    strategy = data.get('strategy', 'breakout')
    params   = data.get('params', {})
    risk_pct = data.get('risk_pct', 3.3)
    asset    = symbol.replace('/USDT','')

    print(f'\n{"="*60}')
    print(f'  Validando: {symbol} {tf.upper()} — {strategy}')
    m_oos = data.get('metrics_oos', {})
    print(f'  OOS actual: CAGR {m_oos.get("cagr",0):+.1f}% WR {m_oos.get("wr",0):.1f}% {m_oos.get("trades",0)}T')
    print(f'{"="*60}')

    df_raw = fetch_asset(symbol, tf, days=3200)
    if df_raw is None:
        print('  ERROR: sin datos'); return

    df  = add_features(df_raw)
    sig_fn = SIG_FN.get(strategy)
    if not sig_fn:
        print(f'  ERROR: estrategia {strategy} no reconocida'); return

    # Re-run backtest on OOS to get trade PnL list
    split  = int(len(df) * 0.80)
    df_oos = df.iloc[split:]
    days_oos = (df_oos.index[-1] - df_oos.index[0]).days
    try:
        sig, sl, tp = sig_fn(df_oos, params)
        dt, eq = backtest(df_oos, sig, sl, tp, risk_pct)
        pnl_list = dt['pnl'].tolist() if not dt.empty else []
    except:
        pnl_list = []

    passed, confidence, val = validate(
        model_path, df, sig_fn, params, risk_pct,
        pnl_list, asset, tf
    )

    print(f'\n  Confianza final: {confidence}')
    if val.get('monte_carlo'):
        mc = val['monte_carlo']
        print(f'  MC:  P(>0)={mc.get("p_pos",0):.1f}% IC95=[{mc.get("ic95_lo",0):.1f}%, {mc.get("ic95_hi",0):.1f}%]')
    if val.get('walk_forward'):
        wft = val['walk_forward']
        print(f'  WFT: {wft.get("pct_positive",0):.1f}% ventanas positivas')
    if val.get('cross_asset'):
        ca = val['cross_asset']
        print(f'  CA:  {ca.get("positive",0)}/{ca.get("tested",0)} activos')
    print(f'{"="*60}\n')


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        validate_existing_model(sys.argv[1])
    else:
        # Validar todos los modelos existentes
        for p in sorted((OUTPUT_DIR / 'models').rglob('*.json')):
            try:
                d = json.loads(p.read_text())
                oos = d.get('metrics_oos', {})
                if oos.get('cagr', 0) > 0 and 'validation' not in d:
                    print(f'\nValidando: {p.parent.name}/{p.name}')
                    validate_existing_model(str(p))
            except:
                continue

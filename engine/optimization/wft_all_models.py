#!/usr/bin/env python3
"""
SIGMA — Walk-Forward para todos los modelos guardados.
Corre WFT en cada modelo JSON y actualiza su confianza.

Uso:
  python engine/optimization/wft_all_models.py
  python engine/optimization/wft_all_models.py --tf 4h       (solo un TF)
  python engine/optimization/wft_all_models.py --symbol SOL   (solo un símbolo)
  python engine/optimization/wft_all_models.py --tf 1h --min_windows 3
  python engine/optimization/wft_all_models.py --dry_run      (sin guardar)
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

from pathlib import Path
from datetime import datetime

# ── PATHS ────────────────────────────────────────────────────────────────────
BASE       = Path(__file__).parent.parent.parent
MODELS_DIR = BASE / 'models'

# Archivos que NO son modelos de trading
SKIP_FILES = {'config.json', 'adaptive_params.json', 'walk_forward_v2.json',
              'current_params.json', 'conservative.json', 'cv_model.json'}

# ── IMPORTAR PIPELINE ────────────────────────────────────────────────────────
# Reusamos fetch_asset, add_features, backtest, metrics, score de asset_pipeline
try:
    from engine.optimization.asset_pipeline import (
        fetch_asset, add_features, backtest, metrics, score,
        SIG_FN, SIG_FN_SHORT, SIG_FN_ADAPTIVE, SIG_FN_1M,
        apply_regime_gate,
    )
    _pipeline_ok = True
except ImportError:
    try:
        from optimization.asset_pipeline import (
            fetch_asset, add_features, backtest, metrics, score,
            SIG_FN, SIG_FN_SHORT, SIG_FN_ADAPTIVE, SIG_FN_1M,
            apply_regime_gate,
        )
        _pipeline_ok = True
    except ImportError as e:
        print(f'  [ERROR] No se pudo importar asset_pipeline: {e}')
        print('  Ejecuta desde la raíz: python engine/optimization/wft_all_models.py')
        sys.exit(1)

# Mapa unificado de todas las funciones de señal
ALL_SIG_FN = {**SIG_FN_ADAPTIVE, **SIG_FN, **SIG_FN_SHORT, **SIG_FN_1M}

# ── NORMALIZACIÓN DE NOMBRES DE ESTRATEGIA ───────────────────────────────────
# Los JSONs antiguos guardan nombres en mayúsculas o con variantes
STRATEGY_ALIASES = {
    # uppercase → lowercase key en ALL_SIG_FN
    'breakout':       'breakout',
    'BREAKOUT':       'breakout',
    'pullback':       'pullback',
    'PULLBACK':       'pullback',
    'tma_bands':      'tma_bands',
    'TMA_BANDS':      'tma_bands',
    'momentum':       'momentum',
    'MOMENTUM':       'momentum',
    'mean_rev':       'mean_rev',
    'MEAN_REV':       'mean_rev',
    'mean_reversion': 'mean_rev',
    'breakdown':      'breakdown',
    'BREAKDOWN':      'breakdown',
    'pullback_short': 'pullback_short',
    'momentum_short': 'momentum_short',
    'regime_adaptive':'regime_adaptive',
    'REGIME_ADAPTIVE':'regime_adaptive',
    # 1m scalping
    'micro_momentum': 'micro_momentum',
    'session_open':   'session_open',
    'vwap_bounce':    'vwap_bounce',
    'range_scalp':    'range_scalp',
    'tick_follow':    'tick_follow',
}


def normalize_strategy(raw_name):
    """Normaliza el nombre de estrategia al key correcto de ALL_SIG_FN."""
    if not raw_name:
        return None
    # Intento directo
    if raw_name in ALL_SIG_FN:
        return raw_name
    # Alias conocido
    if raw_name in STRATEGY_ALIASES:
        return STRATEGY_ALIASES[raw_name]
    # lowercase
    low = raw_name.lower().replace(' ', '_').replace('-', '_')
    if low in ALL_SIG_FN:
        return low
    if low in STRATEGY_ALIASES:
        return STRATEGY_ALIASES[low]
    return None


# ── CARGA DE MODELOS ─────────────────────────────────────────────────────────

def infer_symbol_from_path(jf, data):
    """
    Intenta inferir el símbolo del modelo desde el JSON o el nombre del archivo.
    Retorna formato 'BTC/USDT' o None si no puede determinarlo.
    """
    # 1. Campo 'symbol' explícito en el JSON
    sym = data.get('symbol', '')
    if sym:
        # Normalizar a formato CCXT: "BTCUSDT" → "BTC/USDT"
        if '/' not in sym:
            if sym.endswith('USDT'):
                sym = sym[:-4] + '/USDT'
            elif sym.endswith('BUSD'):
                sym = sym[:-4] + '/BUSD'
        return sym

    # 2. Inferir desde el nombre del archivo: "eth_breakout.json" → "ETH/USDT"
    name = jf.stem  # sin extensión
    parts = name.split('_')
    if parts:
        base = parts[0].upper()
        # Excluir palabras genéricas que no son símbolos
        non_symbols = {'best', 'new', 'strategy', 'model', 'crypto', 'bull', 'current', 'cv'}
        if base.lower() not in non_symbols and len(base) >= 2:
            return f'{base}/USDT'

    # 3. Fallback: BTC/USDT (la mayoría de los modelos legacy son BTC)
    return 'BTC/USDT'


def load_models(tf_filter=None, symbol_filter=None):
    """
    Carga todos los modelos válidos desde MODELS_DIR.
    Filtra por TF y/o símbolo si se especifica.
    Aplica criterios de elegibilidad: cagr > 0 y trades >= 10 en OOS.
    Retorna lista de dicts con metadata del modelo.
    """
    models = []
    if not MODELS_DIR.exists():
        print(f'  [ERROR] No existe directorio de modelos: {MODELS_DIR}')
        return models

    for tf_dir in sorted(MODELS_DIR.iterdir()):
        if not tf_dir.is_dir():
            continue

        tf = tf_dir.name

        # Filtro de TF
        if tf_filter and tf.lower() != tf_filter.lower():
            continue

        # Solo TFs válidos
        if tf not in ('1m', '5m', '15m', '1h', '4h', '1d'):
            continue

        for jf in sorted(tf_dir.glob('*.json')):
            # Saltar archivos de configuración y WFT previos
            if jf.name in SKIP_FILES:
                continue
            if 'archive' in str(jf).lower():
                continue
            # Saltar resultados walk-forward
            if 'walk_forward' in jf.name:
                continue

            try:
                data = json.loads(jf.read_text(encoding='utf-8'))
            except Exception:
                continue

            # Necesitamos al menos estrategia y params
            strategy_raw = data.get('strategy', '')
            params       = data.get('params', {})
            if not strategy_raw or not params:
                continue

            strategy = normalize_strategy(strategy_raw)
            if strategy is None or strategy not in ALL_SIG_FN:
                # Estrategia desconocida — skip silencioso
                continue

            # Inferir símbolo
            symbol = infer_symbol_from_path(jf, data)

            # Filtro de símbolo
            if symbol_filter:
                sym_clean = symbol.replace('/USDT', '').replace('/', '').upper()
                if sym_clean.upper() != symbol_filter.upper():
                    continue

            # Criterios de elegibilidad OOS
            m_oos = data.get('metrics_oos', {})
            if not m_oos:
                continue

            # metrics pueden tener 'cagr' o 'wr' — ambas variantes
            oos_cagr   = m_oos.get('cagr', m_oos.get('cagr_pct', 0))
            oos_trades = m_oos.get('trades', 0)

            if oos_cagr <= 0:
                continue
            if oos_trades < 10:
                continue

            risk_pct = data.get('risk_pct', 3.0)

            models.append({
                'file':     jf,
                'tf':       tf,
                'symbol':   symbol,
                'strategy': strategy,
                'params':   params,
                'risk_pct': risk_pct,
                'oos_cagr': oos_cagr,
                'oos_trades': oos_trades,
                'data':     data,  # original completo para actualizar
            })

    return models


# ── WALK-FORWARD SIMPLE ──────────────────────────────────────────────────────

def run_wft_simple(df, strategy, params, risk_pct,
                   min_windows=3, bars_per_window=100,
                   is_frac=0.60):
    """
    Walk-Forward deslizante simple (sin re-optimización — valida los params fijos).

    Divide df en N ventanas. Por cada ventana:
      - IS (60%): periodo de entrenamiento — verificamos que hay señales
      - OOS (40%): periodo de validación — medimos CAGR

    Retorna dict con resultados o None si no hay suficientes datos.

    Args:
        df           : DataFrame con features calculados
        strategy     : nombre de la estrategia (key en ALL_SIG_FN)
        params       : dict de parámetros del modelo
        risk_pct     : porcentaje de riesgo por trade
        min_windows  : mínimo de ventanas requeridas
        bars_per_window: barras mínimas por ventana (IS+OOS combinadas)
        is_frac      : fracción IS dentro de cada ventana (0.60 = 60% IS, 40% OOS)

    Returns:
        dict con 'windows', 'oos_win_rate', 'n_windows', 'avg_cagr_oos',
             'avg_cagr_pos', 'verdict', 'error' (si falla)
    """
    sig_fn = ALL_SIG_FN.get(strategy)
    if sig_fn is None:
        return {'error': f'Estrategia desconocida: {strategy}'}

    n = len(df)
    if n < bars_per_window * min_windows:
        return {'error': f'Datos insuficientes: {n} barras < {bars_per_window*min_windows} requeridas'}

    # Calcular número de ventanas que caben
    n_windows = min(10, max(min_windows, n // bars_per_window))
    # Asegurar que tenemos el mínimo
    if n_windows < min_windows:
        return {'error': f'Solo {n_windows} ventanas posibles (mínimo {min_windows})'}

    window_size = n // n_windows
    is_size     = int(window_size * is_frac)
    oos_size    = window_size - is_size

    if is_size < 50 or oos_size < 30:
        return {'error': f'Ventanas demasiado pequeñas: IS={is_size}, OOS={oos_size}'}

    window_results = []

    for i in range(n_windows):
        start_idx = i * window_size
        end_is    = start_idx + is_size
        end_oos   = min(end_is + oos_size, n)

        if end_oos <= end_is:
            continue

        df_is  = df.iloc[start_idx:end_is]
        df_oos = df.iloc[end_is:end_oos]

        if len(df_is) < 50 or len(df_oos) < 20:
            continue

        days_is  = max((df_is.index[-1] - df_is.index[0]).days, 1)
        days_oos = max((df_oos.index[-1] - df_oos.index[0]).days, 1)

        # Generar señales OOS con los params del modelo (sin re-optimizar)
        try:
            sig_oos, sl_oos, tp_oos = sig_fn(df_oos, params)
        except Exception as e:
            window_results.append({
                'window': i + 1,
                'start':  df_oos.index[0].strftime('%Y-%m-%d'),
                'cagr':   None,
                'trades': 0,
                'status': 'ERROR',
                'note':   str(e)[:60],
            })
            continue

        n_signals = int((sig_oos != 0).sum())
        if n_signals == 0:
            window_results.append({
                'window': i + 1,
                'start':  df_oos.index[0].strftime('%Y-%m-%d'),
                'cagr':   None,
                'trades': 0,
                'status': 'NO_SIGNALS',
            })
            continue

        # Backtest OOS
        try:
            dt_oos, eq_oos = backtest(df_oos, sig_oos, sl_oos, tp_oos, risk_pct, use_kelly=False)
            m_oos = metrics(dt_oos, eq_oos, days_oos, min_t=3)
        except Exception as e:
            window_results.append({
                'window': i + 1,
                'start':  df_oos.index[0].strftime('%Y-%m-%d'),
                'cagr':   None,
                'trades': 0,
                'status': 'BT_ERROR',
                'note':   str(e)[:60],
            })
            continue

        if m_oos is None:
            window_results.append({
                'window': i + 1,
                'start':  df_oos.index[0].strftime('%Y-%m-%d'),
                'cagr':   None,
                'trades': n_signals,
                'status': 'INSUF_TRADES',
            })
            continue

        cagr    = m_oos.get('cagr', 0)
        wr      = m_oos.get('wr', 0)
        n_trades= m_oos.get('trades', 0)
        status  = 'PASS' if cagr > 0 else 'FAIL'

        window_results.append({
            'window': i + 1,
            'start':  df_oos.index[0].strftime('%Y-%m-%d'),
            'end':    df_oos.index[-1].strftime('%Y-%m-%d'),
            'cagr':   round(cagr, 2),
            'wr':     round(wr, 1),
            'trades': n_trades,
            'status': status,
        })

    # Solo contar ventanas con resultado válido
    valid_windows = [w for w in window_results if w.get('cagr') is not None]
    if len(valid_windows) < min_windows:
        return {
            'error': f'Solo {len(valid_windows)} ventanas válidas (mínimo {min_windows})',
            'windows': window_results,
        }

    positive = [w for w in valid_windows if w['cagr'] > 0]
    oos_win_rate = len(positive) / len(valid_windows) * 100

    cagr_all = [w['cagr'] for w in valid_windows]
    cagr_pos = [w['cagr'] for w in positive]

    return {
        'n_windows':       len(valid_windows),
        'positive_windows':len(positive),
        'oos_win_rate':    round(oos_win_rate, 1),
        'avg_cagr_oos':    round(float(np.mean(cagr_all)), 2),
        'avg_cagr_pos':    round(float(np.mean(cagr_pos)), 2) if cagr_pos else 0.0,
        'verdict':         'PASS' if oos_win_rate >= 60.0 else 'FAIL',
        'windows':         window_results,
    }


# ── ACTUALIZAR JSON DEL MODELO ────────────────────────────────────────────────

def update_model_json(jf, wft_result, dry_run=False):
    """
    Agrega/actualiza el campo 'wft' en el JSON del modelo con los resultados WFT.
    Si dry_run=True, solo imprime sin escribir.
    """
    try:
        data = json.loads(jf.read_text(encoding='utf-8'))
    except Exception as e:
        print(f'    [ERROR] No se pudo leer {jf.name}: {e}')
        return False

    data['wft'] = {
        'oos_win_rate':    wft_result.get('oos_win_rate', 0),
        'n_windows':       wft_result.get('n_windows', 0),
        'positive_windows':wft_result.get('positive_windows', 0),
        'avg_cagr_oos':    wft_result.get('avg_cagr_oos', 0),
        'avg_cagr_pos':    wft_result.get('avg_cagr_pos', 0),
        'verdict':         wft_result.get('verdict', 'UNKNOWN'),
        'date':            datetime.now().strftime('%Y-%m-%d %H:%M'),
    }

    if not dry_run:
        try:
            jf.write_text(json.dumps(data, indent=2, default=str), encoding='utf-8')
            return True
        except Exception as e:
            print(f'    [ERROR] No se pudo guardar {jf.name}: {e}')
            return False
    else:
        print(f'    [DRY RUN] Resultado WFT calculado (no guardado): '
              f'OOS_WR={data["wft"]["oos_win_rate"]:.1f}% verdict={data["wft"]["verdict"]}')
        return True


# ── REPORTE FINAL ─────────────────────────────────────────────────────────────

def print_report(results):
    """Imprime tabla resumen con todos los resultados WFT."""
    print(f'\n{"="*80}')
    print(f'  SIGMA WFT ALL MODELS — REPORTE FINAL')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'{"="*80}')

    if not results:
        print('  Sin resultados.')
        return

    # Separar por resultado
    passed  = [r for r in results if r.get('verdict') == 'PASS']
    failed  = [r for r in results if r.get('verdict') == 'FAIL']
    errors  = [r for r in results if r.get('verdict') == 'ERROR']
    skipped = [r for r in results if r.get('verdict') == 'SKIP']

    header = f'  {"Archivo":<28} {"TF":<5} {"Sym":<8} {"Strategy":<16} {"OOS_WR":>7} {"WndPos":>7} {"AvgCAGR":>8} {"Verdict":>8}'
    print(header)
    print('  ' + '-' * 92)

    # Primero PASS, luego FAIL, luego errores
    for r in sorted(results, key=lambda x: (
        0 if x.get('verdict') == 'PASS'
        else 1 if x.get('verdict') == 'FAIL'
        else 2
    )):
        verdict  = r.get('verdict', '—')
        icon     = {'PASS': 'OK', 'FAIL': 'XX', 'ERROR': 'ER', 'SKIP': '--'}.get(verdict, '??')
        fname    = r.get('file', '?')[:26]
        tf       = r.get('tf', '?')
        sym      = r.get('symbol', '?').replace('/USDT', '')[:7]
        strat    = r.get('strategy', '?')[:15]
        oos_wr   = f'{r["oos_win_rate"]:.1f}%' if r.get('oos_win_rate') is not None else '—'
        wnd_pos  = f'{r.get("positive_windows", 0)}/{r.get("n_windows", 0)}'
        avg_cagr = f'{r["avg_cagr_oos"]:+.1f}%' if r.get('avg_cagr_oos') is not None else '—'
        print(f'  {fname:<28} {tf:<5} {sym:<8} {strat:<16} {oos_wr:>7} {wnd_pos:>7} {avg_cagr:>8} [{icon}] {verdict}')

    print(f'\n{"="*80}')
    print(f'  RESUMEN: PASS={len(passed)} | FAIL={len(failed)} | ERROR={len(errors)} | SKIP={len(skipped)}')
    print(f'  Total modelos procesados: {len(results)}')

    if passed:
        avg_wr = np.mean([r['oos_win_rate'] for r in passed])
        print(f'  OOS Win Rate promedio (PASS): {avg_wr:.1f}%')

    print(f'{"="*80}\n')


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='SIGMA — Walk-Forward para todos los modelos guardados'
    )
    parser.add_argument('--tf',          default=None,
                        help='Filtrar por TF (ej: 1h, 4h, 15m)')
    parser.add_argument('--symbol',      default=None,
                        help='Filtrar por símbolo base (ej: SOL, ETH, BTC)')
    parser.add_argument('--min_windows', type=int, default=3,
                        help='Mínimo de ventanas WFT (default: 3)')
    parser.add_argument('--bars_window', type=int, default=100,
                        help='Barras mínimas por ventana (default: 100)')
    parser.add_argument('--pass_thr',    type=float, default=60.0,
                        help='Umbral %% ventanas positivas para PASS (default: 60)')
    parser.add_argument('--dry_run',     action='store_true',
                        help='Calcular pero NO actualizar los JSONs')
    args = parser.parse_args()

    print(f'\n{"="*65}')
    print(f'  SIGMA WALK-FORWARD — TODOS LOS MODELOS')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'  Umbral PASS: OOS win rate >= {args.pass_thr:.0f}%')
    if args.tf:
        print(f'  Filtro TF: {args.tf}')
    if args.symbol:
        print(f'  Filtro símbolo: {args.symbol}')
    if args.dry_run:
        print(f'  [DRY RUN] No se actualizarán los archivos JSON')
    print(f'{"="*65}\n')

    # 1. Cargar modelos elegibles
    print('  Cargando modelos...')
    models = load_models(tf_filter=args.tf, symbol_filter=args.symbol)

    if not models:
        print('  Sin modelos elegibles (cagr > 0 y trades >= 10 en OOS).')
        print('  Verifica que existan archivos JSON con metrics_oos en models/')
        return

    print(f'  {len(models)} modelos elegibles encontrados\n')

    # Cache de datos por (symbol, tf) para no descargar múltiples veces
    data_cache = {}
    results    = []

    for idx, model in enumerate(models, 1):
        symbol   = model['symbol']
        tf       = model['tf']
        strategy = model['strategy']
        params   = model['params']
        risk_pct = model['risk_pct']
        jf       = model['file']

        print(f'  [{idx}/{len(models)}] {jf.name} — {symbol} {tf} {strategy}')
        print(f'          OOS CAGR guardado: {model["oos_cagr"]:+.1f}% | trades: {model["oos_trades"]}')

        # Descargar datos si no están en cache
        cache_key = (symbol, tf)
        if cache_key not in data_cache:
            print(f'    Descargando datos {symbol} {tf}...', end=' ', flush=True)
            try:
                days_data = 400 if tf == '1m' else (1000 if tf == '15m' else 3200)
                df_raw = fetch_asset(symbol, tf, days=days_data)
                if df_raw is None or len(df_raw) < 200:
                    print(f'ERROR — sin datos suficientes')
                    data_cache[cache_key] = None
                else:
                    df = add_features(df_raw.copy())
                    data_cache[cache_key] = df
                    print(f'{len(df):,} velas OK')
            except Exception as e:
                print(f'ERROR — {e}')
                data_cache[cache_key] = None
        else:
            df = data_cache[cache_key]
            if df is not None:
                print(f'    Datos {symbol} {tf}: {len(df):,} velas (cache)')

        df = data_cache.get(cache_key)
        if df is None:
            result_entry = {
                'file':     jf.name,
                'tf':       tf,
                'symbol':   symbol,
                'strategy': strategy,
                'verdict':  'ERROR',
                'oos_win_rate': None,
                'n_windows': 0,
                'positive_windows': 0,
                'avg_cagr_oos': None,
                'note': 'Sin datos',
            }
            results.append(result_entry)
            print(f'    [SKIP] Sin datos para {symbol} {tf}\n')
            continue

        # Mínimo de barras para el WFT
        min_bars = args.bars_window * args.min_windows
        if len(df) < min_bars:
            print(f'    [SKIP] Datos insuficientes: {len(df)} barras < {min_bars} requeridas')
            result_entry = {
                'file':     jf.name,
                'tf':       tf,
                'symbol':   symbol,
                'strategy': strategy,
                'verdict':  'SKIP',
                'oos_win_rate': None,
                'n_windows': 0,
                'positive_windows': 0,
                'avg_cagr_oos': None,
                'note': f'Datos insuficientes ({len(df)} barras)',
            }
            results.append(result_entry)
            print()
            continue

        # 2. Correr Walk-Forward
        print(f'    Corriendo WFT ({args.min_windows}+ ventanas, {args.bars_window} barras/ventana)...', flush=True)
        try:
            wft = run_wft_simple(
                df, strategy, params, risk_pct,
                min_windows=args.min_windows,
                bars_per_window=args.bars_window,
                is_frac=0.60,
            )
        except Exception as e:
            wft = {'error': str(e)}

        # Manejar error en WFT
        if 'error' in wft:
            print(f'    [ERROR] WFT falló: {wft["error"]}')
            result_entry = {
                'file':     jf.name,
                'tf':       tf,
                'symbol':   symbol,
                'strategy': strategy,
                'verdict':  'ERROR',
                'oos_win_rate': None,
                'n_windows': 0,
                'positive_windows': 0,
                'avg_cagr_oos': None,
                'note': wft['error'],
            }
            results.append(result_entry)
            print()
            continue

        # Aplicar umbral de PASS personalizado
        oos_wr  = wft['oos_win_rate']
        verdict = 'PASS' if oos_wr >= args.pass_thr else 'FAIL'
        wft['verdict'] = verdict

        # Imprimir resultado
        n_valid   = wft.get('n_windows', 0)
        n_pos     = wft.get('positive_windows', 0)
        avg_cagr  = wft.get('avg_cagr_oos', 0)
        icon      = 'OK' if verdict == 'PASS' else 'XX'
        print(f'    [{icon}] {verdict}: {n_pos}/{n_valid} ventanas positivas '
              f'({oos_wr:.1f}%) | avg CAGR OOS: {avg_cagr:+.1f}%')

        # Detalle por ventana
        for w in wft.get('windows', []):
            if w.get('cagr') is not None:
                st = 'OK' if w['cagr'] > 0 else 'XX'
                print(f'      W{w["window"]:02d} [{st}] {w.get("start","?")} '
                      f'CAGR={w["cagr"]:+.1f}% WR={w.get("wr",0):.0f}% T={w.get("trades",0)}')

        # 3. Actualizar JSON del modelo
        updated = update_model_json(jf, wft, dry_run=args.dry_run)
        if updated and not args.dry_run:
            print(f'    [SAVED] {jf.name} actualizado con resultado WFT')

        result_entry = {
            'file':            jf.name,
            'tf':              tf,
            'symbol':          symbol,
            'strategy':        strategy,
            'verdict':         verdict,
            'oos_win_rate':    oos_wr,
            'n_windows':       n_valid,
            'positive_windows':n_pos,
            'avg_cagr_oos':    avg_cagr,
            'avg_cagr_pos':    wft.get('avg_cagr_pos', 0),
        }
        results.append(result_entry)
        print()

    # 4. Guardar reporte global
    report_path = BASE / 'results' / 'reports' / f'wft_all_models_{datetime.now().strftime("%Y%m%d_%H%M")}.json'
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump({
                'date':      datetime.now().strftime('%Y-%m-%d %H:%M'),
                'tf_filter': args.tf,
                'sym_filter':args.symbol,
                'pass_thr':  args.pass_thr,
                'results':   results,
            }, f, indent=2, default=str)
        print(f'  Reporte guardado: {report_path.name}')
    except Exception as e:
        print(f'  [WARN] No se pudo guardar reporte: {e}')

    # 5. Reporte final en consola
    print_report(results)


if __name__ == '__main__':
    main()

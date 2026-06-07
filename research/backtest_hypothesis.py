#!/usr/bin/env python3
"""
SIGMA RESEARCH - Backtest de hipotesis libres.
Aislado de produccion. NO toca signals/SL/TP/kelly/orders del paper trader.

Framework:
  - Define hipotesis como funcion entry_filter(state) -> bool
  - state = {direction, entry_ts (epoch s), entry_price, symbol, tf, strategy}
  - Reusa add_features, sig_*, backtest, metrics de asset_pipeline.py
  - Compara SIN filtro vs CON filtro side-by-side
  - Split 70% IS / 30% OOS por fecha

Salida: stdout legible + JSON en /opt/sigma/research/results/<hypothesis>_<ts>.json
"""
import sys, os, sqlite3, json, argparse, time, traceback
from datetime import datetime, timezone
sys.path.insert(0, '/opt/sigma')
sys.path.insert(0, '/opt/sigma/engine')

import numpy as np
import pandas as pd
import warnings; warnings.filterwarnings('ignore')

# Reusar el motor de SIGMA - NO reinventar
from engine.optimization import asset_pipeline as ap

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
RESEARCH_DIR  = '/opt/sigma/research'
RESULTS_DIR   = os.path.join(RESEARCH_DIR, 'results')
CACHE_DIR     = os.path.join(RESEARCH_DIR, 'cache_ohlcv')
FNG_DB        = '/opt/sigma/results/fng.db'

os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'LTC/USDT']
TFS     = ['1h', '4h']

# Params default por estrategia (razonables, no optimizados - H1 mide si F&G ayuda
# sobre el mismo baseline; no estamos optimizando params)
DEFAULT_PARAMS = {
    'momentum_short': {
        'sl_mult': 1.5, 'tp_mult': 2.5, 'cooldown': 8,
        'vol_mult': 1.2, 'rsi_w_thr': 50, 'rsi_min': 30,
    },
    'breakdown': {
        'lookback': 20, 'vol_mult': 1.3, 'sl_mult': 1.5, 'tp_mult': 2.5,
        'cooldown': 8, 'rsi_w_thr': 50,
    },
    'pullback_short': {
        'sl_mult': 1.5, 'tp_mult': 2.5, 'cooldown': 8,
        'ema_type': 21, 'rsi_w_thr': 50, 'rsi_entry': 55,
    },
}

# NOTA IMPORTANTE: descubierto en research 2026-05-14:
# las funciones sig_*_short en asset_pipeline.py tienen `bs, bl = apply_regime_gate(...)`
# que INVIERTE longs/shorts en el unpacking (apply_regime_gate retorna (bl, bs)).
# El motor productivo ejecuta esas estrategias generando LONGS bajo nombre "short".
# Para esta investigacion necesitamos shorts reales -> envolvemos con un wrapper
# de research que invierte el signo. NO modifica produccion.
def _short_wrapper(sig_fn):
    """Devuelve fn(df, p) que ejecuta sig_fn y luego invierte signo de sig.
    sl_s y tp_s se intercambian/recalculan para shorts reales (entry+slm*atr, entry-tpm*atr)."""
    def _fn(df, p):
        sig, sl_s, tp_s = sig_fn(df, p)
        # Invertir: lo que era 1 (long generado por bug) ahora pasa a -1 (short real)
        new_sig = -sig
        # Recalcular SL/TP para que sean coherentes con short real
        # Entries son indices donde new_sig == -1 (antes sig == 1)
        c = df['close']; atr = df['atr']
        slm = p['sl_mult']; tpm = p['tp_mult']
        new_sl = pd.Series(0.0, index=df.index)
        new_tp = pd.Series(0.0, index=df.index)
        mask = new_sig == -1
        new_sl[mask] = c[mask] + atr[mask] * slm   # short: SL arriba
        new_tp[mask] = c[mask] - atr[mask] * tpm   # short: TP abajo
        return new_sig, new_sl, new_tp
    return _fn

STRATEGY_FNS = {
    'momentum_short': _short_wrapper(ap.sig_momentum_short),
    'breakdown':      _short_wrapper(ap.sig_breakdown),
    'pullback_short': _short_wrapper(ap.sig_pullback_short),
}

# ---------------------------------------------------------------------------
# F&G
# ---------------------------------------------------------------------------

_FNG_CACHE = None
def _load_fng_table():
    global _FNG_CACHE
    if _FNG_CACHE is not None:
        return _FNG_CACHE
    conn = sqlite3.connect(FNG_DB)
    c = conn.cursor()
    c.execute('SELECT ts, value FROM fng ORDER BY ts ASC')
    rows = c.fetchall()
    conn.close()
    ts  = np.array([r[0] for r in rows], dtype=np.int64)
    val = np.array([r[1] for r in rows], dtype=np.int32)
    _FNG_CACHE = (ts, val)
    return _FNG_CACHE

def load_fng_at_ts(target_ts):
    """Devuelve el F&G value <= target_ts (lookahead-safe)."""
    ts, val = _load_fng_table()
    if len(ts) == 0:
        return None
    idx = np.searchsorted(ts, int(target_ts), side='right') - 1
    if idx < 0:
        return None
    return int(val[idx])

# ---------------------------------------------------------------------------
# OHLCV cache local (research)
# ---------------------------------------------------------------------------

def _cache_path(symbol, tf, days):
    safe = symbol.replace('/', '_')
    return os.path.join(CACHE_DIR, f'{safe}_{tf}_{days}d.pkl')

def get_ohlcv(symbol, tf, days):
    """Fetch + cache OHLCV en research/cache (no toca cache de prod).
    Trim al rango exacto de `days` (importante: fetch_asset puede devolver max history)."""
    path = _cache_path(symbol, tf, days)
    if os.path.exists(path):
        try:
            df = pd.read_pickle(path)
            return df
        except Exception:
            pass
    df = ap.fetch_asset(symbol, tf=tf, days=days)
    if df is None or df.empty:
        return None
    # Trim al rango pedido: fetch_asset puede traer historia mas larga
    if isinstance(df.index, pd.DatetimeIndex) and len(df) > 0:
        cutoff = df.index[-1] - pd.Timedelta(days=days)
        df = df[df.index >= cutoff]
    try:
        df.to_pickle(path)
    except Exception:
        pass
    return df

# ---------------------------------------------------------------------------
# Aplicar filtro de hipotesis a un sig/sl/tp
# ---------------------------------------------------------------------------

def _apply_filter(df, sig, sl_s, tp_s, entry_filter_fn, symbol, tf, strategy):
    """Para cada idx con sig != 0, evalua entry_filter_fn(state). Si retorna False,
    pone sig=0 en esa barra (suprime la entrada)."""
    if entry_filter_fn is None:
        return sig, sl_s, tp_s
    sig2  = sig.copy()
    nz    = sig2[sig2 != 0].index
    px    = df['close']
    for ts_idx in nz:
        direction = 'long' if sig2.loc[ts_idx] == 1 else 'short'
        try:
            entry_epoch = int(pd.Timestamp(ts_idx).tz_localize('UTC').timestamp())
        except Exception:
            entry_epoch = int(pd.Timestamp(ts_idx).timestamp())
        state = {
            'direction':  direction,
            'entry_ts':   entry_epoch,
            'entry_price':float(px.loc[ts_idx]),
            'symbol':     symbol,
            'tf':         tf,
            'strategy':   strategy,
        }
        try:
            ok = bool(entry_filter_fn(state))
        except Exception:
            ok = True   # en error, no filtrar - politica conservadora
        if not ok:
            sig2.loc[ts_idx] = 0
    return sig2, sl_s, tp_s

# ---------------------------------------------------------------------------
# Split IS/OOS y backtest
# ---------------------------------------------------------------------------

def _is_oos_split(df, oos_frac=0.30):
    n   = len(df)
    cut = int(n * (1 - oos_frac))
    return df.iloc[:cut].copy(), df.iloc[cut:].copy()

def _summary_from_trades_eq(df_t, eq_s, days):
    m = ap.metrics(df_t, eq_s, days, min_t=3)
    init = ap.CAPITAL
    last = float(eq_s.iloc[-1]) if len(eq_s) else init
    pnl_pct = (last - init) / init * 100
    if m is None:
        return {'trades': 0 if (df_t is None or df_t.empty) else len(df_t),
                'wr': None, 'cagr': None, 'dd': None, 'pf': None,
                'pnl_pct': round(pnl_pct, 2)}
    m['pnl_pct'] = round(pnl_pct, 2)
    if df_t is not None and not df_t.empty:
        m['avg_pnl_usd'] = round(float(df_t['pnl'].mean()), 4)
    return m

def _run_one(df, strategy, params, entry_filter_fn, symbol, tf):
    sig_fn = STRATEGY_FNS[strategy]
    sig, sl_s, tp_s = sig_fn(df, params)
    # Sin filtro
    df_t_no, eq_no = ap.backtest(df, sig, sl_s, tp_s, risk_pct=1.0, use_kelly=False)
    # Con filtro
    sig_f, sl_f, tp_f = _apply_filter(df, sig, sl_s, tp_s, entry_filter_fn, symbol, tf, strategy)
    df_t_yes, eq_yes  = ap.backtest(df, sig_f, sl_f, tp_f, risk_pct=1.0, use_kelly=False)
    days = max((df.index[-1] - df.index[0]).days, 1)
    return {
        'no_filter':  _summary_from_trades_eq(df_t_no,  eq_no,  days),
        'yes_filter': _summary_from_trades_eq(df_t_yes, eq_yes, days),
        'days': days,
    }

def run_hypothesis(name, entry_filter_fn, strategies, symbols=None, tfs=None, days=365, oos_frac=0.30):
    symbols = symbols or SYMBOLS
    tfs     = tfs     or TFS
    out = {
        'name': name,
        'ts':   datetime.now(timezone.utc).isoformat(),
        'days_history': days,
        'oos_frac': oos_frac,
        'strategies': strategies,
        'symbols': symbols,
        'tfs': tfs,
        'rows': [],
    }
    for symbol in symbols:
        for tf in tfs:
            try:
                df_full = get_ohlcv(symbol, tf, days)
            except Exception as e:
                print(f'  [SKIP] {symbol} {tf}: fetch error {type(e).__name__} {e}')
                continue
            if df_full is None or df_full.empty:
                print(f'  [SKIP] {symbol} {tf}: no data')
                continue
            try:
                df_full = ap.add_features(df_full)
            except Exception as e:
                print(f'  [SKIP] {symbol} {tf}: features error {type(e).__name__} {e}')
                continue
            if df_full.empty or len(df_full) < 200:
                print(f'  [SKIP] {symbol} {tf}: too few bars after features ({len(df_full)})')
                continue

            df_is, df_oos = _is_oos_split(df_full, oos_frac)
            for strat in strategies:
                if strat not in STRATEGY_FNS:
                    print(f'  [WARN] {strat} no implementada')
                    continue
                params = DEFAULT_PARAMS[strat]
                try:
                    is_res   = _run_one(df_is,   strat, params, entry_filter_fn, symbol, tf)
                    oos_res  = _run_one(df_oos,  strat, params, entry_filter_fn, symbol, tf)
                    full_res = _run_one(df_full, strat, params, entry_filter_fn, symbol, tf)
                except Exception as e:
                    traceback.print_exc()
                    print(f'  [FAIL] {symbol} {tf} {strat}: {type(e).__name__} {e}')
                    continue
                out['rows'].append({
                    'symbol': symbol, 'tf': tf, 'strategy': strat,
                    'full': full_res, 'is': is_res, 'oos': oos_res,
                })
                _print_row(symbol, tf, strat, full_res, is_res, oos_res)
    _print_summary(out)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_name = ''.join(ch if ch.isalnum() else '_' for ch in name)[:60]
    path = os.path.join(RESULTS_DIR, f'{safe_name}_{stamp}.json')
    with open(path, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f'\n[SAVED] {path}')
    return out

# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

def _fmt(v, suf=''):
    if v is None: return 'n/a'
    if isinstance(v, float): return f'{v:.2f}{suf}'
    return f'{v}{suf}'

def _print_row(symbol, tf, strat, full, is_r, oos):
    def _line(label, r):
        return (f'    {label:<5s} '
                f'n={_fmt(r["no_filter"]["trades"]):>4s}->{_fmt(r["yes_filter"]["trades"]):<4s}  '
                f'WR {_fmt(r["no_filter"]["wr"], "%"):>7s}->{_fmt(r["yes_filter"]["wr"], "%"):<7s}  '
                f'PnL {_fmt(r["no_filter"]["pnl_pct"], "%"):>8s}->{_fmt(r["yes_filter"]["pnl_pct"], "%"):<8s}  '
                f'DD {_fmt(r["no_filter"]["dd"], "%"):>8s}->{_fmt(r["yes_filter"]["dd"], "%"):<8s}')
    print(f'  {strat} - {symbol} {tf}  (days full={full["days"]} is={is_r["days"]} oos={oos["days"]})')
    print(_line('FULL', full))
    print(_line('IS',   is_r))
    print(_line('OOS',  oos))

def _safe_diff(a, b):
    if a is None or b is None: return None
    return b - a

def _avg(xs):
    return None if not xs else sum(xs)/len(xs)

def _print_summary(out):
    print('\n====== RESUMEN ' + ('='*55))
    by_strat = {}
    for r in out['rows']:
        by_strat.setdefault(r['strategy'], []).append(r)
    for strat, rows in by_strat.items():
        def collect(seg, box, key):
            xs = []
            for r in rows:
                v = r[seg][box].get(key)
                if v is not None: xs.append(v)
            return xs
        def trades_sum(seg, box):
            return sum((r[seg][box]['trades'] or 0) for r in rows)
        n_is_no   = trades_sum('is','no_filter')
        n_is_yes  = trades_sum('is','yes_filter')
        n_oos_no  = trades_sum('oos','no_filter')
        n_oos_yes = trades_sum('oos','yes_filter')

        wr_is_no   = _avg(collect('is','no_filter','wr'))
        wr_is_yes  = _avg(collect('is','yes_filter','wr'))
        wr_oos_no  = _avg(collect('oos','no_filter','wr'))
        wr_oos_yes = _avg(collect('oos','yes_filter','wr'))
        dd_is_no   = _avg(collect('is','no_filter','dd'))
        dd_is_yes  = _avg(collect('is','yes_filter','dd'))
        dd_oos_no  = _avg(collect('oos','no_filter','dd'))
        dd_oos_yes = _avg(collect('oos','yes_filter','dd'))
        pnl_is_no  = _avg(collect('is','no_filter','pnl_pct'))
        pnl_is_yes = _avg(collect('is','yes_filter','pnl_pct'))
        pnl_oos_no = _avg(collect('oos','no_filter','pnl_pct'))
        pnl_oos_yes= _avg(collect('oos','yes_filter','pnl_pct'))

        print(f'\n  {strat}')
        print(f'    IS  trades {n_is_no}->{n_is_yes}   '
              f'avgWR  {_fmt(wr_is_no, "%")}->{_fmt(wr_is_yes, "%")}  '
              f'avgDD  {_fmt(dd_is_no, "%")}->{_fmt(dd_is_yes, "%")}  '
              f'avgPnL {_fmt(pnl_is_no, "%")}->{_fmt(pnl_is_yes, "%")}')
        print(f'    OOS trades {n_oos_no}->{n_oos_yes}   '
              f'avgWR  {_fmt(wr_oos_no, "%")}->{_fmt(wr_oos_yes, "%")}  '
              f'avgDD  {_fmt(dd_oos_no, "%")}->{_fmt(dd_oos_yes, "%")}  '
              f'avgPnL {_fmt(pnl_oos_no, "%")}->{_fmt(pnl_oos_yes, "%")}')
        d_wr_is  = _safe_diff(wr_is_no,  wr_is_yes)
        d_wr_oos = _safe_diff(wr_oos_no, wr_oos_yes)
        d_dd_is  = _safe_diff(dd_is_no,  dd_is_yes)
        d_dd_oos = _safe_diff(dd_oos_no, dd_oos_yes)
        d_pnl_is = _safe_diff(pnl_is_no, pnl_is_yes)
        d_pnl_oos= _safe_diff(pnl_oos_no,pnl_oos_yes)
        print(f'    Delta IS  : WR {_fmt(d_wr_is, "pp")}  DD {_fmt(d_dd_is, "pp")}  PnL {_fmt(d_pnl_is, "pp")}')
        print(f'    Delta OOS : WR {_fmt(d_wr_oos,"pp")}  DD {_fmt(d_dd_oos,"pp")}  PnL {_fmt(d_pnl_oos,"pp")}')
        is_pos  = (d_wr_is  is not None and d_wr_is  > 0)
        oos_pos = (d_wr_oos is not None and d_wr_oos > 0)
        if is_pos and oos_pos:
            verdict = 'ROBUST   - mejora WR en IS y OOS'
        elif is_pos and not oos_pos:
            verdict = 'OVERFIT  - mejora IS pero NO OOS'
        elif not is_pos and oos_pos:
            verdict = 'NOISE    - mejora OOS pero no IS'
        else:
            verdict = 'NO_EDGE  - no mejora ni IS ni OOS'
        print(f'    Veredicto: {verdict}')
    print('\n' + '='*70)

# ---------------------------------------------------------------------------
# Hipotesis
# ---------------------------------------------------------------------------

def h1_fng_high(state):
    """H1: Solo abrir SHORTS si F&G en el momento del entry > 50 (mercado codicioso)."""
    if state.get('direction') != 'short':
        return True
    fng = load_fng_at_ts(state['entry_ts'])
    if fng is None:
        return False
    return fng > 50

def h1b_fng_extreme_fear(state):
    """H1b: Solo abrir SHORTS si F&G < 25 (panico extremo - continuacion bajista).
    Variante data-driven: F&G ha estado en zona panico (5-29) todo el ultimo año,
    H1 original (F&G>50) es vacuamente falsa. H1b mide si el panico extremo
    aporta edge a los shorts."""
    if state.get('direction') != 'short':
        return True
    fng = load_fng_at_ts(state['entry_ts'])
    if fng is None:
        return False
    return fng < 25

def h1c_fng_neutral_plus(state):
    """H1c: SHORTS si F&G >= 20 (no panico extremo - evitar rebote tecnico)."""
    if state.get('direction') != 'short':
        return True
    fng = load_fng_at_ts(state['entry_ts'])
    if fng is None:
        return False
    return fng >= 20

HYPOTHESES = {
    'h1':  ('H1: SHORTS con F&G > 50 (codicia)',           h1_fng_high,
           ['momentum_short', 'breakdown', 'pullback_short']),
    'h1b': ('H1b: SHORTS con F&G < 25 (panico extremo)',   h1b_fng_extreme_fear,
           ['momentum_short', 'breakdown', 'pullback_short']),
    'h1c': ('H1c: SHORTS con F&G >= 20 (evitar panico extremo)', h1c_fng_neutral_plus,
           ['momentum_short', 'breakdown', 'pullback_short']),
}

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--hypothesis', default='h1')
    parser.add_argument('--days',       type=int, default=365)
    parser.add_argument('--symbols',    nargs='+', default=None)
    parser.add_argument('--tfs',        nargs='+', default=None)
    parser.add_argument('--strategies', nargs='+', default=None)
    args = parser.parse_args()

    if args.hypothesis not in HYPOTHESES:
        print(f'Hipotesis desconocida: {args.hypothesis}. Disponibles: {list(HYPOTHESES)}')
        sys.exit(2)
    name, fn, strats = HYPOTHESES[args.hypothesis]
    if args.strategies:
        strats = args.strategies

    print(f'\n>>> {name}')
    print(f'    Strategies : {strats}')
    print(f'    Symbols    : {args.symbols or SYMBOLS}')
    print(f'    TFs        : {args.tfs or TFS}')
    print(f'    Days       : {args.days}')
    print(f'    Motor      : asset_pipeline (reused - add_features, sig_*, backtest, metrics)')
    print()

    t0 = time.time()
    run_hypothesis(name, fn, strats,
                   symbols=args.symbols, tfs=args.tfs, days=args.days)
    print(f'\n[TIME] {time.time()-t0:.1f}s')

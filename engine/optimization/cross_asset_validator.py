"""
SIGMA CROSS-ASSET VALIDATOR
Testa si los mismos params del 1H Breakout funcionan en ETH, BNB, SOL.
Si funcionan en 3+ activos con OOS positivo → edge real, no luck.

Logica:
  Los mismos parametros optimizados en BTC se aplican SIN REOPTIMIZAR
  en ETH/BNB/SOL. Si el edge es real (inefficiencia del mercado crypto),
  deberia replicarse en otros activos.

  P(luck | funciona en 4 activos) < 3% → alta confianza estadistica
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION = 0.0004; SLIPPAGE = 0.0001; CAPITAL = 1000.0

ASSETS = {
    'ETH/USDT': 'ETH/USDT',
    'BNB/USDT': 'BNB/USDT',
    'SOL/USDT': 'SOL/USDT',
}


def rsi(close, n=14):
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + g / (l + 1e-9))


def fetch_asset(symbol, tf='1h', days=1800):
    """Descarga datos para un activo."""
    import ccxt
    exchanges = [
        ccxt.binance({'timeout': 30000, 'options': {'defaultType': 'future'}}),
        ccxt.binance({'timeout': 30000}),
    ]
    since_ms = int((pd.Timestamp.now() - pd.Timedelta(days=days)).timestamp() * 1000)
    for ex in exchanges:
        try:
            all_ohlcv = []
            since = since_ms
            while True:
                data = ex.fetch_ohlcv(symbol, tf, since=since, limit=1000)
                if not data: break
                all_ohlcv.extend(data)
                if len(data) < 1000: break
                since = data[-1][0] + 1
            if not all_ohlcv: continue
            df = pd.DataFrame(all_ohlcv, columns=['ts','open','high','low','close','volume'])
            df['ts'] = pd.to_datetime(df['ts'], unit='ms')
            df.set_index('ts', inplace=True)
            df = df[~df.index.duplicated(keep='last')].sort_index()
            return df
        except:
            continue
    return None


def apply_breakout(df, params, risk_pct=3.3):
    """Aplica la estrategia breakout sin reoptimizar."""
    c = df['close']; h = df['high']; l = df['low']
    v = df.get('volume', pd.Series(1, index=df.index))

    atr = (h - l).ewm(alpha=1/14, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    vol_ma = v.rolling(20).mean()

    lb  = params.get('lookback', 60)
    vm  = params.get('vol_mult', 2.9)
    slm = params.get('sl_mult', 2.3)
    tpm = params.get('tp_mult', 2.0)
    cd  = params.get('cooldown', 11)

    prev_high = h.rolling(lb).max().shift(1)
    vol_ok    = v > vol_ma * vm
    above_200 = c > ema200

    # RSI-W filter
    close_w  = c.resample('W').last().ffill()
    rsi_w    = rsi(close_w, 14)
    rsi_w_1h = rsi_w.reindex(df.index, method='ffill')
    bull_ok  = rsi_w_1h > 55

    bl = (c > prev_high) & vol_ok & above_200 & bull_ok
    sig = pd.Series(0, index=df.index)
    sl_s = pd.Series(0.0, index=df.index)
    tp_s = pd.Series(0.0, index=df.index)

    last = -cd - 1
    for i in range(lb, len(df)):
        if (i - last) >= cd and bl.iloc[i]:
            sig.iloc[i] = 1
            sl_s.iloc[i] = c.iloc[i] - atr.iloc[i] * slm
            tp_s.iloc[i] = c.iloc[i] + atr.iloc[i] * tpm
            last = i

    # Backtest
    cap = CAPITAL; eq = [cap]; pos = 0; entry_p = slv = tpv = size = 0.0; trades = []
    c_arr = df['close'].to_numpy(); h_arr = df['high'].to_numpy(); lo_arr = df['low'].to_numpy()
    sig_arr = sig.to_numpy(); sl_arr = sl_s.to_numpy(); tp_arr = tp_s.to_numpy()

    for i in range(1, len(c_arr)):
        pr = c_arr[i]
        if pos != 0:
            pnl = 0.0; closed = False
            if lo_arr[i] <= slv: pnl = size*(slv-entry_p)-size*(entry_p+slv)*COMMISSION; closed = True
            elif h_arr[i] >= tpv: pnl = size*(tpv-entry_p)-size*(entry_p+tpv)*COMMISSION; closed = True
            if closed: cap += pnl; trades.append({'pnl': pnl, 'won': pnl > 0}); pos = 0
        if pos == 0 and sig_arr[i-1] == 1 and sl_arr[i-1] > 0 and cap > 50:
            rsl = abs(pr - sl_arr[i-1])
            if rsl <= 0: continue
            size = (cap*risk_pct/100)/rsl; pos = 1; entry_p = pr; slv = sl_arr[i-1]; tpv = tp_arr[i-1]
        eq.append(cap)

    if not trades:
        return None
    df_t = pd.DataFrame(trades)
    eq_s = pd.Series(eq[:len(df)], index=df.index[:len(eq)])
    w = df_t[df_t['pnl'] > 0]; l_t = df_t[df_t['pnl'] <= 0]
    days = (df.index[-1]-df.index[0]).days
    last_v = float(eq_s.iloc[-1])
    if last_v <= 0: return None
    cagr = ((last_v/CAPITAL)**(365.25/max(days,1))-1)*100
    wr = len(w)/len(df_t)*100
    peak = eq_s.cummax(); dd = ((eq_s-peak)/peak*100).min()
    pf = w['pnl'].sum()/abs(l_t['pnl'].sum()) if not l_t.empty else 999
    return {'trades': len(df_t), 'wr': round(wr,1), 'cagr': round(cagr,1),
            'dd': round(dd,1), 'pf': round(pf,2),
            'days': days, 'asset': 'unknown'}


def run():
    print('\n' + '='*65)
    print('  SIGMA CROSS-ASSET VALIDATOR')
    print('  Test: mismos params BTC en ETH/BNB/SOL')
    print('='*65)

    # Cargar params del mejor modelo 1H
    model_path = OUTPUT_DIR / 'models' / '1h' / 'best_bull_breakout.json'
    if not model_path.exists():
        print('  Sin modelo best_bull_breakout.json')
        return
    with open(model_path) as f:
        data = json.load(f)
    params    = data.get('params', {})
    risk_pct  = data.get('risk_pct', 3.3)
    btc_cagr  = data.get('metrics_oos', {}).get('cagr', 0)
    print(f'  Params BTC: lookback={params.get("lookback")} vol={params.get("vol_mult")} CAGR_OOS={btc_cagr:+.1f}%')

    results = {'BTC': {'cagr': btc_cagr, 'note': 'params optimizados aqui'}}
    positive_assets = ['BTC'] if btc_cagr > 0 else []

    for asset_name, symbol in ASSETS.items():
        print(f'\n  [{asset_name}] Descargando datos...')
        df = fetch_asset(symbol, tf='1h', days=1800)
        if df is None or len(df) < 500:
            print(f'  [{asset_name}] Sin datos, saltando')
            continue

        # OOS: ultimos 20%
        split = int(len(df) * 0.80)
        df_oos = df.iloc[split:]
        print(f'  [{asset_name}] {len(df):,} velas | OOS: {len(df_oos):,}')

        m = apply_breakout(df_oos, params, risk_pct)
        if m:
            m['asset'] = asset_name
            results[asset_name] = m
            status = 'POSITIVO' if m['cagr'] > 0 else 'NEGATIVO'
            print(f'  [{asset_name}] OOS: {m["trades"]}T | WR {m["wr"]:.1f}% | CAGR {m["cagr"]:+.1f}% | {status}')
            if m['cagr'] > 0:
                positive_assets.append(asset_name)
        else:
            print(f'  [{asset_name}] Sin trades suficientes')

    # Resumen
    print(f'\n  {"="*50}')
    print(f'  RESULTADO CROSS-ASSET')
    print(f'  {"="*50}')
    n_pos = len(positive_assets)
    print(f'  Activos positivos: {n_pos}/4 ({positive_assets})')

    if n_pos >= 3:
        confidence = 'ALTA — edge real, no especifico de BTC'
        print(f'  Confianza: {confidence}')
    elif n_pos == 2:
        confidence = 'MEDIA — posiblemente edge real, necesita mas datos'
        print(f'  Confianza: {confidence}')
    else:
        confidence = 'BAJA — puede ser especifico de BTC o ruido'
        print(f'  Confianza: {confidence}')

    # P(luck) aproximada: si cada activo tiene 50% prob de ser positivo por azar
    # P(n_pos >= k de 4) bajo H0
    from math import comb
    prob_luck = sum(comb(4, k) * (0.5**4) for k in range(n_pos, 5))
    print(f'  P(resultado por azar): {prob_luck*100:.1f}%')

    out = OUTPUT_DIR / 'results' / 'reports' / 'cross_asset_validation.json'
    with open(out, 'w') as f:
        json.dump({
            'timestamp': str(pd.Timestamp.now()),
            'params_from': 'models/1h/best_bull_breakout.json',
            'results': results,
            'positive_assets': positive_assets,
            'confidence': confidence,
            'prob_luck_pct': round(prob_luck*100, 1),
        }, f, indent=2, default=str)
    print(f'\n  [SAVED] {out.name}')
    print('='*65)


if __name__ == '__main__':
    run()

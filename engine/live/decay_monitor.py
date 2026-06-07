#!/usr/bin/env python3
"""
SIGMA Decay Monitor — re-valida modelos guardados en datos recientes.
Corre mensualmente via cron. Detecta modelos que degradaron su performance.

Uso: python engine/live/decay_monitor.py
     python engine/live/decay_monitor.py --days 60 --min_trades 5
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np

BASE = Path(__file__).parent.parent.parent

COMMISSION = 0.0004
CAPITAL    = 1000.0

GRADES = [(0.70,'A+'),(0.55,'A'),(0.40,'B'),(0.25,'C'),(0,'D')]
def grade(s):
    if s <= -100: return '—'
    for t, g in GRADES:
        if s >= t: return g
    return 'D'


def fetch_recent(symbol, tf, days=90):
    try:
        import ccxt
        ex = ccxt.binance({'timeout': 30000, 'options': {'defaultType': 'future'}})
        since = int((pd.Timestamp.now() - pd.Timedelta(days=days)).timestamp() * 1000)
        raw = []
        while True:
            batch = ex.fetch_ohlcv(symbol, tf, since=since, limit=1000)
            if not batch: break
            raw.extend(batch)
            if len(batch) < 1000: break
            since = batch[-1][0] + 1
        if not raw: return None
        df = pd.DataFrame(raw, columns=['ts','open','high','low','close','volume'])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms')
        df.set_index('ts', inplace=True)
        return df
    except:
        return None


def add_features(df):
    c = df['close']; h = df['high']; l = df['low']; v = df['volume']
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    df['atr']    = tr.ewm(alpha=1/14,adjust=False).mean()
    df['ema200'] = c.ewm(span=200,adjust=False).mean()
    df['ema50']  = c.ewm(span=50,adjust=False).mean()
    df['ema21']  = c.ewm(span=21,adjust=False).mean()
    df['vol_ma'] = v.rolling(20).mean()
    d = c.diff()
    g = d.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
    ll = (-d.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    df['rsi14'] = 100 - 100/(1+g/(ll+1e-9))
    df['macd']  = c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean()
    df['macd_s']= df['macd'].ewm(span=9,adjust=False).mean()
    df['macd_h']= df['macd']-df['macd_s']
    df.dropna(subset=['atr','ema200'],inplace=True)
    return df


def run_backtest_simple(df, sig, sl_mult, tp_mult, risk_pct=0.01):
    """Backtest simple para validación de decay."""
    trades = []
    in_trade = False
    entry = sl = tp = 0.0
    capital = CAPITAL
    equity = [capital]

    for i in range(1, len(df)):
        row = df.iloc[i]
        if in_trade:
            if row['low'] <= sl:
                pnl = (sl/entry - 1) * risk_pct * capital - COMMISSION * capital
                capital += pnl
                trades.append({'pnl': pnl, 'win': pnl > 0})
                in_trade = False
            elif row['high'] >= tp:
                pnl = (tp/entry - 1) * risk_pct * capital - COMMISSION * capital
                capital += pnl
                trades.append({'pnl': pnl, 'win': pnl > 0})
                in_trade = False
        elif sig.iloc[i] and not in_trade:
            entry = row['close']
            atr   = row['atr']
            sl    = entry - atr * sl_mult
            tp    = entry + atr * tp_mult
            in_trade = True
        equity.append(capital)

    if not trades:
        return None
    wr    = sum(t['win'] for t in trades) / len(trades) * 100
    total = sum(t['pnl'] for t in trades)
    return {'trades': len(trades), 'wr': round(wr,1), 'total_pnl': round(total,2)}


def check_model(jf, days=90, min_trades=5):
    """Verifica si un modelo sigue funcionando en datos recientes."""
    try:
        data     = json.loads(jf.read_text(encoding='utf-8'))
        symbol   = data.get('symbol','')
        tf       = data.get('tf','')
        strategy = data.get('strategy','')
        params   = data.get('params',{})
        m_oos    = data.get('metrics_oos',{})

        if not symbol or not tf or not params:
            return None

        oos_wr   = m_oos.get('wr', 0)
        oos_cagr = m_oos.get('cagr', 0)
        if oos_wr <= 0 or oos_cagr <= 0:
            return None

        df_raw = fetch_recent(symbol, tf, days=days)
        if df_raw is None or len(df_raw) < 50:
            return {'symbol':symbol,'tf':tf,'strategy':strategy,'status':'NO_DATA',
                    'oos_wr':oos_wr,'recent_wr':None,'delta':None}

        df = add_features(df_raw)

        # Señal básica según estrategia
        sl_mult = params.get('sl_mult', 2.0)
        tp_mult = params.get('tp_mult', 3.0)
        sig = pd.Series(False, index=df.index)

        if strategy == 'breakout':
            lb = params.get('lookback', 30)
            vm = params.get('vol_mult', 1.5)
            sig = (df['close'] > df['high'].shift(1).rolling(lb).max()) & \
                  (df['volume'] > df['vol_ma'] * vm)
        elif strategy == 'tma_bands':
            p  = params.get('tma_period', 14)
            am = params.get('atr_mult', 1.5)
            tma = df['close'].rolling(p).mean().rolling(p).mean()
            lower = tma - df['atr'] * am
            sig = df['close'] <= lower
        elif strategy == 'mean_rev':
            ros = params.get('rsi_os', 35)
            sig = df['rsi14'] < ros
        elif strategy == 'pullback':
            et = params.get('ema_type', 21)
            re = params.get('rsi_entry', 45)
            ema_col = f'ema{et}' if f'ema{et}' in df.columns else 'ema21'
            sig = (df['close'] > df[ema_col]) & (df['rsi14'] < re)
        else:
            sig = df['rsi14'] < 40  # fallback genérico

        result = run_backtest_simple(df, sig, sl_mult, tp_mult)
        if result is None or result['trades'] < min_trades:
            return {'symbol':symbol,'tf':tf,'strategy':strategy,'status':'INSUF_TRADES',
                    'oos_wr':oos_wr,'recent_wr':None,'delta':None,
                    'recent_trades':result['trades'] if result else 0}

        delta = result['wr'] - oos_wr
        if delta < -15:
            status = 'DEGRADED'
        elif delta < -8:
            status = 'WARN'
        else:
            status = 'OK'

        return {
            'symbol':symbol,'tf':tf,'strategy':strategy,'status':status,
            'oos_wr':oos_wr,'recent_wr':result['wr'],
            'recent_trades':result['trades'],'delta':round(delta,1),
            'file': jf.name,
        }
    except Exception as e:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days',       type=int, default=90)
    parser.add_argument('--min_trades', type=int, default=5)
    args = parser.parse_args()

    print(f'\n{"="*65}')
    print(f'  SIGMA DECAY MONITOR — últimos {args.days} días')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('='*65)

    results = []
    models_dir = BASE / 'models'
    for tf_dir in sorted(models_dir.iterdir()):
        if not tf_dir.is_dir(): continue
        for jf in sorted(tf_dir.glob('*.json')):
            if jf.name in ('config.json','adaptive_params.json'): continue
            r = check_model(jf, days=args.days, min_trades=args.min_trades)
            if r:
                results.append(r)

    # Reporte
    degraded = [r for r in results if r['status'] == 'DEGRADED']
    warned   = [r for r in results if r['status'] == 'WARN']
    ok       = [r for r in results if r['status'] == 'OK']
    other    = [r for r in results if r['status'] in ('NO_DATA','INSUF_TRADES')]

    print(f'\n  {"Symbol":<10} {"TF":<5} {"Strategy":<20} {"Status":<10} {"OOS_WR":>7} {"Recent":>7} {"Delta":>7} {"Trades":>7}')
    print('  ' + '-'*72)

    for r in sorted(results, key=lambda x: (x['status']=='OK', x.get('delta',0) or 0)):
        sym = (r['symbol'] or '').replace('/USDT','')
        delta_s = f'{r["delta"]:+.1f}%' if r['delta'] is not None else '—'
        wr_r    = f'{r["recent_wr"]:.1f}%' if r['recent_wr'] is not None else '—'
        trades  = r.get('recent_trades','—')
        status_icon = {'DEGRADED':'✗','WARN':'⚠','OK':'✓','NO_DATA':'?','INSUF_TRADES':'~'}.get(r['status'],'?')
        print(f'  {sym:<10} {r["tf"]:<5} {r["strategy"]:<20} {status_icon} {r["status"]:<9} '
              f'{r["oos_wr"]:>6.1f}% {wr_r:>7} {delta_s:>7} {str(trades):>7}')

    print(f'\n  Resumen: ✓ OK={len(ok)} | ⚠ WARN={len(warned)} | ✗ DEGRADED={len(degraded)} | otros={len(other)}')

    if degraded:
        print(f'\n  ACCIÓN RECOMENDADA: estos modelos degradaron >15pp en WR reciente:')
        for r in degraded:
            sym = (r['symbol'] or '').replace('/USDT','')
            print(f'    {sym} {r["tf"]} {r["strategy"]} — WR: {r["oos_wr"]:.0f}% → {r["recent_wr"]:.0f}% ({r["delta"]:+.1f}pp)')

    # Guardar reporte
    report_path = BASE / 'results' / 'reports' / f'decay_{datetime.now().strftime("%Y%m%d")}.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, 'w') as f:
        json.dump({'date': str(datetime.now()), 'days': args.days, 'results': results}, f, indent=2, default=str)
    print(f'\n  Reporte guardado: {report_path.name}')
    print('='*65 + '\n')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
ab_test_bug.py - A/B test del bug de unpacking en sig_*_short

Compara, para cada champion, el backtest con la funcion ORIGINAL (con bug)
vs una version FIXED con el orden correcto de apply_regime_gate.

Solo lectura. Sin escribir trades. Sin tocar produccion.
"""
import sys, json, pickle
from pathlib import Path
import pandas as pd

sys.path.insert(0, '/opt/sigma')
sys.path.insert(0, '/opt/sigma/engine/optimization')

from asset_pipeline import (
    add_features, apply_regime_gate, _apply_cd, backtest, metrics,
    sig_momentum_short as sig_momentum_short_ORIG,
    sig_breakdown      as sig_breakdown_ORIG,
    sig_pullback_short as sig_pullback_short_ORIG,
)

CACHE = Path('/opt/sigma/research/cache_ohlcv')
MODELS = Path('/opt/sigma/models')


def sig_momentum_short_FIXED(df, p):
    c = df['close']; v = df['volume']
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    vm = p.get('vol_mult', 1.5)
    vol_ok = v > df['vol_ma'] * vm
    macd_dn = (df['macd_h'] < 0) & (df['macd_h'] < df['macd_h'].shift(1))
    below_200 = c < df['ema200']
    bear_ok = df['rsi_w'] < p.get('rsi_w_thr', 45)
    not_ob = df['rsi14'] > p.get('rsi_min', 30)
    htf_ok = df.get('htf_bear', pd.Series(True, index=df.index)) | df.get('htf_range', pd.Series(True, index=df.index))
    bs = macd_dn & below_200 & bear_ok & not_ob & vol_ok & htf_ok
    bl, bs = apply_regime_gate(df, pd.Series(False, index=df.index), bs)
    return _apply_cd(df, bl, bs, slm, tpm, cd)


def sig_breakdown_FIXED(df, p):
    c = df['close']; l = df['low']; v = df['volume']
    lb = int(p['lookback']); vm = p['vol_mult']; slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    prev_low = l.rolling(lb).min().shift(1)
    vol_ok = v > df['vol_ma'] * vm
    bear_ok = df['rsi_w'] < p['rsi_w_thr']
    below_200 = c < df['ema200']
    htf_ok = df.get('htf_bear', pd.Series(True, index=df.index)) | df.get('htf_range', pd.Series(True, index=df.index))
    bs = (c < prev_low) & vol_ok & bear_ok & below_200 & htf_ok
    bl, bs = apply_regime_gate(df, pd.Series(False, index=df.index), bs)
    return _apply_cd(df, bl, bs, slm, tpm, cd)


def sig_pullback_short_FIXED(df, p):
    c = df['close']
    slm = p['sl_mult']; tpm = p['tp_mult']; cd = p['cooldown']
    et = int(p.get('ema_type', 21))
    ecol = f'ema{et}' if f'ema{et}' in df.columns else 'ema21'
    ema_v = df[ecol]
    near_ema = (c >= ema_v * 0.995) & (c <= ema_v * 1.005)
    downtrend = df['ema50'] < df['ema200']
    bear_ok = df['rsi_w'] < p.get('rsi_w_thr', 45)
    rsi_entry = df['rsi14'] > p.get('rsi_entry', 58)
    htf_ok = df.get('htf_bear', pd.Series(True, index=df.index)) | df.get('htf_range', pd.Series(True, index=df.index))
    bs = near_ema & downtrend & bear_ok & rsi_entry & htf_ok
    bl, bs = apply_regime_gate(df, pd.Series(False, index=df.index), bs)
    return _apply_cd(df, bl, bs, slm, tpm, cd)


SIG_MAP = {
    'momentum_short': (sig_momentum_short_ORIG, sig_momentum_short_FIXED),
    'breakdown':      (sig_breakdown_ORIG,      sig_breakdown_FIXED),
    'pullback_short': (sig_pullback_short_ORIG, sig_pullback_short_FIXED),
}


def load_data(sym, tf):
    p = CACHE / f'{sym}_USDT_{tf}_365d.pkl'
    if not p.exists():
        return None
    df = pickle.load(open(p, 'rb'))
    return add_features(df.copy())


def run_one(sym, tf, params, fn_orig, fn_fixed):
    df = load_data(sym, tf)
    if df is None or len(df) < 200:
        return None
    sig_o, sl_o, tp_o = fn_orig(df, params)
    n_long_o  = int((sig_o == 1).sum())
    n_short_o = int((sig_o == -1).sum())
    df_t_o, eq_o = backtest(df, sig_o, sl_o, tp_o, params.get('risk_pct', 5.0), use_kelly=True)
    days = (df.index[-1] - df.index[0]).days
    m_o = metrics(df_t_o, eq_o, days, min_t=1)
    total_o = (float(eq_o.iloc[-1]) / 1000.0 - 1) * 100 if len(eq_o) else 0.0

    sig_f, sl_f, tp_f = fn_fixed(df, params)
    n_long_f  = int((sig_f == 1).sum())
    n_short_f = int((sig_f == -1).sum())
    df_t_f, eq_f = backtest(df, sig_f, sl_f, tp_f, params.get('risk_pct', 5.0), use_kelly=True)
    m_f = metrics(df_t_f, eq_f, days, min_t=1)
    total_f = (float(eq_f.iloc[-1]) / 1000.0 - 1) * 100 if len(eq_f) else 0.0

    return {
        'orig':  dict(n_long=n_long_o, n_short=n_short_o, metrics=m_o, total_return_pct=round(total_o, 2)),
        'fixed': dict(n_long=n_long_f, n_short=n_short_f, metrics=m_f, total_return_pct=round(total_f, 2)),
        'days': days,
    }


CHAMPIONS = [
    ('btc_momentum_short.json', '1h'),
    ('eth_momentum_short.json', '1h'),
    ('ltc_momentum_short.json', '4h'),
    ('sol_momentum_short.json', '4h'),
    ('eth_momentum_short.json', '4h'),
    ('sol_breakdown.json',      '1h'),
    ('bnb_breakdown.json',      '1h'),
]

print("=" * 110)
print(" A/B BUG TEST  -  sig_*_short con unpacking incorrecto vs corregido")
print(" Periodo: ~365 dias (cache local)  |  Capital inicial: $1000  |  Kelly: ON")
print("=" * 110)

for fn_name, tf in CHAMPIONS:
    p = MODELS / tf / fn_name
    if not p.exists():
        print(f"\n[SKIP] {fn_name} en {tf} no existe")
        continue
    d = json.loads(p.read_text())
    sym = d.get('symbol', '').replace('/USDT', '')
    strat = d.get('strategy', '')
    if strat not in SIG_MAP:
        print(f"\n[SKIP] {fn_name} strategy={strat} no testeable")
        continue
    params = dict(d.get('params', {}))
    params['risk_pct'] = d.get('risk_pct', 5.0)
    fn_o, fn_f = SIG_MAP[strat]
    res = run_one(sym, tf, params, fn_o, fn_f)
    cagr_champ = d.get('metrics_oos', {}).get('cagr')
    print(f"\n--- {sym} {tf} {strat}  (champion CAGR_OOS={cagr_champ}%) ---")
    if res is None:
        print("  ERROR: sin datos")
        continue
    o = res['orig']; f = res['fixed']
    print(f"  Periodo backtest: {res['days']} dias")
    print(f"  CON BUG  (motor abriria LONG): sig=+1 n={o['n_long']:4d}  sig=-1 n={o['n_short']:4d}")
    if o['metrics']:
        m = o['metrics']
        print(f"           trades={m['trades']:3d}  WR={m['wr']:.1f}%  CAGR={m['cagr']:.1f}%  DD={m['dd']:.1f}%  PF={m['pf']}  TotalRet={o['total_return_pct']}%")
    else:
        print(f"           sin trades suficientes  TotalRet={o['total_return_pct']}%")
    print(f"  SIN BUG  (motor abriria SHORT): sig=+1 n={f['n_long']:4d}  sig=-1 n={f['n_short']:4d}")
    if f['metrics']:
        m = f['metrics']
        print(f"           trades={m['trades']:3d}  WR={m['wr']:.1f}%  CAGR={m['cagr']:.1f}%  DD={m['dd']:.1f}%  PF={m['pf']}  TotalRet={f['total_return_pct']}%")
    else:
        print(f"           sin trades suficientes  TotalRet={f['total_return_pct']}%")

print("\n" + "=" * 110)

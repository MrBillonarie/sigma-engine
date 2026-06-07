#!/usr/bin/env python3
"""
verify_trades.py - Step 3: para cada trade real cerrado en trade_state.json,
ver si su entry coincide en timing con sig=+1 (motor opero por nombre) o
sig=-1 (motor opero por sig). Como el bug fuerza sig=+1, si los trades reales
caen en barras con sig=+1, el motor LIVE NO esta usando sig - decide por nombre.

Solo lectura.
"""
import sys, json, pickle
from pathlib import Path
import pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, '/opt/sigma')
sys.path.insert(0, '/opt/sigma/engine/optimization')

from asset_pipeline import (
    add_features,
    sig_momentum_short, sig_breakdown, sig_pullback_short,
)

CACHE = Path('/opt/sigma/research/cache_ohlcv')
MODELS = Path('/opt/sigma/models')
STATE = Path('/opt/sigma/results/trade_state.json')

SIG_MAP = {
    'momentum_short': sig_momentum_short,
    'breakdown':      sig_breakdown,
    'pullback_short': sig_pullback_short,
}

state = json.loads(STATE.read_text())
hist = state.get('history', [])

print("=" * 100)
print(" STEP 3: trade real cerrado  vs  sig generado por sig_*_short (CON BUG)")
print("=" * 100)
print(f" Total trades cerrados en history: {len(hist)}")
short_trades = [t for t in hist if t.get('direction') == 'short' and t.get('strategy') in SIG_MAP]
print(f" Trades direction=short en estrategias buggy ({list(SIG_MAP)}): {len(short_trades)}")

# Cargar champion params por (sym,tf,strategy)
def load_champ(sym, tf, strat):
    f = MODELS / tf / f'{sym.lower()}_{strat}.json'
    if f.exists():
        return json.loads(f.read_text())
    return None

caches = {}

def get_df(sym, tf):
    key = (sym, tf)
    if key in caches: return caches[key]
    p = CACHE / f'{sym}_USDT_{tf}_365d.pkl'
    if not p.exists(): return None
    df = pickle.load(open(p, 'rb'))
    df = add_features(df.copy())
    caches[key] = df
    return df

print(f"\n{'opened_at':<22} {'sym':<5} {'tf':<4} {'strategy':<18} {'dir':<6} {'sig_at_bar':<10} {'sig+1_within±3bars':<18} {'sig-1_within±3bars':<18} {'pnl%':<8}")
print("-" * 130)

stats = {'sig_plus_at_bar': 0, 'sig_minus_at_bar': 0, 'sig_zero_at_bar': 0, 'plus_near': 0, 'minus_near': 0}

for t in short_trades:
    sym = t['sym']; tf = t['tf']; strat = t['strategy']
    opened_at = pd.to_datetime(t['opened_at'])
    direction = t['direction']
    pnl = t.get('pnl_pct', 0)

    champ = load_champ(sym, tf, strat)
    if champ is None:
        continue
    params = dict(champ.get('params', {}))
    params['risk_pct'] = champ.get('risk_pct', 5.0)

    df = get_df(sym, tf)
    if df is None: continue

    # Encontrar la barra anterior o igual a opened_at
    idx = df.index.searchsorted(opened_at, side='right') - 1
    if idx < 0 or idx >= len(df):
        continue

    fn = SIG_MAP[strat]
    sig, _, _ = fn(df, params)

    sig_at_bar = int(sig.iloc[idx])
    # Ventana +/- 3 barras
    lo = max(0, idx - 3); hi = min(len(sig), idx + 4)
    win = sig.iloc[lo:hi]
    plus_near = bool((win == 1).any())
    minus_near = bool((win == -1).any())

    if sig_at_bar == 1: stats['sig_plus_at_bar'] += 1
    elif sig_at_bar == -1: stats['sig_minus_at_bar'] += 1
    else: stats['sig_zero_at_bar'] += 1
    if plus_near: stats['plus_near'] += 1
    if minus_near: stats['minus_near'] += 1

    print(f"{str(opened_at)[:19]:<22} {sym:<5} {tf:<4} {strat:<18} {direction:<6} {sig_at_bar:<10} {'YES' if plus_near else 'no':<18} {'YES' if minus_near else 'no':<18} {pnl:<8.2f}")

print("\n--- RESUMEN ---")
total = len(short_trades)
print(f" trades direction=short analizados: {total}")
print(f" sig=+1 en la misma barra que el open: {stats['sig_plus_at_bar']}")
print(f" sig=-1 en la misma barra que el open: {stats['sig_minus_at_bar']}")
print(f" sig=0  en la misma barra que el open: {stats['sig_zero_at_bar']}")
print(f" hay un sig=+1 dentro de +/-3 barras del open: {stats['plus_near']}")
print(f" hay un sig=-1 dentro de +/-3 barras del open: {stats['minus_near']}")
print()
print(" INTERPRETACION:")
print(" - sig_*_short CON BUG solo emite sig=+1 (nunca -1). Si los trades reales")
print("   caen en sig=+1, motor LIVE estaba decidiendo direction='short' por NOMBRE,")
print("   no por sig. Bug solo afecta evaluacion Optuna, no la ejecucion.")
print(" - Si los trades reales NO caen en sig=+1, motor LIVE tiene otra fuente de")
print("   senal (web_server._signal()) y bug realmente desconectado de live.")

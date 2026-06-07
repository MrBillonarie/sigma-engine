#!/usr/bin/env python3
import http.server, os, subprocess, threading, time, json, sqlite3, socketserver, signal, sys
from pathlib import Path

BASE = Path('/opt/sigma')
PORT = 8080
DB   = BASE / 'models' / 'sigma.db'



# ══════════════════════════════════════════════════════════════════
#  TRADE STATE MANAGER
#  Registra entradas, detecta SL/TP automáticamente, bloquea re-entrada
# ══════════════════════════════════════════════════════════════════
import json as _json
from datetime import datetime as _dt

# ── Hora Chile (zoneinfo — funciona independiente del TZ del OS) ─────────────
try:
    import zoneinfo as _zi_ch
    _TZ_CL = _zi_ch.ZoneInfo("America/Santiago")
except ImportError:
    from datetime import timezone as _tz_fb, timedelta as _td_fb
    _TZ_CL = _tz_fb(_td_fb(hours=-4))  # CLT fallback

def _strftime_chile(fmt="%H:%M"):
    from datetime import datetime as _dz
    return _dz.now(_TZ_CL).strftime(fmt)

def _now_chile():
    from datetime import datetime as _dz
    return _dz.now(_TZ_CL)


TRADE_STATE_FILE = __import__("pathlib").Path("/opt/sigma/results/trade_state.json")

def _load_trades():
    import json as _j2
    try:
        if TRADE_STATE_FILE.exists():
            with open(TRADE_STATE_FILE) as _f: return _j2.load(_f)
    except Exception as _e:
        print(f"[TRADE STATE ERROR] {_e}", flush=True)
    return {"open": {}, "history": [], "portfolio": {}}


def _update_live_stats(trade):
    """Actualiza estadisticas live por modelo en sigma.db tras cada trade cerrado."""
    try:
        import sqlite3 as _sq
        sym      = trade.get('sym', trade.get('symbol', ''))
        tf       = trade.get('tf', '')
        strategy = trade.get('strategy', '')
        pnl      = trade.get('pnl_pct', 0)
        if not sym or not tf or not strategy:
            return
        conn = _sq.connect('/opt/sigma/models/sigma.db')
        conn.execute('''CREATE TABLE IF NOT EXISTS model_live_stats (
            sym TEXT, tf TEXT, strategy TEXT,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            last_updated TEXT,
            PRIMARY KEY (sym, tf, strategy)
        )''')
        w = 1 if pnl > 0 else 0
        l = 0 if pnl > 0 else 1
        conn.execute('''INSERT INTO model_live_stats
            (sym, tf, strategy, wins, losses, total_pnl, last_updated)
            VALUES (?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(sym,tf,strategy) DO UPDATE SET
                wins=wins+?, losses=losses+?,
                total_pnl=total_pnl+?,
                last_updated=datetime('now')
        ''', (sym, tf, strategy, w, l, pnl, w, l, pnl))
        conn.commit()
        conn.close()
    except Exception:
        pass

def _save_trades(state):
    import json as _j2
    try:
        TRADE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TRADE_STATE_FILE, "w") as _f: _j2.dump(state, _f, default=str, indent=2)
    except Exception as _e:
        print(f"[TRADE SAVE ERROR] {_e}", flush=True)

def _check_decay(sym, tf, backtest_wr):
    """Detecta si un modelo está degradando en live vs backtest.
    Retorna (is_decay, live_wr, n_trades).
    Umbral: live_wr < backtest_wr - 15% con al menos 6 trades recientes."""
    try:
        state  = _load_trades()
        recent = [t for t in state.get('history', [])
                  if t.get('sym') == sym and t.get('tf') == tf][-12:]
        if len(recent) < 6:
            return False, None, len(recent)
        live_wr = sum(1 for t in recent if t.get('pnl_pct', 0) > 0) / len(recent) * 100
        is_decay = live_wr < (backtest_wr - 15)
        return is_decay, round(live_wr, 1), len(recent)
    except:
        return False, None, 0

def _calc_ror(wr_pct, kelly_pct, n_sims=1000, dd_limit=25):
    """Risk-of-ruin: % de simulaciones donde el portfolio pierde >dd_limit%."""
    import random
    wr = wr_pct / 100.0; k = kelly_pct / 100.0
    ruin = 0
    for _ in range(n_sims):
        eq = 1.0; peak = 1.0; hit = False
        for _ in range(60):
            if random.random() < wr:
                rr = 2.0  # RR promedio del sistema
                eq *= (1 + k * rr)
            else:
                eq *= (1 - k)
            if eq > peak: peak = eq
            if (eq - peak) / peak < -dd_limit / 100:
                hit = True; break
        if hit: ruin += 1
    return round(ruin / n_sims * 100, 1)



def _calc_readiness(history, model_perf, port):
    """Gate estadistico: si el sistema esta listo para capital real. Score 0-100."""
    checks = []; score = 0; n = len(history)
    # 1. Trades suficientes (min 30)
    if n >= 30:
        checks.append({'name': 'Trades suficientes', 'ok': True,  'val': str(n)+' trades', 'pts': 25}); score += 25
    else:
        checks.append({'name': 'Trades suficientes', 'ok': False, 'val': str(n)+'/30 trades', 'pts': 0})
    # 2. WR live vs backtest (diff <= 12pp con >= 15 trades)
    if n >= 15:
        live_wr  = sum(1 for t in history if t.get('pnl_pct', 0) > 0) / n * 100
        bt_wrs   = [v['wr_bt'] for v in model_perf.values() if v.get('wr_bt', 0) > 0]
        avg_bt   = sum(bt_wrs) / len(bt_wrs) if bt_wrs else 60
        diff     = abs(live_wr - avg_bt)
        if diff <= 12:
            checks.append({'name': 'WR live vs backtest', 'ok': True,  'val': 'live '+str(round(live_wr,0))+'% vs bt '+str(round(avg_bt,0))+'%', 'pts': 25}); score += 25
        else:
            checks.append({'name': 'WR live vs backtest', 'ok': False, 'val': 'dif '+str(round(diff,0))+'pp (limite 12)', 'pts': 0})
    else:
        checks.append({'name': 'WR live vs backtest', 'ok': False, 'val': 'Necesitas '+str(15-n)+' trades mas', 'pts': 0})
    # 3. Max DD < -20%
    max_dd = port.get('max_dd_pct', 0)
    if max_dd > -20:
        checks.append({'name': 'Max DD paper', 'ok': True,  'val': str(max_dd)+'% (limite -20%)', 'pts': 20}); score += 20
    else:
        checks.append({'name': 'Max DD paper', 'ok': False, 'val': str(max_dd)+'% excede -20%', 'pts': 0})
    # 4. Profit factor >= 1.3
    if n >= 10:
        wins  = [t['pnl_pct'] for t in history if t.get('pnl_pct', 0) > 0]
        loss_ = [t['pnl_pct'] for t in history if t.get('pnl_pct', 0) < 0]
        pf    = sum(wins) / max(abs(sum(loss_)), 0.01) if loss_ else 9.9
        if pf >= 1.3:
            checks.append({'name': 'Profit Factor', 'ok': True,  'val': str(round(pf,2))+' (min 1.3)', 'pts': 15}); score += 15
        else:
            checks.append({'name': 'Profit Factor', 'ok': False, 'val': str(round(pf,2))+' < 1.3', 'pts': 0})
    else:
        checks.append({'name': 'Profit Factor', 'ok': False, 'val': 'Datos insuficientes', 'pts': 0})
    # 5. Sin circuit breaker
    try:
        cb = _load_trades().get('circuit_breaker', {})
    except:
        cb = {}
    if not cb.get('active', False):
        checks.append({'name': 'Circuit Breaker', 'ok': True,  'val': 'Inactivo', 'pts': 15}); score += 15
    else:
        checks.append({'name': 'Circuit Breaker', 'ok': False, 'val': 'ACTIVO', 'pts': 0})
    level = ('LISTO' if score >= 85 else 'CASI' if score >= 60 else 'BUILDING' if score >= 30 else 'EARLY')
    return {'score': score, 'level': level, 'checks': checks, 'trades_done': n}


def _model_vs_backtest():
    """Carga las métricas backtest de los modelos y las compara con live."""
    import json as _jj
    from pathlib import Path as _P
    bts = {}
    for jf in _P('/opt/sigma/models').glob('*/*.json'):
        try:
            d = _jj.loads(jf.read_text())
            oos = d.get('metrics_oos', {})
            if oos.get('cagr', 0) > 0:
                k = f"{d.get('symbol','').replace('/USDT','')}_{jf.parent.name}_{d.get('strategy','')}"
                bts[k] = {
                    'wr_bt': oos.get('wr', 0),
                    'cagr_bt': oos.get('cagr', 0),
                    'trades_bt': oos.get('trades', 0),
                    'mc': d.get('mc', {}).get('mc_confidence', 0),
                }
        except: pass
    return bts


def get_trade_summary():
    try:
      return _get_trade_summary_impl()
    except Exception as _e:
      import traceback as _tb
      print(f'[TRADES API ERROR] {type(_e).__name__}: {_e}', flush=True)
      _tb.print_exc()
      return {'open':[],'cooldowns':[],'history':[],'stats':{'total':0,'wins':0,'losses':0,'win_rate':0,'total_pnl':0,'avg_win':0,'avg_loss':0,'best':0,'worst':0,'profit_factor':0},'portfolio':{},'model_performance':{},'live_readiness':{'score':0,'level':'ERROR','checks':[]},'circuit_breaker':False}

def _get_trade_summary_impl():
    state       = _load_trades()
    open_trades = [v for v in state['open'].values() if v.get('status') == 'open']
    cooldowns   = [v for v in state['open'].values() if v.get('status') == 'cooldown']
    all_hist    = state.get('history', [])
    wins        = [t for t in all_hist if t.get('pnl_pct', 0) > 0]
    losses      = [t for t in all_hist if t.get('pnl_pct', 0) < 0]
    n           = len(all_hist)
    live_wr     = round(len(wins) / max(n, 1) * 100, 1)
    total_pnl   = round(sum(t.get('pnl_pct', 0) for t in all_hist), 2)
    avg_win     = round(sum(t['pnl_pct'] for t in wins) / max(len(wins),1), 2)
    avg_loss    = round(sum(t['pnl_pct'] for t in losses) / max(len(losses),1), 2)
    best        = round(max((t.get('pnl_pct',0) for t in all_hist), default=0), 2)
    worst       = round(min((t.get('pnl_pct',0) for t in all_hist), default=0), 2)
    pf          = round(sum(t['pnl_pct'] for t in wins) /
                        max(abs(sum(t['pnl_pct'] for t in losses)), 0.01), 2)

    # ── Portfolio real ──────────────────────────────────────────────────────
    port        = state.get('portfolio', {})
    equity      = port.get('equity', 10000.0)
    initial     = port.get('initial_capital', 10000.0)
    start_str   = port.get('start_date', str(_dt.now().date()))
    eq_hist     = port.get('equity_history', [])
    try:
        days_active = max(1, (_dt.now() - _dt.fromisoformat(start_str)).days)
    except:
        days_active = 1
    ret_pct  = round((equity / initial - 1) * 100, 2)
    cagr_live = round(((equity / initial) ** (365.0 / max(days_active, 1)) - 1) * 100, 2) if days_active >= 3 else None
    max_dd    = port.get('max_dd_pct', 0.0)
    calmar    = round(cagr_live / abs(max_dd), 2) if (cagr_live and max_dd and max_dd < 0) else None
    # Sharpe from equity curve
    sharpe = None
    if len(eq_hist) >= 5:
        try:
            daily_r = [(eq_hist[i]['eq'] - eq_hist[i-1]['eq']) / eq_hist[i-1]['eq'] * 100
                       for i in range(1, len(eq_hist))]
            if daily_r:
                mu = sum(daily_r) / len(daily_r)
                sd = (sum((x-mu)**2 for x in daily_r) / len(daily_r)) ** 0.5
                sharpe = round(mu / sd * (252**0.5), 2) if sd > 0 else None
        except: pass

    # Floating P&L of open trades
    floating_pct = 0.0
    for ot in open_trades:
        floating_pct += ot.get('pnl_pct', 0)  # updated by watcher

    # ── Per-model stats vs backtest ─────────────────────────────────────────
    from collections import defaultdict as _dd2
    mstats = _dd2(lambda: {'w':0,'l':0,'pnl':0.0})
    for t in all_hist:
        mk = f"{t.get('sym','?')}_{t.get('tf','?')}_{t.get('strategy','?')}"
        pnl = t.get('pnl_pct', 0)
        if pnl > 0: mstats[mk]['w'] += 1
        else:       mstats[mk]['l'] += 1
        mstats[mk]['pnl'] += pnl
    try:
        bts = _model_vs_backtest()
    except:
        bts = {}
    model_perf = {}
    for mk, v in mstats.items():
        total_m = v['w'] + v['l']
        live_wr_m = round(v['w'] / max(total_m, 1) * 100, 1)
        bt = bts.get(mk, {})
        wr_bt = bt.get('wr_bt', 0)
        confidence = min(100, round(total_m / 20 * 100))  # 20 trades = 100%
        model_perf[mk] = {
            'trades_live': total_m, 'wr_live': live_wr_m,
            'pnl_live': round(v['pnl'], 2),
            'wr_bt': wr_bt, 'wr_diff': round(live_wr_m - wr_bt, 1),
            'cagr_bt': bt.get('cagr_bt', 0),
            'confidence_pct': confidence,
            'trades_needed': max(0, 20 - total_m),
            'status': ('OK' if abs(live_wr_m - wr_bt) <= 15 or total_m < 10
                       else ('DEGRADADO' if live_wr_m < wr_bt - 15 else 'MEJOR')),
        }

    # ── Risk-of-ruin ───────────────────────────────────────────────────────
    ror = None
    kelly_avg = round(sum(t.get('kelly_pct', 3.3) for t in all_hist) / max(n, 1), 2)
    if n >= 5:
        try: ror = _calc_ror(live_wr, kelly_avg)
        except: pass

    # ── Floating equity (incluyendo trades abiertos) ──────────────────────
    float_equity = round(equity * (1 + floating_pct / 100), 2)

    return {
        'open':     open_trades,
        'cooldowns': cooldowns,
        'history':  list(reversed(all_hist[-30:])),
        'circuit_breaker': state.get('circuit_breaker', {}).get('active', False),
        'stats': {
            'total': n, 'wins': len(wins), 'losses': len(losses),
            'win_rate': live_wr, 'total_pnl': total_pnl,
            'avg_win': avg_win, 'avg_loss': avg_loss,
            'best': best, 'worst': worst, 'profit_factor': pf,
        },
        'portfolio': {
            'initial': initial, 'equity': round(equity, 2),
            'float_equity': float_equity,
            'return_pct': ret_pct, 'cagr_live': cagr_live,
            'max_dd': max_dd, 'calmar': calmar, 'sharpe': sharpe,
            'days_active': days_active,
            'commission_paid': round(port.get('total_commission', 0), 2),
            'funding_received': round(port.get('total_funding', 0), 2),
            'equity_history': eq_hist[-100:],
            'peak': round(port.get('peak_equity', equity), 2),
            'risk_of_ruin': ror,
            'kelly_avg': kelly_avg,
        },
        'model_performance': model_perf,
        'live_readiness': _calc_readiness(all_hist, model_perf, port),
    }


def regen_dashboard():
    # Espera inicial para no competir al arrancar
    time.sleep(15)
    while True:
        try:
            subprocess.run(
                ['/opt/sigma_env/bin/python', 'engine/live/dashboard.py'],
                cwd=str(BASE), capture_output=True, timeout=60
            )
        except:
            pass
        # 5 minutos — los datos en vivo se actualizan por JS (/api/stats cada 5s)
        time.sleep(300)


def get_live_stats():
    try:
        conn  = sqlite3.connect(str(DB))
        total = conn.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        rate  = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE ts > datetime('now','localtime','-1 hours')"
        ).fetchone()[0]
        by_tf = {r[0]: r[1] for r in conn.execute('SELECT tf,COUNT(*) FROM runs GROUP BY tf')}
        conn.close()
        return {'total': total, 'rate_hr': rate, 'by_tf': by_tf}
    except:
        return {'total': 0, 'rate_hr': 0, 'by_tf': {}}


_regime_cache   = {}
_regime_ts      = 0
_regime_changes = []   # historial de cambios de régimen (últimas 24h)

def _refresh_regime():
    """Actualiza el cache de regimenes en background cada 5 minutos."""
    global _regime_cache, _regime_ts, _regime_changes
    while True:
        try:
            new = _compute_regime()
            # Detectar cambios asset por asset
            for asset, data in new.items():
                old_reg = _regime_cache.get(asset, {}).get('regime', '')
                new_reg = data.get('regime', '')
                if old_reg and new_reg and old_reg != new_reg and new_reg != 'UNKNOWN':
                    change = {
                        'asset': asset,
                        'from':  old_reg,
                        'to':    new_reg,
                        'ts':    time.strftime('%Y-%m-%d %H:%M'),
                        'price': data.get('price', 0),
                        'rsi_w': data.get('rsi_w', 0),
                    }
                    _regime_changes.insert(0, change)
                    _regime_changes[:] = _regime_changes[:20]  # máx 20
            _regime_cache = new
            _regime_ts    = time.time()
        except:
            pass
        time.sleep(300)

def _compute_regime():
    """Calcula el regimen actual (BULL/RANGE/BEAR) de cada par en tiempo real."""
    try:
        import ccxt, pandas as pd
        ex = ccxt.binance({'timeout': 15000})
        symbols = {
            'BTC': 'BTC/USDT', 'ETH': 'ETH/USDT', 'LTC': 'LTC/USDT',
            'SOL': 'SOL/USDT', 'BNB': 'BNB/USDT',
        }
        result = {}
        for asset, sym in symbols.items():
            try:
                # Get last 200 weekly closes for RSI_W
                ohlcv = ex.fetch_ohlcv(sym, '1w', limit=30)
                if not ohlcv or len(ohlcv) < 15:
                    result[asset] = {'regime': 'UNKNOWN', 'rsi_w': 50, 'price': 0, 'ema200': 0}
                    continue
                df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','close','v'])
                c  = df['close']
                d  = c.diff()
                g  = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
                ll = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
                rsi_w = float((100 - 100/(1 + g/(ll+1e-9))).iloc[-1])
                price = float(c.iloc[-1])
                # EMA200 from daily
                ohlcv_d = ex.fetch_ohlcv(sym, '1d', limit=210)
                df_d = pd.DataFrame(ohlcv_d, columns=['ts','o','h','l','close','v'])
                ema200 = float(df_d['close'].ewm(span=200, adjust=False).mean().iloc[-1])

                if rsi_w > 55 and price > ema200:
                    regime = 'BULL'
                elif rsi_w < 40 or price < ema200 * 0.97:
                    regime = 'BEAR'
                else:
                    regime = 'RANGE'

                result[asset] = {
                    'regime': regime,
                    'rsi_w':  round(rsi_w, 1),
                    'price':  round(price, 4),
                    'ema200': round(ema200, 4),
                    'pct_vs_ema': round((price/ema200-1)*100, 1),
                }
            except:
                result[asset] = {'regime': 'UNKNOWN', 'rsi_w': 50, 'price': 0, 'ema200': 0}
        return result
    except:
        return {}


def get_regime():
    """Devuelve cache. Nunca bloquea — si no hay cache devuelve loading."""
    if _regime_cache:
        return _regime_cache
    return {a: {'regime': 'LOADING', 'rsi_w': 0, 'price': 0, 'ema200': 0}
            for a in ('BTC','ETH','LTC','SOL','BNB')}


# ── RISK STATUS ───────────────────────────────────────────────────────────────

def get_risk_status():
    """
    Stop mensual de pérdidas basado en datos reales de mercado.
    Calcula cuántos días consecutivos el mercado lleva en BEAR dominante
    y cuántos de los 5 activos están en BEAR ahora mismo.
    Niveles: OK / CAUTION / PAUSE
    """
    try:
        reg = _regime_cache or {}
        if not reg:
            return {'level': 'OK', 'msg': 'Sin datos de régimen', 'bear_assets': 0, 'bear_days': 0}

        # Contar activos en BEAR ahora
        bear_now  = sum(1 for v in reg.values() if v.get('regime') == 'BEAR')
        bull_now  = sum(1 for v in reg.values() if v.get('regime') == 'BULL')
        total     = len(reg)

        # Contar días consecutivos con cambios hacia BEAR en el historial
        bear_streak = 0
        for ch in _regime_changes:
            if ch.get('to') == 'BEAR':
                bear_streak += 1
            elif ch.get('to') == 'BULL':
                break

        # Calcular RSI_W promedio (< 42 indica mercado muy débil)
        avg_rsi_w = sum(v.get('rsi_w', 50) for v in reg.values()) / max(total, 1)

        # Reglas de pausa
        if bear_now >= 4 and avg_rsi_w < 38:
            level = 'PAUSE'
            msg   = (f'{bear_now}/5 activos en BEAR + RSI_W promedio {avg_rsi_w:.0f} — '
                     f'REDUCIR EXPOSICIÓN, solo adaptive si hay modelo')
        elif bear_now >= 3 or avg_rsi_w < 44:
            level = 'CAUTION'
            msg   = (f'{bear_now}/5 activos en BEAR — '
                     f'PRECAUCIÓN: solo modelos A+ y máximo 1 slot')
        else:
            level = 'OK'
            msg   = (f'{bull_now}/5 activos en BULL — condiciones normales, '
                     f'2 slots disponibles')

        return {
            'level':       level,
            'msg':         msg,
            'bear_assets': bear_now,
            'bull_assets': bull_now,
            'avg_rsi_w':   round(avg_rsi_w, 1),
            'bear_streak': bear_streak,
            'max_slots':   0 if level == 'PAUSE' else (1 if level == 'CAUTION' else 2),
        }
    except Exception as e:
        return {'level': 'OK', 'msg': str(e), 'bear_assets': 0, 'bear_days': 0}


# ── SIGNALS CACHE ─────────────────────────────────────────────────────────────
# ── TRADE MANAGEMENT FUNCTIONS ────────────────────────────────────────────────


_CORR_GROUPS = [
    {'BTC/USDT', 'ETH/USDT'},
    {'SOL/USDT', 'BNB/USDT'},
]

def _correlation_penalty(sym, direction, open_trades):
    """Retorna factor de reduccion Kelly si hay posicion correlacionada abierta."""
    try:
        for group in _CORR_GROUPS:
            if sym not in group:
                continue
            peers = group - {sym}
            for key, t in open_trades.items():
                peer_sym = t.get('sym', '')
                peer_dir = t.get('direction', '')
                if peer_sym in peers and peer_dir == direction and t.get('status') == 'open':
                    return 0.5
    except Exception:
        pass
    return 1.0

_vol_cache = {'factor': 1.0, 'regime': 'NORMAL', 'ts': 0.0}

def _get_vol_factor():
    """
    Calcula volatilidad realizada BTC 24h vs promedio 48h.
    Retorna (factor, regime): factor reduce kelly en vol extrema.
    Cache de 30 minutos para no llamar ccxt en cada trade.
    """
    global _vol_cache
    import time as _t
    if _t.time() - _vol_cache['ts'] < 1800:
        return _vol_cache['factor'], _vol_cache['regime']
    try:
        import ccxt as _cx, numpy as _np
        exc   = _cx.binance({'timeout': 5000, 'enableRateLimit': True})
        ohlcv = exc.fetch_ohlcv('BTC/USDT', '1h', limit=49)
        closes = _np.array([c[4] for c in ohlcv], dtype=float)
        rets   = _np.diff(_np.log(closes))
        vol_24 = float(_np.std(rets[-24:]) * _np.sqrt(24))
        vol_48 = float(_np.std(rets) * _np.sqrt(24))
        ratio  = vol_24 / max(vol_48, 0.0001)
        if ratio > 2.5:
            factor, regime = 0.50, 'EXTREME'
        elif ratio > 1.8:
            factor, regime = 0.70, 'HIGH'
        elif ratio > 1.3:
            factor, regime = 0.85, 'ELEVATED'
        else:
            factor, regime = 1.00, 'NORMAL'
        _vol_cache = {'factor': factor, 'regime': regime, 'ts': _t.time()}
        return factor, regime
    except Exception:
        return 1.0, 'NORMAL'


def open_trade(sym, tf, direction, price, sl, tp, strategy='',
               paper=False, grade='B', wr=50.0, cagr=0.0, kelly_pct=3.3):
    # Correlation guard: reducir kelly si hay posicion correlacionada
    _corr_factor = _correlation_penalty(sym, direction, state.get('open', {}))
    if _corr_factor < 1.0:
        kelly_pct = round(kelly_pct * _corr_factor, 2)
    # Volatility Adapter: reducir kelly en volatilidad extrema
    try:
        _vf, _vr = _get_vol_factor()
        if _vf < 1.0:
            kelly_pct = round(kelly_pct * _vf, 2)
            print(f"[VOL ADAPTER] {_vr} vol — kelly reducido a {kelly_pct}%", flush=True)
    except Exception:
        pass
    """Abre un nuevo trade en paper trading."""
    state = _load_trades()
    key   = f'{sym}_{tf}'
    now   = _strftime_chile('%Y-%m-%d %H:%M:%S.%f')
    state.setdefault('open', {})[key] = {
        'sym': sym, 'tf': tf, 'direction': direction,
        'entry': price, 'sl': sl, 'tp': tp,
        'strategy': strategy, 'grade': grade, 'wr': wr, 'cagr': cagr,
        'kelly_pct': round(kelly_pct, 2),
        'opened_at': now, 'status': 'open',
    }
    port = state.setdefault('portfolio', {
        'initial_capital': 10000, 'equity': 10000,
        'start_date': now[:10], 'equity_history': [],
        'peak_equity': 10000, 'max_dd_pct': 0,
        'total_commission': 0, 'total_funding': 0,
    })
    _save_trades(state)
    return state['open'][key]


def close_trade(sym, tf, exit_price, reason='MANUAL'):
    """Cierra un trade y registra resultado en historial."""
    state = _load_trades()
    key   = f'{sym}_{tf}'
    trade = state.get('open', {}).get(key)
    if not trade or trade.get('status') != 'open':
        return None

    entry     = trade.get('entry', 0)
    direction = trade.get('direction', 'long')
    kelly_pct = trade.get('kelly_pct', 3.3)

    if entry > 0 and exit_price > 0:
        raw = ((exit_price - entry) / entry if direction == 'long'
               else (entry - exit_price) / entry) * 100
        pnl_pct = round(raw * 100, 2)  # P&L real sin escalar por Kelly
    else:
        pnl_pct = 0.0

    commission = round((entry + exit_price) * 0.0004, 4) if entry > 0 else 0.0
    funding    = 0.0

    port = state.setdefault('portfolio', {
        'initial_capital': 10000, 'equity': 10000,
        'start_date': _strftime_chile('%Y-%m-%d'), 'equity_history': [],
        'peak_equity': 10000, 'max_dd_pct': 0,
        'total_commission': 0, 'total_funding': 0,
    })
    eq_before   = port.get('equity', 10000)
    eq_after    = round(eq_before * (1 + pnl_pct / 100), 2)
    port['equity'] = eq_after
    port['total_commission'] = round(port.get('total_commission', 0) + commission, 4)
    peak = max(port.get('peak_equity', eq_after), eq_after)
    port['peak_equity'] = peak
    dd = round((eq_after - peak) / peak * 100, 2) if peak > 0 else 0
    port['max_dd_pct'] = min(port.get('max_dd_pct', 0), dd)
    hist_eq = port.get('equity_history', [])
    hist_eq.append({'eq': eq_after, 'date': now[:10]})
    port['equity_history'] = hist_eq[-100:]

    now = _strftime_chile('%Y-%m-%d %H:%M:%S.%f')
    closed = {**trade,
              'exit_price': exit_price, 'pnl_pct': pnl_pct,
              'reason': reason, 'closed_at': now,
              'commission': commission, 'funding': funding,
              'equity_after': eq_after}

    hist = state.setdefault('history', [])
    hist.append(closed)
    state['history'] = hist[-200:]

    # Cooldown: 1h para evitar re-entrada inmediata
    trade['status']         = 'cooldown'
    trade['cooldown_until'] = time.time() + 3600

    _save_trades(state)
    _update_live_stats(closed)
    return closed


def check_auto_close(sym, tf, current_price):
    """Verifica si SL o TP fue tocado y cierra el trade automaticamente."""
    state = _load_trades()
    key   = f'{sym}_{tf}'
    trade = state.get('open', {}).get(key)
    if not trade or trade.get('status') != 'open':
        return None

    entry     = trade.get('entry', 0)
    sl        = trade.get('sl', 0)
    tp        = trade.get('tp', 0)
    direction = trade.get('direction', 'long')

    if not (entry and sl and tp and current_price):
        return None

    hit = None
    if direction == 'long':
        if current_price <= sl:    hit = 'SL_HIT'
        elif current_price >= tp:  hit = 'TP_HIT'
    else:
        if current_price >= sl:    hit = 'SL_HIT'
        elif current_price <= tp:  hit = 'TP_HIT'

    if hit:
        return close_trade(sym, tf, current_price, hit)
    return None


def is_blocked(sym, tf):
    """True si hay trade abierto o cooldown activo para este sym/tf."""
    state = _load_trades()
    key   = f'{sym}_{tf}'
    trade = state.get('open', {}).get(key)
    if not trade:
        return False
    if trade.get('status') == 'open':
        return True
    if trade.get('status') == 'cooldown':
        if time.time() < trade.get('cooldown_until', 0):
            return True
        del state['open'][key]
        _save_trades(state)
    return False


def check_circuit_breaker():
    """True si el circuit breaker esta activo."""
    cb = _load_trades().get('circuit_breaker', {})
    return bool(cb and cb.get('active', False))


_signals_cache = {}
_signals_ts    = 0

def _refresh_signals():
    global _signals_cache, _signals_ts
    time.sleep(2)  # pequeña pausa inicial
    while True:
        try:
            result = _compute_signals()
            if result and result.get('models') is not None:
                _signals_cache = result
                _signals_ts    = time.time()
        except Exception as _e:
            print(f'[SIGNALS ERROR] {_e}', flush=True)
        time.sleep(30)  # cada 30 segundos

def _compute_signals():
    """Chequea señal activa en último candle cerrado para cada modelo guardado."""
    import glob, json as _j
    try:
        import ccxt, pandas as pd
        ex = ccxt.binance({'timeout': 20000, 'options': {'defaultType': 'future'}})
    except:
        return {}

    SKIP = {'config.json','adaptive_params.json','walk_forward_v2.json',
            'current_params.json','regime_params.json','config_aggressive.json',
            'new_strategy.json','conservative.json'}

    GRADES = [(0.72,'A+'),(0.58,'A'),(0.44,'B'),(0.30,'C')]
    GRADES_LOWR= [(0.44,'B'),(0.30,'C')]  # WR<50% nunca mejor que B
    def grade(s, wr=100):
        caps = GRADES_LOWR if (0 < wr < 50) else GRADES
        for t,g in caps:
            if s >= t: return g
        return 'D'

    def score_fn(m):
        t=m.get('trades',0); ty=m.get('trades_year',0)
        wr=m.get('wr',0); cagr=m.get('cagr',0)
        dd=m.get('dd',0); pf=m.get('pf',1)
        if t<10 or cagr<=0: return -9999
        if ty<=0 and t>0: ty=t*(365/600)
        if ty<3: return -9999
        if wr<=0 and cagr>0: wr=50
        return round(min(ty/12,1)*0.20+min(cagr,60)/60*0.40+
                     max(wr/100-.5,0)/.20*0.20+
                     min(cagr/abs(dd) if dd<0 else 0,5)/5*.15+
                     min(pf,3)/3*.05, 4)

    def recommend(m, mtype, regime):
        """Calcula si un modelo debe activarse. Extrae todos los valores
        con conversion explicita para evitar bugs de closure con variables globales."""
        try:
            # Extraccion segura — convierte explicitamente a tipos numericos
            _tr   = float(m.get('trades')      or 0)
            _wr   = float(m.get('wr')          or 0)
            _dd   = float(m.get('dd')          or 0)
            _cagr = float(m.get('cagr')        or 0)
            _ty   = float(m.get('trades_year') or 0)
            if _ty <= 0 and _tr > 0:
                _ty = _tr * (365.0 / 600)
            _g = grade(score_fn(m), float(m.get('wr') or 0))
        except Exception as _ex:
            return 'NO_ACTIVAR', f'Error extrayendo metricas: {_ex}'

        # Filtros de calidad
        if _g == 'D':                        return 'NO_ACTIVAR', 'Grade D — sin edge'
        if _cagr > 0 and _cagr < 12:        return 'NO_ACTIVAR', f'CAGR {_cagr:.1f}% insuf. (<12%)'
        if _cagr > 0 and _cagr < 15:        return 'CONDICIONAL', f'CAGR {_cagr:.1f}% bajo'
        if _tr < 10:                         return 'NO_ACTIVAR', f'Solo {int(_tr)} trades OOS'
        if _wr > 0 and _wr < 42:            return 'NO_ACTIVAR', f'WR {_wr:.0f}% muy bajo'
        if _dd < -35:                        return 'NO_ACTIVAR', f'DD {_dd:.0f}% excesivo'
        if _g == 'C':                        return 'CONDICIONAL', 'Grade C'
        if _dd < -25:                        return 'CONDICIONAL', f'DD {_dd:.0f}% alto'
        if _ty > 0 and _ty < 4:             return 'CONDICIONAL', f'{_ty:.0f} trades/ano — pocas senales'
        if _wr > 0 and _wr < 50:            return 'CONDICIONAL', f'WR {_wr:.0f}% — monitorear'
        # Regimen incompatible
        if regime == 'BULL' and mtype == 'short': return 'ESPERAR', 'Regimen BULL — no shorts'
        if regime == 'BEAR' and mtype == 'long':  return 'ESPERAR', 'Regimen BEAR — no longs'
        return 'ACTIVAR', f'Grade {_g} | WR {_wr:.0f}% | DD {_dd:.0f}%'

    def _feats(df):
        c=df['close']; h=df['high']; l=df['low']; v=df['volume']
        tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        df['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
        df['ema200']=c.ewm(span=200,adjust=False).mean()
        df['ema50']=c.ewm(span=50,adjust=False).mean()
        df['ema21']=c.ewm(span=21,adjust=False).mean()
        df['vol_ma']=v.rolling(20).mean()
        d=c.diff(); g=d.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
        ll=(-d.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
        df['rsi14']=100-100/(1+g/(ll+1e-9))
        cw=c.resample('W').last().ffill(); dw=cw.diff()
        gw=dw.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
        lw=(-dw.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
        rsiw=100-100/(1+gw/(lw+1e-9))
        df['rsi_w']=rsiw.reindex(df.index,method='ffill').fillna(50)
        return df

    def _signal(df, strategy, params):
        """True si hay señal en el último candle cerrado."""
        try:
            row=df.iloc[-2]; c=df['close']; h=df['high']
            if strategy=='breakout':
                lb=params.get('lookback',30); vm=params.get('vol_mult',1.5)
                return bool(row['close']>h.iloc[-lb-2:-2].max() and row['volume']>row['vol_ma']*vm)
            elif strategy=='tma_bands':
                p=params.get('tma_period',14); am=params.get('atr_mult',1.5)
                tma=c.rolling(p).mean().rolling(p).mean()
                return bool(row['close']<=tma.iloc[-2]-df['atr'].iloc[-2]*am)
            elif strategy=='mean_rev':
                return bool(row['rsi14']<params.get('rsi_os',35))
            elif strategy=='pullback':
                et=params.get('ema_type',21); re=params.get('rsi_entry',45)
                ecol=f'ema{et}' if f'ema{et}' in df.columns else 'ema21'
                return bool(row['close']>row[ecol] and row['rsi14']<re)
            elif strategy in ('regime_adaptive','momentum'):
                return bool(row['rsi14']<45)
            elif 'short' in strategy or 'breakdown' in strategy:
                return bool(row['rsi14']>55)
        except: pass
        return False

    # Fetch regime
    regime = 'RANGE'
    try:
        reg_d = _compute_regime()
        bear=sum(1 for v in reg_d.values() if v.get('regime')=='BEAR')
        bull=sum(1 for v in reg_d.values() if v.get('regime')=='BULL')
        regime = 'BEAR' if bear>=3 else ('BULL' if bull>=3 else 'RANGE')
    except: pass

    # ── Actualizar BTC Dominance proxy (cached 30min) ───────────────────────
    try:
        _get_btc_dominance_proxy()
    except: pass

    # ── Drawdown actual del portfolio (para ajuste dinámico de Kelly) ───────
    _port_state = _load_trades()
    _port       = _port_state.get('portfolio', {})
    _equity_now = _port.get('equity', 10000)
    _peak_eq    = _port.get('peak_equity', _equity_now)
    _current_dd = round((_equity_now - _peak_eq) / _peak_eq * 100, 2) if _peak_eq > 0 else 0
    # Escala de Kelly por drawdown:
    # 0% a -5%   → Kelly normal
    # -5% a -10% → Kelly × 0.70  (reducir 30%)
    # -10% a -15%→ Kelly × 0.50  (reducir 50%)
    # > -15%     → Kelly × 0.25  (modo supervivencia)
    if _current_dd <= -15:
        _dd_kelly_mult = 0.25
    elif _current_dd <= -10:
        _dd_kelly_mult = 0.50
    elif _current_dd <= -5:
        _dd_kelly_mult = 0.70
    else:
        _dd_kelly_mult = 1.00

    # ── Cerrar trades con SL/TP tocado ANTES de procesar señales ─────────────
    # check_auto_close se llama aquí para TODOS los trades abiertos,
    # no solo cuando has_signal=True (bug anterior: SL se veía pero no cerraba)
    try:
        import urllib.request as _ur2, json as _jw2
        _state2 = _load_trades()
        for _key2, _tr2 in list(_state2.get('open', {}).items()):
            if _tr2.get('status') != 'open': continue
            _sym2 = _tr2.get('sym',''); _tf2 = _tr2.get('tf','')
            if not _sym2: continue
            try:
                _url2 = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={_sym2}USDT'
                _cp2  = float(_jw2.loads(_ur2.urlopen(_url2, timeout=4).read())['price'])
                check_auto_close(_sym2, _tf2, _cp2)
            except: pass
    except: pass

    # Load models
    data_cache = {}
    results = []
    _paper_candidates = {}  # paper trades a abrir tras slot assignment
    tf_alias = {'15m':'15min','5m':'5min','1m':'1min'}

    models_dir = BASE / 'models'
    for tf_dir in sorted(models_dir.iterdir()):
        if not tf_dir.is_dir() or tf_dir.name=='archive': continue
        tf = tf_dir.name
        if tf not in {'4h','1h','15m','5m','1m'}:
            continue  # ignorar TFs no estandar (2h, 3h, etc)
        for jf in sorted(tf_dir.glob('*.json')):
            if jf.name in SKIP: continue
            try:
                d = _j.loads(jf.read_text(encoding='utf-8'))
                m = d.get('metrics_oos',{})
                if not m or m.get('cagr',0)<=0: continue
                symbol   = d.get('symbol','')
                strategy = d.get('strategy','')
                params   = d.get('params',{})
                if not symbol or not strategy: continue

                sym = symbol.replace('/USDT','')
                is_short = any(x in strategy for x in ['short','breakdown'])
                is_adapt = 'adaptive' in strategy
                mtype = 'adaptive' if is_adapt else ('short' if is_short else 'long')

                sc = score_fn(m)
                gr = grade(sc, m.get('wr', 0))
                rec, reason = recommend(m, mtype, regime)
                dd2x = round(m.get('dd',0), 1)   # sin apalancamiento
                # cagr ya incluye funding rate real (historico de Binance Futures)
                # CAGR real del backtest OOS (sin apalancamiento)
                c2x  = round(m.get('cagr',0), 1)  # sin apalancamiento

                # Leer resultado WFT del JSON si existe
                wft_data     = d.get('wft', {})
                wft_pass_rate= wft_data.get('oos_win_rate', None)
                wft_verdict  = wft_data.get('verdict', '')
                wft_windows  = wft_data.get('n_windows', 0)

                # Check live signal
                has_signal = False
                price = 0; sl_price = 0; tp_price = 0
                try:
                    key = (symbol, tf)
                    if key not in data_cache:
                        tf_ccxt = tf_alias.get(tf, tf)
                        raw = ex.fetch_ohlcv(symbol, tf_ccxt, limit=120)
                        if raw:
                            df = pd.DataFrame(raw, columns=['ts','open','high','low','close','volume'])
                            df['ts'] = pd.to_datetime(df['ts'], unit='ms')
                            df.set_index('ts', inplace=True)
                            data_cache[key] = _feats(df)
                    df = data_cache.get(key)
                    if df is not None and len(df) > 50:
                        has_signal = _signal(df, strategy, params)
                        if has_signal:
                            # Precio de entrada = precio LIVE de Binance Futures
                            # (no el close de la vela anterior — refleja la realidad)
                            try:
                                import urllib.request as _ur_e, json as _j_e
                                _url_e = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT'
                                price  = round(float(_j_e.loads(_ur_e.urlopen(_url_e, timeout=3).read())['price']), 4)
                            except:
                                price = round(float(df['close'].iloc[-1]), 4)
                            # ATR de la última vela completa para SL/TP
                            atr   = float(df['atr'].iloc[-1])
                            sl_m  = params.get('sl_mult', 2.0)
                            tp_m  = params.get('tp_mult', 3.0)
                            sl_price = round(price - atr*sl_m if mtype!='short' else price + atr*sl_m, 4)
                            tp_price = round(price + atr*tp_m if mtype!='short' else price - atr*tp_m, 4)
                except: pass

                # Auto-detectar SL/TP y bloquear re-entrada
                if has_signal and price:
                    check_auto_close(sym, tf, price)
                if is_blocked(sym, tf):
                    has_signal = False
                    sl_price = tp_price = price = 0

                # ── PAPER TRADING AUTOMÁTICO ─────────────────────────
                _short_strats = {'breakdown','pullback_short','momentum_short'}
                direction = 'short' if strategy in _short_strats or 'short' in mtype else 'long'
                state = _load_trades()
                key   = f'{sym}_{tf}'
                # Cierre por cambio de regimen
                open_t = state['open'].get(key)
                if open_t and open_t.get('status')=='open' and price:
                    if (regime=='BEAR' and open_t['direction']=='long') or                        (regime=='BULL' and open_t['direction']=='short'): close_trade(sym, tf, price, 'REGIME_CHANGE')
                # Circuit breaker + abrir nuevo trade
                # Registrar candidato para paper trade (se abre solo si slot=1 o 2)
                if (has_signal and price and sl_price and tp_price
                        and key not in state.get('open', {})
                        and not check_circuit_breaker()):
                    _paper_candidates[key] = {
                        'sym':sym,'tf':tf,'dir':direction,'price':price,
                        'sl':sl_price,'tp':tp_price,'strat':strategy,
                        'grade':gr,'wr':m.get('wr',0),'cagr':m.get('cagr',0),
                        'kelly_pct': round(sc * 3.3, 2),
                    }

                # ── EV y Decay ───────────────────────────────────────────
                _wr = m.get('wr', 50)
                _ev = None
                if has_signal and price and sl_price and tp_price:
                    _sl_pct = abs(price - sl_price) / price * 100
                    _tp_pct = abs(tp_price - price) / price * 100
                    _ev = round((_wr/100 * _tp_pct - (1-_wr/100) * _sl_pct) * 2, 2)

                _decay, _live_wr, _n_live = _check_decay(sym, tf, _wr)

                # ── Regime filter: LONG en BEAR solo con ensemble≥2 y MC≥70% ─
                _mc_conf  = d.get('mc', {}).get('mc_confidence', 0) or 0
                _ens_cnt  = 1  # actualizado abajo tras ensemble detection
                _regime_ok = not (regime=='BEAR' and mtype=='long') and not (regime=='BULL' and mtype=='short')

                results.append({
                    'sym': sym, 'tf': tf, 'strategy': strategy, 'type': mtype,
                    'grade': gr, 'score': round(sc,4),
                    'cagr': m.get('cagr',0), 'cagr_2x': c2x,
                    'wr': m.get('wr',0), 'dd': m.get('dd',0), 'dd_2x': dd2x,
                    'trades': m.get('trades',0),
                    'recommendation': rec, 'reason': reason,
                    'signal': has_signal,
                    'price': price, 'sl': sl_price, 'tp': tp_price,
                    'regime_ok': _regime_ok,
                    'wft_pass_rate': wft_pass_rate,
                    'mc_confidence': round(_mc_conf, 1) if d.get('mc') else None,
                    'val_confidence': d.get('validation', {}).get('confidence'),
                    'corr_warning': False,
                    'eff_risk_pct': None,
                    'conf_mult':    None,
                    'val_mc':         d.get('validation', {}).get('monte_carlo', {}).get('p_pos'),
                    'val_wft':        d.get('validation', {}).get('walk_forward', {}).get('pct_positive'),
                    'mc_cagr_p05':   min(d.get('mc', {}).get('mc_cagr_p05') or 0, 999) or None,
                    'mc_dd_p95':     d.get('mc', {}).get('mc_dd_p95'),
                    'wft_verdict':   wft_verdict,
                    'wft_windows':   wft_windows,
                    'ev':            _ev,
                    'decay_warning': _decay,
                    'live_wr':       _live_wr,
                    'n_live_trades': _n_live,
                    'mc_conf_raw':   _mc_conf,
                    'htf_confirms':  True,   # actualizado tras slot assignment
                    'htf_reason':    '',
                    'htf_penalty':   False,
                    'btcd_penalty':  False,
                    'btcd_boost':    False,
                    'btcd_value':    round(_btcd_cache.get('value', 0.5), 2),
                    'current_dd_pct': _current_dd,
                    'dd_kelly_mult':  _dd_kelly_mult,
                })
            except Exception:
                continue

    # Correlación entre activos: clusters que se mueven juntos
    CORR_CLUSTERS = [{'BTC','ETH','LTC'}, {'SOL','BNB'}]

    # MAX_SLOTS dinámico:
    # BEAR: siempre 2 (todos los shorts correlacionados)
    # RANGE/BULL: hasta 3 si el 3er slot aporta diversificación
    # BEAR: conservador (2 slots). RANGE/BULL: hasta 3.
    # Excepción: si hay 3+ señales A+ en cualquier régimen, abrir 3.
    _ap_count = sum(1 for r in results if r.get('grade') in ('A+','A') and r.get('recommendation')=='ACTIVAR')
    MAX_SLOTS = 3  # 3 slots en todos los regimenes
    if _ap_count >= 3 and MAX_SLOTS < 3: MAX_SLOTS = 3  # bonus slot para A+/A

    # ── Trades abiertos: siempre tienen slot asignado ───────────────────────
    try:
        _open_state = _load_trades()
        _open_keys  = {
            f"{t.get('sym','').upper()}_{t.get('tf','')}": i+1
            for i, (k, t) in enumerate(
                sorted(_open_state.get('open', {}).items(),
                       key=lambda x: x[1].get('opened_at', ''))
            )
            if t.get('status') == 'open'
        }
    except Exception:
        _open_keys = {}

    # Poner modelos con trade abierto al frente del sort
    def _sort_key(r):
        k = f"{r.get('sym','').upper()}_{r.get('tf','')}"
        if k in _open_keys:
            return (2, _open_keys[k], r.get('score', 0))   # prioridad maxima
        return (int(r.get('signal', False)), 0, r.get('score', 0))

    results.sort(key=_sort_key, reverse=True)
    seen_sym   = set()
    slot_n     = 0
    slots_dirs = []
    slots_clus = []

    for r in results:
        sym_tf_key = f"{r.get('sym','').upper()}_{r.get('tf','')}"
        has_open   = sym_tf_key in _open_keys

        # Modelos con trade abierto: siempre activos en su slot
        if has_open:
            r['signal']         = True
            r['recommendation'] = 'ACTIVAR'
            r['regime_ok']      = True
            r['has_open_trade'] = True
            # Poblar price/sl/tp desde los datos reales del trade
            try:
                _ot2 = _open_state.get('open', {}).get(sym_tf_key)
                if _ot2 and _ot2.get('status') == 'open':
                    r['price'] = _ot2.get('entry', 0) or r.get('price', 0)
                    r['sl']    = _ot2.get('sl', 0)    or r.get('sl', 0)
                    r['tp']    = _ot2.get('tp', 0)    or r.get('tp', 0)
                    r['open_trade_entry'] = _ot2.get('entry', 0)
                    r['open_trade_since'] = str(_ot2.get('opened_at', ''))[:16]
            except Exception:
                pass

        if r['recommendation'] == 'ACTIVAR' and r['regime_ok']:
            _bear_slot2_ok = not (slot_n >= 1 and r.get('cagr', 0) < 15)
            if r['sym'] not in seen_sym and slot_n < MAX_SLOTS and (_bear_slot2_ok or has_open):
                this_dir  = r['type']
                this_clus = next((i for i,cl in enumerate(CORR_CLUSTERS)
                                  if r['sym'] in cl), -1)

                if slot_n == 2 and not has_open:
                    dir_new  = this_dir not in slots_dirs
                    clus_new = this_clus not in slots_clus or this_clus < 0
                    grade_ok = r.get('grade') in ('A+', 'A')
                    if not (dir_new or clus_new) or not grade_ok:
                        r['slot'] = 0; continue

                r['slot'] = slot_n + 1
                r['corr_warning'] = (this_dir in slots_dirs and
                                     this_clus in slots_clus and this_clus >= 0)
                if slot_n == 2:
                    r['slot3_reduced'] = True
                seen_sym.add(r['sym'])
                slots_dirs.append(this_dir)
                slots_clus.append(this_clus)
                slot_n += 1
            else:
                r['slot'] = 0
        else:
            r['slot'] = -1

    # ── Regime filter: LONG en BEAR sin confirmación → silenciar señal ─────────
    for r in results:
        if regime == 'BEAR' and r.get('type') == 'long' and r.get('signal'):
            ens_c  = r.get('ensemble_count', 1)
            mc_c   = r.get('mc_conf_raw', 0) or 0
            # Permitir solo si ensemble≥2 Y MC≥70% (alta convicción en reversal)
            if ens_c < 2 or mc_c < 70:
                r['signal']         = False
                r['sl']             = 0; r['tp'] = 0; r['price'] = 0
                r['regime_muted']   = True
                r['reason']         = f'BEAR — necesita ensemble≥2 y MC≥70% (actual: E{ens_c} MC{mc_c:.0f}%)'

    # ── Consenso multi-TF: verificar que TF mayor confirme la dirección ──────
    for r in results:
        if not r.get('signal'): continue
        direction = 'short' if r.get('type') == 'short' else 'long'
        try:
            confirms, htf_reason = _htf_confirms(r['sym'], r['tf'], direction, ex, data_cache)
            r['htf_confirms'] = confirms
            r['htf_reason']   = htf_reason
        except:
            r['htf_confirms'] = True
            r['htf_reason']   = 'check omitido'

    # Sizing dinámico por confianza de validación
    CONF_MULT = {'ALTA':1.0,'MEDIA':0.65,'BAJA':0.35,'DESCARTADO':0.0}
    for r in results:
        val_conf   = r.get('val_confidence') or ''
        conf_mult  = CONF_MULT.get(val_conf, 0.5)
        # Ensemble boost: 2 modelos coinciden → +30%, 3+ → +60%
        ens_mult   = r.get('ensemble_mult', 1.0)   # calculado por ensemble v2
        base_risk  = 3.3 * _dd_kelly_mult
        r['eff_risk_pct'] = round(min(base_risk * conf_mult * ens_mult, 6.0), 2)
        r['dd_kelly_mult'] = _dd_kelly_mult
        r['conf_mult']    = conf_mult
        r['ens_mult']     = ens_mult
        # ── Correlation penalty: -40% Kelly si posición correlacionada ─────
        if r.get('corr_warning'):
            r['eff_risk_pct'] = round(r['eff_risk_pct'] * 0.60, 2)
        # ── Decay penalty: -50% Kelly si modelo está degradando en live ────
        if r.get('decay_warning'):
            r['eff_risk_pct'] = round(r['eff_risk_pct'] * 0.50, 2)
        # ── Multi-TF penalty: -35% Kelly si TF mayor no confirma ───────────
        if r.get('signal') and r.get('htf_confirms') == False:
            r['eff_risk_pct'] = round(r['eff_risk_pct'] * 0.65, 2)
            r['htf_penalty']  = True
        # ── Slot 3: Kelly 65% — menos certeza, más conservador ───────────
        if r.get('slot3_reduced'):
            r['eff_risk_pct'] = round(r['eff_risk_pct'] * 0.65, 2)
        # ── BTC Dominance filter: reducir Kelly en alts cuando BTC domina ──
        btcd = _btcd_cache.get('value', 0.5)
        if r.get('signal') and r['sym'] not in ('BTC', 'ETH'):
            if btcd < 0.35:  # BTC domina fuerte → alts en debilidad
                r['eff_risk_pct'] = round(r['eff_risk_pct'] * 0.70, 2)
                r['btcd_penalty'] = True
            elif btcd > 0.65:  # Alts dominan → boost ligero
                r['eff_risk_pct'] = round(min(r['eff_risk_pct'] * 1.15, 6.0), 2)
                r['btcd_boost']   = True

    # ── Ensemble v2: agrupado por sym+tf, solo señales activas, ponderado por diversidad ──
    # Fuentes de señal agrupadas por tipo (cuanto más distintas, más valiosas)
    _SRC_GROUP = {
        # Precio/estructura
        'breakout':'price','pullback':'price','tma_bands':'price','mean_rev':'price',
        'breakdown':'price','pullback_short':'price','higher_highs':'price','lower_lows':'price',
        'engulfing':'price','three_candles':'price','inside_bar':'price','pin_bar':'price',
        'donchian_break':'price','linear_reg_break':'price','atr_channel':'price',
        'pivot_bounce':'price','consecutive_wick':'price','psar_flip':'price',
        # Momentum/osciladores
        'momentum':'momentum','momentum_short':'momentum','roc_momentum':'momentum',
        'dmi_trend':'momentum','aroon_cross':'momentum','trend_strength':'momentum',
        'elder_impulse':'momentum','hull_cross':'momentum','ema_ribbon':'momentum',
        'tema_cross':'momentum','wma_momentum':'momentum','open_close_cross':'momentum',
        # RSI/estocásticos
        'stoch_rsi':'oscillator','cci_reversal':'oscillator','williams_r':'oscillator',
        'mfi_reversal':'oscillator','rsi_divergence':'oscillator','rsi_trend':'oscillator',
        # MACD/trend
        'macd_divergence':'macd','supertrend':'macd','regime_adaptive':'macd',
        'volatility_breakout':'macd','keltner_breakout':'macd','squeeze_pro':'macd',
        'bb_squeeze':'macd','bb_bandwidth':'macd',
        # Volumen
        'chaikin_mf':'volume','obv_divergence':'volume','volume_exhaustion':'volume',
        'volume_climax':'volume','cvd_divergence':'volume','htf_divergence':'volume',
        # Estadístico
        'zscore_reversion':'statistical','vwap_deviation':'statistical',
        'heikin_ashi':'statistical','ichimoku':'statistical',
        # Crypto-específico
        'funding_reversal':'crypto','funding_momentum':'crypto',
    }

    from collections import defaultdict as _dd
    # Agrupar por sym+tf (no solo sym)
    _ens_by_symtf = _dd(lambda: {'long': [], 'short': []})
    for _r in results:
        if not _r.get('signal') or not _r.get('regime_ok'):
            continue
        _key  = f"{_r['sym']}_{_r['tf']}"
        _dir  = _r.get('type', 'long') if _r.get('type') in ('long','short') else 'long'
        _strat= _r.get('strategy','unknown')
        _src  = _SRC_GROUP.get(_strat, 'other')
        _score= _r.get('score', 0)
        _ens_by_symtf[_key][_dir].append((_strat, _src, _score))

    # Calcular ensemble real para cada modelo
    for _r in results:
        _key = f"{_r['sym']}_{_r['tf']}"
        _dir = _r.get('type', 'long') if _r.get('type') in ('long','short') else 'long'
        _signals = _ens_by_symtf[_key][_dir]
        _count   = len(_signals)

        # Diversidad de fuentes (cuantas fuentes DISTINTAS hay)
        _sources  = set(s[1] for s in _signals)
        _n_src    = len(_sources)

        # Multiplicador basado en count + diversidad de fuente
        if _count >= 3 and _n_src >= 3:
            _ens_mult = 1.7   # 3+ señales de 3+ fuentes distintas → máxima convicción
        elif _count >= 3 and _n_src >= 2:
            _ens_mult = 1.5   # 3+ señales de 2 fuentes
        elif _count >= 2 and _n_src >= 2:
            _ens_mult = 1.35  # 2 señales de fuentes distintas (precio+volumen, etc)
        elif _count >= 2 and _n_src == 1:
            _ens_mult = 1.15  # 2 señales pero misma fuente (menos valor)
        else:
            _ens_mult = 1.0   # señal individual

        _r['ensemble_count']    = _count
        _r['ensemble_sources']  = _n_src
        _r['ensemble_mult']     = _ens_mult
        _r['ensemble_boost']    = round(min(_count / 3.0, 1.0), 2)
        _r['ensemble_detail']   = ','.join(sorted(_sources)) if _sources else ''
    
    _cb = _load_trades().get('circuit_breaker',{}).get('active',False)

    # Abrir paper trades solo para slots 1 y 2 (después del slot assignment)
    _state = _load_trades()
    # No abrir nuevas posiciones si el sistema esta en pausa manual
    if os.path.exists('/opt/sigma/results/pausa.flag'):
        pass  # pausa activa — skip apertura
    else:
     _open_n = sum(1 for t in _state['open'].values() if t.get('status')=='open')
    for _r in sorted(results, key=lambda x: x.get('slot',0), reverse=True):
        if _r.get('slot',0) not in (1,2): continue
        _k = _r['sym']+'_'+_r['tf']
        if _k not in _paper_candidates or _k in _state['open']: continue
        if _open_n >= 2: break
        _c = _paper_candidates[_k]
        open_trade(_c['sym'],_c['tf'],_c['dir'],_c['price'],_c['sl'],_c['tp'],
                   _c['strat'],paper=True,grade=_c['grade'],wr=_c['wr'],cagr=_c['cagr'])
        _open_n += 1

    # ── Modelos con trade abierto: signal=True + datos reales ──────────────
    try:
        _ts_final = _load_trades()
        _open_final = _ts_final.get('open', {})
        for _rf in results:
            if not _rf.get('has_open_trade'):
                continue
            _kf = f"{_rf.get('sym','').upper()}_{_rf.get('tf','')}"
            _otf = _open_final.get(_kf)
            if not _otf or _otf.get('status') != 'open':
                continue
            # Forzar signal=True y datos del trade real
            _rf['signal']   = True
            _rf['price']    = _otf.get('entry') or _rf.get('price') or 0
            _rf['sl']       = _otf.get('sl')    or _rf.get('sl')    or 0
            _rf['tp']       = _otf.get('tp')    or _rf.get('tp')    or 0
            # Corregir type y strategy desde el trade real
            _trade_dir = _otf.get('direction', '')
            if _trade_dir == 'short':
                _rf['type'] = 'short'
            elif _trade_dir == 'long':
                _rf['type'] = 'long'
            if _otf.get('strategy'):
                _rf['strategy'] = _otf['strategy']
            _rf['open_trade_entry'] = _otf.get('entry', 0)
            _rf['open_trade_since'] = str(_otf.get('opened_at', ''))[:16]
    except Exception:
        pass

    return {'regime':regime,'models':results,'updated':_strftime_chile('%H:%M:%S'),'circuit_breaker':_cb}


HUD_PINE      = BASE / 'results' / 'pine_scripts' / 'SIGMA_v13_COMPLETO.pine'
ENGINE_PINE   = BASE / 'results' / 'pine_scripts' / 'SIGMA_ENGINE_v1.pine'
STRATEGY_PINE = BASE / 'results' / 'pine_scripts' / 'SIGMA_ENGINE_STRATEGY_v1.pine'
UPDATE_HUD_PY = BASE / 'update_hud.py'

def _serve_file(handler, path, filename):
    if path.exists():
        body = path.read_bytes()
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/plain; charset=utf-8')
        handler.send_header('Content-Length', len(body))
        handler.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        handler.send_header('Access-Control-Allow-Origin', '*')
        handler.end_headers()
        handler.wfile.write(body)
    else:
        handler.send_response(404)
        handler.end_headers()
        handler.wfile.write(b'File not found')

def _send_json(handler, data):
    body = json.dumps(data, default=str).encode()
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', len(body))
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.end_headers()
    handler.wfile.write(body)


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/regime':
            _send_json(self, get_regime())

        elif self.path == '/api/stats':
            _send_json(self, get_live_stats())

        elif self.path == '/download/engine':
            # Actualiza modelos antes de descargar
            try:
                subprocess.run(
                    ['/opt/sigma_env/bin/python', str(UPDATE_HUD_PY)],
                    cwd=str(BASE), capture_output=True, timeout=30
                )
            except Exception:
                pass
            _serve_file(self, ENGINE_PINE, 'SIGMA_ENGINE_v1.pine')

        elif self.path == '/download/strategy':
            _serve_file(self, STRATEGY_PINE, 'SIGMA_ENGINE_STRATEGY_v1.pine')

        elif self.path == '/download/terminal':
            _serve_file(self, HUD_PINE, 'SIGMA_v13_COMPLETO.pine')

        elif self.path.startswith('/download/model/'):
            fname = self.path.split('/download/model/')[-1]
            fpath = BASE / 'results' / 'pine_scripts' / fname
            _serve_file(self, fpath, fname)
        elif self.path == '/download/hud':
            # Backward compat → redirige al engine
            _serve_file(self, ENGINE_PINE, 'SIGMA_ENGINE_v1.pine')

        elif self.path == '/api/hud_info':
            info = {'available': False, 'models': 0, 'updated': '—'}
            if HUD_PINE.exists():
                import re
                src = HUD_PINE.read_text(encoding='utf-8')
                ts_match = re.search(r'[Aa]ctualizado\s*[:\s]+(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}[^\n]*)', src)
                try:
                    from pathlib import Path as _P2
                    import json as _j2
                    n_models = sum(1 for f in (_P2('/opt/sigma/models')).glob('*/*.json')
                                   if 'archive' not in str(f)
                                   and _j2.loads(f.read_text()).get('metrics_oos',{}).get('cagr',0)>0)
                except:
                    n_models = len(re.findall(r'str\.contains\(_tk,"(?:ETH|SOL|BNB|LTC|BTC|XRP)"', src)) // 2
                info = {
                    'available': True,
                    'models':    n_models,
                    'updated':   ''.join(c for c in (ts_match.group(1) if ts_match else '—') if ord(c) < 128).strip() if ts_match else '—',
                    'size_kb':   round(HUD_PINE.stat().st_size / 1024, 1),
                }
            _send_json(self, info)

        elif self.path == '/api/new_records':
            # Modelos guardados en las últimas 24h
            try:
                import glob, json as _json
                records = []
                for jf in glob.glob(str(BASE / 'models' / '**' / '*.json'), recursive=True):
                    if 'archive' in jf or 'config' in jf: continue
                    try:
                        import os
                        mtime = os.path.getmtime(jf)
                        if time.time() - mtime < 86400:  # últimas 24h
                            d = _json.loads(open(jf).read())
                            m = d.get('metrics_oos', {})
                            if m.get('cagr', 0) > 0:
                                records.append({
                                    'file': os.path.basename(jf),
                                    'symbol': d.get('symbol',''),
                                    'tf': d.get('tf',''),
                                    'strategy': d.get('strategy',''),
                                    'cagr': m.get('cagr',0),
                                    'wr': m.get('wr',0),
                                    'saved_at': d.get('saved_at',''),
                                })
                    except: pass
                _send_json(self, {'records': sorted(records, key=lambda x: x['saved_at'], reverse=True)})
            except Exception as e:
                _send_json(self, {'records': [], 'error': str(e)})

        elif self.path == '/api/regime_changes':
            _send_json(self, {'changes': _regime_changes})

        elif self.path == '/api/risk_status':
            _send_json(self, get_risk_status())

        elif self.path == '/api/trades':
            try:
                _send_json(self, get_trade_summary())
            except Exception as _te:
                print(f'[API/trades ERROR] {_te}', flush=True)
                _send_json(self, {'open':[],'history':[],'stats':{},'portfolio':{},'live_readiness':{'score':0,'level':'ERROR','checks':[]}})



        elif self.path == '/api/pine_html':
            try:
                import time as _ti
                pine_dir = BASE / 'results' / 'pine_scripts'
                models = []
                now = _ti.time()
                for fname in sorted(pine_dir.glob('SIGMA_*_CAGR*.pine')):
                    name = fname.stem; parts = name.split('_')
                    try:
                        sym = parts[1]; tf = parts[2].lower()
                        ci = next(i for i,p in enumerate(parts) if p.startswith('CAGR'))
                        strategy = '_'.join(parts[3:ci])
                        cagr = float(parts[ci].replace('CAGR','').replace('pct',''))
                        stat = fname.stat()
                        age_min = (now - stat.st_mtime) / 60
                        age_h = age_min / 60
                        age_str = _dt.fromtimestamp(stat.st_mtime, tz=_TZ_CL).strftime('%H:%M')
                        models.append({'sym':sym,'tf':tf,'strategy':strategy,'cagr':cagr,
                                       'age_str':age_str,'age_min':age_min,
                                       'mtime':stat.st_mtime,'fname':fname.name,
                                       'is_new':age_min<120})
                    except: pass
                models.sort(key=lambda x: -x['mtime'])

                SC = {'BTC':'#f7931a','ETH':'#627eea','LTC':'#bfbbbb','SOL':'#9945ff','BNB':'#f3ba2f'}
                SM = {'momentum_short':'MOM↓','breakdown':'BDN↓','pullback_short':'PBK↓',
                      'breakout':'BRK↑','tma_bands':'TMA','mean_rev':'MRV',
                      'regime_adaptive':'RAD','momentum':'MOM↑','pullback':'PBK↑'}

                def cagr_col(v):
                    return '#00c853' if v>=40 else '#69f0ae' if v>=20 else '#8bc48b' if v>=0 else '#f44336'

                new_count = sum(1 for m in models if m['is_new'])
                nb = (f'<span style="background:#00c853;color:#000;padding:1px 6px;'
                      f'border-radius:8px;font-size:9px;font-weight:bold;margin-left:6px">'
                      f'+{new_count} new</span>') if new_count else ''

                rows = ''
                for m in models:
                    sc = SC.get(m['sym'], '#58a6ff')
                    strat = SM.get(m['strategy'], m['strategy'])
                    cc = cagr_col(m['cagr'])
                    dot = ('<span style="display:inline-block;width:7px;height:7px;'
                           'background:#00c853;border-radius:50%;'
                           'animation:pulse 1.5s infinite"></span>'
                           if m['is_new'] else
                           '<span style="display:inline-block;width:7px;height:7px;'
                           'background:#21262d;border-radius:50%"></span>')
                    cagr_txt = f'+{m["cagr"]:.0f}%' if m['cagr'] > 0 else f'{m["cagr"]:.0f}%'
                    bg = 'background:rgba(0,200,83,0.03)' if m['is_new'] else ''
                    rows += (
                        f'<tr style="border-bottom:1px solid #0d1117;{bg}">'
                        f'<td style="padding:4px 8px;width:16px">{dot}</td>'
                        f'<td style="padding:4px 6px;color:#555;font-size:10px;'
                        f'font-family:monospace;white-space:nowrap">{m["age_str"]}</td>'
                        f'<td style="padding:4px 6px">'
                        f'<b style="color:{sc};font-size:11px">{m["sym"]}</b></td>'
                        f'<td style="padding:4px 4px;color:#555;font-size:10px">'
                        f'{m["tf"].upper()}</td>'
                        f'<td style="padding:4px 6px;color:#6a737d;font-size:10px">'
                        f'{strat}</td>'
                        f'<td style="padding:4px 8px;text-align:right">'
                        f'<b style="color:{cc};font-size:11px">{cagr_txt}</b></td>'
                        f'<td style="padding:4px 8px;text-align:right">'
                        f'<a href="/{m["fname"]}" download="{m["fname"]}" '
                        f'style="color:#58a6ff;font-size:12px;text-decoration:none">&#11015;</a>'
                        f'</td></tr>'
                    )

                TH = ('padding:3px 6px;color:#333;font-size:9px;text-transform:uppercase;'
                      'letter-spacing:.8px;border-bottom:2px solid #21262d;font-weight:500')
                ts = _strftime_chile('%H:%M')
                html = (
                    f'<div style="background:#0d1117;border:1px solid #21262d;'
                    f'border-radius:8px;padding:12px 14px;margin:16px 0">'
                    f'<div style="display:flex;justify-content:space-between;'
                    f'align-items:center;margin-bottom:8px">'
                    f'<span style="color:#c9d1d9;font-weight:700;font-size:12px">'
                    f'&#127794; Pine Scripts &#8212; &#218;ltimas actualizaciones{nb}</span>'
                    f'<span style="color:#333;font-size:10px">{len(models)} modelos &middot; {ts}</span>'
                    f'</div>'
                    f'<table style="width:100%;border-collapse:collapse">'
                    f'<thead><tr>'
                    f'<th style="{TH};width:16px"></th>'
                    f'<th style="{TH}">Cu&#225;ndo</th>'
                    f'<th style="{TH}">Activo</th>'
                    f'<th style="{TH}">TF</th>'
                    f'<th style="{TH}">Estrategia</th>'
                    f'<th style="{TH};text-align:right">CAGR</th>'
                    f'<th style="{TH};text-align:right">Pine</th>'
                    f'</tr></thead>'
                    f'<tbody>{rows}</tbody>'
                    f'</table>'
                    f'<div style="margin-top:6px;color:#30363d;font-size:10px">'
                    f'&#9432; Ordenado por &#250;ltima actualizaci&#243;n &middot; '
                    f'Verde = generado hace &lt;2h por el optimizador</div>'
                    f'</div>'
                )
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(html.encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())

        elif self.path == '/api/pine_status':
            try:
                import time as _ti, re as _re, glob as _gl
                pine_dir = BASE / 'results' / 'pine_scripts'
                models = []
                now = _ti.time()
                for fname in sorted(pine_dir.glob('SIGMA_*_CAGR*.pine')):
                    name = fname.stem
                    parts = name.split('_')
                    try:
                        sym = parts[1]; tf = parts[2].lower()
                        cagr_idx = next(i for i,p in enumerate(parts) if p.startswith('CAGR'))
                        strategy = '_'.join(parts[3:cagr_idx])
                        cagr_str = parts[cagr_idx].replace('CAGR','').replace('pct','')
                        cagr = float(cagr_str) if cagr_str.lstrip('-').isdigit() else 0
                        stat = fname.stat()
                        age_min = (now - stat.st_mtime) / 60
                        age_h = age_min / 60
                        age_str = _dt.fromtimestamp(stat.st_mtime, tz=_TZ_CL).strftime('%H:%M')
                        models.append({
                            'sym': sym, 'tf': tf, 'strategy': strategy,
                            'cagr': cagr, 'size_kb': round(stat.st_size/1024, 1),
                            'age_str': age_str, 'age_min': age_min,
                            'mtime': stat.st_mtime, 'fname': fname.name,
                            'is_new': age_min < 120,
                        })
                    except: pass
                # Sort by most recently updated first
                models.sort(key=lambda x: -x['mtime'])

                # Scrape recent NUEVO MEJOR events from pipeline logs
                events = []
                rep = BASE / 'results' / 'reports'
                pat = _re.compile(
                    r'\[(\d{2}:\d{2}:\d{2})\]\s+\[(\w+)/USDT\s+(\w+)\]\s+NUEVO MEJOR:\s+'
                    r'(\w+)(?:\s+score=[\d.]+)?\s+OOS\s+([+-]?\d+\.?\d*)%'
                    r'(?:\s+WR(\d+)%)?'
                )
                for log in sorted(rep.glob('*_pipeline.log'))[-12:]:
                    try:
                        lines = log.read_text(encoding='utf-8', errors='replace').splitlines()
                        for line in lines[-300:]:
                            m = pat.search(line)
                            if m:
                                events.append({
                                    'time': m.group(1),
                                    'sym': m.group(2),
                                    'tf': m.group(3).lower(),
                                    'strategy': m.group(4),
                                    'oos': float(m.group(5)),
                                    'wr': int(m.group(6)) if m.group(6) else None,
                                })
                    except: pass
                # Deduplicate by sym+tf+strategy, keep last occurrence
                seen = {}
                for ev in events:
                    k = f"{ev['sym']}_{ev['tf']}_{ev['strategy']}"
                    seen[k] = ev
                recent_events = list(seen.values())[-20:]

                _send_json(self, {
                    'models': models, 'total': len(models),
                    'events': recent_events,
                    'updated': _strftime_chile('%H:%M'),
                })
            except Exception as e:
                _send_json(self, {'models': [], 'total': 0, 'events': [], 'updated': '--', 'error': str(e)})


        elif self.path == '/api/trainer_status':
            try:
                log_path = BASE / 'results/reports/trainer.log'
                lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
                recent = [l for l in lines if l.strip()][-40:]
                db_total = 0
                try:
                    import sqlite3 as _sq
                    c = _sq.connect(str(DB))
                    db_total = c.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
                    rate_hr  = c.execute("SELECT COUNT(*) FROM runs WHERE ts > datetime('now','localtime','-1 hours')").fetchone()[0]
                    c.close()
                except: rate_hr = 0
                _send_json(self, {'lines': recent, 'db_total': db_total, 'rate_hr': rate_hr})
            except Exception as e:
                _send_json(self, {'lines': [str(e)], 'db_total': 0, 'rate_hr': 0})

        elif self.path == '/api/signals':
            if _signals_cache:
                _send_json(self, _signals_cache)
            else:
                _send_json(self, {'regime': 'LOADING', 'models': [], 'updated': '—'})

        elif self.path in ('/', '/dashboard.html'):
            # Serve dashboard with no-cache headers
            try:
                body = (BASE / 'results' / 'charts' / 'dashboard.html').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', len(body))
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                self.send_header('Pragma', 'no-cache')
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_error(500, str(e))
        else:
            super().do_GET()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Content-Length')
        self.end_headers()

    def do_POST(self):
        if self.path == '/api/trades/open':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length) or b'{}')
            t = open_trade(body['sym'], body['tf'], body['direction'],
                          body['entry'], body['sl'], body['tp'], body.get('strategy',''))
            _send_json(self, {'ok': True, 'trade': t})
            return

        elif self.path == '/api/trades/close':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length) or b'{}')
            t = close_trade(body['sym'], body['tf'], body.get('exit_price', 0), body.get('reason','MANUAL'))
            _send_json(self, {'ok': True, 'trade': t})
            return

        if self.path == '/upload/terminal':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body   = self.rfile.read(length)
                HUD_PINE.parent.mkdir(parents=True, exist_ok=True)
                HUD_PINE.write_bytes(body)
                lines  = len(body.decode('utf-8').splitlines())
                _send_json(self, {'ok': True, 'lines': lines, 'path': str(HUD_PINE)})
            except Exception as e:
                _send_json(self, {'ok': False, 'error': str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass  # silenciar logs de request


_t_regen = threading.Thread(target=regen_dashboard, daemon=True)
_t_regen.start()

# Background thread para pre-cargar regimenes cada 5 min
def _reopen_after_close(sym, tf, live_price):
    """Si sigue activa la señal para sym/tf, reabre inmediatamente al precio live."""
    try:
        models = _signals_cache.get('models', [])
        for sig in models:
            if sig.get('sym') != sym or sig.get('tf') != tf: continue
            if not sig.get('signal'): continue
            if check_circuit_breaker(): return
            if is_blocked(sym, tf): return
            # Preservar las distancias SL/TP del modelo (ratio RR intacto)
            bar_price = sig.get('price', live_price)
            sl_bar    = sig.get('sl', 0)
            tp_bar    = sig.get('tp', 0)
            if not sl_bar or not tp_bar: return
            sl_dist   = abs(sl_bar - bar_price)
            tp_dist   = abs(tp_bar - bar_price)
            direction = sig.get('type', 'long')
            _short    = direction == 'short'
            new_sl    = round(live_price + sl_dist if _short else live_price - sl_dist, 4)
            new_tp    = round(live_price - tp_dist if _short else live_price + tp_dist, 4)
            open_trade(sym, tf, direction, live_price, new_sl, new_tp,
                       sig.get('strategy','?'), paper=True,
                       grade=sig.get('grade','?'),
                       wr=sig.get('wr', 0), cagr=sig.get('cagr', 0),
                       kelly_pct=float(sig.get('eff_risk_pct') or 3.3))
            print(f'[REOPEN] {sym}/{tf} {direction.upper()} @ {live_price} '
                  f'SL={new_sl} TP={new_tp}', flush=True)
            return
    except Exception as _er:
        print(f'[REOPEN ERROR] {_er}', flush=True)



def _htf_confirms(sym, tf, direction, ex, data_cache):
    """Verifica que el TF mayor (HTF) confirme la dirección de la señal.
    Retorna (confirms: bool, reason: str)
    """
    TF_PARENT = {'5m':'15m', '15m':'1h', '1h':'4h', '2h':'4h', '4h':None}
    parent_tf = TF_PARENT.get(tf)
    if not parent_tf:
        return True, 'TF mayor no aplica'  # 4H no tiene padre

    try:
        tf_alias  = {'15m':'15min','5m':'5min'}
        tf_ccxt   = tf_alias.get(parent_tf, parent_tf)
        symbol    = f'{sym}/USDT'
        key       = (symbol, parent_tf)
        if key not in data_cache:
            raw = ex.fetch_ohlcv(symbol, tf_ccxt, limit=60)
            if raw:
                import pandas as pd
                df = pd.DataFrame(raw, columns=['ts','open','high','low','close','volume'])
                df['ts'] = pd.to_datetime(df['ts'], unit='ms')
                df.set_index('ts', inplace=True)
                _feats(df)
                data_cache[key] = _feats(df)

        df_h = data_cache.get(key)
        if df_h is None or len(df_h) < 20:
            return True, 'Sin datos HTF'

        # Tendencia del HTF: EMA20 vs EMA50 + RSI semanal
        ema20 = df_h['close'].ewm(span=20).mean().iloc[-1]
        ema50 = df_h['close'].ewm(span=50).mean().iloc[-1]
        close = df_h['close'].iloc[-1]

        htf_bull = close > ema50 and ema20 > ema50
        htf_bear = close < ema50 and ema20 < ema50

        if direction == 'long' and htf_bear:
            return False, f'{parent_tf.upper()} bajista (precio<EMA50, EMA20<EMA50)'
        if direction == 'short' and htf_bull:
            return False, f'{parent_tf.upper()} alcista (precio>EMA50, EMA20>EMA50)'

        return True, f'{parent_tf.upper()} confirma {direction}'
    except:
        return True, 'HTF check error (pasando)'


_btcd_cache   = {'value': 0.5, 'ts': 0}  # 0=BTC domina, 1=alts dominan

def _get_btc_dominance_proxy():
    """Proxy de dominancia BTC usando momentum relativo 7D.
    Retorna valor 0-1: <0.4=BTC domina, >0.6=alts dominan, 0.4-0.6=neutral.
    Cached 30 minutos."""
    import time as _t, urllib.request as _ur, json as _jb
    if _t.time() - _btcd_cache['ts'] < 1800:
        return _btcd_cache['value']
    try:
        prices = {}
        for sym in ['BTCUSDT','ETHUSDT','SOLUSDT','LTCUSDT']:
            url = f'https://api.binance.com/api/v3/klines?symbol={sym}&interval=1d&limit=8'
            kl  = _jb.loads(_ur.urlopen(url, timeout=5).read())
            if kl and len(kl) >= 8:
                p0 = float(kl[0][4])  # close 7 days ago
                p1 = float(kl[-1][4]) # close today
                prices[sym] = (p1 - p0) / p0 * 100  # 7d return %
        if len(prices) >= 3:
            btc_ret  = prices.get('BTCUSDT', 0)
            alt_rets = [v for k,v in prices.items() if k != 'BTCUSDT']
            avg_alt  = sum(alt_rets) / len(alt_rets)
            # Spread positivo → BTC outperforma → alta dominancia
            spread = btc_ret - avg_alt
            # Normalizar a 0-1: spread=-10 → 1.0 (alts dominan), spread=+10 → 0.0 (BTC domina)
            val = max(0.0, min(1.0, 0.5 - spread / 20.0))
            _btcd_cache['value'] = round(val, 3)
            _btcd_cache['ts']    = _t.time()
            return _btcd_cache['value']
    except: pass
    return 0.5  # neutral si falla

def _watch_open_trades():
    """Chequea SL/TP de trades abiertos cada 30s y reabre si sigue la señal."""
    import urllib.request as _ur, json as _jw
    while True:
        try:
            state = _load_trades()
            for key, tr in list(state.get('open', {}).items()):
                if tr.get('status') != 'open': continue
                sym = tr.get('sym',''); tf = tr.get('tf','')
                if not sym: continue
                try:
                    # Precio live desde Binance Futures
                    url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT'
                    cp  = float(_jw.loads(_ur.urlopen(url, timeout=5).read())['price'])
                    result = check_auto_close(sym, tf, cp)
                    if result:
                        reason = result.get('reason', '?')
                        pnl    = result.get('pnl_pct', 0)
                        print(f'[WATCHER] {sym}/{tf} {reason} @ {cp:.4f} P&L={pnl:+.2f}%', flush=True)
                        # ── Reabrir inmediatamente si sigue la señal ─────────
                        time.sleep(1)  # pequeña pausa antes de reabrir
                        try:
                            url2 = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT'
                            cp2  = float(_jw.loads(_ur.urlopen(url2, timeout=5).read())['price'])
                            _reopen_after_close(sym, tf, cp2)
                        except Exception as _er2:
                            print(f'[REOPEN] error precio: {_er2}', flush=True)
                except Exception as _ew:
                    pass
        except Exception as _eo:
            pass
        time.sleep(30)

t2 = threading.Thread(target=_refresh_regime, daemon=True)

def _proactive_trade_opener():
    """Cada 60s: si hay slots libres y señales activas, abre trades automáticamente."""
    import urllib.request as _ur2, json as _jw2
    time.sleep(20)  # espera inicial para que se caliente el cache de señales
    while True:
        try:
            state      = _load_trades()
            open_trades= state.get('open', {})
            open_count = sum(1 for t in open_trades.values() if t.get('status')=='open')
            regime     = _signals_cache.get('regime', 'RANGE')

            # Calcular MAX_SLOTS igual que en _compute_signals
            _ap_sigs = [m for m in _signals_cache.get('models', [])
                        if m.get('grade') in ('A+','A') and m.get('recommendation')=='ACTIVAR']
            max_slots = 2 if regime == 'BEAR' else 3
            if len(_ap_sigs) >= 3 and max_slots < 3:
                max_slots = 3

            if open_count >= max_slots:
                time.sleep(60); continue
            if check_circuit_breaker():
                time.sleep(60); continue

            # Buscar señales activas con slot asignado que NO tienen trade abierto
            open_keys = set()
            for t in open_trades.values():
                if t.get('status') == 'open':
                    open_keys.add(f"{t['sym']}_{t['tf']}")

            for sig in _signals_cache.get('models', []):
                if open_count >= max_slots: break
                if not sig.get('signal'): continue
                if sig.get('slot', 0) <= 0: continue
                if sig.get('recommendation') not in ('ACTIVAR',): continue

                key = f"{sig['sym']}_{sig['tf']}"
                if key in open_keys: continue  # ya tiene trade abierto
                if is_blocked(sig['sym'], sig['tf']): continue

                # BEAR slot 2: requiere CAGR >= 20%
                if regime == 'BEAR' and open_count >= 1 and sig.get('cagr', 0) < 20:
                    continue

                # Obtener precio live
                try:
                    url3 = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sig['sym']}USDT"
                    lp   = float(_jw2.loads(_ur2.urlopen(url3, timeout=5).read())['price'])
                except:
                    continue

                bar_p = sig.get('price', lp)
                sl_b  = sig.get('sl', 0)
                tp_b  = sig.get('tp', 0)
                if not sl_b or not tp_b: continue

                sl_d = abs(sl_b - bar_p)
                tp_d = abs(tp_b - bar_p)
                dirn = sig.get('type', 'long')
                _sh  = dirn == 'short'
                new_sl = round(lp + sl_d if _sh else lp - sl_d, 4)
                new_tp = round(lp - tp_d if _sh else lp + tp_d, 4)

                open_trade(sig['sym'], sig['tf'], dirn, lp, new_sl, new_tp,
                           sig.get('strategy', '?'), paper=True,
                           grade=sig.get('grade','?'), wr=sig.get('wr', 0),
                           cagr=sig.get('cagr', 0),
                           kelly_pct=float(sig.get('eff_risk_pct') or 3.3))
                print(f'[OPENER] {sig["sym"]}/{sig["tf"]} {dirn.upper()} @ {lp} '
                      f'SL={new_sl} TP={new_tp} slot={sig["slot"]}', flush=True)
                open_keys.add(key)
                open_count += 1
                time.sleep(1)

        except Exception as _ep:
            print(f'[OPENER ERROR] {_ep}', flush=True)
        time.sleep(60)

t_watch = threading.Thread(target=_watch_open_trades, daemon=True)
t2.start()
t_watch.start()

t_opener = threading.Thread(target=_proactive_trade_opener, daemon=True)
t_opener.start()

t3 = threading.Thread(target=_refresh_signals, daemon=True)
t3.start()

os.chdir(str(BASE / 'results' / 'charts'))

import socket

class ReusableHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Threaded — cada request en su propio hilo, nunca se bloquean entre sí."""
    allow_reuse_address = True
    daemon_threads      = True   # hilos mueren con el proceso principal

    def server_bind(self):
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        super().server_bind()


def _graceful_shutdown(signum, frame):
    """Shutdown limpio en SIGTERM — responde rápido para no recibir SIGKILL."""
    print('SIGMA Web: shutdown solicitado', flush=True)
    threading.Thread(target=httpd.shutdown, daemon=True).start()
    sys.exit(0)

signal.signal(signal.SIGTERM, _graceful_shutdown)
signal.signal(signal.SIGINT,  _graceful_shutdown)

httpd = ReusableHTTPServer(('0.0.0.0', PORT), Handler)
print(f'Dashboard en http://178.104.10.97:{PORT}/dashboard.html', flush=True)
httpd.serve_forever()

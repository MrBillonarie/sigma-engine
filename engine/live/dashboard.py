"""
SIGMA ENGINE - Dashboard Multi-Activo
Genera dashboard.html con matriz completa: 5 activos x 5 TFs
Se regenera cada 2 minutos via web_server.py
"""
import sys, os

# --- SIGMA strategies registry (centralized 2026-05-14) ---
import sys as _sig_sys
if "/opt/sigma" not in _sig_sys.path: _sig_sys.path.insert(0, "/opt/sigma")
from utils.strategies import SHORT_STRATEGIES as _SIGMA_SHORTS
# --- end SIGMA strategies ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, sqlite3, re, time
from pathlib import Path
from datetime import datetime, date, timezone

from utils.snapshot_schema import merge_snapshot

OUTPUT_DIR = Path(__file__).parent.parent.parent

ASSETS    = ['BTC','ETH','LTC','SOL','BNB','XAU','XAG']
ASSETS_M1 = ['BTC','ETH','LTC','SOL','BNB']          # Motor 1: Crypto
ASSETS_M2 = ['XAU','XAG','WTI','HG','NG','PL']         # Motor 2: 6 Commodities
TIMEFRAMES_M2 = ['1d','4h','1h','15m']               # Motor 2: 1d/4h/1h/15m
ASSET_EMOJI = {'BTC':'&#8383;','ETH':'&#926;','LTC':'&#321;','SOL':'&#9678;','BNB':'&#11042;','XAU':'Au','XAG':'Ag','WTI':'&#9651;','HG':'Cu','NG':'&#9650;','PL':'Pt'}
ASSET_COLOR = {'BTC':'#f7931a','ETH':'#627eea','LTC':'#345d9d','SOL':'#9945ff','BNB':'#f3ba2f','XAU':'#FFD700','XAG':'#C0C0C0','WTI':'#4a90d9','HG':'#B87333','NG':'#e67e22','PL':'#7ec8e3'}
TIMEFRAMES  = ['4h','1h','15m','5m']
TF_LABEL    = {'1d':'1D','4h':'4H','1h':'1H','15m':'15m','5m':'5m'}
TF_COLORS_H = {'1d':'#e0bb3a','4h':'#2ecc71','1h':'#c9a227','15m':'#f1c40f','5m':'#e67e22'}


# ── SNAPSHOT del ponderado ────────────────────────────────────────────────────

def _funding_kelly_boost_estimate(champions_data):
    """Estima pp extra de port_cagr por Kelly v2 (solo shorts).
    Backtest histórico: shorts ganan promedio +3% sobre CAGR puro."""
    SHORT_BOOST_PCT = 0.03
    total_trades = 0
    short_alpha = 0.0
    for ch in champions_data:
        direction = ch.get('direction', 'long')
        trades = ch.get('trades', 0) or 0
        cagr = ch.get('cagr', 0) or 0
        total_trades += trades
        if direction == 'short':
            short_alpha += cagr * trades * SHORT_BOOST_PCT
    return round(short_alpha / total_trades, 4) if total_trades > 0 else 0.0


def _write_snapshot(path, cagr, wr, dd, pf, calmar, trades, n_grade_a, champions, trigger):
    path.parent.mkdir(parents=True, exist_ok=True)
    _prev_snap = {}
    if path.exists():
        try:
            _prev_snap = json.loads(path.read_text())
        except Exception:
            _prev_snap = {}
    # Calcular funding Kelly boost (alpha esperado del Kelly v2 sobre shorts)
    _kelly_boost = 0.0
    try:
        _champ_list = []
        for _slot, _val in (champions or {}).items():
            if '|' not in _val: continue
            _strat, _dir = _val.split('|', 1)
            _sym, _tf = _slot.split('|')
            _jp = OUTPUT_DIR / 'models' / _tf / (_sym.lower() + '_' + _strat + '.json')
            if _jp.exists():
                _d = json.loads(_jp.read_text())
                _m = _d.get('metrics_oos') or {}
                _champ_list.append({
                    'direction': _dir,
                    'cagr': _m.get('cagr', 0) or 0,
                    'trades': _m.get('trades', 0) or 0,
                })
        _kelly_boost = _funding_kelly_boost_estimate(_champ_list)
    except Exception as _e_kb:
        print(f'[KELLY BOOST ERROR] {_e_kb}', flush=True)
    # Preservar boost previo si nuestro cálculo dio 0 pero el snapshot anterior tenía valor
    if _kelly_boost == 0.0:
        _prev_boost = _prev_snap.get('port_cagr_kelly_boost', 0)
        if _prev_boost and _prev_boost > 0:
            _kelly_boost = _prev_boost

    # Merge M2 champions (XAU/XAG/WTI/NG/HG/PL) del snapshot anterior en los
    # que este calculo (M1) no toco -- esto es merge PARCIAL del dict
    # 'champions', no preservacion de campo completo, asi que sigue siendo
    # logica explicita aqui (merge_snapshot no puede saber esto).
    _M2_ASSETS = {'XAU', 'XAG', 'WTI', 'NG', 'HG', 'PL'}
    champions = dict(champions or {})
    for _m2_slot, _m2_val in _prev_snap.get('champions', {}).items():
        _m2_sym = _m2_slot.split('|')[0]
        if _m2_sym in _M2_ASSETS and _m2_slot not in champions:
            champions[_m2_slot] = _m2_val

    # Preservar métricas M1+M2 calculadas por champion_watcher si son mas
    # completas que el calculo M1-only de este modulo (mismo criterio que antes).
    if _prev_snap.get('total_trades', 0) > trades:
        cagr = _prev_snap['port_cagr']
        trades = _prev_snap['total_trades']
        n_grade_a = _prev_snap.get('n_grade_a', n_grade_a)

    updates = {
        'port_cagr':    cagr,
        'port_cagr_kelly_boost': _kelly_boost,
        'port_cagr_with_kelly':  round(cagr + _kelly_boost, 4),
        'port_wr':      wr,
        'port_dd':      dd,
        'port_pf':      pf,
        'port_calmar':  calmar,
        'total_trades': trades,
        'n_grade_a':    n_grade_a,
        'snapshot_at':  datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'trigger':      trigger,
        'champions':    champions,
    }
    # merge_snapshot preserva automaticamente cualquier campo que dashboard.py
    # no calcule (champions_secondary, champions_countertrend cuando exista,
    # o cualquier campo futuro) -- sin necesidad de un parche ad-hoc por campo
    # nuevo, que era la causa raiz del bug de 2026-06-20.
    snap = merge_snapshot(_prev_snap, updates, owner='dashboard')
    path.write_text(json.dumps(snap, indent=2))


# ── SCORE / GRADE ─────────────────────────────────────────────────────────────

def _compute_score(m):
    """DELEGATE a utils.scoring.canonical_score (verified lossless 2026-05-16).
    Mantiene la firma legacy: recibe dict de metrics (con cagr/trades/dd/etc al top),
    devuelve score o -9999 si descalifica.
    """
    if not m: return -9999
    # canonical_score espera d con metrics_oos dentro. Wrappeamos.
    try:
        from utils.scoring import canonical_score as _ucs
        return _ucs({'metrics_oos': m}, strict=True)
    except Exception:
        return -9999

def _score_grade(score):
    """Retorna badge HTML con grade A+/A/B/C/D según score."""
    if score is None or score < 0:
        return ''
    if score >= 0.70:
        color, label = '#00c853', 'A+'
    elif score >= 0.55:
        color, label = '#69f0ae', 'A'
    elif score >= 0.40:
        color, label = '#ffeb3b', 'B'
    elif score >= 0.25:
        color, label = '#ff9800', 'C'
    else:
        color, label = '#f44336', 'D'
    return f'<span style="background:{color};color:#000;padding:1px 5px;border-radius:3px;font-size:10px;font-weight:bold">{label}</span>'


# ── LOADERS ──────────────────────────────────────────────────────────────────

LONG_STRATEGIES = {
    # LONG-only (logica clara de long)
    'breakout', 'tma_breakout', 'momentum', 'pullback', 'regime_adaptive', 'mean_rev',
    'higher_highs', 'ema50_bounce', 'donchian_break', 'keltner_breakout', 'supertrend',
    'three_candles', 'engulfing', 'pin_bar', 'consecutive_wick',
    # bidireccionales (incluidas en LONG)
    'tma_bands', 'dmi_trend', 'ichimoku', 'mfi_reversal', 'psar_flip',
    'volatility_breakout', 'break_of_structure', 'aroon_cross', 'atr_channel',
    'bb_bandwidth', 'bb_squeeze', 'cci_reversal', 'chaikin_mf', 'cvd_divergence',
    'elder_impulse', 'ema_ribbon', 'ema_triple', 'funding_momentum', 'funding_reversal',
    'heikin_ashi', 'htf_divergence', 'hull_cross', 'inside_bar', 'linear_reg_break',
    'macd_divergence', 'obv_divergence', 'open_close_cross', 'pivot_bounce',
    'roc_momentum', 'rsi_divergence', 'rsi_trend', 'squeeze_pro', 'stoch_rsi',
    'tema_cross', 'trend_strength', 'volume_climax', 'vwap_deviation',
    'williams_r', 'wma_momentum', 'zscore_reversion',
    # Agregadas 2026-05-14 (sprint shorts expansion)
    'rsi_overbought_short', 'death_cross_short', 'ema200_rejection_short', 'macd_bear_cross', 'lower_high_break_short',
    'wedge_breakdown_short', 'supply_zone_rejection', 'bearish_rsi_divergence', 'volume_climax_top', 'range_break_down', 'macd_zero_cross_down',
    'stoch_rsi_short', 'williams_r_short', 'cci_reversal_short', 'engulfing_short', 'three_candles_short', 'inside_bar_short',
    'zscore_rich_short', 'heikin_ashi_short', 'roc_negative_short', 'dmi_bear', 'vwap_overpriced_short', 'keltner_breakdown_short',
}
SHORT_STRATEGIES = _SIGMA_SHORTS  # centralizado en utils.strategies (2026-05-14)




# === HEDGE FUND VISUALS ============================================

def _daily_pnl_map(hist, days=30):
    """Suma pnl_pct por dia ultimos N dias."""
    from datetime import datetime, timedelta, date
    end = date.today()
    start = end - timedelta(days=days-1)
    daily = {}
    for t in hist:
        try:
            cdt = t.get("closed_at", "")
            if not cdt: continue
            d = datetime.fromisoformat(str(cdt)[:19]).date()
            if d >= start:
                daily[d] = daily.get(d, 0) + (t.get("pnl_pct", 0) or 0)
        except Exception:
            continue
    return daily


def render_calendar_heatmap(hist, days=30):
    """Heatmap horizontal 30 cuadrados."""
    from datetime import date, timedelta
    daily = _daily_pnl_map(hist, days)
    end = date.today()
    cells = []
    for i in range(days):
        d = end - timedelta(days=days-1-i)
        pnl = daily.get(d, None)
        if pnl is None:
            color = "#141b38"
            tip = str(d) + ": sin trades"
        elif pnl > 1.0: color = "#00c853"; tip = str(d) + ": +" + str(round(pnl,2)) + "%"
        elif pnl > 0.2: color = "#43a047"; tip = str(d) + ": +" + str(round(pnl,2)) + "%"
        elif pnl > 0:   color = "#66bb6a"; tip = str(d) + ": +" + str(round(pnl,2)) + "%"
        elif pnl == 0:  color = "#555";    tip = str(d) + ": 0%"
        elif pnl > -0.2: color = "#ff9800"; tip = str(d) + ": " + str(round(pnl,2)) + "%"
        elif pnl > -1.0: color = "#ef5350"; tip = str(d) + ": " + str(round(pnl,2)) + "%"
        else: color = "#c62828"; tip = str(d) + ": " + str(round(pnl,2)) + "%"
        cells.append('<div title="' + tip + '" style="background:' + color + ';width:20px;height:20px;border-radius:3px;display:inline-block;margin:2px;transition:transform .15s" onmouseover="this.style.transform=&quot;scale(1.3)&quot;" onmouseout="this.style.transform=&quot;scale(1)&quot;"></div>')
    return "".join(cells)


def render_donut_exposure(open_trades):
    """Donut SVG de open positions por ticker."""
    sym_colors = {"BTC":"#f7931a","ETH":"#627eea","LTC":"#345d9d","SOL":"#9945ff","BNB":"#f3ba2f"}
    by_ticker = {}
    raw = open_trades if isinstance(open_trades, dict) else {str(i):t for i,t in enumerate(open_trades or [])}
    for k, t in raw.items():
        if not isinstance(t, dict): continue
        if t.get("status") != "open": continue
        sym = t.get("sym", "?")
        by_ticker[sym] = by_ticker.get(sym, 0) + 1
    if not by_ticker:
        return '<div style="color:#4e5f90;font-size:11px;padding:12px;text-align:center">Sin exposicion activa</div>'
    total = sum(by_ticker.values())
    svg = '<svg viewBox="0 0 100 100" width="100" height="100" style="vertical-align:middle">'
    offset = 0
    circumference = 2 * 3.14159265 * 40
    items = []
    for sym, count in sorted(by_ticker.items(), key=lambda x: -x[1]):
        pct = count / total * 100
        color = sym_colors.get(sym, "#888")
        dash = pct / 100 * circumference
        offset_d = round(offset / 100 * circumference, 2)
        svg += '<circle cx="50" cy="50" r="40" fill="transparent" stroke="' + color + '" stroke-width="16" stroke-dasharray="' + str(round(dash,2)) + ' ' + str(round(circumference,2)) + '" stroke-dashoffset="-' + str(offset_d) + '" transform="rotate(-90 50 50)"/>'
        offset += pct
        items.append((sym, count, color))
    svg += '<text x="50" y="50" text-anchor="middle" dy=".35em" fill="#dde3f5" font-family="IBM Plex Mono" font-size="16" font-weight="700">' + str(total) + '</text>'
    svg += '</svg>'
    legend = ''
    for sym, count, color in items:
        legend += '<div style="display:flex;align-items:center;gap:6px;font-size:10px;color:#7a8db5;margin:2px 0"><span style="width:10px;height:10px;background:' + color + ';border-radius:2px;display:inline-block"></span>' + sym + ' (' + str(count) + ')</div>'
    return '<div style="display:flex;align-items:center;gap:16px">' + svg + '<div>' + legend + '</div></div>'


def render_rolling_metrics(hist, days=30):
    """Rolling Sharpe, streak, best/worst, win days."""
    daily = _daily_pnl_map(hist, days)
    if not daily: return None
    pnls = list(daily.values())
    n = len(pnls)
    avg = sum(pnls) / n if n else 0
    var = sum((x-avg)**2 for x in pnls) / n if n > 1 else 0
    sd = var ** 0.5
    sharpe = (avg / sd) * (365**0.5) if sd > 0 else 0
    streak = 0; cur = 0
    for v in pnls:
        if v > 0: cur += 1; streak = max(streak, cur)
        else: cur = 0
    return dict(
        sharpe=sharpe, streak=streak,
        best=max(pnls), worst=min(pnls),
        wins=sum(1 for v in pnls if v > 0),
        losses=sum(1 for v in pnls if v < 0),
        n_days=n,
    )


def load_model(asset, tf, direction='long'):
    """Carga el MEJOR modelo OOS para asset+tf segun direccion (mayor CAGR)."""
    d = OUTPUT_DIR / 'models' / tf
    if not d.exists():
        return None
    sym = asset.lower()

    if direction == 'short':
        candidates = [f'{sym}_{s}.json' for s in _SIGMA_SHORTS]  # centralizado utils.strategies (2026-05-14)
        _COM_PFX_S = {'XAU':'xauusd','XAG':'xagusd','WTI':'wtiusd','HG':'hgusd','NG':'ngusd','PL':'plusd'}
        if asset in _COM_PFX_S:
            candidates += [f"{_COM_PFX_S[asset]}_{s}.json" for s in _SIGMA_SHORTS]
    else:
        long_strats = [
            # STRATEGIES — originales core
            'breakout', 'pullback', 'tma_bands', 'momentum', 'mean_rev', 'regime_adaptive',
            # STRATEGIES_EXPLORE — indicadores avanzados
            'funding_reversal', 'volatility_breakout', 'htf_divergence', 'rsi_divergence',
            'bb_squeeze', 'ema_triple', 'supertrend', 'break_of_structure',
            'cvd_divergence', 'vwap_deviation', 'inside_bar', 'pin_bar',
            'heikin_ashi', 'macd_divergence', 'keltner_breakout', 'rsi_trend',
            'ema50_bounce', 'volume_climax',
            # STRATEGIES_NEW — sistemas completos
            'ema_ribbon', 'hull_cross', 'tema_cross', 'wma_momentum',
            'stoch_rsi', 'cci_reversal', 'williams_r', 'mfi_reversal', 'roc_momentum',
            'dmi_trend', 'higher_highs', 'lower_lows', 'aroon_cross',
            'psar_flip', 'donchian_break', 'trend_strength', 'elder_impulse',
            'engulfing', 'three_candles', 'consecutive_wick', 'volume_exhaustion',
            'chaikin_mf', 'obv_divergence', 'zscore_reversion', 'bb_bandwidth',
            'ichimoku', 'pivot_bounce', 'atr_channel', 'linear_reg_break',
            'squeeze_pro', 'open_close_cross', 'funding_momentum', 'ema50_bounce']
        candidates = [f'{sym}_{s}.json' for s in long_strats]
        # Legacy BTC filenames por compatibilidad con archivos viejos en disco
        if asset == 'BTC':
            candidates += [
                'best_bull_breakout.json', 'best_bull_tma_bands.json',
                'best_bull_pullback.json', 'best_validated.json',
            ]
        # Fallback: Optuna generaba con prefijo {sym}usd_ para commodities (antes del fix)
        _COMMODITY_PREFIXES = {
            'XAU': 'xauusd', 'XAG': 'xagusd', 'WTI': 'wtiusd',
            'HG':  'hgusd',  'NG':  'ngusd',   'PL':  'plusd',
        }
        if asset in _COMMODITY_PREFIXES:
            _pfx = _COMMODITY_PREFIXES[asset]
            candidates += [f'{_pfx}_{s}.json' for s in long_strats]

    best_dict = None
    best_cagr = 0.0  # solo guardamos modelos con CAGR > 0
    best_rank = (-1, 0.0)  # 2026-05-19: (robustness_rank, cagr) — preferir no-BLOCKED primero

    for fname in candidates:
        p = d / fname
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
            sym_field = data.get('symbol', '').upper()
            if sym_field and asset not in sym_field and asset != 'BTC':
                continue
            m    = data.get('metrics_oos') or {}
            cagr = m.get('cagr', 0)
            if cagr <= 0:
                continue
            # 2026-05-19: ranking (robustness_rank, cagr) — preferir validados antes que high-CAGR overfit
            try:
                import sys as _sl
                if '/opt/sigma' not in _sl.path: _sl.path.insert(0, '/opt/sigma')
                from utils.robustness import robustness_score as _rs_lm
                _rank_lm = {'PASS_LIVE': 3, 'PAPER_ONLY': 2, 'BLOCKED': 1}.get(_rs_lm(data).get('action','?'), 0)
            except Exception:
                _rank_lm = 0
            this_rank = (_rank_lm, cagr)
            if this_rank <= best_rank:
                continue
            v     = data.get('validation', {})
            strat = data.get('strategy', '') or fname.replace('.json','').split('_',1)[-1]
            best_cagr = cagr
            best_rank = this_rank
            _params = data.get('params', {}) or {}
            _sl_m = _params.get('sl_mult', 0) or 0
            _tp_m = _params.get('tp_mult', 0) or 0
            _rr   = (_tp_m / _sl_m) if _sl_m > 0 else 0
            best_dict = {
                'cagr':       cagr,
                'wr':         m.get('wr', m.get('winrate', 0)),
                'dd':         m.get('dd', m.get('max_dd', 0)),
                'pf':         m.get('pf', m.get('profit_factor', 0)),
                'trades':     m.get('trades', 0),
                'sl_mult':    _sl_m,
                'tp_mult':    _tp_m,
                'rr':         _rr,
                'cagr_is':    data.get('metrics_is', {}).get('cagr', 0),
                'source':     fname,
                'strategy':   strat,
                'adaptive':   strat == 'regime_adaptive',
                'confidence': v.get('confidence', ''),
                'mc_p_pos':   v.get('monte_carlo', {}).get('p_pos', 0),
                'wft_pct':    v.get('walk_forward', {}).get('pct_positive', 0),
            }
        except:
            continue

    if best_dict:
        try:
            best_dict['score'] = _compute_score(best_dict)
        except Exception:
            best_dict['score'] = -9999
    return best_dict


def is_running(asset, tf):
    """Devuelve True si el optimizador para asset+tf fue modificado recientemente."""
    name = f'{asset.lower()}_{tf}'
    candidates_log = [
        OUTPUT_DIR / 'results' / 'reports' / f'{name}.log',
        OUTPUT_DIR / 'results' / 'reports' / f'commodities_{asset}_{tf}.log',
    ]
    for lp in candidates_log:
        if lp.exists():
            age_min = (time.time() - lp.stat().st_mtime) / 60
            if age_min < 10:
                return True
    return False


def load_events(n=30):
    """Lee los ultimos N eventos del pipeline_events.jsonl."""
    p = OUTPUT_DIR / 'results' / 'reports' / 'pipeline_events.jsonl'
    if not p.exists():
        return []
    events = []
    try:
        for line in p.read_text(encoding='utf-8', errors='replace').splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except:
                    pass
    except:
        pass
    return events[-n:]


def load_wft():
    p = OUTPUT_DIR / 'results' / 'reports' / 'wft_1h.log'
    if not p.exists():
        return []
    rows = []
    pat  = re.compile(r'(\d{4}-\d{2})\s+\d+d\s+\d+d\s+(\d+)\s+([\d.]+)%\s+([+-][\d.]+)%\s+(\w+)')
    for line in p.read_text(encoding='utf-8', errors='replace').splitlines():
        m = pat.search(line)
        if m:
            rows.append({
                'window': m.group(1),
                'trades': int(m.group(2)),
                'wr':     float(m.group(3)),
                'cagr':   float(m.group(4)),
                'ok':     m.group(5) == 'OK',
            })
    return rows


def load_mc():
    p = OUTPUT_DIR / 'results' / 'reports' / 'monte_carlo_results.json'
    try: return json.loads(p.read_text()) if p.exists() else {}
    except: return {}


def load_cross_asset():
    p = OUTPUT_DIR / 'results' / 'reports' / 'cross_asset_validation.json'
    try: return json.loads(p.read_text()) if p.exists() else {}
    except: return {}


def get_db_stats():
    db = OUTPUT_DIR / 'models' / 'sigma.db'
    if not db.exists():
        return {'total': 0, 'by_tf': {}, 'top3': []}
    try:
        conn = sqlite3.connect(str(db))
        total  = conn.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        by_tf  = {r[0]: r[1] for r in conn.execute('SELECT tf,COUNT(*) FROM runs GROUP BY tf')}
        top3   = [{'tf':r[0],'mode':r[1],'cagr':r[2],'wr':r[3],'score':r[4]}
                  for r in conn.execute(
                    'SELECT tf,mode,cagr,winrate,score FROM runs '
                    'WHERE cagr>10 AND winrate>55 ORDER BY score DESC LIMIT 3')]
        rate   = conn.execute(
                    "SELECT COUNT(*) FROM runs WHERE ts > datetime('now','-1 hours')"
                 ).fetchone()[0]
        conn.close()
        # Optuna per-study: throughput real
        import glob as _g, datetime as _d
        optuna_rate = 0
        try:
            _cut = (_d.datetime.now() - _d.timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
            for _db in _g.glob(str(OUTPUT_DIR / 'models' / 'optuna_per_study' / '*.db')):
                try:
                    _cx = sqlite3.connect(_db, timeout=1)
                    optuna_rate += _cx.execute("SELECT count(*) FROM trials WHERE state='COMPLETE' AND datetime_complete >= ?", (_cut,)).fetchone()[0]
                    _cx.close()
                except: pass
        except: pass
        return {'total': total, 'by_tf': by_tf, 'top3': top3, 'rate_hr': rate, 'optuna_rate_hr': optuna_rate}
    except:
        return {'total': 0, 'by_tf': {}, 'top3': []}


# ── COLOR HELPERS ─────────────────────────────────────────────────────────────

def c_cagr(v):
    if v >= 20: return '#2ecc71'
    if v >= 10: return '#f1c40f'
    if v >  0:  return '#e67e22'
    return '#e74c3c'

def c_wr(v):
    if v >= 60: return '#2ecc71'
    if v >= 55: return '#f1c40f'
    if v >= 50: return '#e67e22'
    return '#e74c3c'

def c_dd(v):
    if v >= -5:  return '#2ecc71'
    if v >= -10: return '#f1c40f'
    if v >= -20: return '#e67e22'
    return '#e74c3c'

def c_conf(p):
    if p >= 75: return ('#2ecc71','ALTA')
    if p >= 60: return ('#f1c40f','MEDIA')
    return ('#e74c3c','BAJA')


# ── CELL GENERATOR ────────────────────────────────────────────────────────────

def _red_flags(m):
    """Devuelve lista de red flags detectados en el modelo (no muta nada).
    Posibles: LOW_N, WFT_FAIL, CAGR_TOO_HIGH, IS_OOS_GAP, LOW_MC, NOISE_SCALPER.
    """
    if not m: return []
    flags = []
    try:
        t = float(m.get('trades', 0) or 0)
        cagr = float(m.get('cagr', 0) or 0)
        wr = float(m.get('wr', 0) or 0)
        cagr_is = float(m.get('cagr_is', 0) or 0)
        wft_pct = m.get('wft_pct')
        mc_pos = m.get('mc_p_pos')

        if t > 0 and t < 30:
            flags.append('LOW_N')  # sample insuficiente
        if wft_pct is not None and wft_pct > 0 and wft_pct < 50:
            flags.append('WFT_FAIL')  # walk-forward inestable
        if cagr > 80:
            flags.append('CAGR_TOO_HIGH')  # sospechoso por overfit
        if cagr_is and abs(cagr_is - cagr) > 40:
            flags.append('IS_OOS_GAP')  # in-sample vs out-of-sample diff alto
        if mc_pos and mc_pos > 0 and mc_pos < 0.70:
            flags.append('LOW_MC')  # Monte Carlo confianza baja
        if t >= 100 and 0 < wr < 55:
            flags.append('NOISE_SCALPER')  # muchos trades + WR coin-flip
    except Exception:
        pass
    return flags


def _red_flags_badge(m):
    """HTML badge con tooltip custom CSS (instantaneo, dark mode)."""
    flags = _red_flags(m)
    if not flags:
        return ''
    descriptions = {
        'LOW_N':           'LOW_N — Menos de 30 trades en backtest (muestra chica, baja confianza estadistica)',
        'WFT_FAIL':        'WFT_FAIL — Walk-forward test: menos de 50% de ventanas con OOS positivo',
        'CAGR_TOO_HIGH':   'CAGR_TOO_HIGH — CAGR mayor a 80% (sospechoso de overfit, no realista en live)',
        'IS_OOS_GAP':      'IS_OOS_GAP — Diferencia entre CAGR in-sample y out-of-sample mayor a 40pp',
        'LOW_MC':          'LOW_MC — Monte Carlo: probabilidad de PnL positivo menor a 70%',
        'NOISE_SCALPER':   'NOISE_SCALPER — Mas de 100 trades pero WR menor a 55% (parece ruido, no edge)',
    }
    lines = [descriptions.get(f, f) for f in flags]
    # Construir el tooltip con cada flag en su linea (separador \n para data-attr y &#10; HTML)
    tooltip_text = '\n'.join(lines)
    # Escape para HTML attribute
    tooltip_html = tooltip_text.replace('"', '&quot;')
    color = '#f1c40f' if len(flags) == 1 else '#e67e22' if len(flags) == 2 else '#e74c3c'
    return (
        f'<span class="rf-tip" data-tip="{tooltip_html}" '
        f'style="color:{color};font-size:9px;margin-left:3px;cursor:help;position:relative" '
        f'data-flags="{",".join(flags)}">&#9888;{len(flags)}</span>'
    )


def _row_model(m, direction):
    """HTML de una fila LONG o SHORT dentro de la celda."""
    arrow  = '&#9650;' if direction == 'long' else '&#9660;'
    col    = '#2ecc71' if direction == 'long' else '#e74c3c'
    empty  = f'<div style="color:#242f55;font-size:9px;padding:2px 0">{arrow} <span style="color:#242f55">pendiente</span></div>'
    if not m:
        return empty
    cagr  = float(m.get('cagr', 0) or 0)
    wr    = float(m.get('wr', 0) or 0)
    t     = int(m.get('trades', 0) or 0)
    conf  = m.get('confidence','')
    cc    = {'ALTA':'#2ecc71','MEDIA':'#f1c40f','BAJA':'#e67e22'}.get(conf,'#555')
    strat = (m.get('strategy','') or '')[:10]
    grade_badge = _score_grade(m.get('score', -9999))
    return (
        f'<div style="display:flex;justify-content:space-between;align-items:center;padding:2px 0 1px">'
        f'<span style="color:{col};font-size:9px">{arrow} {strat}</span>'
        f'<span style="font-family:monospace;color:{c_cagr(cagr)};font-weight:700;font-size:11px">{cagr:+.1f}%</span>'
        f'</div>'
        f'<div style="display:flex;justify-content:space-between;font-size:9px;padding-bottom:1px">'
        f'<span style="color:{cc}">{conf}&nbsp;{grade_badge}{_red_flags_badge(m)}</span>'
        f'<span style="color:#7a8db5">WR {wr:.0f}% {t}T</span>'
        f'</div>'
    )

def _combined_row(ml, ms, ma):
    """HTML fila COMBINED — usa adaptive si existe, sino estima."""
    sep = '<div style="border-top:1px solid #141b38;margin:2px 0"></div>'
    if ma:
        # Adaptive ya es el combined real backtestado
        cagr = ma['cagr']; wr = ma['wr']; t = ma['trades']
        conf = ma.get('confidence','')
        cc   = {'ALTA':'#2ecc71','MEDIA':'#f1c40f','BAJA':'#e67e22'}.get(conf,'#555')
        return (
            sep +
            f'<div style="background:rgba(88,166,255,.08);border-radius:3px;padding:2px 3px;margin-top:1px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center">'
            f'<span style="color:#c9a227;font-size:9px;font-weight:700">&#9670; ADAPTIVE</span>'
            f'<span style="font-family:monospace;color:{c_cagr(cagr)};font-weight:700;font-size:11px">{cagr:+.1f}%</span>'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;font-size:9px">'
            f'<span style="color:{cc}">{conf}</span>'
            f'<span style="color:#7a8db5">WR {wr:.0f}% {t}T</span>'
            f'</div>'
            f'</div>'
        )
    elif ml and ms:
        # Estimar: BULL ~40% del tiempo, BEAR ~35% — resto RANGE sin trades
        est = ml['cagr'] * 0.40 + ms['cagr'] * 0.35
        return (
            sep +
            f'<div style="background:rgba(88,166,255,.05);border-radius:3px;padding:2px 3px;margin-top:1px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center">'
            f'<span style="color:#c9a227;font-size:9px">&#9670; COMBINED ~</span>'
            f'<span style="font-family:monospace;color:{c_cagr(est)};font-weight:700;font-size:11px">{est:+.1f}%</span>'
            f'</div>'
            f'<div style="font-size:9px;color:#555">estimado (40% bull + 35% bear)</div>'
            f'</div>'
        )
    return ''

def cell_html(asset, tf):
    """Celda con 3 secciones: LONG / SHORT / COMBINED."""
    ml = load_model(asset, tf, direction='long')
    ms = load_model(asset, tf, direction='short')

    # Separar adaptive del long normal
    ma = None
    if ml and ml.get('adaptive'):
        ma = ml
        ml = None  # adaptive reemplaza a long+short

    if ml or ms or ma:
        long_row  = _row_model(ml, 'long')
        short_row = _row_model(ms, 'short')
        comb_row  = _combined_row(ml, ms, ma)

        if ma and not ml and not ms:
            # Solo adaptive — mostrar compacto
            _sep_div = '<div style="border-top:1px solid #141b38;margin:2px 0"></div>'
            _adp = comb_row.replace('margin-top:1px', 'margin-top:6px').replace(_sep_div, '', 1)
            return (
                f'<td class="cell-ok" style="background:linear-gradient(180deg,rgba(88,166,255,.07),rgba(88,166,255,.01))">'
                f'{_adp}'
                f'</td>'
            )

        return (
            f'<td class="cell-ok">'
            f'{long_row}'
            f'<div style="border-top:1px solid #141b38;margin:2px 0"></div>'
            f'{short_row}'
            f'{comb_row}'
            f'</td>'
        )

    if is_running(asset, tf):
        return '<td class="cell-run"><div class="spin">&#9696;</div><div class="cell-sub">Optimizando</div></td>'

    name = f'{asset.lower()}_{tf}'
    for lp in [OUTPUT_DIR/'results'/'reports'/f'{name}_pipeline.log',
               OUTPUT_DIR/'results'/'reports'/f'{name}.log']:
        if lp.exists():
            last = lp.read_text(encoding='utf-8', errors='replace')[-300:]
            if 'Probando:' in last or 'Ciclo' in last:
                return '<td class="cell-run"><div class="spin">&#9696;</div><div class="cell-sub">Explorando</div></td>'
            if 'OOS negativo' in last or 'sin trades' in last:
                return '<td class="cell-neg"><div>&#9654;</div><div class="cell-sub">Reintentando</div></td>'

    return '<td class="cell-run" style="opacity:0.5"><div>&#8230;</div><div class="cell-sub">En cola</div></td>'


# ── MAIN HTML ─────────────────────────────────────────────────────────────────



def _dca_benchmark():
    """Compara paper trading vs BTC DCA desde el inicio."""
    try:
        BASE2 = Path('/opt/sigma')
        ts_f = BASE2 / 'results' / 'trade_state.json'
        bl_f = BASE2 / 'results' / 'reports' / 'btc_dca_baseline.json'
        if not ts_f.exists() or not bl_f.exists():
            return None
        ts = json.loads(ts_f.read_text())
        bl = json.loads(bl_f.read_text())
        port     = ts.get('portfolio', {})
        equity   = float(port.get('equity', 10000))
        initial  = float(port.get('initial_capital', 10000))
        start_dt = port.get('start_date', bl.get('start_date', ''))
        start_btc = float(bl.get('start_btc_price', 0))
        if start_btc <= 0:
            return None
        paper_ret = (equity - initial) / initial * 100
        import urllib.request as _ur
        _resp = json.loads(_ur.urlopen('https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT', timeout=3).read())
        btc_now = float(_resp['price'])
        dca_ret = (btc_now - start_btc) / start_btc * 100
        alpha   = paper_ret - dca_ret
        return {
            'paper_ret': paper_ret, 'dca_ret': dca_ret, 'alpha': alpha,
            'equity': equity, 'btc_now': btc_now, 'btc_start': start_btc,
            'start_date': start_dt, 'n_trades': len(ts.get('history', []))
        }
    except:
        return None


def _dca_benchmark_widget():
    """HTML: paper trading vs BTC DCA comparison."""
    dca = _dca_benchmark()
    if not dca:
        return ''
    pr   = dca['paper_ret']
    dr   = dca['dca_ret']
    alph = dca['alpha']
    eq   = dca['equity']
    btcn = dca['btc_now']
    n    = dca['n_trades']
    sd   = dca.get('start_date', '')[:10]
    pc   = '#2ecc71' if pr > 0 else '#e74c3c'
    dc   = '#2ecc71' if dr > 0 else '#e74c3c'
    ac   = '#2ecc71' if alph > 0 else '#e74c3c'
    cf   = ('#f1c40f', f'n={n} sin poder estadistico') if n < 30 else ('#2ecc71', f'n={n} valido')
    mono = 'IBM Plex Mono,monospace'
    h  = '<div style="background:#07091c;border:1px solid #1a2240;border-radius:10px;padding:14px 18px;margin-bottom:14px">'
    h += '<div style="font-size:9px;color:#7a8db5;letter-spacing:1px;text-transform:uppercase;margin-bottom:10px">'
    h += f'Paper Trading vs BTC DCA desde {sd} '
    h += f'<span style="color:{cf[0]};font-size:9px">{cf[1]}</span></div>'
    h += '<div style="display:flex;gap:10px">'
    h += '<div style="flex:1;background:#0d1428;border-radius:8px;padding:10px;text-align:center">'
    h += '<div style="font-size:9px;color:#7a8db5;margin-bottom:4px">SIGMA Paper</div>'
    h += f'<div style="font-size:20px;font-weight:800;color:{pc};font-family:{mono}">{pr:+.1f}%</div>'
    h += f'<div style="font-size:9px;color:#7a8db5">${eq:,.0f}</div>'
    h += '</div>'
    h += '<div style="flex:1;background:#0d1428;border-radius:8px;padding:10px;text-align:center">'
    h += '<div style="font-size:9px;color:#7a8db5;margin-bottom:4px">BTC DCA</div>'
    h += f'<div style="font-size:20px;font-weight:800;color:{dc};font-family:{mono}">{dr:+.1f}%</div>'
    h += f'<div style="font-size:9px;color:#7a8db5">${btcn:,.0f}/BTC</div>'
    h += '</div>'
    h += '<div style="flex:1;background:rgba(46,204,113,.06);border:1px solid rgba(46,204,113,.2);border-radius:8px;padding:10px;text-align:center">'
    h += '<div style="font-size:9px;color:#7a8db5;margin-bottom:4px">Alpha real</div>'
    h += f'<div style="font-size:20px;font-weight:800;color:{ac};font-family:{mono}">{alph:+.1f}pp</div>'
    h += '<div style="font-size:9px;color:#7a8db5">ventaja</div>'
    h += '</div>'
    h += '</div>'
    h += '</div>'
    return h

def _btc_cold_storage_widget():
    """HTML: progreso BTC cold storage hacia la meta."""
    try:
        cs_f = Path('/opt/sigma/results/reports/btc_cold_storage.json')
        if not cs_f.exists():
            return ''
        cs    = json.loads(cs_f.read_text())
        total = float(cs.get('total_btc', 0))
        goal  = float(cs.get('goal_btc', 1.0))
        pct   = min(100, total / goal * 100) if goal > 0 else 0
        btc_usd = 0
        try:
            import urllib.request as _ur3
            _r3 = json.loads(_ur3.urlopen('https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT', timeout=2).read())
            btc_usd = float(_r3['price'])
        except:
            pass
        value_usd = total * btc_usd
        pc2 = '#2ecc71' if pct >= 50 else ('#f1c40f' if pct >= 20 else '#e74c3c')
        mono = 'IBM Plex Mono,monospace'
        entries  = cs.get('entries', [])
        last_str = ''
        if entries:
            le = entries[-1]
            last_str = (f'<div style="font-size:9px;color:#7a8db5;margin-top:4px">'
                        f'Ultimo: {le.get("btc",0):.6f} BTC @ ${le.get("price_usd",0):,.0f}</div>')
        h  = '<div style="background:#07091c;border:1px solid #1a2240;border-radius:10px;padding:14px 18px;margin-bottom:14px">'
        h += '<div style="font-size:9px;color:#f7931a;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px">BTC Cold Storage</div>'
        h += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
        h += f'<span style="color:#f7931a;font-size:22px;font-weight:800;font-family:{mono}">{total:.6f} BTC</span>'
        h += f'<span style="font-size:11px;color:#7a8db5">${value_usd:,.0f} USD</span>'
        h += '</div>'
        h += '<div style="background:#1a2240;border-radius:4px;height:6px;overflow:hidden;margin-bottom:5px">'
        h += f'<div style="background:{pc2};width:{pct:.1f}%;height:100%;border-radius:4px"></div>'
        h += '</div>'
        h += f'<div style="display:flex;justify-content:space-between;font-size:9px;color:#7a8db5">'
        h += f'<span>{pct:.1f}% hacia {goal:.2f} BTC</span>'
        h += '<span>Not your keys, not your coins.</span>'
        h += '</div>'
        h += last_str
        h += '</div>'
        return h
    except:
        return ''



def _executor_gate_widget():
    """HTML: progreso del gate multi-factor hacia activacion del executor."""
    try:
        import urllib.request as _ur4
        ts_f = Path('/opt/sigma/results/trade_state.json')
        if not ts_f.exists():
            return ''
        ts   = json.loads(ts_f.read_text())
        hist = ts.get('history', [])
        port = ts.get('portfolio', {})
        n    = len(hist)
        # Criterio 1: n >= 30
        c1_ok  = n >= 30
        c1_val = f"{n}/30 trades"
        # Criterio 2: WR live >= 55%
        wins  = sum(1 for t in hist if float(t.get('pnl_pct',0)) > 0)
        wr    = wins/n*100 if n > 0 else 0
        c2_ok  = n >= 15 and wr >= 55
        c2_val = f"WR {wr:.1f}%" + (" ✓" if c2_ok else " (min 55%, necesita 15+ trades)")
        # Criterio 3: PF >= 1.3
        wl = [t.get('pnl_pct',0) for t in hist if float(t.get('pnl_pct',0)) > 0]
        ll = [t.get('pnl_pct',0) for t in hist if float(t.get('pnl_pct',0)) < 0]
        pf = sum(wl)/max(abs(sum(ll)),0.01) if ll else 9.9
        c3_ok  = n >= 10 and pf >= 1.3
        c3_val = f"PF {pf:.2f}" + (" ✓" if c3_ok else " (min 1.3)")
        # Criterio 4: Diversidad de regimen >= 2
        regimes = set(t.get('regime_at_close','BEAR') for t in hist if t.get('regime_at_close'))
        if not regimes: regimes = {'BEAR'}
        c4_ok  = len(regimes) >= 2
        c4_val = ', '.join(sorted(regimes)) + (" ✓" if c4_ok else " — falta BULL o RANGE")
        # Criterio 5: Duracion >= 6 semanas
        from datetime import datetime as _dt
        start_str = port.get('start_date','')
        try:
            start_dt = _dt.strptime(start_str[:10],'%Y-%m-%d')
            weeks = (_dt.now() - start_dt).days / 7
        except:
            weeks = 0
        c5_ok  = weeks >= 6
        c5_val = f"{weeks:.1f} semanas" + (" ✓" if c5_ok else f" (min 6)")
        # Score
        criteria = [c1_ok, c2_ok, c3_ok, c4_ok, c5_ok]
        n_ok = sum(criteria)
        pct  = n_ok / len(criteria) * 100
        bar_color = '#2ecc71' if n_ok == 5 else ('#f1c40f' if n_ok >= 3 else '#e74c3c')
        level_text = 'LISTO' if n_ok == 5 else ('CASI' if n_ok >= 4 else ('BUILDING' if n_ok >= 2 else 'EARLY'))
        level_color = {'LISTO':'#2ecc71','CASI':'#f1c40f','BUILDING':'#e67e22','EARLY':'#e74c3c'}.get(level_text,'#7a8db5')
        rows = ''
        for ok, val, name in [
            (c1_ok, c1_val, 'Trades suficientes (≥30)'),
            (c2_ok, c2_val, 'WR live vs benchmark (≥55%)'),
            (c3_ok, c3_val, 'Profit Factor (≥1.3)'),
            (c4_ok, c4_val, 'Diversidad de regimen (≥2)'),
            (c5_ok, c5_val, 'Duracion validacion (≥6 sem)'),
        ]:
            ic = '#2ecc71' if ok else '#e74c3c'
            sym_i = '✅' if ok else '❌'
            rows += (f'<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #141b38">'
                     f'<span style="font-size:10px;color:#b8c5e0">{sym_i} {name}</span>'
                     f'<span style="font-size:10px;color:{ic};font-family:IBM Plex Mono,monospace">{val}</span>'
                     f'</div>')
        mono = 'IBM Plex Mono,monospace'
        h  = '<div style="background:#07091c;border:1px solid #1a2240;border-radius:10px;padding:14px 18px;margin-bottom:14px">'
        h += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:6px">'
        h += '<div style="font-size:9px;color:#7a8db5;letter-spacing:1px;text-transform:uppercase">Executor Gate — Criterios de Activacion</div>'
        h += f'<div style="font-size:13px;font-weight:800;color:{level_color}">{level_text} {n_ok}/5</div>'
        h += '</div>'
        h += '<div style="background:#1a2240;border-radius:4px;height:5px;overflow:hidden;margin-bottom:12px">'
        h += f'<div style="background:{bar_color};width:{pct:.0f}%;height:100%;border-radius:4px;transition:width .3s"></div>'
        h += '</div>'
        h += rows
        h += '</div>'
        return h
    except:
        return ''


def _stress_test_widget():
    """HTML: stress test forward-looking sobre las posiciones reales abiertas
    hoy (shock BTC -10/-20/-30%), calculado en utils/portfolio_risk.py."""
    try:
        pr_f = Path('/opt/sigma/results/reports/portfolio_risk.json')
        if not pr_f.exists():
            return ''
        pr = json.loads(pr_f.read_text())
        stress = pr.get('stress_test', {})
        if not stress:
            return ''
        mono = 'IBM Plex Mono,monospace'
        rows = ''
        for sc in stress.values():
            shock = sc.get('btc_shock_pct', 0)
            pnl_pct = sc.get('portfolio_pnl_pct', 0)
            pnl_usd = sc.get('portfolio_pnl_usd', 0)
            color = '#2ecc71' if pnl_pct >= 0 else '#e74c3c'
            rows += (f'<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #141b38">'
                     f'<span style="font-size:10px;color:#b8c5e0">BTC {shock:+.0f}%</span>'
                     f'<span style="font-size:10px;color:{color};font-family:{mono}">{pnl_pct:+.2f}% (${pnl_usd:+,.2f})</span>'
                     f'</div>')
        h  = '<div style="background:#07091c;border:1px solid #1a2240;border-radius:10px;padding:14px 18px;margin-bottom:14px">'
        h += '<div style="font-size:9px;color:#7a8db5;letter-spacing:1px;text-transform:uppercase;margin-bottom:10px">Stress Test — Posiciones Reales Abiertas Hoy</div>'
        h += rows
        h += '</div>'
        return h
    except:
        return ''


def _aum_widget():
    """HTML: AUM total gestionado (capital propio + seguidores de Copy
    Trading). El AUM total es manual (Binance no lo expone por API,
    actualizado via /aum N en Telegram); el capital propio se lee en vivo
    de Binance directo, no del historial (ver _aum_own_balance en
    telegram_notifier.py)."""
    try:
        aum_f = Path('/opt/sigma/results/reports/aum.json')
        if not aum_f.exists():
            return ''
        aum = json.loads(aum_f.read_text())
        aum_total = aum.get('aum_total')
        if aum_total is None:
            return ''
        try:
            import sys as _sys_aum
            if '/opt/sigma' not in _sys_aum.path:
                _sys_aum.path.insert(0, '/opt/sigma')
            from engine.live.live_executor import _get_exchange as _aum_get_ex
            own = float(_aum_get_ex().fetch_balance().get('USDT', {}).get('total', 0))
        except Exception:
            own = None
        mono = 'IBM Plex Mono,monospace'
        own_txt = f'${own:,.2f}' if own is not None else '—'
        followers_txt = f'${max(0.0, aum_total - own):,.2f}' if own is not None else '—'
        h  = '<div style="background:#07091c;border:1px solid #1a2240;border-radius:10px;padding:14px 18px;margin-bottom:14px">'
        h += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">'
        h += '<div style="font-size:9px;color:#7a8db5;letter-spacing:1px;text-transform:uppercase">AUM Total Gestionado</div>'
        h += f'<div style="font-size:9px;color:#7a8db5;font-family:{mono}">act. {aum.get("updated_at","?")}</div>'
        h += '</div>'
        h += f'<div style="font-size:22px;font-weight:800;color:#d4af37;font-family:{mono};margin-bottom:8px">${aum_total:,.2f}</div>'
        h += (f'<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #141b38">'
              f'<span style="font-size:10px;color:#b8c5e0">Capital propio (Binance)</span>'
              f'<span style="font-size:10px;color:#2ecc71;font-family:{mono}">{own_txt}</span></div>')
        h += (f'<div style="display:flex;justify-content:space-between;padding:4px 0">'
              f'<span style="font-size:10px;color:#b8c5e0">Capital de seguidores (Copy Trading)</span>'
              f'<span style="font-size:10px;color:#378ADD;font-family:{mono}">{followers_txt}</span></div>')
        h += '</div>'
        return h
    except:
        return ''


def generate_html():
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    mc     = load_mc()
    ca     = load_cross_asset()
    wft    = load_wft()
    db     = get_db_stats()
    events = load_events(25)

    # Halving cycle
    halving   = date(2024, 4, 20)
    cyc_pct   = (date.today() - halving).days / 1461 * 100
    phase_ok  = not (50 <= cyc_pct <= 75)
    phase_txt = f'FASE OK ({cyc_pct:.0f}%)' if phase_ok else f'FASE DIFICIL ({cyc_pct:.0f}%)'
    phase_col = '#2ecc71' if phase_ok else '#e74c3c'

    # Top models for portfolio rule (Motor 1 only — Motor 2 is independent)
    all_m = []
    for asset in ASSETS_M1:
        for tf in ['1h','4h','15m','5m']:
            ml = load_model(asset, tf, direction='long')
            ms = load_model(asset, tf, direction='short')
            m = None
            if ml and ms:
                # 2026-05-19: tie-break por ROBUSTNESS rank primero, score como segundo
                def _rob_rank_cell(_m, _tf, _asset):
                    if not _m: return -1
                    try:
                        import sys as _src
                        if '/opt/sigma' not in _src.path: _src.path.insert(0, '/opt/sigma')
                        from utils.robustness import robustness_score as _rsc
                        _strat = _m.get('strategy', '')
                        _jp = OUTPUT_DIR / 'models' / _tf / (_asset.lower() + '_' + _strat + '.json')
                        if not _jp.exists(): return 0
                        _d = json.loads(_jp.read_text())
                        return {'PASS_LIVE': 3, 'PAPER_ONLY': 2, 'BLOCKED': 1}.get(_rsc(_d).get('action','?'), 0)
                    except Exception:
                        return 0
                _r_l2 = _rob_rank_cell(ml, tf, asset)
                _r_s2 = _rob_rank_cell(ms, tf, asset)
                if _r_l2 != _r_s2:
                    m = ml if _r_l2 > _r_s2 else ms
                else:
                    m = ml if (ml.get('score', -9999) or -9999) >= (ms.get('score', -9999) or -9999) else ms
            elif ml: m = ml
            elif ms: m = ms
            if m:
                all_m.append((m['cagr'], asset, tf, m))
    all_m.sort(reverse=True)
    top2 = all_m[:2]

    # Count total ready
    n_ready   = sum(1 for c,a,t,m in all_m)
    n_total   = len(ASSETS_M1) * len(TIMEFRAMES)  # M1 only
    # Pre-computar widgets para inyectar en f-string
    _dca_html  = _dca_benchmark_widget()
    _cs_html   = _btc_cold_storage_widget()
    _gate_html = _executor_gate_widget()
    _stress_html = _stress_test_widget()
    _aum_html = _aum_widget()

    # Walk-forward stats
    wft_done = len(wft)
    wft_pos  = sum(1 for w in wft if w['ok'])
    wft_pct  = round(wft_pos/wft_done*100,1) if wft_done else 0
    wft_col  = '#2ecc71' if wft_pct>=60 else '#f1c40f' if wft_pct>=50 else '#e74c3c'

    # Cross-asset
    ca_pos   = ca.get('positive_assets', [])
    ca_conf  = ca.get('confidence', '')

    # Matrix rows
    matrix_rows = ''
    # Collect model data for summary row
    tf_models = {tf: [] for tf in TIMEFRAMES}  # tf -> list of (cagr, wr, trades, score)
    current_champions = {}  # 'ASSET|tf|dir' -> strategy_name (para detectar cambios de campeon)

    for asset in ASSETS_M1:
        color  = ASSET_COLOR[asset]
        emoji  = ASSET_EMOJI[asset]
        cells  = ''.join(cell_html(asset, tf) for tf in TIMEFRAMES)
        matrix_rows += f'''
        <tr>
          <td class="asset-col">
            <div class="asset-box" style="--asset-color:{color}">
              <span class="asset-emoji" style="color:{color}">{emoji}</span>
              <span class="asset-name">{asset}</span>
            </div>
          </td>
          {cells}
        </tr>'''
        # Collect for summary — UN modelo por slot (el mejor entre long y short),
        # consistente con lo que el Pine realmente deploya en TradingView.
        for tf in TIMEFRAMES:
            ml = load_model(asset, tf, direction='long')
            ms = load_model(asset, tf, direction='short')
            best = None
            if ml and ms:
                # 2026-05-19: tie-break por ROBUSTNESS rank primero, score como segundo
                # PASS_LIVE > PAPER_ONLY > BLOCKED. Resuelve "slot stuck con champion overfit"
                def _rob_rank(_m, _tf, _asset):
                    if not _m: return -1
                    try:
                        import sys as _sr
                        if '/opt/sigma' not in _sr.path: _sr.path.insert(0, '/opt/sigma')
                        from utils.robustness import robustness_score as _rs
                        _strat = _m.get('strategy', '')
                        _jp = OUTPUT_DIR / 'models' / _tf / (_asset.lower() + '_' + _strat + '.json')
                        if not _jp.exists(): return 0
                        _d = json.loads(_jp.read_text())
                        _act = _rs(_d).get('action', '?')
                        return {'PASS_LIVE': 3, 'PAPER_ONLY': 2, 'BLOCKED': 1}.get(_act, 0)
                    except Exception:
                        return 0
                _r_l = _rob_rank(ml, tf, asset)
                _r_s = _rob_rank(ms, tf, asset)
                if _r_l != _r_s:
                    best = ml if _r_l > _r_s else ms
                else:
                    # Empate de robustness: usa score
                    best = ml if (ml.get('score', -9999) or -9999) >= (ms.get('score', -9999) or -9999) else ms
                best_dir = 'long' if best is ml else 'short'
            elif ml:
                best, best_dir = ml, 'long'
            elif ms:
                best, best_dir = ms, 'short'
            if best:
                # 2026-05-19: robustness_action (idx 7) para port_cagr honesto
                _rob_action = '?'
                try:
                    import sys as _sys_rob
                    if '/opt/sigma' not in _sys_rob.path: _sys_rob.path.insert(0, '/opt/sigma')
                    from utils.robustness import robustness_score as _rob_score
                    _strat_rob = best.get('strategy','')
                    _jp_rob = OUTPUT_DIR / 'models' / tf / (asset.lower() + '_' + _strat_rob + '.json')
                    if _jp_rob.exists():
                        _d_rob = json.loads(_jp_rob.read_text())
                        _rob_action = _rob_score(_d_rob).get('action', '?')
                except Exception:
                    pass
                tf_models[tf].append((
                    best['cagr'], best['wr'], best['trades'], best.get('score', -9999),
                    best.get('dd', 0), best.get('pf', 0), best.get('rr', 0),
                    _rob_action,
                ))
                current_champions[f'{asset}|{tf}'] = f'{best.get("strategy","?")}|{best_dir}'

    # Summary row — weighted averages per TF column
    summary_cells = ''
    all_cagrs, all_wrs, all_trades_sum = [], [], 0
    for tf in TIMEFRAMES:
        ms = tf_models[tf]
        if ms:
            cagrs  = [x[0] for x in ms]
            wrs    = [x[1] for x in ms]
            trades = [x[2] for x in ms]
            total_t = sum(trades)
            # Weighted WR by trades
            w_wr   = sum(wrs[i]*trades[i] for i in range(len(ms))) / max(total_t, 1)
            avg_c  = sum(cagrs) / len(cagrs)
            all_cagrs.extend(cagrs); all_wrs.extend(wrs); all_trades_sum += total_t
            summary_cells += (
                f'<td style="text-align:center;border-top:2px solid #242f55;padding:7px 4px">'
                f'<div style="color:{c_cagr(avg_c)};font-family:\'IBM Plex Mono\',monospace;font-size:12px;font-weight:700">{avg_c:+.1f}%</div>'
                f'<div style="font-size:10px;color:{c_wr(w_wr)}">WR {w_wr:.0f}%</div>'
                f'<div style="font-size:10px;color:#7a8db5">{total_t}T</div>'
                f'</td>'
            )
        else:
            summary_cells += '<td style="border-top:2px solid #242f55;text-align:center;color:#242f55">—</td>'

    # Overall portfolio — promedio simple de TODOS los slots activos (best por sym+tf)
    # Cada slot cuenta igual: 1 voto cada uno. Se auto-actualiza al agregar nuevos champions.
    if all_cagrs:
        all_models_w = []
        for tf in TIMEFRAMES:
            for m_data in tf_models[tf]:
                all_models_w.append(m_data)
        tot_t = all_trades_sum
        CAGR_OPERABLE = 12.0
        def _calc_port(ms):
            """Port metrics. CAGR ponderado por trades (m[2]) — slots más activos pesan más."""
            if not ms:
                return dict(cagr=0, wr=0, dd=0, dd_worst=0, pf=0, rr=0, ev_r=0, calmar=0, std=0, n=0, cagr_simple=0)
            n = len(ms)
            # Weights por trades (m[2]). Si no hay trades o len<3, peso=1 (modelo poco probado pesa mínimo)
            weights = [max(m[2] if len(m) > 2 else 1, 1) for m in ms]
            total_w = sum(weights)
            # CAGR ponderado (la métrica principal ahora)
            cagr_weighted = sum(m[0] * w for m, w in zip(ms, weights)) / total_w if total_w > 0 else 0
            # CAGR simple (referencia, preservado por compatibilidad)
            cagr_simple = sum(m[0] for m in ms) / n
            cagr = cagr_weighted
            # WR también ponderado por trades — más justo
            wr = sum(m[1] * w for m, w in zip(ms, weights)) / total_w if total_w > 0 else 0
            dd_v = [m[4] for m in ms if len(m) > 4]
            dd_w = [max(m[2] if len(m) > 2 else 1, 1) for m in ms if len(m) > 4]
            dd_avg = sum(d * w for d, w in zip(dd_v, dd_w)) / sum(dd_w) if dd_w else 0
            dd_worst = min(dd_v) if dd_v else 0
            pf_v = [m[5] for m in ms if len(m) > 5 and m[5] > 0]
            pf = sum(pf_v) / max(len(pf_v), 1)
            rr_v = [m[6] for m in ms if len(m) > 6 and m[6] > 0]
            rr = sum(rr_v) / len(rr_v) if rr_v else 0
            wr_d = wr / 100 if wr else 0
            ev_r = wr_d * rr - (1 - wr_d) if rr > 0 else 0
            std = (sum((m[0] - cagr) ** 2 for m in ms) / n) ** 0.5 if n > 1 else 0
            calmar = round(cagr / abs(dd_worst), 2) if dd_worst < 0 else 0
            return dict(cagr=cagr, wr=wr, dd=dd_avg, dd_worst=dd_worst, pf=pf, rr=rr, ev_r=ev_r, calmar=calmar, std=std, n=n, cagr_simple=cagr_simple)
        # 2026-05-19: PORT_CAGR HONESTO — BLOCKED no cuenta en operacional.
        def _is_blocked(m):
            return len(m) > 7 and m[7] == 'BLOCKED'
        def _is_pass_live(m):
            return len(m) > 7 and m[7] == 'PASS_LIVE'
        operational_models = [m for m in all_models_w if not _is_blocked(m)]
        pass_live_models = [m for m in all_models_w if _is_pass_live(m)]

        op = _calc_port([m for m in operational_models if m[0] >= CAGR_OPERABLE])
        tot = _calc_port(all_models_w)
        op_full = _calc_port(operational_models)
        pl = _calc_port(pass_live_models)
        total_trades_w = sum(m[2] for m in all_models_w) if all_models_w else 0
        port_cagr = op["cagr"]; port_wr = op["wr"]; port_dd = op["dd"]; port_dd_worst = op["dd_worst"]
        port_pf = op["pf"]; port_rr = op["rr"]; port_ev_r = op["ev_r"]; port_calmar = op["calmar"]; port_cagr_std = op["std"]
        n_operable = op["n"]
        port_cagr_total = tot["cagr"]; port_wr_total = tot["wr"]; port_dd_total = tot["dd"]
        n_total_slots = tot["n"]; n_no_op = n_total_slots - n_operable
        # Honestos
        port_cagr_operational = op_full["cagr"]
        port_cagr_pass_live = pl["cagr"]
        n_pass_live = pl["n"]
        n_blocked = sum(1 for m in all_models_w if _is_blocked(m))
        # Calcular kelly_boost para mostrar en la matrix (2026-05-14)
        try:
            _ch_list_for_kelly = []
            for _slot_k, _val_k in current_champions.items():
                if "|" not in _val_k: continue
                _strat_k, _dir_k = _val_k.split("|", 1)
                _sym_k, _tf_k = _slot_k.split("|")
                _jp_k = OUTPUT_DIR / "models" / _tf_k / (_sym_k.lower() + "_" + _strat_k + ".json")
                if _jp_k.exists():
                    _d_k = json.loads(_jp_k.read_text())
                    _m_k = _d_k.get("metrics_oos") or {}
                    _ch_list_for_kelly.append({
                        "direction": _dir_k,
                        "cagr": _m_k.get("cagr", 0) or 0,
                        "trades": _m_k.get("trades", 0) or 0,
                    })
            port_kelly_boost = _funding_kelly_boost_estimate(_ch_list_for_kelly)
            port_cagr_with_kelly = round(port_cagr + port_kelly_boost, 2)
        except Exception:
            port_kelly_boost = 0.0
            port_cagr_with_kelly = port_cagr
    else:
        port_cagr = port_wr = port_dd = port_dd_worst = port_pf = port_calmar = port_cagr_std = port_rr = port_ev_r = 0
        port_cagr_total = port_wr_total = port_dd_total = 0
        n_operable = n_total_slots = n_no_op = tot_t = 0
        port_kelly_boost = 0.0
        port_cagr_with_kelly = 0.0

    # Contar modelos grado A+/A (score >= 0.55) — operables
    n_grade_a = sum(1 for m in all_models_w if len(m) > 3 and m[3] >= 0.55) if all_cagrs else 0

    # ── SNAPSHOT DEL PONDERADO — referencia del último cambio de campeón ────────
    # Los valores port_* se mantienen VIVOS (recálculo real). El snapshot solo guarda
    # el ponderado al momento del último reranking, para mostrar el delta vs hoy.
    SNAPSHOT_PATH = OUTPUT_DIR / 'results' / 'reports' / 'port_snapshot.json'
    snap_age_min = 0
    snap_trigger = '—'
    snap_cagr = port_cagr  # default: si no hay snapshot, delta=0
    try:
        if SNAPSHOT_PATH.exists():
            snap = json.loads(SNAPSHOT_PATH.read_text())
            snap_cagr    = snap.get('port_cagr', port_cagr)
            snap_trigger = snap.get('trigger', '—')
            snap_ts      = snap.get('snapshot_at', '')
            if snap_ts:
                try:
                    snap_dt = datetime.fromisoformat(snap_ts.replace('Z',''))
                    if snap_dt.tzinfo is None:
                        snap_dt = snap_dt.replace(tzinfo=timezone.utc)
                    snap_age_min = int((datetime.now(timezone.utc) - snap_dt).total_seconds() / 60)
                except Exception:
                    snap_age_min = 0
            # Detectar cambio de campeón y regrabar snapshot con los valores LIVE de hoy
            if snap.get('champions', {}) != current_champions and current_champions:
                old_ch = snap.get('champions', {})
                diffs = [f'{k.split("|",1)[0]} {k.split("|",1)[1]}: {old_ch.get(k,"—")}→{v}'
                         for k, v in current_champions.items() if old_ch.get(k) != v]
                snap_trigger = '; '.join(diffs[:3]) + ('…' if len(diffs) > 3 else '')
                _write_snapshot(SNAPSHOT_PATH, port_cagr, port_wr, port_dd, port_pf, port_calmar,
                                tot_t, n_grade_a, current_champions, snap_trigger)
                # 2026-05-19: fields honestos post-snapshot
                try:
                    _snap_data = json.loads(SNAPSHOT_PATH.read_text())
                    _snap_data['port_cagr_operational'] = round(port_cagr_operational, 2)
                    _snap_data['port_cagr_pass_live'] = round(port_cagr_pass_live, 2)
                    _snap_data['port_cagr_all_inc_blocked'] = round(port_cagr_total, 2)
                    _snap_data['n_pass_live'] = n_pass_live
                    _snap_data['n_blocked'] = n_blocked
                    SNAPSHOT_PATH.write_text(json.dumps(_snap_data, indent=2))
                except Exception as _e_snap_ext:
                    print('[port_cagr ext error] ' + str(_e_snap_ext), flush=True)
                snap_cagr = port_cagr
                snap_age_min = 0
        elif current_champions:
            _write_snapshot(SNAPSHOT_PATH, port_cagr, port_wr, port_dd, port_pf, port_calmar,
                            tot_t, n_grade_a, current_champions, 'Initial snapshot')
            snap_trigger = 'Initial snapshot'
            snap_cagr = port_cagr
    except Exception:
        pass

    # 2026-05-19: SIEMPRE actualizar honest fields en snapshot (independiente de champion change)
    try:
        if SNAPSHOT_PATH.exists():
            _snap_now = json.loads(SNAPSHOT_PATH.read_text())
            _snap_now['port_cagr_operational'] = round(port_cagr_operational, 2)
            _snap_now['port_cagr_pass_live'] = round(port_cagr_pass_live, 2)
            _snap_now['port_cagr_all_inc_blocked'] = round(port_cagr_total, 2)
            _snap_now['n_pass_live'] = n_pass_live
            _snap_now['n_blocked'] = n_blocked
            # port_cagr es M1+M2 (set by champion_watcher). NO sobreescribir con M1-only.
        # Usar port_cagr_operational para la vista M1-only.
            SNAPSHOT_PATH.write_text(json.dumps(_snap_now, indent=2))
    except Exception as _e_honest:
        print('[honest update err] ' + str(_e_honest), flush=True)

    # ── Mini-chart SVG de equity ─────────────────────────────────────────
    eq_history = []
    try:
        _ts = json.loads((OUTPUT_DIR / 'results' / 'trade_state.json').read_text())
        eq_history = _ts.get('portfolio', {}).get('equity_history', [])
    except Exception:
        pass

    if eq_history and len(eq_history) >= 1:
        eqs = [10000.0] + [float(p.get('eq', 10000)) for p in eq_history]
        _n = len(eqs)
        _w, _h, _pad = 600, 130, 14
        _mn_raw, _mx_raw = min(eqs), max(eqs)
        # Padding vertical en el rango — evita que la curva toque los bordes
        _vpad = max((_mx_raw - _mn_raw) * 0.15, 5.0)
        _mn = _mn_raw - _vpad
        _mx = _mx_raw + _vpad
        _rng = max(_mx - _mn, 1.0)

        # Generar puntos (x, y)
        _pts_xy = []
        for _i, _e in enumerate(eqs):
            _x = _pad + (_w - 2*_pad) * _i / max(_n-1, 1)
            _y = _h - _pad - (_h - 2*_pad) * (_e - _mn) / _rng
            _pts_xy.append((round(_x,1), round(_y,1)))

        # Path suave con Catmull-Rom -> cubic bezier
        def _smooth_path(pts):
            if len(pts) < 2:
                return ''
            if len(pts) == 2:
                return 'M ' + str(pts[0][0]) + ',' + str(pts[0][1]) + ' L ' + str(pts[1][0]) + ',' + str(pts[1][1])
            d = 'M ' + str(pts[0][0]) + ',' + str(pts[0][1])
            for i in range(len(pts) - 1):
                p0 = pts[i-1] if i > 0 else pts[i]
                p1 = pts[i]
                p2 = pts[i+1]
                p3 = pts[i+2] if i < len(pts) - 2 else pts[i+1]
                cp1x = p1[0] + (p2[0] - p0[0]) / 6
                cp1y = p1[1] + (p2[1] - p0[1]) / 6
                cp2x = p2[0] - (p3[0] - p1[0]) / 6
                cp2y = p2[1] - (p3[1] - p1[1]) / 6
                d += ' C ' + str(round(cp1x,1)) + ',' + str(round(cp1y,1))
                d += ' ' + str(round(cp2x,1)) + ',' + str(round(cp2y,1))
                d += ' ' + str(p2[0]) + ',' + str(p2[1])
            return d

        _path = _smooth_path(_pts_xy)
        # Path para el area (cierra con linea al bottom y vuelve al inicio)
        _bottom_y = _h - _pad
        _area_path = _path + ' L ' + str(_pts_xy[-1][0]) + ',' + str(_bottom_y) + ' L ' + str(_pts_xy[0][0]) + ',' + str(_bottom_y) + ' Z'

        _last_x, _last_y = _pts_xy[-1]
        _is_positive = eqs[-1] >= eqs[0]
        _line_color = '#00e676' if _is_positive else '#f85149'
        _glow_color = '#00e676' if _is_positive else '#ff6b6b'
        _delta_pct = (eqs[-1] / eqs[0] - 1) * 100
        _ref_y = round(_h-_pad-(_h-2*_pad)*(10000-_mn)/_rng,1)

        # Grid lines horizontales sutiles (4 lineas)
        _grid_lines = ''
        for _g in range(1, 5):
            _gy = _pad + (_h - 2*_pad) * _g / 5
            _grid_lines += '<line x1="' + str(_pad) + '" y1="' + str(round(_gy,1)) + '" x2="' + str(_w-_pad) + '" y2="' + str(round(_gy,1)) + '" stroke="#141b38" stroke-width="1"/>'

        equity_svg = (
            '<svg width="' + str(_w) + '" height="' + str(_h) + '" style="background:linear-gradient(180deg,#07091c 0%,#050914 100%);border:1px solid #141b38;border-radius:8px;display:block">'
            '<defs>'
            '<linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0%" stop-color="' + _line_color + '" stop-opacity="0.35"/>'
            '<stop offset="60%" stop-color="' + _line_color + '" stop-opacity="0.08"/>'
            '<stop offset="100%" stop-color="' + _line_color + '" stop-opacity="0"/>'
            '</linearGradient>'
            '<filter id="eqGlow" x="-50%" y="-50%" width="200%" height="200%">'
            '<feGaussianBlur stdDeviation="2.5" result="b"/>'
            '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
            '</filter>'
            '</defs>'
            # Grid sutil
            + _grid_lines +
            # Linea de referencia $10,000 (capital inicial)
            '<line x1="' + str(_pad) + '" y1="' + str(_ref_y) + '" x2="' + str(_w-_pad) + '" y2="' + str(_ref_y) + '" stroke="#444" stroke-width="1" stroke-dasharray="4,4" opacity="0.6"/>'
            '<text x="' + str(_w-_pad-3) + '" y="' + str(_ref_y-3) + '" fill="#666" font-size="9" text-anchor="end" font-family="IBM Plex Mono,monospace">$10,000</text>'
            # Area con gradiente
            '<path d="' + _area_path + '" fill="url(#eqGrad)"/>'
            # Linea principal con glow
            '<path d="' + _path + '" stroke="' + _line_color + '" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" filter="url(#eqGlow)"/>'
            # Punto final con halo
            '<circle cx="' + str(_last_x) + '" cy="' + str(_last_y) + '" r="6" fill="' + _glow_color + '" opacity="0.3"/>'
            '<circle cx="' + str(_last_x) + '" cy="' + str(_last_y) + '" r="3" fill="' + _line_color + '" stroke="#07091c" stroke-width="1.5"/>'
            # Labels
            '<text x="' + str(_w-_pad) + '" y="' + str(_pad+12) + '" fill="' + _line_color + '" font-size="14" text-anchor="end" font-family="IBM Plex Mono,monospace" font-weight="700">$' + ('%.2f' % eqs[-1]) + '</text>'
            '<text x="' + str(_w-_pad) + '" y="' + str(_pad+26) + '" fill="' + _line_color + '" font-size="11" text-anchor="end" font-family="IBM Plex Mono,monospace" font-weight="600" opacity="0.85">' + ('%+.2f' % _delta_pct) + '%</text>'
            '<text x="' + str(_pad) + '" y="' + str(_pad+12) + '" fill="#7a8db5" font-size="10" font-family="IBM Plex Mono,monospace" font-weight="600" letter-spacing="1">EQUITY</text>'
            '<text x="' + str(_pad) + '" y="' + str(_pad+24) + '" fill="#4e5f90" font-size="9" font-family="IBM Plex Mono,monospace">' + str(_n) + ' puntos · min $' + ('%.0f' % _mn_raw) + ' · max $' + ('%.0f' % _mx_raw) + '</text>'
            '</svg>'
        )
    else:
        equity_svg = '<div style="font-size:11px;color:#4e5f90;padding:14px;font-family:monospace;background:#07091c;border:1px solid #141b38;border-radius:8px;text-align:center">Equity curve aparecera tras 1+ trade cerrado</div>'

    # Delta vs último campeón
    delta_cagr = port_cagr - snap_cagr

    # Detección de concentración + slots con muestra chica
    concentration_warn = ''
    if all_models_w and total_trades_w > 0:
        max_weight_pct = 0
        for m_w in all_models_w:
            if len(m_w) > 2 and m_w[2] > 0:
                w = m_w[2] / total_trades_w * 100
                if w > max_weight_pct: max_weight_pct = w
        n_thin = sum(1 for m_w in all_models_w if len(m_w) > 2 and m_w[2] < 15)
        warnings_parts = []
        if max_weight_pct > 30:
            warnings_parts.append(f'⚠ slot con {max_weight_pct:.0f}% del peso (>30% = concentración)')
        if n_thin > 0:
            warnings_parts.append(f'⚠ {n_thin} slot(s) con <15 trades OOS (muestra chica)')
        concentration_warn = ' · '.join(warnings_parts)
    if snap_age_min < 60:
        snap_age_txt = f'{snap_age_min}m'
    elif snap_age_min < 1440:
        snap_age_txt = f'{snap_age_min // 60}h {snap_age_min % 60}m'
    else:
        snap_age_txt = f'{snap_age_min // 1440}d {(snap_age_min % 1440) // 60}h'

    matrix_rows += f'''
        <tr style="background:#07091c">
          <td style="border-top:2px solid #242f55;padding:7px 8px">
            <span style="font-size:10px;font-weight:700;color:#7a8db5;text-transform:uppercase;letter-spacing:.05em">Ponderado</span>
          </td>
          {summary_cells}
        </tr>
        <tr style="background:#060d20">
          <td colspan="6" style="padding:8px 10px;border-top:1px solid #141b38">
            <span style="font-size:11px;color:#7a8db5">Portafolio operable (CAGR &ge; 12%): &nbsp;</span>
            <span style="font-family:\'IBM Plex Mono\',monospace;color:{c_cagr(port_cagr)};font-weight:700;font-size:14px">{port_cagr:+.1f}%</span>
            <span style="font-family:\'IBM Plex Mono\',monospace;color:#7a8db5;font-size:11px"> &plusmn;{port_cagr_std:.1f}%</span>
            <span style="font-size:11px;color:#e0bb3a;font-weight:700"> +Kelly </span>
            <span style="font-family:'IBM Plex Mono',monospace;color:#e0bb3a;font-weight:700;font-size:12px">+{port_kelly_boost:.2f}pp</span>
            <span style="font-size:11px;color:#69f0ae;font-weight:700"> = </span>
            <span style="font-family:'IBM Plex Mono',monospace;color:#69f0ae;font-weight:700;font-size:14px">{port_cagr_with_kelly:+.2f}%</span>
            <span style="font-size:11px;color:#7a8db5"> CAGR estimado anual &nbsp;|&nbsp; WR: </span>
            <span style="font-family:\'IBM Plex Mono\',monospace;color:{c_wr(port_wr)};font-weight:700;font-size:13px">{port_wr:.1f}%</span>
            <span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; DD avg: </span>
            <span style="font-family:\'IBM Plex Mono\',monospace;color:{"#f85149" if port_dd<-20 else "#ff9800" if port_dd<-10 else "#dde3f5"};font-weight:700;font-size:13px">{port_dd:.1f}%</span>
            <span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; DD peor: </span>
            <span style="font-family:\'IBM Plex Mono\',monospace;color:{"#f85149" if port_dd_worst<-20 else "#ff9800" if port_dd_worst<-10 else "#dde3f5"};font-weight:700;font-size:13px">{port_dd_worst:.1f}%</span>
            <span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; PF: </span>
            <span style="font-family:\'IBM Plex Mono\',monospace;color:{"#00e676" if port_pf>=2 else "#69f0ae" if port_pf>=1.5 else "#ff9800"};font-weight:700;font-size:13px">{port_pf:.2f}</span>
            <span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; Calmar: </span>
            <span style="font-family:\'IBM Plex Mono\',monospace;color:{"#00e676" if port_calmar>=2 else "#69f0ae" if port_calmar>=1 else "#ff9800"};font-weight:700;font-size:13px">{port_calmar:.2f}</span>
            <span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; RR: </span>
            <span style="font-family:\'IBM Plex Mono\',monospace;color:{"#00e676" if port_rr>=1.5 else "#69f0ae" if port_rr>=1 else "#ff9800"};font-weight:700;font-size:13px">{port_rr:.2f}</span>
            <span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; EV: </span>
            <span style="font-family:\'IBM Plex Mono\',monospace;color:{"#00e676" if port_ev_r>=0.3 else "#69f0ae" if port_ev_r>=0.1 else "#ff9800" if port_ev_r>=0 else "#f85149"};font-weight:700;font-size:13px">{port_ev_r:+.2f}R</span>
            <span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; Trades OOS: </span>
            <span style="font-family:\'IBM Plex Mono\',monospace;color:#dde3f5;font-size:12px">{tot_t}</span>
            <span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; Activos: </span>
            <span style="font-family:\'IBM Plex Mono\',monospace;color:#c9a227;font-size:12px">{n_ready}/{n_total}</span>
            <span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; Grado A+/A: </span>
            <span style="font-family:\'IBM Plex Mono\',monospace;color:#69f0ae;font-size:13px;font-weight:700">{n_grade_a}</span>
          </td>
        </tr>
        <tr style="background:#060d20">
          <td colspan="6" style="padding:4px 10px 8px;font-size:10px;color:#4e5f90;border-top:1px dashed #141b38;font-family:\'IBM Plex Mono\',monospace">
            CAGR ponderado vivo · Δ desde último cambio de campeón:
            <span style="color:{"#69f0ae" if delta_cagr>0 else "#f85149" if delta_cagr<0 else "#7a8db5"};font-weight:700">{delta_cagr:+.2f}%</span>
            (hace <span style="color:#7a8db5">{snap_age_txt}</span>) ·
            último trigger: <span style="color:#7a8db5">{snap_trigger or "—"}</span>
          </td>
        </tr>
'''

    if concentration_warn:
        matrix_rows += f'''
        <tr style="background:#1c1410">
          <td colspan="6" style="padding:4px 10px 8px;font-size:10px;color:#ffab40;border-top:1px dashed #2a1f10;font-family:'IBM Plex Mono',monospace">
            {concentration_warn}
          </td>
        </tr>'''

    # BTC detailed rows for MC confidence
    btc_detail = ''
    btc_models = [
        ('1H Breakout','1h'),
        ('4H Aggressive','4h'),
        ('15m TMA','15m'),
    ]
    for label, tf in btc_models:
        m = load_model('BTC', tf)
        if not m: continue
        cagr=m['cagr']; wr=m['wr']; dd=m['dd']; t=m['trades']
        # MC confidence
        p_pos = 0
        for k, v in mc.items():
            if isinstance(v, dict):
                raw = v.get('p_cagr_gt0', v.get('P_cagr_gt0', 0))
                if raw > 0 and tf in k.lower():
                    p_pos = raw*100 if raw < 1 else raw; break
        cc2, ct2 = c_conf(p_pos) if p_pos else ('#555','N/D')
        ca_b = f' <span class="badge green">{len(ca_pos)}/4 &#10003;</span>' if tf=='1h' and len(ca_pos)>=3 else ''
        btc_detail += f'''
        <tr>
          <td><strong>{label}</strong>{ca_b}</td>
          <td style="color:{c_cagr(cagr)}">{cagr:+.1f}%</td>
          <td style="color:{c_wr(wr)}">{wr:.1f}%</td>
          <td style="color:{c_dd(dd)}">{dd:.1f}%</td>
          <td>{t}</td>
          <td><span class="badge" style="background:{cc2}20;color:{cc2};border:1px solid {cc2}">{ct2}{f" ({p_pos:.0f}%)" if p_pos else ""}</span></td>
        </tr>'''


    #  Motor 2 matrix (XAU/XAG x 4h/1h/15m/5m) - misma tabla que Motor 1
    m2_tf_models = {tf: [] for tf in TIMEFRAMES_M2}
    matrix_rows_m2 = ''
    for _m2a in ASSETS_M2:
        _color = ASSET_COLOR[_m2a]
        _emoji = ASSET_EMOJI[_m2a]
        _cells = ''.join(cell_html(_m2a, tf) for tf in TIMEFRAMES_M2)
        matrix_rows_m2 += (
            f'<tr><td class="asset-col">'
            f'<div class="asset-box" style="--asset-color:{_color}">'
            f'<span class="asset-emoji" style="color:{_color}">{_emoji}</span>'
            f'<span class="asset-name">{_m2a}</span>'
            f'</div></td>{_cells}</tr>'
        )
        for _tf2 in TIMEFRAMES_M2:
            _m2ml = load_model(_m2a, _tf2, direction='long')
            _m2ms = load_model(_m2a, _tf2, direction='short')
            _m2b  = None
            if _m2ml and _m2ms:
                _m2b = _m2ml if ((_m2ml.get('score') or -9999) >= (_m2ms.get('score') or -9999)) else _m2ms
            elif _m2ml: _m2b = _m2ml
            elif _m2ms: _m2b = _m2ms
            if _m2b:
                m2_tf_models[_tf2].append((
                    float(_m2b.get('cagr', 0) or 0),
                    float(_m2b.get('wr', 0) or 0),
                    int(_m2b.get('trades', 0) or 0),
                    float(_m2b.get('score', -9999) or -9999),
                    float(_m2b.get('dd', 0) or 0),
                    float(_m2b.get('pf', 0) or 0),
                    float(_m2b.get('rr', 0) or 0),
                ))

    # Ponderado row (same style as M1)
    _m2_sum_cells = ''
    _m2_all_c, _m2_all_wr, _m2_all_dd, _m2_all_t = [], [], [], 0
    for _tf2 in TIMEFRAMES_M2:
        _ms2 = m2_tf_models[_tf2]
        if _ms2:
            _c2 = [x[0] for x in _ms2]; _w2 = [x[1] for x in _ms2]
            _t2 = [x[2] for x in _ms2]; _d2 = [x[4] for x in _ms2]
            _tot2 = sum(_t2)
            _wwr2 = sum(_w2[i]*_t2[i] for i in range(len(_ms2))) / max(_tot2, 1)
            _avg2 = sum(_c2) / len(_c2)
            _m2_all_c.extend(_c2); _m2_all_wr.extend(_w2)
            _m2_all_dd.extend(_d2); _m2_all_t += _tot2
            _m2_sum_cells += (
                f'<td style="text-align:center;border-top:2px solid #FFD70033;padding:7px 4px">'
                f'<div style="color:{c_cagr(_avg2)};font-family:\'IBM Plex Mono\',monospace;font-size:12px;font-weight:700">{_avg2:+.1f}%</div>'
                f'<div style="font-size:10px;color:{c_wr(_wwr2)}">WR {_wwr2:.0f}%</div>'
                f'<div style="font-size:10px;color:#7a8db5">{_tot2}T</div>'
                f'</td>'
            )
        else:
            _m2_sum_cells += '<td style="border-top:2px solid #FFD70033;text-align:center;color:#242f55">--</td>'
    matrix_rows_m2 += (
        f'<tr style="background:#07091c">'
        f'<td style="border-top:2px solid #FFD70033;padding:7px 8px">'
        f'<span style="font-size:10px;font-weight:700;color:#FFD700;text-transform:uppercase;letter-spacing:.05em">Ponderado</span>'
        f'</td>{_m2_sum_cells}</tr>'
    )

    # ── Motor 2 comprehensive portfolio stats ───────────────────────────────────
    _m2_all_ms = []
    for _tf2x in TIMEFRAMES_M2:
        _m2_all_ms.extend(m2_tf_models[_tf2x])

    def _m2_calc_port(ms):
        if not ms:
            return dict(cagr=0, wr=0, dd=0, dd_worst=0, pf=0, rr=0, ev_r=0, calmar=0, std=0, n=0)
        n = len(ms)
        weights = [max(m[2] if len(m) > 2 else 1, 1) for m in ms]
        total_w = sum(weights)
        cagr = sum(m[0] * w for m, w in zip(ms, weights)) / total_w if total_w > 0 else 0
        wr   = sum(m[1] * w for m, w in zip(ms, weights)) / total_w if total_w > 0 else 0
        dd_v = [m[4] for m in ms if len(m) > 4]
        dd_w = [max(m[2] if len(m) > 2 else 1, 1) for m in ms if len(m) > 4]
        dd_avg   = sum(d * w for d, w in zip(dd_v, dd_w)) / sum(dd_w) if dd_w else 0
        dd_worst = min(dd_v) if dd_v else 0
        pf_v = [m[5] for m in ms if len(m) > 5 and m[5] > 0]
        pf   = sum(pf_v) / max(len(pf_v), 1)
        rr_v = [m[6] for m in ms if len(m) > 6 and m[6] > 0]
        rr   = sum(rr_v) / len(rr_v) if rr_v else 0
        wr_d = wr / 100 if wr else 0
        ev_r = wr_d * rr - (1 - wr_d) if rr > 0 else 0
        std  = (sum((m[0] - cagr) ** 2 for m in ms) / n) ** 0.5 if n > 1 else 0
        calmar = round(cagr / abs(dd_worst), 2) if dd_worst < 0 else 0
        return dict(cagr=cagr, wr=wr, dd=dd_avg, dd_worst=dd_worst, pf=pf, rr=rr, ev_r=ev_r, calmar=calmar, std=std, n=n)

    _m2op  = _m2_calc_port([m for m in _m2_all_ms if m[0] >= 12.0])
    _m2_ns = len(_m2_all_c)
    _m2_nt = len(ASSETS_M2) * len(TIMEFRAMES_M2)
    _m2pc          = round(_m2op['cagr'], 1)
    _m2wr          = round(_m2op['wr'], 1)
    _m2dd          = round(_m2op['dd'], 1)
    _m2dd_worst    = round(_m2op['dd_worst'], 1)
    _m2pf          = round(_m2op['pf'], 2)
    _m2calmar      = round(_m2op['calmar'], 2)
    _m2rr          = round(_m2op['rr'], 2)
    _m2ev_r        = round(_m2op['ev_r'], 2)
    _m2cagr_std    = round(_m2op['std'], 1)
    _m2_grade_a    = sum(1 for m in _m2_all_ms if len(m) > 3 and m[3] >= 0.55)
    _m2_all_t      = sum(m[2] for m in _m2_all_ms)
    _m2_operable_n = _m2op['n']

    # Macro context (DXY / US10Y) — relevant for metals
    _m2_dxy = _m2_y10 = 0.0
    try:
        import pandas as _mpd
        _m2_csv = OUTPUT_DIR / 'models' / 'data_XAU_1h_max.csv'
        if _m2_csv.exists():
            _m2_df = _mpd.read_csv(str(_m2_csv), index_col=0)
            if 'dxy' in _m2_df.columns:
                _s = _m2_df['dxy'].dropna()
                if len(_s): _m2_dxy = float(_s.iloc[-1])
            if 'yield_10y' in _m2_df.columns:
                _s = _m2_df['yield_10y'].dropna()
                if len(_s): _m2_y10 = float(_s.iloc[-1])
    except Exception: pass
    _m2_dxy_col  = '#f85149' if _m2_dxy > 104 else '#ff9800' if _m2_dxy > 101 else '#2ecc71' if _m2_dxy > 0 else '#7a8db5'
    _m2_y10_col  = '#f85149' if _m2_y10 > 4.5 else '#ff9800' if _m2_y10 > 4.0 else '#2ecc71' if _m2_y10 > 0 else '#7a8db5'
    _m2_dxy_str  = f'DXY <span style="color:{_m2_dxy_col};font-family:monospace;font-weight:700">{_m2_dxy:.1f}</span>' if _m2_dxy else 'DXY <span style="color:#555">--</span>'
    _m2_y10_str  = f'US10Y <span style="color:{_m2_y10_col};font-family:monospace;font-weight:700">{_m2_y10:.2f}%</span>' if _m2_y10 else 'US10Y <span style="color:#555">--</span>'
    _m2_slots_col = '#2ecc71' if _m2_ns == _m2_nt else '#f1c40f' if _m2_ns > 0 else '#555'
    _m2_ncols = 1 + len(TIMEFRAMES_M2)

    if _m2_all_ms:
        matrix_rows_m2 += (
            f'<tr style="background:#060d20">'
            f'<td colspan="{_m2_ncols}" style="padding:8px 10px;border-top:1px solid #141b38">'
            f'<span style="font-size:11px;color:#7a8db5">Portafolio operable (CAGR &ge; 12%): &nbsp;</span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;color:{c_cagr(_m2pc)};font-weight:700;font-size:14px">{_m2pc:+.1f}%</span>'
            f' <span style="font-family:\'IBM Plex Mono\',monospace;color:#7a8db5;font-size:11px">&plusmn;{_m2cagr_std:.1f}%</span>'
            f'<span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; WR: </span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;color:{c_wr(_m2wr)};font-weight:700;font-size:13px">{_m2wr:.1f}%</span>'
            f'<span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; DD avg: </span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;color:{"#f85149" if _m2dd<-20 else "#ff9800" if _m2dd<-10 else "#dde3f5"};font-weight:700;font-size:13px">{_m2dd:.1f}%</span>'
            f'<span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; DD peor: </span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;color:{"#f85149" if _m2dd_worst<-20 else "#ff9800" if _m2dd_worst<-10 else "#dde3f5"};font-weight:700;font-size:13px">{_m2dd_worst:.1f}%</span>'
            f'<span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; PF: </span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;color:{"#00e676" if _m2pf>=2 else "#69f0ae" if _m2pf>=1.5 else "#ff9800"};font-weight:700;font-size:13px">{_m2pf:.2f}</span>'
            f'<span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; Calmar: </span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;color:{"#00e676" if _m2calmar>=2 else "#69f0ae" if _m2calmar>=1 else "#ff9800"};font-weight:700;font-size:13px">{_m2calmar:.2f}</span>'
            f'<span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; RR: </span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;color:{"#00e676" if _m2rr>=1.5 else "#69f0ae" if _m2rr>=1 else "#ff9800"};font-weight:700;font-size:13px">{_m2rr:.2f}</span>'
            f'<span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; EV: </span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;color:{"#00e676" if _m2ev_r>=0.3 else "#69f0ae" if _m2ev_r>=0.1 else "#ff9800" if _m2ev_r>=0 else "#f85149"};font-weight:700;font-size:13px">{_m2ev_r:+.2f}R</span>'
            f'<span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; Trades OOS: </span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;color:#dde3f5;font-size:12px">{_m2_all_t}</span>'
            f'<span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; Activos: </span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;color:#FFD700;font-size:12px">{_m2_ns}/{_m2_nt}</span>'
            f'<span style="font-size:11px;color:#7a8db5"> &nbsp;|&nbsp; Grado A+/A: </span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;color:#69f0ae;font-size:13px;font-weight:700">{_m2_grade_a}</span>'
            f'</td></tr>'
        )
        matrix_rows_m2 += (
            f'<tr style="background:#060d20">'
            f'<td colspan="{_m2_ncols}" style="padding:4px 10px 8px;font-size:10px;color:#4e5f90;border-top:1px dashed #141b38;font-family:\'IBM Plex Mono\',monospace">'
            f'Macro contexto: {_m2_dxy_str} &nbsp;&middot;&nbsp; {_m2_y10_str}'
            f' &nbsp;&middot;&nbsp; <span style="color:#555">Slots activos: <span style="color:{_m2_slots_col};font-weight:700">{_m2_ns}/{_m2_nt}</span> &bull; {_m2_all_t}T OOS</span>'
            f'</td></tr>'
        )

    #  end Motor 2 matrix

    # WFT table (last 15 windows)
    wft_rows = ''
    for w in wft[-15:]:
        col  = '#2ecc71' if w['ok'] else '#e74c3c'
        icon = '&#10003;' if w['ok'] else '&#10007;'
        wft_rows += f'''<tr>
          <td>{w["window"]}</td><td>{w["trades"]}</td>
          <td style="color:{c_wr(w["wr"])}">{w["wr"]:.1f}%</td>
          <td style="color:{c_cagr(w["cagr"])}">{w["cagr"]:+.1f}%</td>
          <td style="color:{col}">{icon}</td></tr>'''

    # DB counts
    tf_counts = ''.join(
        f'<span class="tf-pill">{tf.upper()} <b>{cnt:,}</b></span>'
        for tf, cnt in sorted(db['by_tf'].items())
    )
    top3_rows = ''
    for r in db.get('top3', []):
        cagr_r = r.get('cagr') or 0
        wr_r   = r.get('wr')   or 0
        top3_rows += (
            f'<tr><td>{r["tf"].upper()}</td><td>{r.get("mode","?")[:20]}</td>'
            f'<td style="color:{c_cagr(cagr_r)}">{cagr_r:+.1f}%</td>'
            f'<td style="color:{c_wr(wr_r)}">{wr_r:.1f}%</td>'
            f'<td>{r["score"]:.3f}</td></tr>'
        )

    # Activity feed HTML
    RESULT_CONFIG = {
        'NUEVO_RECORD':        ('rec',  '&#11088;', '#2ecc71'),
        'VALIDADO_ALTA':       ('rec',  '&#10003; ALTA',  '#2ecc71'),
        'VALIDADO_MEDIA':      ('rec',  '&#10003; MEDIA', '#f1c40f'),
        'VALIDADO_BAJA':       ('pos',  '&#10003; BAJA',  '#e67e22'),
        'POSITIVO_NO_MEJOR':   ('pos',  '&#9650;',  '#f1c40f'),
        'OOS_NEG':             ('neg',  '&#10007;', '#e74c3c'),
        'SIN_TRADES':          ('skip', '&#8212;',  '#555'),
        'SIN_EDGE_IS':         ('skip', '&#8592;',  '#555'),
    }
    feed_rows = ''
    for ev in reversed(events):
        result  = ev.get('result', '')
        cfg     = RESULT_CONFIG.get(result, ('skip', '?', '#555'))
        cls, icon, col = cfg
        asset   = ev.get('asset','?')
        tf      = ev.get('tf','?')
        strat   = ev.get('strategy','?')
        note    = ev.get('note','')
        cagr    = ev.get('cagr_oos')
        ts      = ev.get('ts','')
        t_oos   = ev.get('trades_oos')
        a_color = ASSET_COLOR.get(asset, '#dde3f5')
        # Display honesto: si N OOS < 20, marcamos como low-N (solo visual, no afecta logica)
        _lowN = (t_oos is not None and t_oos < 20)
        _lowN_suf = f' <span style="color:#e67e22;font-size:10px" title="muestra chica (N OOS &lt; 20)">[N={t_oos}]</span>' if _lowN else ''
        cagr_html = (f'<span style="color:{c_cagr(cagr)}">{cagr:+.1f}%</span>{_lowN_suf}') if cagr is not None else ''
        feed_rows += (
            f'<div class="feed-item {cls}">'
            f'<span class="feed-ts">{ts}</span>'
            f'<span class="feed-asset" style="color:{a_color}">{asset} {tf}</span>'
            f'<span class="feed-strat">{strat}</span>'
            f'<span style="color:{col};min-width:22px;text-align:center">{icon}</span>'
            f'<span class="feed-note">{note}</span>'
            f'<span class="feed-cagr">{cagr_html}</span>'
            f'</div>'
        )
    if not feed_rows:
        feed_rows = '<div style="color:#555;text-align:center;padding:20px">Pipeline iniciando — los eventos apareceran aqui en minutos</div>'

    # Cross-asset cards (build before f-string)
    ca_cards_html = ''
    for a, r in ca.get('results', {}).items():
        if isinstance(r, dict) and 'cagr' in r:
            cag = r.get('cagr', 0)
            ca_cards_html += (
                f'<div style="background:#1a2240;border-radius:6px;padding:10px 14px;min-width:90px;text-align:center">'
                f'<div style="font-size:10px;color:#7a8db5;margin-bottom:4px">{a}</div>'
                f'<div style="font-size:18px;font-weight:700;color:{c_cagr(cag)}">{cag:+.1f}%</div>'
                f'<div style="font-size:10px;color:#7a8db5">{r.get("trades","-")}T</div></div>'
            )

    # TF counter pills for banner (solo TFs activos en TIMEFRAMES, ignora 1m/2h deprecated)
    TF_COLORS = {'1d':'#e0bb3a','1h':'#c9a227','4h':'#2ecc71','15m':'#f1c40f','5m':'#e67e22'}
    tf_counter_html = ''
    by_tf_active = {tf: db.get('by_tf', {}).get(tf, 0) for tf in TIMEFRAMES}
    for tf in TIMEFRAMES:
        cnt = by_tf_active.get(tf, 0)
        col = TF_COLORS.get(tf, '#dde3f5')
        tf_counter_html += (
            f'<div class="counter-stat">'
            f'<div class="val" style="color:{col}" id="tf-{tf}">{cnt:,}</div>'
            f'<div class="lbl">{tf.upper()}</div>'
            f'</div>'
        )

    top2_pills = ''.join(
        f'<div class="pill" style="border-color:{ASSET_COLOR.get(a,"#c9a227")}">'
        f'<span style="color:{ASSET_COLOR.get(a,"#c9a227")}">{ASSET_EMOJI.get(a,a)} {a}</span>'
        f'<span>{tf.upper()}</span>'
        f'<span style="color:{c_cagr(cagr)}">{cagr:+.1f}%</span></div>'
        for cagr, a, tf, m in top2
    ) or '<span style="color:#7a8db5">Sin modelos OOS positivos aun</span>'

    # Performance snapshot: heatmap + donut + rolling metrics
    try:
        _ts = json.loads((OUTPUT_DIR / "results" / "trade_state.json").read_text())
        _hist = _ts.get("history", [])
        _open = _ts.get("open", {})
    except Exception:
        _hist = []
        _open = {}
    heatmap_html = render_calendar_heatmap(_hist, days=30)
    donut_html = render_donut_exposure(_open)
    rolling = render_rolling_metrics(_hist, days=30)
    if rolling:
        sh_col = "#00e676" if rolling["sharpe"] >= 1 else "#ff9800" if rolling["sharpe"] >= 0 else "#f85149"
        rolling_html = (
            '<div style="display:flex;gap:18px;font-family:IBM Plex Mono,monospace;font-size:11px;flex-wrap:wrap;color:#7a8db5;margin-top:12px;padding-top:12px;border-top:1px solid #1a2240">'
            '<span><span style="color:#4e5f90;text-transform:uppercase;letter-spacing:0.5px;font-size:9px">SHARPE 30D</span> <b style="color:' + sh_col + '">' + ("{:+.2f}".format(rolling["sharpe"])) + '</b></span>'
            '<span><span style="color:#4e5f90;text-transform:uppercase;letter-spacing:0.5px;font-size:9px">STREAK</span> <b style="color:#dde3f5">' + str(rolling["streak"]) + 'd</b></span>'
            '<span><span style="color:#4e5f90;text-transform:uppercase;letter-spacing:0.5px;font-size:9px">DIAS GANADORES</span> <b style="color:#00e676">' + str(rolling["wins"]) + '</b><span style="color:#444">/</span><b style="color:#f85149">' + str(rolling["losses"]) + '</b></span>'
            '<span><span style="color:#4e5f90;text-transform:uppercase;letter-spacing:0.5px;font-size:9px">MEJOR DIA</span> <b style="color:#00e676">' + ("{:+.2f}".format(rolling["best"])) + '%</b></span>'
            '<span><span style="color:#4e5f90;text-transform:uppercase;letter-spacing:0.5px;font-size:9px">PEOR DIA</span> <b style="color:#f85149">' + ("{:+.2f}".format(rolling["worst"])) + '%</b></span>'
            '</div>'
        )
    else:
        rolling_html = '<div style="color:#4e5f90;font-size:11px;margin-top:8px">Esperando trades para metricas rolling</div>'
    performance_snapshot_html = (
        '<div class="card-purple" style="background:linear-gradient(180deg,#0d1428,#0a1020);border:1px solid #1a2240;border-radius:12px;padding:16px;margin-bottom:18px;box-shadow:0 1px 0 rgba(255,255,255,0.03) inset,0 6px 20px -12px rgba(0,0,0,0.7)">'
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid #1a2240">'
        '<div style="font-size:11px;font-weight:700;color:#7a8db5;text-transform:uppercase;letter-spacing:0.12em;display:flex;align-items:center;gap:8px">'
        '<span style="display:inline-block;width:3px;height:14px;background:linear-gradient(180deg,#c9a227,#f0d060);border-radius:2px"></span>'
        'Performance Snapshot'
        '</div>'
        '<span style="font-size:10px;color:#4e5f90">ultimos 30 dias</span>'
        '</div>'
        '<div class="perf-snap-cols" style="display:flex;gap:24px;align-items:center;flex-wrap:wrap">'
        '<div style="flex:1;min-width:280px">'
        '<div style="font-size:9px;color:#4e5f90;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">P&amp;L Diario (heatmap)</div>'
        '<div class="heatmap-wrap">' + heatmap_html + '</div>'
        '</div>'
        '<div>'
        '<div style="font-size:9px;color:#4e5f90;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Exposicion abierta</div>'
        + donut_html +
        '</div>'
        '</div>'
        + rolling_html +
        '</div>'
    )

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<!-- Sin meta refresh — todo actualiza via JavaScript sin parpadeo -->
<title>SIGMA ENGINE</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>

@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap');

*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --gold:#c9a227;--gold-b:#e8c040;--gold-d:#8a6b15;
  --n0:#020510;--n1:#060c1c;--n2:#080f22;--n3:#0c1330;
  --bd-gold:rgba(201,162,39,.25);--bd-mid:#182040;--bd-dim:#0d1528;
  --tx:#e2e8f8;--ts:#7a8db5;--td:#3a4d78;
}}
html{{scroll-behavior:smooth}}
body{{
  background:
    radial-gradient(ellipse 75% 40% at 18% -8%,#0b1e55 0%,transparent 52%),
    radial-gradient(ellipse 55% 35% at 88% 108%,#071540 0%,transparent 52%),
    #020510;
  background-attachment:fixed;
  color:var(--tx);
  font-family:'IBM Plex Sans',-apple-system,sans-serif;
  font-size:13px;
  -webkit-font-smoothing:antialiased;
  min-height:100vh;
}}
body::after{{
  content:'';position:fixed;inset:0;
  background:repeating-linear-gradient(0deg,rgba(0,0,12,.04) 0,rgba(0,0,12,.04) 1px,transparent 1px,transparent 4px);
  pointer-events:none;z-index:9998;
}}
.mono{{font-family:'IBM Plex Mono',monospace;font-feature-settings:"tnum"}}
.container{{max-width:1440px;margin:0 auto;padding:28px 24px 40px}}

.hdr{{
  display:flex;justify-content:space-between;align-items:flex-start;
  margin-bottom:28px;padding-bottom:18px;
  border-bottom:1px solid var(--bd-gold);
  position:relative;
}}
.hdr::after{{
  content:'';position:absolute;bottom:-1px;left:0;width:100px;height:2px;
  background:linear-gradient(90deg,var(--gold),transparent);
}}
.hdr h1{{
  font-size:20px;font-weight:700;
  font-family:'IBM Plex Mono',monospace;
  letter-spacing:4px;text-transform:uppercase;
  color:var(--gold);
  text-shadow:0 0 40px rgba(201,162,39,.35);
}}
.hdr .meta{{
  font-family:'IBM Plex Mono',monospace;
  font-size:10px;color:var(--ts);
  text-align:right;line-height:2;letter-spacing:.05em;
}}

.card{{
  background:linear-gradient(150deg,#08112a 0%,#050d1e 100%);
  border:1px solid var(--bd-gold);
  border-top:2px solid var(--gold);
  border-radius:3px;
  padding:20px 22px;margin-bottom:16px;
  box-shadow:
    0 0 0 1px rgba(0,0,0,.55) inset,
    0 20px 48px -20px rgba(0,0,0,.85),
    0 1px 0 rgba(201,162,39,.07) inset;
  transition:border-color .25s,transform .25s,box-shadow .25s;
}}
.card:hover{{
  border-color:rgba(201,162,39,.5);
  transform:translateY(-2px);
  box-shadow:
    0 0 0 1px rgba(0,0,0,.55) inset,
    0 32px 56px -20px rgba(0,0,0,.85),
    0 0 48px -20px rgba(201,162,39,.1),
    0 1px 0 rgba(201,162,39,.1) inset;
}}
.card-title{{
  font-size:9px;font-weight:600;
  font-family:'IBM Plex Mono',monospace;
  color:var(--gold);
  text-transform:uppercase;letter-spacing:5px;
  margin-bottom:18px;padding-bottom:12px;
  border-bottom:1px solid rgba(201,162,39,.12);
  display:flex;align-items:center;gap:10px;
}}
.card-title::before{{
  content:'';width:2px;height:12px;
  background:var(--gold);
  box-shadow:0 0 10px rgba(201,162,39,.7);
  flex-shrink:0;
}}

.rule{{
  background:linear-gradient(135deg,#07102a 0%,#050d1e 100%);
  border:1px solid rgba(201,162,39,.3);
  border-radius:3px;padding:18px 22px;margin-bottom:16px;
  display:flex;flex-wrap:wrap;gap:16px;align-items:center;
  box-shadow:0 0 40px -20px rgba(201,162,39,.18),0 8px 24px -16px rgba(0,0,0,.7);
}}
.rule-title{{font-weight:700;color:var(--gold);font-size:13px;letter-spacing:.05em;font-family:'IBM Plex Mono',monospace}}
.rule-sub{{color:var(--ts);font-size:11px;margin-top:3px}}
.pills{{display:flex;gap:6px;flex-wrap:wrap}}
.pill{{
  display:inline-flex;gap:6px;align-items:center;
  background:rgba(201,162,39,.06);
  border:1px solid rgba(201,162,39,.18);
  border-radius:2px;padding:4px 12px;
  font-size:11px;font-weight:500;
  font-family:'IBM Plex Mono',monospace;letter-spacing:.03em;
  transition:all .15s;
}}
.pill:hover{{background:rgba(201,162,39,.12);border-color:rgba(201,162,39,.4);transform:translateY(-1px)}}
.prog{{font-size:11px;color:var(--ts);margin-left:auto;white-space:nowrap}}
.prog strong{{color:var(--tx)}}

.matrix-wrap{{overflow-x:auto;-webkit-overflow-scrolling:touch;display:flex;justify-content:center;padding:4px}}
.matrix{{width:100%;max-width:820px;min-width:660px;border-collapse:separate;border-spacing:8px 5px;table-layout:fixed;margin:0 auto}}
.matrix th{{
  padding:8px 12px 14px;
  font-size:9px;font-weight:600;
  font-family:'IBM Plex Mono',monospace;
  color:var(--td);text-align:center;width:22%;
  letter-spacing:4px;text-transform:uppercase;
  border-bottom:1px solid rgba(201,162,39,.12);
}}
.matrix th.th-asset{{text-align:left;width:12%;padding-left:8px}}
.matrix td{{padding:10px 12px;text-align:center;vertical-align:middle;height:66px;transition:transform .15s,box-shadow .15s}}
.matrix td:not(.asset-col):hover{{transform:translateY(-2px)}}
.asset-col{{text-align:left!important;padding:6px 4px 6px 0!important}}
.asset-box{{
  display:flex;align-items:center;gap:8px;padding:10px 12px;
  background:linear-gradient(135deg,color-mix(in srgb,var(--asset-color) 12%,#08112a) 0%,color-mix(in srgb,var(--asset-color) 5%,#050d1e) 100%);
  border:1px solid color-mix(in srgb,var(--asset-color) 28%,rgba(201,162,39,.08));
  border-radius:2px;
  box-shadow:0 1px 0 color-mix(in srgb,var(--asset-color) 10%,transparent) inset,0 4px 12px -8px color-mix(in srgb,var(--asset-color) 35%,transparent);
  transition:transform .15s,border-color .15s;
  position:relative;overflow:hidden;
}}
.asset-box::before{{
  content:'';position:absolute;left:0;top:0;bottom:0;width:2px;
  background:var(--asset-color);box-shadow:0 0 6px var(--asset-color);
}}
.asset-box:hover{{transform:translateY(-2px);border-color:color-mix(in srgb,var(--asset-color) 55%,transparent)}}
.asset-emoji{{font-size:18px;display:inline-block;vertical-align:middle}}
.asset-name{{font-weight:700;font-size:13px;letter-spacing:.05em;color:var(--tx)}}

.cell-ok{{background:linear-gradient(180deg,#091e14 0%,#061410 100%);border:1px solid rgba(0,230,118,.16);border-radius:2px;box-shadow:0 1px 0 rgba(0,230,118,.06) inset}}
.cell-ok:hover{{border-color:rgba(0,230,118,.4);box-shadow:0 0 16px -4px rgba(0,230,118,.18)}}
.cell-run{{background:linear-gradient(180deg,#08132a 0%,#060d20 100%);border:1px solid rgba(201,162,39,.3);border-radius:2px;color:var(--gold)}}
.cell-neg{{background:linear-gradient(180deg,#180a0a 0%,#110707 100%);border:1px solid rgba(231,76,60,.12);border-radius:2px;color:rgba(231,76,60,.38)}}
.cell-pending{{color:rgba(24,32,64,.8);border:1px dashed rgba(24,32,64,.5);border-radius:2px}}
.cell-na{{color:var(--bd-mid);font-size:18px}}
.cell-cagr{{font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:600;line-height:1.25;letter-spacing:-.01em;font-feature-settings:"tnum"}}
.cell-sub{{font-size:10px;color:var(--ts);margin-top:2px;font-family:'IBM Plex Mono',monospace;letter-spacing:.02em}}

@keyframes spin{{to{{transform:rotate(360deg)}}}}
@keyframes pulse{{0%,100%{{opacity:1;box-shadow:0 0 0 0 rgba(0,230,118,.35)}}50%{{opacity:.7;box-shadow:0 0 0 4px rgba(0,230,118,0)}}}}
@keyframes flashIn{{from{{opacity:0;transform:translateY(-10px)}}to{{opacity:1;transform:translateY(0)}}}}
@keyframes pulse-dot{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
.spin{{display:inline-block;animation:spin 1.5s linear infinite;font-size:14px}}

table.t{{width:100%;border-collapse:collapse}}
table.t th{{
  padding:7px 10px;font-size:9px;font-weight:600;
  font-family:'IBM Plex Mono',monospace;color:var(--td);
  border-bottom:1px solid var(--bd-gold);
  text-align:left;letter-spacing:3px;text-transform:uppercase;
}}
table.t td{{
  padding:8px 10px;
  font-family:'IBM Plex Mono',monospace;font-size:11px;
  border-bottom:1px solid var(--bd-dim);font-feature-settings:"tnum";
}}
table.t tr:last-child td{{border:none}}
table.t tr:hover td{{background:rgba(201,162,39,.025)}}

.badge{{display:inline-block;padding:2px 7px;border-radius:2px;font-size:9px;font-weight:600;letter-spacing:2px;text-transform:uppercase;font-family:'IBM Plex Mono',monospace}}
.badge.green{{background:rgba(0,230,118,.08);color:#00e676;border:1px solid rgba(0,230,118,.28)}}

.wft-stats{{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:12px}}
.wft-num{{text-align:center}}
.wft-num .n{{font-size:22px;font-weight:700;font-family:'IBM Plex Mono',monospace;font-feature-settings:"tnum"}}
.wft-num .l{{font-size:10px;color:var(--ts);letter-spacing:2px;text-transform:uppercase;font-family:'IBM Plex Mono',monospace}}
.progress{{background:var(--bd-dim);border-radius:2px;height:3px;margin-bottom:12px;overflow:hidden}}
.progress-fill{{height:100%;background:linear-gradient(90deg,var(--gold),#2ecc71);border-radius:2px}}
.wft-scroll{{max-height:200px;overflow-y:auto}}

.tf-pills{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}}
.tf-pill{{background:rgba(201,162,39,.05);border:1px solid rgba(201,162,39,.16);border-radius:2px;padding:3px 10px;font-size:10px;font-family:'IBM Plex Mono',monospace;letter-spacing:2px;text-transform:uppercase}}
.phase{{display:inline-block;padding:2px 8px;border-radius:2px;font-size:10px;font-weight:600;letter-spacing:2px;text-transform:uppercase;font-family:'IBM Plex Mono',monospace}}

.kpi-strip{{
  display:grid;grid-template-columns:repeat(6,1fr);
  gap:0;margin-bottom:20px;
  background:linear-gradient(180deg,#060f26 0%,#040a18 100%);
  border:1px solid rgba(201,162,39,.22);
  border-top:2px solid var(--gold);
  border-radius:3px;
  box-shadow:
    0 0 0 1px rgba(0,0,0,.55) inset,
    0 12px 32px -16px rgba(0,0,0,.9),
    0 0 60px -30px rgba(201,162,39,.12);
  position:relative;overflow:hidden;
}}
.kpi-card{{
  display:flex;flex-direction:column;padding:18px 20px;
  border-right:1px solid rgba(201,162,39,.08);
  min-width:0;position:relative;
}}
.kpi-card:last-child{{border-right:none}}
.kpi-label{{
  font-size:8px;font-weight:600;
  font-family:'IBM Plex Mono',monospace;color:var(--td);
  text-transform:uppercase;letter-spacing:3px;margin-bottom:8px;
}}
.kpi-value{{
  font-family:'IBM Plex Mono',monospace;
  font-size:28px;font-weight:700;
  letter-spacing:-.04em;margin-bottom:4px;line-height:1;
  font-feature-settings:"tnum";
}}
.kpi-sub{{
  font-family:'IBM Plex Mono',monospace;
  font-size:10px;color:var(--ts);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;letter-spacing:.03em;
}}
.kpi-pos{{color:var(--gold);text-shadow:0 0 28px rgba(201,162,39,.55)}}
.kpi-neg{{color:#f44336;text-shadow:0 0 20px rgba(244,67,54,.3)}}
.kpi-warn{{color:#f59e0b}}
.kpi-neutral{{color:var(--tx)}}
.kpi-pill{{
  display:inline-block;padding:1px 5px;border-radius:2px;
  font-size:8px;font-weight:600;
  background:rgba(201,162,39,.08);border:1px solid rgba(201,162,39,.18);
  margin-left:4px;color:var(--gold);letter-spacing:1px;text-transform:uppercase;
  font-family:'IBM Plex Mono',monospace;
}}

@media(max-width:900px){{
  .kpi-strip{{grid-template-columns:repeat(3,1fr)}}
  .kpi-card{{border-right:none;border-bottom:1px solid rgba(201,162,39,.06);padding-bottom:14px}}
}}

.risk-panel{{
  background:linear-gradient(150deg,#08112a 0%,#050d1e 100%);
  border:1px solid var(--bd-gold);border-top:2px solid #f59e0b;
  border-radius:3px;padding:18px 22px;margin-bottom:16px;
  box-shadow:0 0 0 1px rgba(0,0,0,.55) inset,0 24px 48px -20px rgba(0,0,0,.8);
}}
.risk-header{{
  display:flex;justify-content:space-between;align-items:center;
  margin-bottom:14px;padding-bottom:12px;
  border-bottom:1px solid rgba(201,162,39,.1);
}}
.risk-title{{
  font-size:9px;font-weight:600;
  font-family:'IBM Plex Mono',monospace;color:var(--gold);
  text-transform:uppercase;letter-spacing:5px;
  display:flex;align-items:center;gap:10px;
}}
.risk-title::before{{content:'';width:2px;height:12px;background:#f59e0b;box-shadow:0 0 8px rgba(245,158,11,.6);flex-shrink:0}}
.risk-grid{{display:grid;grid-template-columns:repeat(7,1fr);gap:0}}
.risk-cell{{text-align:center;padding:8px 12px;border-right:1px solid rgba(201,162,39,.07)}}
.risk-cell:last-child{{border-right:none}}
.risk-cell-label{{
  font-size:8px;font-weight:600;
  font-family:'IBM Plex Mono',monospace;color:var(--td);
  text-transform:uppercase;letter-spacing:3px;margin-bottom:8px;
}}
.risk-cell-val{{
  font-family:'IBM Plex Mono',monospace;font-size:22px;font-weight:700;
  letter-spacing:-.02em;line-height:1.1;margin-bottom:2px;font-feature-settings:"tnum";
}}
.risk-cell-ctx{{font-size:9px;color:var(--td);font-family:'IBM Plex Mono',monospace;letter-spacing:1px;text-transform:uppercase}}
@media(max-width:900px){{
  .risk-grid{{grid-template-columns:repeat(4,1fr)}}
  .risk-cell{{border-right:none;border-bottom:1px solid rgba(201,162,39,.06);padding:10px}}
}}

.section-divider{{display:flex;align-items:center;gap:16px;margin:28px 0 16px}}
.section-divider-line{{flex:1;height:1px;background:linear-gradient(90deg,var(--bd-gold),transparent)}}
.section-divider-text{{
  font-size:8px;font-weight:600;
  font-family:'IBM Plex Mono',monospace;color:var(--gold);
  text-transform:uppercase;letter-spacing:5px;white-space:nowrap;
}}

.footer-pro{{
  margin-top:36px;padding:20px 0 28px;
  border-top:1px solid var(--bd-gold);
  display:flex;justify-content:space-between;align-items:flex-start;gap:24px;flex-wrap:wrap;
}}
.footer-col{{flex:1;min-width:200px}}
.footer-title{{
  font-size:8px;font-weight:600;
  font-family:'IBM Plex Mono',monospace;color:var(--td);
  text-transform:uppercase;letter-spacing:4px;margin-bottom:8px;
}}
.footer-text{{font-size:11px;color:var(--td);line-height:1.8}}
.footer-pill{{
  display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:2px;
  background:rgba(0,230,118,.06);border:1px solid rgba(0,230,118,.2);
  font-size:10px;font-weight:600;color:#00e676;
  font-family:'IBM Plex Mono',monospace;letter-spacing:2px;text-transform:uppercase;
}}
.footer-pill::before{{
  content:'';width:5px;height:5px;border-radius:50%;
  background:#00e676;box-shadow:0 0 6px #00e676;animation:pulse-dot 2s ease-in-out infinite;
}}

.regime-grid{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}}
.regime-card{{
  background:linear-gradient(150deg,#08112a 0%,#050d1e 100%);
  border:1px solid var(--bd-gold);border-radius:2px;
  padding:14px 16px;min-width:130px;flex:1;text-align:center;
  transition:transform .15s,border-color .15s;
  box-shadow:0 4px 16px -8px rgba(0,0,0,.7);
}}
.regime-card:hover{{transform:translateY(-2px);border-color:rgba(201,162,39,.35)}}
.regime-asset{{font-weight:700;font-size:11px;margin-bottom:8px;letter-spacing:4px;text-transform:uppercase;font-family:'IBM Plex Mono',monospace;color:var(--ts)}}
.regime-badge{{
  display:inline-block;padding:4px 14px;border-radius:2px;
  font-size:10px;font-weight:700;margin-bottom:6px;letter-spacing:4px;
  text-transform:uppercase;font-family:'IBM Plex Mono',monospace;
}}
.regime-bull{{background:rgba(0,230,118,.08);color:#00e676;border:1px solid rgba(0,230,118,.38)}}
.regime-range{{background:rgba(245,158,11,.08);color:#f59e0b;border:1px solid rgba(245,158,11,.38)}}
.regime-bear{{background:rgba(244,67,54,.08);color:#f44336;border:1px solid rgba(244,67,54,.38)}}
.regime-unk{{background:rgba(100,100,100,.08);color:#666;border:1px solid rgba(100,100,100,.18)}}
.regime-rsi{{font-size:10px;color:var(--ts);font-family:'IBM Plex Mono',monospace;letter-spacing:2px}}

.feed{{
  background:linear-gradient(150deg,#08112a 0%,#050d1e 100%);
  border:1px solid var(--bd-gold);border-radius:3px;
  padding:18px 20px;margin-bottom:16px;
  box-shadow:0 0 0 1px rgba(0,0,0,.55) inset,0 24px 48px -20px rgba(0,0,0,.8);
}}
.feed-title{{
  font-size:9px;font-weight:600;
  font-family:'IBM Plex Mono',monospace;color:var(--gold);
  text-transform:uppercase;letter-spacing:5px;
  margin-bottom:14px;display:flex;justify-content:space-between;align-items:center;
}}
.feed-list{{max-height:280px;overflow-y:auto;display:flex;flex-direction:column;gap:3px}}
.feed-list::-webkit-scrollbar{{width:4px}}
.feed-list::-webkit-scrollbar-track{{background:var(--n0);border-radius:2px}}
.feed-list::-webkit-scrollbar-thumb{{background:rgba(201,162,39,.18);border-radius:2px}}
.feed-list::-webkit-scrollbar-thumb:hover{{background:rgba(201,162,39,.38)}}
.feed-item{{
  display:flex;align-items:center;gap:10px;padding:7px 10px;border-radius:2px;
  font-size:11px;background:rgba(255,255,255,.012);
  border-left:2px solid rgba(201,162,39,.12);
  transition:transform .1s,background .1s;
  font-family:'IBM Plex Mono',monospace;
}}
.feed-item:hover{{transform:translateX(2px);background:rgba(201,162,39,.035)}}
.feed-item.rec{{border-left-color:rgba(0,230,118,.55);background:rgba(0,230,118,.03)}}
.feed-item.neg{{border-left-color:rgba(244,67,54,.55);background:rgba(244,67,54,.03)}}
.feed-item.pos{{border-left-color:rgba(245,158,11,.55);background:rgba(245,158,11,.03)}}
.feed-item.skip{{border-left-color:rgba(100,100,100,.25);opacity:.6}}
.feed-ts{{color:var(--td);font-size:9px;min-width:38px;letter-spacing:.05em}}
.feed-asset{{font-weight:600;min-width:52px;letter-spacing:.05em}}
.feed-strat{{color:var(--ts);min-width:80px;letter-spacing:.02em}}
.feed-note{{color:var(--ts);flex:1;font-size:10px;letter-spacing:.02em}}
.feed-cagr{{font-weight:700;min-width:55px;text-align:right;font-feature-settings:"tnum"}}

.counter-banner{{
  background:linear-gradient(135deg,#060f26 0%,#050d1e 60%,#060f26 100%);
  border:1px solid rgba(201,162,39,.28);border-top:2px solid var(--gold);
  border-radius:3px;padding:24px 32px;margin-bottom:20px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:20px;
  position:relative;overflow:hidden;
  box-shadow:
    0 0 0 1px rgba(0,0,0,.55) inset,
    0 16px 48px -16px rgba(0,0,0,.9),
    0 0 80px -40px rgba(201,162,39,.18);
}}
.counter-banner::before{{
  content:'';position:absolute;inset:0;
  background:
    radial-gradient(ellipse at 15% 30%,rgba(201,162,39,.06) 0%,transparent 50%),
    radial-gradient(ellipse at 85% 70%,rgba(0,230,118,.04) 0%,transparent 50%);
  pointer-events:none;
}}
.counter-main{{display:flex;align-items:baseline;gap:14px;position:relative}}
.counter-number{{
  font-family:'IBM Plex Mono',monospace;font-size:62px;font-weight:700;
  color:var(--gold);
  text-shadow:0 0 40px rgba(201,162,39,.5),0 0 80px rgba(201,162,39,.2);
  line-height:1;letter-spacing:-4px;font-feature-settings:"tnum";
}}
.counter-label{{font-size:12px;color:var(--ts);line-height:1.5;letter-spacing:.03em;font-family:'IBM Plex Mono',monospace}}
.counter-label strong{{color:var(--tx);display:block;font-size:13px;letter-spacing:.08em;text-transform:uppercase}}
.counter-stats{{display:flex;gap:24px;flex-wrap:wrap;position:relative}}
.counter-stat{{text-align:center;padding:0 4px}}
.counter-stat .val{{font-family:'IBM Plex Mono',monospace;font-size:22px;font-weight:700;color:var(--tx);letter-spacing:-.02em;font-feature-settings:"tnum"}}
.counter-stat .lbl{{font-size:8px;color:var(--ts);margin-top:4px;letter-spacing:3px;font-weight:600;text-transform:uppercase;font-family:'IBM Plex Mono',monospace}}
.counter-rate{{
  font-family:'IBM Plex Mono',monospace;font-size:11px;color:#00e676;font-weight:600;
  background:rgba(0,230,118,.05);border:1px solid rgba(0,230,118,.18);
  border-radius:2px;padding:5px 14px;letter-spacing:2px;text-transform:uppercase;
}}

.footer{{
  text-align:center;color:var(--td);font-size:10px;padding:20px 0;
  border-top:1px solid var(--bd-gold);margin-top:16px;
  font-family:'IBM Plex Mono',monospace;letter-spacing:2px;text-transform:uppercase;
}}

::-webkit-scrollbar{{width:6px;height:6px}}
::-webkit-scrollbar-track{{background:var(--n0)}}
::-webkit-scrollbar-thumb{{background:rgba(201,162,39,.18);border-radius:2px}}
::-webkit-scrollbar-thumb:hover{{background:rgba(201,162,39,.38)}}

@media(max-width:600px){{
  .container{{padding:12px 10px 20px}}
  .hdr{{flex-direction:column;gap:6px}}
  .hdr .meta{{text-align:left}}
  .matrix{{border-spacing:3px 2px}}
  .matrix th,.matrix td{{padding:5px 3px}}
  .matrix th{{font-size:8px;letter-spacing:2px}}
  .asset-name{{font-size:11px}}
  .counter-banner{{padding:16px 18px;gap:12px}}
  .counter-number{{font-size:40px;letter-spacing:-2px}}
  .counter-stats{{gap:14px}}
  .section-divider{{margin:16px 0 12px}}
  .feed-item{{gap:5px}}
  .feed-ts{{min-width:28px}}
  .feed-strat{{min-width:46px}}
  .feed-note{{font-size:9px}}
  .card{{padding:14px 16px}}
  .risk-panel{{padding:12px 16px}}
}}

@media(max-width:480px){{
  .kpi-strip{{grid-template-columns:repeat(2,1fr);border-radius:2px}}
  .kpi-value{{font-size:22px}}
  .kpi-card{{border-right:none;border-bottom:1px solid rgba(201,162,39,.06);padding:12px 14px}}
  .kpi-card:last-child{{border-bottom:none}}
  .kpi-label{{font-size:7px;letter-spacing:2px}}
  .kpi-sub{{font-size:9px}}
  .risk-grid{{grid-template-columns:repeat(2,1fr)}}
  .risk-cell{{border-right:none;border-bottom:1px solid rgba(201,162,39,.06);padding:10px 8px}}
  .risk-cell-val{{font-size:16px}}
  .counter-number{{font-size:34px;letter-spacing:-1px}}
  .counter-label strong{{font-size:12px}}
  .counter-banner{{flex-direction:column;align-items:flex-start;padding:14px 16px;gap:10px}}
  .counter-rate{{font-size:10px;padding:4px 10px}}
  .counter-stat .val{{font-size:18px}}
  .regime-grid{{gap:6px}}
  .regime-card{{min-width:calc(50% - 3px);padding:10px 12px;flex:none}}
  .hdr h1{{font-size:16px;letter-spacing:2px}}
  .rule{{padding:12px 14px}}
  .pills{{gap:5px}}
  .pill{{padding:3px 8px;font-size:10px}}
  .heatmap-wrap{{overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:4px}}
  .heatmap-wrap>div{{white-space:nowrap;min-width:max-content}}
  .perf-snap-cols{{flex-direction:column!important;gap:12px}}
  .container{{padding:10px 8px 16px}}
  .risk-cell-label{{font-size:7px;letter-spacing:2px}}
  .footer-pro{{flex-direction:column;gap:12px}}
  .footer-col{{min-width:unset}}
}}

.card-purple{{border-top:2px solid var(--gold-b)!important}}
.card-purple:hover{{border-color:rgba(232,192,64,.48)!important;box-shadow:0 0 48px -20px rgba(232,192,64,.12),0 0 0 1px rgba(0,0,0,.55) inset!important}}
.card-green{{border-top:2px solid rgba(0,230,118,.75)!important;transition:border-color .25s,transform .25s,box-shadow .25s}}
.card-green:hover{{border-top-color:rgba(0,230,118,.55)!important;box-shadow:0 0 48px -20px rgba(0,230,118,.1),0 0 0 1px rgba(0,0,0,.55) inset!important;transform:translateY(-2px)}}
.regime-card{{border-top:2px solid transparent}}
.regime-card:has(.regime-bull){{border-top-color:rgba(0,230,118,.48)}}
.regime-card:has(.regime-bear){{border-top-color:rgba(244,67,54,.48)}}
.regime-card:has(.regime-range){{border-top-color:rgba(245,158,11,.48)}}

.rf-tip{{position:relative;display:inline-block}}
.rf-tip:hover::after{{
  content:attr(data-tip);white-space:pre-line;
  position:absolute;bottom:130%;left:50%;transform:translateX(-50%);
  background:#060c1c;color:var(--tx);
  border:1px solid var(--bd-gold);
  padding:10px 14px;border-radius:3px;
  font-size:11px;font-weight:400;line-height:1.6;
  width:320px;max-width:90vw;z-index:9999;
  box-shadow:0 8px 32px rgba(0,0,0,.7);
  pointer-events:none;text-align:left;
  font-family:'IBM Plex Mono',monospace;
}}
.rf-tip:hover::before{{
  content:'';position:absolute;bottom:120%;left:50%;transform:translateX(-50%);
  border:5px solid transparent;border-top-color:var(--bd-gold);z-index:10000;
}}

</style>
</head>
<body>
<div id="cache-stale-banner" style="display:none;position:fixed;top:0;left:0;right:0;z-index:9999;background:linear-gradient(90deg,#ff9800,#f44336);color:#fff;padding:8px 16px;text-align:center;font-weight:600;font-size:13px;box-shadow:0 2px 8px rgba(0,0,0,0.3)">
  ⚠️ <span id="cache-stale-msg">Cache de señales desactualizado</span>
  <span style="opacity:0.7;font-size:11px;margin-left:8px">(el sistema reintentará automaticamente)</span>
</div>

<div class="container">

<!-- HEADER INSTITUCIONAL -->
<div class="hdr" style="border-bottom:1px solid #1a2240;padding-bottom:14px;margin-bottom:18px">
  <div>
    <div style="display:flex;align-items:baseline;gap:14px">
      <h1 style="margin:0">&#963; SIGMA</h1>
      <span style="color:#4e5f90;font-size:11px;letter-spacing:.15em;text-transform:uppercase;font-weight:600">Quantitative Multi-Asset Strategy</span>
    </div>
    <div style="color:#4e5f90;font-size:10px;margin-top:6px;letter-spacing:.05em">
      <span style="color:#7a8db5">STRATEGY:</span> Long/Short Crypto Futures
      <span style="color:#444">  |  </span>
      <span style="color:#7a8db5">UNIVERSE:</span> BTC · ETH · SOL · LTC · BNB
      <span style="color:#444">  |  </span>
      <span style="color:#7a8db5">TF:</span> 15m · 1H · 4H
      <span style="color:#444">  |  </span>
      <span style="color:#7a8db5">VENUE:</span> Binance Futures
    </div>
  </div>
  <div class="meta" style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
      <button id="bell-btn" onclick="toggleBell()" style="position:relative;background:#0d1428;border:1px solid #242f55;color:#b8c5e0;padding:6px 12px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600">
        🔔 <span id="bell-badge" style="display:none;position:absolute;top:-4px;right:-4px;background:#f85149;color:#fff;border-radius:10px;padding:1px 6px;font-size:9px;font-weight:700;min-width:16px;text-align:center">0</span>
      </button>
      <a href="/models" style="background:#0d1428;border:1px solid #242f55;color:#e0bb3a;padding:6px 12px;border-radius:6px;text-decoration:none;font-size:11px;font-weight:600;letter-spacing:.05em">Per-Model Paper &rarr;</a>
    </div>
    <!-- Bell panel -->
    <div id="bell-panel" style="display:none;position:absolute;top:90px;right:24px;width:380px;max-height:480px;overflow-y:auto;background:#07091c;border:1px solid #242f55;border-radius:10px;padding:12px;box-shadow:0 12px 36px rgba(0,0,0,.6);z-index:1000">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid #1a2240">
        <span style="color:#b8c5e0;font-weight:700;font-size:12px;letter-spacing:.05em;text-transform:uppercase">Eventos recientes</span>
        <button onclick="markAllRead()" style="background:none;border:none;color:#c9a227;cursor:pointer;font-size:10px;font-weight:600;font-family:inherit">Marcar leidos</button>
      </div>
      <div id="bell-list" style="display:flex;flex-direction:column;gap:6px"></div>
    </div>
    <!-- Toast container -->
    <div id="toast-container" style="position:fixed;bottom:24px;right:24px;display:flex;flex-direction:column;gap:8px;z-index:9999;pointer-events:none"></div>
    <div style="color:#4e5f90;font-size:10px;letter-spacing:.1em;text-transform:uppercase">As of</div>
    <div style="color:#b8c5e0;font-size:11px;font-family:'IBM Plex Mono',monospace">{now}</div>
    <span class="phase" style="background:{phase_col}18;color:{phase_col};border:1px solid {phase_col}">{phase_txt}</span>
  </div>
</div>

<!-- ────────────────────────────────────────────────────────────────────── -->
<!-- COMMAND CENTER KPI STRIP                                                -->
<!-- ────────────────────────────────────────────────────────────────────── -->
<div class="kpi-strip" id="kpi-strip">
  <div class="kpi-card">
    <div class="kpi-label">Equity Total</div>
    <div class="kpi-value kpi-neutral" id="kpi-equity">$10,000</div>
    <div class="kpi-sub" id="kpi-equity-sub">cargando…</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Capital Realizado</div>
    <div class="kpi-value kpi-neutral" id="kpi-realized">+0.00%</div>
    <div class="kpi-sub" id="kpi-realized-sub">— trades</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">P&amp;L Flotante</div>
    <div class="kpi-value kpi-neutral" id="kpi-floating">+0.00%</div>
    <div class="kpi-sub" id="kpi-floating-sub">— abiertos</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Win Rate</div>
    <div class="kpi-value kpi-neutral" id="kpi-winrate">—%</div>
    <div class="kpi-sub" id="kpi-winrate-sub">— W / — L</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Señales Activas</div>
    <div class="kpi-value kpi-neutral" id="kpi-signals">—</div>
    <div class="kpi-sub" id="kpi-signals-sub">de — modelos</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Régimen BTC</div>
    <div class="kpi-value kpi-neutral" id="kpi-regime">—</div>
    <div class="kpi-sub" id="kpi-regime-sub">cargando…</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Apalancamiento</div>
    <div class="kpi-value kpi-neutral" id="kpi-leverage">—</div>
    <div class="kpi-sub" id="kpi-leverage-sub">exposición actual</div>
  </div>
</div>

<!-- ────────────────────────────────────────────────────────────────────── -->
<!-- RISK METRICS PANEL                                                       -->
<!-- ────────────────────────────────────────────────────────────────────── -->
<div class="risk-panel">
  <div class="risk-header">
    <div class="risk-title">Risk Metrics &amp; Performance Ratios</div>
    <div style="font-size:10px;color:#4e5f90">actualizado en vivo</div>
  </div>
  <div class="risk-grid">
    <div class="risk-cell">
      <div class="risk-cell-label">Sharpe</div>
      <div class="risk-cell-val kpi-neutral" id="risk-sharpe">—</div>
      <div class="risk-cell-ctx" id="risk-sharpe-ctx">n/a</div>
    </div>
    <div class="risk-cell">
      <div class="risk-cell-label">Calmar</div>
      <div class="risk-cell-val kpi-neutral" id="risk-calmar">—</div>
      <div class="risk-cell-ctx" id="risk-calmar-ctx">n/a</div>
    </div>
    <div class="risk-cell">
      <div class="risk-cell-label">Max DD</div>
      <div class="risk-cell-val kpi-neutral" id="risk-maxdd">—</div>
      <div class="risk-cell-ctx" id="risk-maxdd-ctx">desde inicio</div>
    </div>
    <div class="risk-cell">
      <div class="risk-cell-label">Profit Factor</div>
      <div class="risk-cell-val kpi-neutral" id="risk-pf">—</div>
      <div class="risk-cell-ctx" id="risk-pf-ctx">wins/losses</div>
    </div>
    <div class="risk-cell">
      <div class="risk-cell-label">Avg Trade</div>
      <div class="risk-cell-val kpi-neutral" id="risk-avgtr">—</div>
      <div class="risk-cell-ctx" id="risk-avgtr-ctx">por trade</div>
    </div>
    <div class="risk-cell">
      <div class="risk-cell-label">Risk of Ruin</div>
      <div class="risk-cell-val kpi-neutral" id="risk-ror">—</div>
      <div class="risk-cell-ctx" id="risk-ror-ctx">prob. ruina</div>
    </div>
    <div class="risk-cell">
      <div class="risk-cell-label">Kelly Avg</div>
      <div class="risk-cell-val kpi-neutral" id="risk-kelly">—</div>
      <div class="risk-cell-ctx" id="risk-kelly-ctx">size sugerido</div>
    </div>
  </div>
</div>

{performance_snapshot_html}

<div class="section-divider">
  <span class="section-divider-text">Paper Trading — Resultados en vivo</span>
  <div class="section-divider-line"></div>
</div>

<div id="trades-section" style="margin:16px 0"></div>

<div class="section-divider">
  <span class="section-divider-text">Proof of Work — Backtests Ejecutados</span>
  <div class="section-divider-line"></div>
</div>

<!-- COUNTER BANNER (mantiene tamano grande — es nuestra prueba de trabajo) -->
<div class="counter-banner">
  <div class="counter-main">
    <div class="counter-number" id="live-count">{db["total"]:,}</div>
    <div class="counter-label">
      <strong>Backtests ejecutados</strong>
      en {len(db.get("by_tf",{}))} timeframes &bull; 5 activos
    </div>
  </div>
  <div class="counter-stats">
    {tf_counter_html}
  </div>
  <div style="text-align:right">
    <div class="counter-rate" id="live-rate">+{db.get("optuna_rate_hr", db.get("rate_hr",0)):,} / hora</div>
    <div style="font-size:11px;color:#7a8db5;margin-top:6px">{n_ready} modelos OOS positivos</div>
    <div style="font-size:11px;color:#7a8db5">{n_total - n_ready} pendientes M1</div>
  </div>
</div>

{_dca_html}
{_cs_html}
{_gate_html}
{_stress_html}
{_aum_html}

<div class="section-divider">
  <span class="section-divider-text">Mercado &amp; Modelos</span>
  <div class="section-divider-line"></div>
</div>

<!-- REGIME PANEL -->
<div style="margin-bottom:18px">
  <div style="font-size:11px;font-weight:700;color:#7a8db5;text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px;display:flex;justify-content:space-between">
    <span>&#127760; Regimen Actual por Par <span style="font-weight:400;color:#555">(actualiza cada 5min)</span></span>
    <span style="font-weight:400;color:#555">&#11088; Bull &gt;55 RSI_W + sobre EMA200 &nbsp; ~ Range &nbsp; &#10060; Bear</span>
  </div>
  <div class="regime-grid" id="regime-grid">
    <div class="regime-card" style="color:#555;text-align:center;padding:20px">Cargando regimenes...</div>
  </div>
</div>

<!-- ACTIVITY FEED -->
<div class="feed">
  <div class="feed-title">
    <span>&#128336; Actividad del Pipeline — ultimas actualizaciones</span>
    <span style="font-weight:400;color:#555">
      <span style="color:#2ecc71">&#11088;</span> Record &nbsp;
      <span style="color:#f1c40f">&#9650;</span> Positivo &nbsp;
      <span style="color:#e74c3c">&#10007;</span> OOS neg. &nbsp;
      <span style="color:#555">&#8212;</span> Skip
    </span>
  </div>
  <div class="feed-list" id="feed-list">
    {feed_rows}
  </div>
</div>

<!-- PORTFOLIO RULE -->
<div class="rule">
  <div>
    <div class="rule-title">&#9881; Regla de Portfolio</div>
    <div class="rule-sub">Max 2 activos activos &bull; 2.5% riesgo c/u &bull; 5% total maximo</div>
  </div>
  <div>
    <div style="font-size:11px;color:#7a8db5;margin-bottom:6px">Mejores 2 ahora:</div>
    <div class="pills">{top2_pills}</div>
  </div>
  <div class="prog">Modelos listos: <strong>{n_ready}/{n_total}</strong></div>
</div>

<!-- MOTOR 1 -->

  <!-- Motor 1: Crypto -->
  <div class="card" id="matrix-section">
    <div class="card-title" style="display:flex;justify-content:space-between">
      <span>Motor 1 &mdash; Crypto &nbsp;<span style="font-weight:400;color:#7a8db5;font-size:12px">BTC / ETH / LTC / SOL / BNB</span></span>
      <span style="font-weight:400;color:#555;font-size:12px">
        <span style="color:#2ecc71">&#9632;</span> Positivo &nbsp;
        <span style="color:#c9a227">&#9632;</span> Optimizando &nbsp;
        <span style="color:#242f55">&#9632;</span> Pendiente &nbsp;
        <span style="color:#e74c3c44">&#9632;</span> OOS neg.
      </span>
    </div>
    <div class="matrix-wrap">
      <table class="matrix">
        <thead>
          <tr>
            <th class="th-asset">Activo</th>
            <th>4H</th>
            <th>1H</th>
            <th>15m</th>
            <th>5m</th>
          </tr>
        </thead>
        <tbody>
          {matrix_rows}
        </tbody>
      </table>
    </div>
  </div>

  <!-- Motor 2: Commodities -->
  <div class="card" id="matrix-section-m2" style="border:1px solid #FFD70044;background:linear-gradient(180deg,rgba(255,215,0,.05),#0d1428)">
    <div class="card-title" style="display:flex;justify-content:space-between;align-items:center">
      <span><span style="color:#ffa657;font-weight:700">SIGMA MACRO</span> <span style="color:#7a8db5;font-weight:400">&mdash; Metals &amp; Energy</span></span>
      <span style="font-weight:400;color:#555;font-size:10px"><span style="color:#2ecc71">&#9632;</span> Listo &nbsp;<span style="color:#c9a227">&#9632;</span> Optim. &nbsp;<span style="color:#242f55">&#9632;</span> Pendiente</span>
    </div>
    <div class="matrix-wrap">
      <table class="matrix">
        <thead><tr>
          <th class="th-asset">Activo</th>
          <th>1D</th><th>4H</th><th>1H</th><th>15m</th>
        </tr></thead>
        <tbody>{matrix_rows_m2}</tbody>
      </table>
    </div>
  </div>


<!-- BTC DETAIL + MC CONFIDENCE --><div id="btc-detail">
{"" if not btc_detail else f'''
<div class="card">
  <div class="card-title">BTC/USDT — Detalle + Confianza Monte Carlo</div>
  <table class="t">
    <thead><tr><th>Estrategia</th><th>CAGR OOS</th><th>Win Rate</th><th>Max DD</th><th>Trades</th><th>Confianza MC</th></tr></thead>
    <tbody>{btc_detail}</tbody>
  </table>
</div>'''}

<!-- WALK-FORWARD BTC 1H -->
<div class="card">
  <div class="card-title">Walk-Forward BTC 1H &mdash; Consistencia Historica 2017-2026</div>
  <div class="wft-stats">
    <div class="wft-num"><div class="n" style="color:#c9a227">{wft_done}/97</div><div class="l">Ventanas</div></div>
    <div class="wft-num"><div class="n" style="color:{wft_col}">{wft_pct:.0f}%</div><div class="l">Positivas</div></div>
    <div class="wft-num"><div class="n" style="color:#2ecc71">{wft_pos}</div><div class="l">OK</div></div>
    <div class="wft-num"><div class="n" style="color:#e74c3c">{wft_done-wft_pos}</div><div class="l">Negativas</div></div>
  </div>
  <div class="progress"><div class="progress-fill" style="width:{round(wft_done/97*100,1)}%"></div></div>
  {"<div class='wft-scroll'><table class='t'><thead><tr><th>Ventana</th><th>Trades</th><th>WR</th><th>CAGR</th><th>&#10003;</th></tr></thead><tbody>" + wft_rows + "</tbody></table></div>" if wft_rows else "<div style='color:#7a8db5;text-align:center;padding:16px'>Corriendo...</div>"}
</div>

<!-- CROSS-ASSET -->
{f'''
<div class="card">
  <div class="card-title">Cross-Asset &mdash; Params BTC en otros activos</div>
  <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">{ca_cards_html}</div>
  <div style="font-size:12px;color:#7a8db5">
    Positivos: <strong style="color:#dde3f5">{len(ca_pos)}/4</strong> &nbsp;&bull;&nbsp;
    <strong style="color:{"#2ecc71" if len(ca_pos)>=3 else "#f1c40f"}">{ca_conf[:50] if ca_conf else "N/D"}</strong>
  </div>
</div>''' if ca_cards_html else ""}

<!-- VPS ACTIVITY -->
<div class="card">
  <div class="card-title">VPS &mdash; Actividad del Optimizador</div>
  <div class="tf-pills">{tf_counts if tf_counts else '<span style="color:#7a8db5">Sin datos</span>'}</div>
  {"" if not top3_rows else f'<table class="t"><thead><tr><th>TF</th><th>Estrategia</th><th>CAGR IS</th><th>WR</th><th>Score</th></tr></thead><tbody>{top3_rows}</tbody></table>'}
</div>

<div class="card" style="text-align:center;padding:28px 20px">
  <div class="card-title" style="justify-content:center;margin-bottom:8px">
    &#9660; Descargar Pine Scripts &mdash; TradingView
  </div>
  <p style="color:#7a8db5;font-size:13px;margin-bottom:20px;max-width:520px;margin-left:auto;margin-right:auto">
    Carga <strong style="color:#dde3f5">ambos indicadores</strong> en el mismo chart de TradingView.
    El ENGINE detecta par y temporalidad automaticamente y aplica el modelo validado.
  </p>
  <div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin-bottom:20px">
    <div style="text-align:center">
      <a href="/download/strategy" download="SIGMA_ENGINE_STRATEGY_v1.pine"
         style="display:inline-flex;align-items:center;gap:8px;padding:12px 28px;
                background:linear-gradient(135deg,#238636,#2ea043);color:#fff;
                border-radius:8px;text-decoration:none;font-weight:700;font-size:15px;
                border:1px solid #3fb950;transition:all .2s"
         onmouseover="this.style.opacity='.85'" onmouseout="this.style.opacity='1'"
         onclick="hudDownloadClick(this)">
        &#11015; SIGMA ENGINE v1.0
      </a>
      <div style="font-size:11px;color:#7a8db5;margin-top:6px">Senales + SL/TP + Backtest</div>
    </div>
    <div style="text-align:center">
      <a href="/download/terminal" download="SIGMA_v13_COMPLETO.pine"
         style="display:inline-flex;align-items:center;gap:8px;padding:12px 24px;
                background:#1a2240;color:#dde3f5;
                border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;
                border:1px solid #242f55;transition:all .2s"
         onmouseover="this.style.borderColor='#c9a227'" onmouseout="this.style.borderColor='#242f55'">
        &#11015; SIGMA TERMINAL v13.0
      </a>
      <div style="font-size:11px;color:#7a8db5;margin-top:6px">Analisis ICT / OFI / CVD / Bayesian</div>
    </div>
  </div>
  <div id="hud-info" style="font-size:12px;color:#7a8db5">Cargando info...</div>
</div>

<div class="footer">
  SIGMA ENGINE &nbsp;&mdash;&nbsp; Counter en vivo cada 5s &nbsp;&mdash;&nbsp; Pagina cada 60s &nbsp;&mdash;&nbsp; {now}
</div>

</div>

<script>
const TF_COLORS = {{'1h':'#c9a227','4h':'#2ecc71','15m':'#f1c40f','5m':'#e67e22'}};

function animateCount(el, target) {{
  const start = parseInt(el.innerText.replace(/,/g,'')) || 0;
  if (start === target) return;
  const diff = target - start;
  const steps = 20;
  let i = 0;
  const iv = setInterval(() => {{
    i++;
    const val = Math.round(start + diff * (i/steps));
    el.innerText = val.toLocaleString();
    if (i >= steps) {{ el.innerText = target.toLocaleString(); clearInterval(iv); }}
  }}, 30);
}}

function updateLive() {{
  fetch('/api/stats')
    .then(r => r.json())
    .then(d => {{
      // Big counter
      const el = document.getElementById('live-count');
      if (el) animateCount(el, d.total);

      // Rate
      const re = document.getElementById('live-rate');
      if (re) re.innerText = '+' + (d.optuna_rate_hr || d.rate_hr || 0).toLocaleString() + ' / hora';

      // Per-TF counters
      Object.entries(d.by_tf || {{}}).forEach(([tf, cnt]) => {{
        const e = document.getElementById('tf-' + tf);
        if (e) animateCount(e, cnt);
      }});
    }})
    .catch(() => {{}});
}}

updateLive();
setInterval(updateLive, 5000);

// Regime: fetch cada 5 minutos (datos weekly no cambian tan rapido)
const ASSET_COLORS = {{'BTC':'#f7931a','ETH':'#627eea','LTC':'#345d9d','SOL':'#9945ff','BNB':'#f3ba2f'}};
const ASSET_EMOJI  = {{'BTC':'&#8383;','ETH':'&#926;','LTC':'&#321;','SOL':'&#9678;','BNB':'&#11042;'}};

function renderRegime(data) {{
  const grid = document.getElementById('regime-grid');
  if (!grid || !data || Object.keys(data).length === 0) return;
  let html = '';
  for (const [asset, r] of Object.entries(data)) {{
    const regime = r.regime || 'UNKNOWN';
    const cls = regime === 'BULL' ? 'regime-bull' : regime === 'BEAR' ? 'regime-bear' : regime === 'RANGE' ? 'regime-range' : 'regime-unk';
    const icon = regime === 'BULL' ? '&#11088;' : regime === 'BEAR' ? '&#10060;' : regime === 'RANGE' ? '&#126;' : '?';
    const col  = ASSET_COLORS[asset] || '#dde3f5';
    const pct  = r.pct_vs_ema ? (r.pct_vs_ema > 0 ? '+' : '') + r.pct_vs_ema + '% vs EMA200' : '';
    html += `<div class="regime-card">
      <div class="regime-asset" style="color:${{col}}">${{ASSET_EMOJI[asset] || asset}} ${{asset}}</div>
      <div class="regime-badge ${{cls}}">${{icon}} ${{regime}}</div>
      <div class="regime-rsi">RSI_W ${{r.rsi_w || '?'}}</div>
      <div class="regime-rsi">${{pct}}</div>
    </div>`;
  }}
  grid.innerHTML = html;
}}

function fetchRegime() {{
  fetch('/api/regime')
    .then(r => r.json())
    .then(d => renderRegime(d))
    .catch(() => {{}});
}}

function fetchRegime() {{
  fetch('/api/regime')
    .then(r => r.json())
    .then(d => renderRegime(d))
    .catch(() => {{}});
}}

fetchRegime();
setInterval(fetchRegime, 300000); // cada 5 minutos

// Matriz + portfolio: actualiza sin recargar la pagina entera (sin parpadeo)
function refreshMatrix() {{
  fetch(window.location.pathname + '?v=' + Date.now())
    .then(r => r.text())
    .then(html => {{
      const parser = new DOMParser();
      const doc    = parser.parseFromString(html, 'text/html');
      // Actualiza solo la seccion de la matriz
      const ids = ['matrix-section', 'portfolio-bar', 'btc-detail'];
      ids.forEach(id => {{
        const newEl = doc.getElementById(id);
        const curEl = document.getElementById(id);
        if (newEl && curEl && newEl.innerHTML !== curEl.innerHTML) {{
          curEl.innerHTML = newEl.innerHTML;
        }}
      }});
    }})
    .catch(() => {{}});
}}
setInterval(refreshMatrix, 120000); // cada 2 minutos, sin parpadeo

// Feed refresh: recarga solo el feed cada 15s via fetch del HTML parcial
function refreshFeed() {{
  fetch(window.location.href + '?nocache=' + Date.now())
    .then(r => r.text())
    .then(html => {{
      const parser = new DOMParser();
      const doc    = parser.parseFromString(html, 'text/html');
      const newFeed = doc.getElementById('feed-list');
      const curFeed = document.getElementById('feed-list');
      if (newFeed && curFeed && newFeed.innerHTML !== curFeed.innerHTML) {{
        curFeed.innerHTML = newFeed.innerHTML;
      }}
    }})
    .catch(() => {{}});
}}
setInterval(refreshFeed, 15000);

// HUD download info
function refreshHudInfo() {{
  fetch('/api/hud_info')
    .then(r => r.json())
    .then(d => {{
      const el = document.getElementById('hud-info');
      if (!el) return;
      if (d.available) {{
        el.innerHTML = '&#9679; <strong style="color:#3fb950">' + d.size_kb + ' KB</strong>' +
          '&nbsp;&nbsp;&#9679; <strong style="color:#c9a227">' + d.models + ' modelos</strong>' +
          '&nbsp;&nbsp;&#9679; actualizado <strong style="color:#dde3f5">' + d.updated + '</strong>';
      }} else {{
        el.innerHTML = '<span style="color:#f85149">HUD no disponible en el servidor</span>';
      }}
    }})
    .catch(() => {{ document.getElementById('hud-info').textContent = 'Error al verificar'; }});
}}

function hudDownloadClick(btn) {{
  const orig = btn.innerHTML;
  btn.innerHTML = '&#8987; Actualizando modelos...';
  btn.style.opacity = '0.7';
  setTimeout(() => {{ btn.innerHTML = orig; btn.style.opacity = '1'; }}, 4000);
}}

refreshHudInfo();
</script>

<!-- trades-section moved up -->
<div id="signals-section"><p style="color:#888;padding:16px">Cargando señales...</p></div>
<div id="trainer-section" style="margin:16px 0"></div>
<script>
async function loadTrainer(){{
  try{{
    const res = await fetch('/api/trainer_status');
    const d   = await res.json();
    const lines = d.lines || [];

    // Filtrar líneas relevantes: cabeceras, resultados, progreso
    const relevant = lines.filter(l =>
      l.includes('SIGMA') || l.includes('ASSET PIPELINE') || l.includes('Probando') ||
      l.includes('GANADOR') || l.includes('BEAR') || l.includes('BULL') ||
      l.includes('score=') || l.includes('CAGR') || l.includes('OOS') ||
      l.includes('CLT]') || l.includes('RÉGIMEN') || l.includes('DEGRADED') ||
      l.includes('PINE') || l.includes('ERROR') || l.includes('velas')
    ).slice(-18);

    let html = `<div style="background:#07091c;border:1px solid #1a2240;border-radius:8px;padding:14px;margin:16px 0">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <span style="color:#b8c5e0;font-weight:700;font-size:13px">⚙ Optimizador VPS — Actividad</span>
        <span style="color:#555;font-size:11px">
          DB: <b style="color:#7a8db5">${{d.db_total?.toLocaleString()??'—'}}</b> runs ·
          <b style="color:#69f0ae">${{(d.optuna_rate_hr??d.rate_hr??0).toLocaleString()}}</b>/hr
        </span>
      </div>
      <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;line-height:1.7">`;

    relevant.forEach(line => {{
      let col = '#555';
      if(line.includes('GANADOR') || line.includes('CAGR') || line.includes('score=')) col = '#69f0ae';
      else if(line.includes('SIGMA') || line.includes('PIPELINE')) col = '#90a8f0';
      else if(line.includes('Probando')) col = '#7a8db5';
      else if(line.includes('ERROR')) col = '#f85149';
      else if(line.includes('CLT]')) col = '#4a5580';
      else if(line.includes('BEAR') || line.includes('BULL') || line.includes('RÉGIMEN')) col = '#ff9800';
      html += `<div style="color:${{col}};white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${{line.replace(/</g,'&lt;')}}</div>`;
    }});

    html += `</div></div>`;
    document.getElementById('trainer-section').innerHTML = html;
  }} catch(e) {{
    document.getElementById('trainer-section').innerHTML = '<p style="color:#555;font-size:11px;padding:8px">Optimizador: sin datos</p>';
  }}
}}
loadTrainer();
setInterval(loadTrainer, 30000);
</script>

<script>
function recIcon(r){{
  if(r==='ACTIVAR') return '<span style="background:#00c853;color:#000;padding:2px 7px;border-radius:4px;font-weight:bold;font-size:11px">✅ ACTIVAR</span>';
  if(r==='ESPERAR') return '<span style="background:#ff9800;color:#000;padding:2px 7px;border-radius:4px;font-size:11px">⏸ ESPERAR</span>';
  if(r==='CONDICIONAL') return '<span style="background:#ffeb3b;color:#000;padding:2px 7px;border-radius:4px;font-size:11px">⚠ CONDIC.</span>';
  return '<span style="background:#f44336;color:#fff;padding:2px 7px;border-radius:4px;font-size:11px">❌ NO</span>';
}}
function sigBadge(s, slot){{
  if(s && slot===1) return '<span style="background:#00e676;color:#000;padding:2px 8px;border-radius:4px;font-weight:bold;font-size:12px">🟢 SEÑAL — PRIO 1</span>';
  if(s && slot===2) return '<span style="background:#00e676;color:#000;padding:2px 8px;border-radius:4px;font-weight:bold;font-size:12px">🟢 SEÑAL — PRIO 2</span>';
  if(s && slot===0) return '<span style="background:#ffeb3b;color:#000;padding:2px 8px;border-radius:4px;font-size:11px">🟡 SEÑAL — EN COLA</span>';
  return '<span style="color:#666;font-size:11px">— sin señal</span>';
}}
function gradeColor(g){{
  if(g==='A+') return '#00c853'; if(g==='A') return '#69f0ae';
  if(g==='B') return '#ffeb3b'; if(g==='C') return '#ff9800'; return '#f44336';
}}
let _prevSignalKeys = new Set();
let _countdown = 5;

function _beep() {{
  try {{
    const ctx = new (window.AudioContext||window.webkitAudioContext)();
    [880,1100,880].forEach((f,i) => {{
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.connect(g); g.connect(ctx.destination);
      o.frequency.value = f; g.gain.value = 0.15;
      o.start(ctx.currentTime + i*0.12);
      o.stop(ctx.currentTime + i*0.12 + 0.10);
    }});
  }} catch(e) {{}}
}}

function _updateLive() {{
  _countdown--;
  if(_countdown <= 0) {{ _countdown = 5; loadSignals(); return; }}
  const el = document.getElementById('live-counter');
  if(el) el.textContent = `en ${{_countdown}}s`;
}}
setInterval(_updateLive, 1000);

// ─── SSE: push instantaneo del backend al detectar nueva data ────────────
let _sseLoadInFlight = false;
let _sseLastTs = 0;
function _initSSE() {{
  try {{
    const es = new EventSource('/api/signals/stream');
    es.onmessage = (e) => {{
      try {{
        const d = JSON.parse(e.data);
        if (d.ts && d.ts !== _sseLastTs) {{
          _sseLastTs = d.ts;
          if (!_sseLoadInFlight) {{
            _sseLoadInFlight = true;
            _countdown = 5;
            loadSignals().finally(() => {{ _sseLoadInFlight = false; }});
          }}
        }}
      }} catch(err) {{}}
    }};
    es.onerror = () => {{
      // EventSource reconecta automaticamente; polling cada 5s queda como fallback
    }};
  }} catch(e) {{
    // SSE no soportado, queda solo el polling
  }}
}}
// Iniciar SSE despues de la primera carga
setTimeout(_initSSE, 1500);

const TD  = 'padding:5px 6px;border-bottom:1px solid #161622;white-space:nowrap;font-size:11px';
const TDC = TD+';text-align:center';
const TDR = TD+';text-align:right';
const TH  = 'padding:4px 5px;text-align:left;color:#444;font-size:9px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid #1a2240;font-weight:500';
const THC = TH+';text-align:center';
const THR = TH+';text-align:right';
const gradeC    = g => g==='A+'?'#00c853':g==='A'?'#69f0ae':g==='B'?'#ffeb3b':g==='C'?'#ff9800':'#f44336';
const stratShort = s => s.replace('momentum_short','MOM↓').replace('breakdown','BDN↓')
  .replace('pullback_short','PBK↓').replace('breakout','BRK↑').replace('tma_bands','TMA')
  .replace('momentum','MOM↑').replace('pullback','PBK↑').replace('mean_rev','MRV')
  .replace('regime_adaptive','RAD').replace('_',' ');

function renderHistTablePage(page) {{
  const hist = window._histData || [];
  const PS = 10;
  const totalPages = Math.max(1, Math.ceil(hist.length / PS));
  page = Math.max(0, Math.min(page, totalPages - 1));
  window._histPage = page;
  const slice = hist.slice(page * PS, (page + 1) * PS);
  const tbody = document.getElementById('hist-tbody');
  if (!tbody) return;
  tbody.innerHTML = slice.map(function(t) {{
    const pnl = t.pnl_pct || 0;
    const col = pnl >= 0 ? '#00e676' : '#f44336';
    const icon = t.reason === 'TP_HIT' ? 'TP' : t.reason === 'SL_HIT' ? 'SL' : t.reason === 'TRAIL_HIT' ? 'TRAIL' : (t.reason||'?');
    const iconCol = t.reason === 'TP_HIT' ? '#00e676' : t.reason === 'SL_HIT' ? '#f44336' : t.reason === 'TRAIL_HIT' ? '#f39c12' : '#555';
    return '<tr>'
      + '<td style="' + TD + ';font-weight:bold;color:' + iconCol + '">' + icon + '</td>'
      + '<td style="' + TD + '"><b style="color:#888">' + (t.sym||'') + '</b></td>'
      + '<td style="' + TD + ';color:#555">' + (t.tf||'').toUpperCase() + '</td>'
      + '<td style="' + TD + ';color:#444">' + stratShort(t.strategy||'') + '</td>'
      + '<td style="' + TDC + '"><span style="background:' + gradeC(t.grade||'D') + ';color:#000;padding:1px 6px;border-radius:8px;font-weight:bold;font-size:10px">' + (t.grade||'?') + '</span></td>'
      + '<td style="' + TDR + ';font-family:monospace;color:#666">' + (t.entry||'') + '</td>'
      + '<td style="' + TDR + ';font-family:monospace;color:#666">' + (t.exit_price||'?') + '</td>'
      + '<td style="' + TDR + ';color:' + col + ';font-weight:bold">' + (pnl>=0?'+':'') + pnl.toFixed(2) + '%</td>'
      + '<td style="' + TD + ';color:#444;font-size:10px">' + (t.closed_at||'').substring(5,16) + '</td>'
      + '<td colspan="2"></td>'
      + '</tr>';
  }}).join('') || '<tr><td colspan="10" style="color:#444;text-align:center;padding:14px">-</td></tr>';
  const pag = document.getElementById('hist-pagination');
  if (!pag) return;
  if (totalPages <= 1) {{ pag.innerHTML = ''; return; }}
  let btns = '';
  for (let i = 0; i < totalPages; i++) {{
    const isCur = i === page;
    btns += '<button onclick="renderHistTablePage(' + i + ')" style="background:' + (isCur?'#1a2240':'transparent') + ';color:' + (isCur?'#b8c5e0':'#555') + ';border:1px solid ' + (isCur?'#c9a227':'#1a2240') + ';border-radius:4px;padding:2px 10px;font-size:11px;cursor:pointer;margin:0 2px">' + (i+1) + '</button>';
  }}
  pag.innerHTML = '<div style="display:flex;align-items:center;gap:4px;margin-top:8px;padding-top:6px;border-top:1px solid #141b38">'
    + '<span style="color:#444;font-size:10px;margin-right:4px">Pag:</span>'
    + '<button onclick="renderHistTablePage(Math.max(0,window._histPage-1))" style="background:transparent;color:#555;border:1px solid #1a2240;border-radius:4px;padding:2px 10px;font-size:11px;cursor:pointer">&#171;</button>'
    + btns
    + '<button onclick="renderHistTablePage(Math.min(' + (totalPages-1) + ',window._histPage+1))" style="background:transparent;color:#555;border:1px solid #1a2240;border-radius:4px;padding:2px 10px;font-size:11px;cursor:pointer">&#187;</button>'
    + '<span style="color:#333;font-size:10px;margin-left:8px">' + hist.length + ' trades p. ' + (page+1) + '/' + totalPages + '</span>'
    + '</div>';
}}

async function loadSignals(){{
  _countdown = 5;
  try{{
    const res = await fetch('/api/signals');
    const d   = await res.json();
    const models = d.models || [];
    const regime = d.regime || '?';
    const upd    = d.updated || '';

    // ── COLA DE PRIORIDAD ─────────────────────────────────────────────
    // Deduplica por (sym,tf): mejor por señal luego score
    const mByKey = {{}};
    models.forEach(m => {{
      const k = m.sym+'_'+m.tf;
      const ex = mByKey[k];
      if(!ex || (m.signal && !ex.signal) || (m.signal===ex.signal && m.score>ex.score))
        mByKey[k] = m;
    }});
    const queue = Object.values(mByKey).filter(m =>
      m.recommendation==='ACTIVAR' || m.recommendation==='CONDICIONAL' ||
      (m.signal && m.slot>=0)
    );
    // Orden: slot (1>2>0>-1), luego señal, luego score
    queue.sort((a,b) => {{
      const sa = a.slot>0?10-a.slot : a.signal?5 : a.recommendation==='ACTIVAR'?2:1;
      const sb = b.slot>0?10-b.slot : b.signal?5 : b.recommendation==='ACTIVAR'?2:1;
      if(sa!==sb) return sb-sa;
      return b.score-a.score;
    }});
    // gradeC y stratShort definidos globalmente

    // ── Panel unificado: todos los modelos en una sola lista ordenada ──────
    // Dedup por sym+tf: señal activa primero, luego mayor score
    const allByKey = {{}};
    models.forEach(m => {{
      const k = m.sym+'_'+m.tf;
      const ex = allByKey[k];
      if(!ex || (m.signal && !ex.signal) || (m.signal===ex.signal && m.score>ex.score))
        allByKey[k] = m;
    }});

    // Ordenar: slot1(100) > slot2(90) > señal(50) > ACTIVAR(20) > CONDICIONAL(10) > ESPERAR(2) > NO_ACTIVAR(0) → luego score
    const allSorted = Object.values(allByKey);
    const pts = m => m.slot===1?100 : m.slot===2?90 : m.signal?50 :
      m.recommendation==='ACTIVAR'?20 : m.recommendation==='CONDICIONAL'?10 :
      m.recommendation==='ESPERAR'?2 : 0;
    allSorted.sort((a,b) => {{ const d=pts(b)-pts(a); return d!==0?d:b.score-a.score; }});

    // Grade counts
    const gradeCounts = {{'A+':0,'A':0,'B':0,'C':0,'D':0}};
    models.forEach(m => {{ if(gradeCounts[m.grade]!==undefined) gradeCounts[m.grade]++; }});
    const totalModels = Object.values(gradeCounts).reduce((a,b)=>a+b,0);
    let gradeBar = Object.entries(gradeCounts).filter(([g,n])=>n>0)
      .map(([g,n])=>`<span style="background:${{gradeC(g)}};color:#000;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:bold">${{g}} ${{n}}</span>`).join(' ');

    // Detectar señales nuevas
    const currKeys = new Set(models.filter(m=>m.signal&&m.slot>=0).map(m=>m.sym+'_'+m.tf));
    const newSignals = [...currKeys].filter(k=>!_prevSignalKeys.has(k));
    if(newSignals.length>0 && _prevSignalKeys.size>0) {{
      _beep();
      newSignals.forEach(k => {{
        const [sym,tf] = k.split('_');
        const m = models.find(x=>x.sym===sym&&x.tf===tf);
        if(m) {{
          const flash = document.createElement('div');
          flash.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999;background:#07091c;border:2px solid '+(m.type!=='short'?'#00e676':'#f85149')+';border-radius:8px;padding:14px 20px;font-family:monospace;animation:flashIn 0.3s ease';
          flash.innerHTML = `<b style="color:#e0e0e0">NUEVA SEÑAL ${{m.type!=='short'?'▲ LONG':'▼ SHORT'}}</b><br><span style="color:#aaa">${{sym}} ${{tf.toUpperCase()}} · ${{m.strategy}}</span>`;
          document.body.appendChild(flash);
          setTimeout(()=>flash.remove(), 8000);
        }}
      }});
    }}
    _prevSignalKeys = currKeys;

    // ── TABLA SEÑALES ─────────────────────────────────────────────────────────
    let rows = '';
    allSorted.forEach(m => {{
      const slot   = m.slot;
      const sig    = m.signal;
      const isLong = m.type!=='short';
      const rec    = m.recommendation;
      const dirC   = isLong?'#00e676':'#f85149';
      const gc     = gradeC(m.grade);

      // Estado badge
      let stateBadge, rowStyle;
      if(slot===1||slot===2) {{
        stateBadge = `<span style="background:${{dirC}};color:#000;padding:2px 9px;border-radius:4px;font-weight:bold;font-size:11px">PRIO ${{slot}} ${{isLong?'▲':'▼'}}</span>`;
        rowStyle   = `background:rgba(${{isLong?'0,230,118':'248,81,73'}},0.06);border-left:3px solid ${{dirC}}`;
      }} else if(rec==='ACTIVAR') {{
        stateBadge = `<span style="background:rgba(96,130,220,0.18);color:#90a8f0;border:1px solid #4060b0;padding:2px 9px;border-radius:4px;font-size:11px;font-weight:600">◉ COLA</span>`;
        rowStyle   = 'background:rgba(64,96,192,0.04);border-left:3px solid #4060b0';
      }} else if(rec==='CONDICIONAL') {{
        stateBadge = `<span style="color:#ff9800;font-size:11px">◈ COND</span>`;
        rowStyle   = 'opacity:0.65;border-left:3px solid #7a4400';
      }} else {{
        stateBadge = `<span style="color:#333;font-size:11px">⏸ PAUSA</span>`;
        rowStyle   = 'opacity:0.35;border-left:3px solid #141b38';
      }}

      // Confianza
      const conf = m.val_confidence
        ? `<span style="color:${{m.val_confidence==='ALTA'?'#00c853':m.val_confidence==='MEDIA'?'#69f0ae':'#ff9800'}};font-weight:700">${{m.val_confidence}}</span>`
        : m.mc_confidence!=null
          ? `<span style="color:${{m.mc_confidence>=90?'#00c853':m.mc_confidence>=70?'#69f0ae':'#ff9800'}}">MC ${{m.mc_confidence?.toFixed(0)}}%</span>`
          : '<span style="color:#333">—</span>';

      // Precio / SL / TP / RR
      let precio='—', sl='—', tp='—', rr='—', rrCol='#555', dirTag='—';
      if(sig && m.price) {{
        precio  = m.price;
        sl      = m.sl || '—';
        tp      = m.tp || '—';
        dirTag  = `<span style="color:${{dirC}};font-weight:bold">${{isLong?'▲ L':'▼ S'}}</span>`;
        if(m.sl && m.tp) {{
          const rn = Math.abs(m.tp-m.price)/Math.abs(m.price-m.sl);
          rr    = rn.toFixed(1);
          rrCol = rn>=2?'#00c853':rn>=1.5?'#69f0ae':'#ff9800';
        }}
      }}

      const cagrCol = m.cagr>30?'#00c853':m.cagr>15?'#69f0ae':m.cagr>0?'#8bc48b':'#f44336';
      const ddCol   = m.dd>-15?'#69f0ae':m.dd>-25?'#ff9800':'#f44336';
      const wrCol   = m.wr>=65?'#00c853':m.wr>=55?'#69f0ae':'#7a8db5';
      const corrW   = m.corr_warning ? ' title="Correlacionado — reducir size"' : '';

      rows += `<tr style="${{rowStyle}}">
        <td style="${{TD}}">${{stateBadge}}</td>
        <td style="${{TD}}"><b style="color:#b8c5e0">${{m.sym}}</b></td>
        <td style="${{TD}};color:#4a5580">${{m.tf.toUpperCase()}}</td>
        <td style="${{TD}};color:#ddd;overflow:hidden;text-overflow:ellipsis" title="${{m.strategy}}">${{stratShort(m.strategy)}}</td>
        <td style="${{TDC}}"><span style="background:${{gc}};color:#000;padding:1px 7px;border-radius:10px;font-weight:bold;font-size:11px">${{m.grade}}</span></td>
        <td style="${{TDC}};font-size:11px">${{conf}}</td>
        <td style="${{TDR}};color:${{cagrCol}};font-weight:600">${{m.cagr>0?'+':''}}${{m.cagr?.toFixed(1)}}%</td>
        <td style="${{TDR}};color:${{ddCol}}">${{m.dd?.toFixed(1)}}%</td>
        <td style="${{TDR}};color:${{wrCol}}">${{m.wr?.toFixed(0)??'—'}}%</td>
        <td style="${{TDC}};color:#555">${{m.trades}}</td>
        <td style="${{TDR}};color:#555;font-size:11px">${{m.eff_risk_pct!=null?m.eff_risk_pct+'%':'—'}}${{m.corr_warning?' ⚠':''}}${{m.ensemble_count>1?' E'+m.ensemble_count:''}}</td>
        <td style="${{TDR}};font-family:'IBM Plex Mono',monospace;color:#e0bb3a;font-weight:600" title="Posicion nominal en USD. Riesgo si SL: $${{(m.risk_usd||0).toFixed(0)}}. Ganancia si TP: $${{(m.reward_usd_at_tp||0).toFixed(0)}}">${{m.notional_usd?'$'+Math.round(m.notional_usd).toLocaleString():'—'}}</td>
        <td style="${{TDC}}">${{dirTag}}</td>
        <td style="${{TDR}};font-family:monospace;color:#7a8db5">${{precio}}</td>
        <td style="${{TDR}};font-family:monospace;color:#f85149">${{sl}}</td>
        <td style="${{TDR}};font-family:monospace;color:#00e676">${{tp}}</td>
        <td style="${{TDR}};color:${{rrCol}};font-weight:bold">${{rr!='—'?rr+':1':'—'}}</td>
      </tr>`;
    }});

    // ── 🎯 LONGS ARMADOS (bloqueados solo por régimen) ───────────────────
    const armedLongs = Object.values(allByKey).filter(m =>
      m.type!=='short' && m.recommendation==='ESPERAR' &&
      typeof m.reason==='string' && /[Rr]egimen/.test(m.reason)
    ).sort((a,b)=>(b.cagr||0)-(a.cagr||0)).slice(0,10);
    let armedHtml = '';
    if(armedLongs.length>0) {{
      const items = armedLongs.map(m => {{
        const cagr = (m.cagr>=0?'+':'')+(m.cagr?.toFixed(1)??'?')+'%';
        const wr   = (m.wr?.toFixed(0)??'?')+'%';
        const strat= (typeof stratShort==='function')?stratShort(m.strategy):m.strategy;
        return `<div style="display:flex;justify-content:space-between;font-size:11px;color:#b8c5e0;padding:3px 0;border-bottom:1px solid #0d1428">
          <span><b style="color:#00e676">${{m.sym}}</b> <span style="color:#4a5580">${{m.tf.toUpperCase()}}</span> <span style="color:#e0bb3a">${{strat}}</span></span>
          <span><span style="color:#69f0ae">CAGR ${{cagr}}</span> · <span style="color:#7a8db5">WR ${{wr}}</span> · <span style="color:#ff9800">bloqueado: ${{m.reason||'régimen'}}</span></span>
        </div>`;
      }}).join('');
      armedHtml = `<div style="background:#07091c;border:1px solid #1a2240;border-radius:8px;padding:12px 14px;margin:16px 0">
        <div style="color:#b8c5e0;font-weight:700;font-size:13px;margin-bottom:6px">🎯 LONGS ARMADOS <span style="color:#4a5580;font-weight:400;font-size:11px">— esperando rotación del régimen (${{armedLongs.length}})</span></div>
        ${{items}}
      </div>`;
    }}

    let html = armedHtml + `<div style="background:#07091c;border:1px solid #1a2240;border-radius:8px;padding:14px;margin:16px 0;overflow-x:auto">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <div style="display:flex;align-items:center;gap:10px">
          <span style="color:#b8c5e0;font-weight:700;font-size:14px">📊 Panel de Señales</span>
          <span style="color:#444;font-size:11px">Régimen <b style="color:#ff9800">${{regime}}</b></span>
          <span style="color:#333;font-size:10px" title="Backtest usa datos Futuros Binance, comisión 0.04%, funding rate histórico real incluido. CAGR sin leverage. Kelly sizing.">ⓘ Futuros · fee 0.04% · funding incluido</span>
          ${{gradeBar}}
        </div>
        <span style="color:#444;font-size:11px">
          <span style="display:inline-block;width:6px;height:6px;background:#00e676;border-radius:50%;margin-right:4px;animation:pulse 1.5s infinite"></span>
          LIVE · <span id="live-counter">30s</span> · ${{upd}}
          ${{d.circuit_breaker?'<span style="background:#f44336;color:#fff;padding:2px 8px;border-radius:4px;font-weight:bold;margin-left:8px">⛔ CIRCUIT BREAKER</span>':''}}
        </span>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:11px;table-layout:fixed">
        <colgroup>
          <col style="width:88px"><col style="width:44px"><col style="width:34px">
          <col style="width:140px"><col style="width:54px"><col style="width:62px">
          <col style="width:56px"><col style="width:52px"><col style="width:40px">
          <col style="width:38px"><col style="width:58px"><col style="width:70px">
          <col style="width:32px"><col style="width:68px"><col style="width:68px">
          <col style="width:68px"><col style="width:38px">
        </colgroup>
        <thead><tr>
          <th style="${{TH}}">Estado</th>
          <th style="${{TH}}">Activo</th>
          <th style="${{TH}}">TF</th>
          <th style="${{TH}}">Estrategia</th>
          <th style="${{THC}}">Grade</th>
          <th style="${{THC}}">Confianza</th>
          <th style="${{THR}}">CAGR</th>
          <th style="${{THR}}">DD</th>
          <th style="${{THR}}">WR</th>
          <th style="${{THC}}">T/año</th>
          <th style="${{THR}}">Kelly</th>
          <th style="${{THR}}" title="Posicion nominal en USD (equity x leverage implicito)">Posición $</th>
          <th style="${{THC}}">Dir</th>
          <th style="${{THR}}">Entrada</th>
          <th style="${{THR}}">SL</th>
          <th style="${{THR}}">TP</th>
          <th style="${{THR}}">RR</th>
        </tr></thead>
        <tbody>${{rows}}</tbody>
      </table>
    </div>`;

    document.getElementById('signals-section').innerHTML = html;
  }} catch(e){{
    document.getElementById('signals-section').innerHTML = '<p style="color:#888">Cargando señales...</p>';
  }}
}}
loadSignals();

// ── PANEL DE TRADES EN VIVO ──────────────────────────────────────────────────
let _openTradesCache = [];   // ultimo snapshot para refresh rapido del capital
let _portCache = {{}};
let _lastCapitalVal = null;

// ── Command Center KPI Strip updater ─────────────────────────────────────
function _setKpi(id, value, klass, sub) {{
  const v = document.getElementById('kpi-'+id);
  const s = document.getElementById('kpi-'+id+'-sub');
  if (!v) return;
  v.textContent = value;
  v.className = 'kpi-value ' + (klass || 'kpi-neutral');
  if (s && sub != null) s.textContent = sub;
}}

function _updateRiskPanel(trades) {{
  if (!trades) return;
  try {{
    const port = trades.portfolio || {{}};
    const stats = trades.stats || {{}};
    const total = stats.total || 0;
    const _set = (id, val, klass, ctx) => {{
      const v = document.getElementById('risk-' + id);
      const c = document.getElementById('risk-' + id + '-ctx');
      if (v) {{ v.textContent = val; v.className = 'risk-cell-val ' + (klass || 'kpi-neutral'); }}
      if (c && ctx != null) c.textContent = ctx;
    }};
    // Sharpe
    if (port.sharpe != null) {{
      const sh = port.sharpe;
      _set('sharpe', sh.toFixed(2),
           sh >= 1 ? 'kpi-pos' : sh >= 0 ? 'kpi-warn' : 'kpi-neg',
           sh >= 1 ? 'excelente' : sh >= 0 ? 'aceptable' : 'pobre');
    }} else {{
      _set('sharpe', '—', 'kpi-neutral', total < 5 ? 'min 5 trades' : 'computing');
    }}
    // Calmar
    if (port.calmar != null) {{
      const ca = port.calmar;
      _set('calmar', ca.toFixed(2),
           ca >= 2 ? 'kpi-pos' : ca >= 1 ? 'kpi-warn' : 'kpi-neg',
           'CAGR/DD');
    }} else {{
      _set('calmar', '—', 'kpi-neutral', total < 5 ? 'min 5 trades' : 'computing');
    }}
    // Max DD
    const dd = port.max_dd || 0;
    _set('maxdd', dd.toFixed(2) + '%',
         dd > -5 ? 'kpi-pos' : dd > -15 ? 'kpi-warn' : 'kpi-neg',
         dd > -5 ? 'bajo' : dd > -15 ? 'medio' : 'alto');
    // Profit Factor
    const pf = stats.profit_factor || 0;
    _set('pf', pf.toFixed(2),
         pf >= 1.5 ? 'kpi-pos' : pf >= 1 ? 'kpi-warn' : 'kpi-neg',
         total > 0 ? 'wins/losses' : 'sin trades');
    // Avg trade
    const avgW = stats.avg_win || 0;
    const avgL = stats.avg_loss || 0;
    const totalPnl = stats.total_pnl || 0;
    const avgTrade = total > 0 ? totalPnl / total : 0;
    _set('avgtr', (avgTrade >= 0 ? '+' : '') + avgTrade.toFixed(2) + '%',
         avgTrade >= 0 ? 'kpi-pos' : 'kpi-neg',
         total + ' trades');
    // Risk of Ruin
    const ror = port.risk_of_ruin;
    if (ror != null) {{
      _set('ror', ror.toFixed(1) + '%',
           ror < 10 ? 'kpi-pos' : ror < 30 ? 'kpi-warn' : 'kpi-neg',
           ror < 10 ? 'muy bajo' : ror < 30 ? 'aceptable' : 'elevado');
    }} else {{
      _set('ror', '—', 'kpi-neutral', 'min 5 trades');
    }}
    // Kelly avg
    const kelly = port.kelly_avg || 0;
    _set('kelly', kelly.toFixed(2) + '%', 'kpi-warn', 'sizing recom.');
  }} catch(e) {{
    console.error('Risk panel update error', e);
  }}
}}

function _updateKpiStrip(trades, signals, floatPctLive) {{
  try {{
    // Equity & Realized
    const port = (trades && trades.portfolio) || {{}};
    const stats = (trades && trades.stats) || {{}};
    const equity = port.equity || 10000;
    const initial = port.initial || 10000;
    const retPct = port.return_pct != null ? port.return_pct : ((equity/initial - 1) * 100);
    // Float pct calculado en JS desde open trades (mas confiable que API)
    const floatPct = (floatPctLive != null) ? floatPctLive : 0;
    // Equity total = capital realizado * (1 + floatPct/100)
    const equityTotal = equity * (1 + floatPct/100);
    const totalRetPct = retPct + floatPct;  // aprox: realizado + flotante
    const fmtMoney = v => '$' + v.toFixed(0).replace(/\B(?=(\d{{3}})+(?!\d))/g, ',');

    // Equity card — muestra el total incluyendo flotante
    _setKpi('equity', fmtMoney(equityTotal), totalRetPct >= 0 ? 'kpi-pos' : 'kpi-neg',
            (totalRetPct >= 0 ? '+' : '') + totalRetPct.toFixed(2) + '% total');

    // Realized
    const wins = stats.wins || 0;
    const losses = stats.losses || 0;
    const total = stats.total || 0;
    _setKpi('realized', (retPct >= 0 ? '+' : '') + retPct.toFixed(2) + '%',
            retPct >= 0 ? 'kpi-pos' : 'kpi-neg',
            total + ' trades cerrados');

    // Floating (P&L flotante de abiertos)
    const openTrades = (trades && trades.open) || [];
    const nOpen = Array.isArray(openTrades) ? openTrades.length : Object.keys(openTrades).length;
    _setKpi('floating', (floatPct >= 0 ? '+' : '') + floatPct.toFixed(2) + '%',
            floatPct >= 0 ? 'kpi-pos' : 'kpi-neg',
            nOpen + ' posiciones abiertas');

    // Win rate
    const wr = stats.win_rate || 0;
    let wrClass = 'kpi-neutral';
    if (wr >= 60) wrClass = 'kpi-pos';
    else if (wr < 40 && total >= 5) wrClass = 'kpi-neg';
    else if (total >= 3) wrClass = 'kpi-warn';
    _setKpi('winrate', wr.toFixed(0) + '%', wrClass, wins + 'W  /  ' + losses + 'L');

    // Señales
    const models = (signals && signals.models) || [];
    const active = models.filter(m => m.signal).length;
    _setKpi('signals', active.toString(),
            active > 0 ? 'kpi-pos' : 'kpi-neutral',
            'de ' + models.length + ' modelos');

    // Régimen
    const regime = (signals && signals.regime) || '?';
    const regMap = {{'BULL':{{c:'kpi-pos',t:'🐂 BULL'}},'BEAR':{{c:'kpi-neg',t:'🐻 BEAR'}},'RANGE':{{c:'kpi-warn',t:'🦘 RANGE'}}}};
    const reg = regMap[regime] || {{c:'kpi-neutral',t:regime}};
    _setKpi('regime', reg.t, reg.c, regime === 'BULL' ? 'tendencia alcista' :
                                       regime === 'BEAR' ? 'tendencia bajista' :
                                       regime === 'RANGE' ? 'lateral' : 'cargando…');

    // Apalancamiento — exposición actual (notional total / equity) + max si todos slots se llenan
    try {{
      const opens = (trades && trades.open) || [];
      const eq = equityTotal || 10000;
      // Leverage actual: suma de notionals de trades abiertos / equity
      let notionalOpen = 0;
      opens.forEach(t => {{
        const slDistPct = t.sl_dist_pct_at_open || (t.entry>0 ? Math.abs(t.sl-t.entry)/t.entry*100 : 0);
        const k = t.kelly_pct || 3.3;
        if (slDistPct > 0) notionalOpen += eq * (k/slDistPct);
      }});
      const levNow = notionalOpen / eq;
      // Max leverage individual entre modelos ACTIVAR
      let levMax = 0;
      models.forEach(m => {{
        if (m.recommendation==='ACTIVAR' && m.size_factor_x) levMax = Math.max(levMax, m.size_factor_x);
      }});
      const levClass = levNow >= 4 ? 'kpi-neg' : levNow >= 2 ? 'kpi-warn' : levNow > 0 ? 'kpi-pos' : 'kpi-neutral';
      _setKpi('leverage',
              levNow > 0 ? levNow.toFixed(2)+'x' : '0x',
              levClass,
              opens.length>0 ? 'max indiv: '+levMax.toFixed(1)+'x' : 'sin posiciones');
    }} catch(e) {{ console.warn('leverage calc error',e); }}
  }} catch(e) {{
    console.error('KPI strip update error', e);
  }}
}}

async function loadTrades() {{
  try {{
    const res = await fetch('/api/trades');
    const d   = await res.json();
    const open = d.open || [];
    const cds  = d.cooldowns || [];
    const hist = d.history || [];
    const st   = d.stats || {{}};
    const port = d.portfolio || {{}};
    _openTradesCache = open;
    _portCache = port;

    const pnlCol  = (st.total_pnl||0) >= 0 ? '#00e676' : '#f44336';
    const wrCol   = (st.win_rate||0) >= 55 ? '#00c853' : (st.win_rate||0) >= 45 ? '#ff9800' : '#f44336';

    // Precios en vivo: /api/m2_prices para commodities, Binance para crypto
    const _COM_SYMS = new Set(['HG','WTI','XAU','XAG','NG','PL']);
    const _livePrices = {{}};
    try {{
      try {{ const _m2r=await fetch('/api/m2_prices'); const _m2j=await _m2r.json(); Object.assign(_livePrices,_m2j); }} catch(e) {{}}
      const pairs = [...new Set(open.filter(t=>!_COM_SYMS.has(t.sym)).map(t=>t.sym+'USDT'))];
      await Promise.all(pairs.map(async p => {{
        const r = await fetch(`https://api.binance.com/api/v3/ticker/price?symbol=${{p}}`);
        const j = await r.json();
        if(j.price) _livePrices[p.replace('USDT','')] = parseFloat(j.price);
      }}));
    }} catch(e) {{}}

    let floatPnlTotal = 0;

    // ── Filas trades abiertos
    let openRows = open.map(t => {{
      const isLong = t.direction!=='short';
      const dirC   = isLong?'#00e676':'#f85149';
      const cp     = _livePrices[t.sym] || t.entry;
      const raw    = isLong ? (cp-t.entry)/t.entry*100 : (t.entry-cp)/t.entry*100;
      const slDistPctF = t.sl_dist_pct_at_open || (t.entry>0 ? Math.abs(t.sl-t.entry)/t.entry*100 : 0);
      const kellyF     = t.kelly_pct || 3.3;
      const pnl        = slDistPctF > 0 ? raw * (kellyF/slDistPctF) : raw * (kellyF/100);
      const pnlCol = pnl >= 0 ? '#00e676' : '#f44336';
      floatPnlTotal += pnl;
      const slHit  = isLong?(cp<=t.sl):(cp>=t.sl);
      const tpHit  = isLong?(cp>=t.tp):(cp<=t.tp);
      const estado = slHit?'SL':tpHit?'TP':'';
      const rStyle = `background:rgba(${{isLong?'0,230,118':'248,81,73'}},0.05);border-left:3px solid ${{slHit?'#f44336':tpHit?'#00c853':dirC}}`;
      const rn = (t.sl && t.tp) ? Math.abs(t.tp-t.entry)/Math.abs(t.entry-t.sl) : 0;
      const rrC = rn>=2?'#00c853':rn>=1.5?'#69f0ae':'#ff9800';
      // Posicion nominal en USD: equity × (kelly% / sl_dist_pct_at_open)
      // EXCEPTO para trades LIVE con contracts reales conocidos -- ahi se usa
      // notional real (contracts x precio), no el equity simulado del paper
      // (antes mostraba miles de USD de margen para una posicion real de ~$60).
      const eq        = port.equity || 10000;
      const slDistPct = t.sl_dist_pct_at_open || (t.entry>0 ? Math.abs(t.sl-t.entry)/t.entry*100 : 0);
      const kelly     = t.kelly_pct || 3.3;
      const isLiveReal = t.mode === 'LIVE' && t.live_contracts;
      const notional  = isLiveReal ? t.live_contracts * cp
                       : (slDistPct>0 ? eq * (kelly/slDistPct) : 0);
      const riskUsd   = isLiveReal ? notional * slDistPct / 100 : eq * kelly / 100;
      const rewardUsd = (t.tp && t.entry) ? notional * Math.abs(t.tp-t.entry)/t.entry : 0;
      // Margen requerido (lo que sale del wallet) — asume leverage 5x del exchange
      const LEVERAGE_EXCHANGE = 5;
      const margenUsd = notional > 0 ? notional / LEVERAGE_EXCHANGE : 0;
      const notionalTxt = notional>0 ? '$'+Math.round(notional).toLocaleString() : '—';
      const margenTxt   = margenUsd>0 ? '$'+Math.round(margenUsd).toLocaleString() : '—';
      const realPrefix = isLiveReal ? 'REAL (Binance, ' + t.live_contracts + ' contratos). ' : '';
      const notionalTitle = realPrefix + `Posicion nominal (notional). Si SL toca: -$${{Math.round(riskUsd).toLocaleString()}} (-${{kelly}}%). Si TP toca: +$${{Math.round(rewardUsd).toLocaleString()}} (+${{rewardUsd>0?(rewardUsd/eq*100).toFixed(1):0}}%)`;
      const margenTitle   = realPrefix + `MARGEN — lo que sale de tu wallet con leverage ${{LEVERAGE_EXCHANGE}}x del exchange. Notional/$${{LEVERAGE_EXCHANGE}} = $${{Math.round(margenUsd).toLocaleString()}}. Si usas leverage distinto: con 10x serían $${{Math.round(notional/10).toLocaleString()}}, con 20x $${{Math.round(notional/20).toLocaleString()}}`;
      return `<tr style="${{rStyle}}">
        <td style="${{TD}}"><span style="background:${{dirC}};color:#000;padding:2px 8px;border-radius:4px;font-weight:bold;font-size:11px">${{isLong?'L':'S'}}</span></td>
        <td style="${{TD}}"><b style="color:#b8c5e0">${{t.sym}}</b></td>
        <td style="${{TD}};color:#4a5580">${{t.tf?.toUpperCase()}}</td>
        <td style="${{TD}};color:#7a8db5">${{stratShort(t.strategy||'')}}</td>
        <td style="${{TDC}}"><span style="background:${{gradeC(t.grade||'D')}};color:#000;padding:1px 7px;border-radius:8px;font-weight:bold;font-size:11px">${{t.grade||'?'}}</span></td>
        <td style="${{TDR}};font-family:monospace;color:#7a8db5">${{t.entry}}</td>
        <td style="${{TDR}};font-family:monospace;color:#dde3f5;font-weight:bold">${{cp.toFixed(2)}}</td>
        <td style="${{TDR}};font-family:monospace;color:#f85149">${{t.sl}}</td>
        <td style="${{TDR}};font-family:monospace;color:#00e676">${{t.tp}}</td>
        <td style="${{TDR}};color:${{rrC}};font-weight:bold">${{rn>0?rn.toFixed(1)+':1':'—'}}</td>
        <td style="${{TDR}};font-family:'IBM Plex Mono',monospace;color:${{isLiveReal?'#00bcd4':'#e0bb3a'}};font-weight:600" title="${{notionalTitle}}">${{notionalTxt}}${{isLiveReal?'<br><span style="font-size:9px;color:#00bcd4">REAL</span>':''}}</td>
        <td style="${{TDR}};font-family:'IBM Plex Mono',monospace;color:#00e676;font-weight:700;font-size:13px" title="${{margenTitle}}">${{margenTxt}}</td>
        <td style="${{TDR}};font-family:'IBM Plex Mono',monospace;color:#ff9800;font-weight:700;font-size:13px" title="Apalancamiento del trade: notional/margen = ${{LEVERAGE_EXCHANGE}}x (config Binance). Apalancamiento sobre equity total: ${{notional>0?(notional/eq).toFixed(2):0}}x">${{notional>0?LEVERAGE_EXCHANGE+'x':'—'}}</td>
        <td style="${{TDR}};color:${{pnlCol}};font-weight:bold;font-size:13px">${{pnl>=0?'+':''}}${{pnl.toFixed(2)}}%</td>
        <td style="${{TD}};color:#444;font-size:10px">${{t.opened_at?.substring(11,16)||''}}</td>
        <td style="${{TDC}}">
          ${{slHit||tpHit?`<span style="color:${{slHit?'#f44336':'#00c853'}};font-weight:bold;font-size:11px">${{estado}} DETECTADO</span>`:''}}
        </td>
      </tr>`;
    }}).join('');

    // Cooldowns como filas dimmed
    cds.forEach(c => {{
      const col = c.reason==='SL_HIT'?'#f44336':c.reason==='TP_HIT'?'#00c853':'#555';
      openRows += `<tr style="opacity:0.5;border-left:3px solid ${{col}}">
        <td style="${{TD}};color:${{col}}">⏸ COOLDOWN</td>
        <td style="${{TD}}"><b style="color:#666">${{c.sym}}</b></td>
        <td style="${{TD}};color:#444">${{c.tf?.toUpperCase()}}</td>
        <td style="${{TD}};color:#333">${{c.reason}}</td>
        <td colspan="9" style="${{TD}};color:#333">hasta ${{c.until?.substring(11,16)||'--'}}</td>
      </tr>`;
    }});

    // Historial
    window._histData = hist;  // pagination via renderHistTablePage()

    let html = `<div class="card-green" style="background:#07091c;border:1px solid #1a2240;border-radius:8px;padding:14px;margin:16px 0;overflow-x:auto">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <div style="display:flex;align-items:center;gap:8px">
          <span style="color:#b8c5e0;font-weight:700;font-size:14px">🤖 Simulación — Paper Trading Automático</span>
          <span style="color:#444;font-size:10px">
            <span style="display:inline-block;width:6px;height:6px;background:#00e676;border-radius:50%;animation:pulse 1.5s infinite;margin-right:3px"></span>
            precio vivo · actualiza en <span id="trade-counter">10s</span>
          </span>
        </div>
        <div style="display:flex;gap:20px;font-size:12px;align-items:center">
          <span style="color:#555;font-size:10px">FLOTANTE</span>
          <span id="float-pnl" style="font-weight:bold;font-size:14px">—</span>
          <span style="color:#333">│</span>
          <span style="color:#555;font-size:10px">CERRADOS</span>
          <span>P&L <b style="color:${{pnlCol}}">${{(st.total_pnl||0)>=0?'+':''}}${{(st.total_pnl||0).toFixed(2)}}%</b></span>
          <span>WR <b style="color:${{wrCol}}">${{(st.win_rate||0).toFixed(0)}}%</b> (${{st.wins||0}}W/${{st.losses||0}}L)</span>
          <span style="color:#444">${{st.total||0}} trades</span>
          ${{st.total>0?`<span style="color:#444">AvgW <span style="color:#00e676">+${{(st.avg_win||0).toFixed(1)}}%</span> AvgL <span style="color:#f44336">${{(st.avg_loss||0).toFixed(1)}}%</span></span>`:''}}
        </div>
      </div>

      <div style="display:flex;gap:16px;align-items:center;padding:6px 0;border-bottom:1px solid #141b38;margin-bottom:10px;font-size:11px;flex-wrap:wrap">
        <span style="color:#555;font-size:10px;text-transform:uppercase;letter-spacing:0.8px">Capital</span>
        <span id="capital-live" data-initial="${{port.initial||10000}}" data-base-equity="${{port.equity||port.initial||10000}}" style="font-family:'IBM Plex Mono',monospace;color:#dde3f5;font-weight:700;font-size:13px;transition:color .3s ease,text-shadow .3s ease">${{(port.float_equity||port.equity||10000).toLocaleString('en-US',{{minimumFractionDigits:2,maximumFractionDigits:2}})}}</span>
        <span style="color:${{(port.return_pct||0)>=0?'#00e676':'#f44336'}};font-weight:700">${{(port.return_pct||0)>=0?'+':''}}${{(port.return_pct||0).toFixed(2)}}%</span>
        ${{port.cagr_live!=null?`<span style="color:#333">|</span><span style="color:#7a8db5;font-size:10px">CAGR <b style="color:${{(port.cagr_live||0)>=20?'#00e676':'#ff9800'}}">${{(port.cagr_live||0)>=0?'+':''}}${{(port.cagr_live||0).toFixed(1)}}%/año</b></span>`:''}}
        ${{port.max_dd?`<span style="color:#333">|</span><span style="color:#7a8db5;font-size:10px">MaxDD <b style="color:#f44336">${{(port.max_dd||0).toFixed(1)}}%</b></span>`:''}}
        ${{port.sharpe!=null?`<span style="color:#333">|</span><span style="color:#7a8db5;font-size:10px">Sharpe <b style="color:#b8c5e0">${{(port.sharpe||0).toFixed(2)}}</b></span>`:''}}
        <span style="color:#333">|</span>
        <span style="color:#444;font-size:10px">inicial ${{(port.initial||10000).toLocaleString('en-US',{{minimumFractionDigits:0,maximumFractionDigits:0}})}}</span>
      </div>

      ${{open.length>0||cds.length>0?`
      <div style="color:#555;font-size:10px;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px">Posiciones abiertas</div>
      <table style="width:100%;border-collapse:collapse;font-size:12px;margin-bottom:12px">
        <thead><tr>
          <th style="${{TH}}">Dir</th><th style="${{TH}}">Activo</th><th style="${{TH}}">TF</th>
          <th style="${{TH}}">Estrategia</th><th style="${{THC}}">Grade</th>
          <th style="${{THR}}">Entrada</th><th style="${{THR}}">Live</th>
          <th style="${{THR}}">SL</th><th style="${{THR}}">TP</th>
          <th style="${{THR}}">RR</th><th style="${{THR}}" title="Posicion nominal en USD (equity x leverage implicito)">Posición $</th><th style="${{THR}}" title="MARGEN — lo que sale de tu wallet asumiendo leverage 5x del exchange">Margen $</th><th style="${{THR}}" title="Apalancamiento implicito = notional/equity">Lev</th><th style="${{THR}}">P&L live</th>
          <th style="${{THR}}">Hora</th>
        </tr></thead>
        <tbody>${{openRows}}</tbody>
      </table>`:'<p style="color:#333;font-size:12px;margin:6px 0 10px">Sin posiciones abiertas</p>'}}

      <div style="margin-top:10px;padding-top:8px;border-top:1px solid #141b38">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <span style="color:#555;font-size:10px;text-transform:uppercase;letter-spacing:0.8px">Curva de Equity</span>
          <span style="color:#444;font-size:10px">${{hist.length}} trades cerrados · flotante <span id="equity-float" style="font-weight:bold">—</span></span>
        </div>
        <div id="equity-wrap" style="position:relative">
          <canvas id="equity-curve" height="180" style="width:100%;display:block;border-radius:6px;background:#050914"></canvas>
          <div id="equity-tooltip" style="position:absolute;display:none;pointer-events:none;background:rgba(13,17,23,0.96);border:1px solid #242f55;border-radius:6px;padding:8px 10px;font-family:'IBM Plex Mono',monospace;font-size:11px;color:#dde3f5;box-shadow:0 4px 16px rgba(0,0,0,0.6);z-index:100;min-width:200px;max-width:280px"></div>
        </div>
      </div>
      ${{hist.length>0?`
      <div style="color:#555;font-size:10px;text-transform:uppercase;letter-spacing:0.8px;margin:10px 0 4px">Historial (${{hist.length}} trades)</div>
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead><tr>
          <th style="${{TH}}">Resultado</th><th style="${{TH}}">Activo</th><th style="${{TH}}">TF</th>
          <th style="${{TH}}">Estrategia</th><th style="${{THC}}">Grade</th>
          <th style="${{THR}}">Entrada</th><th style="${{THR}}">Salida</th>
          <th style="${{THR}}">P&L</th><th style="${{THR}}">Hora</th><th colspan="2"></th>
        </tr></thead>
        <tbody id="hist-tbody"></tbody>
      </table>`:'<p style="color:#333;font-size:12px;margin:6px 0">Sin historial aún — los trades cerrarán solos cuando toquen SL o TP</p>'}}
      <div id="hist-pagination" style="text-align:center"></div>
    </div>`;

    document.getElementById('trades-section').innerHTML = html;
    renderHistTablePage(window._histPage || 0);

    // ── Equity curve — animada en tiempo real (60fps, dashes que fluyen) ─────
    // Construir serie afuera de la animacion para no recalcularla cada frame
    let _cumPnl = 0;
    const _equityPoints = [{{v:0, label:'inicio', isStart:true}}];
    // hist viene newest-first de API; lo invertimos para iterar cronologicamente
    [...hist].reverse().forEach(t => {{
      _cumPnl += (t.pnl_pct||0);
      _equityPoints.push({{
        v: _cumPnl,
        label: t.sym+' '+t.tf,
        closed: t.pnl_pct>=0,
        trade: t  // datos completos del trade para el tooltip
      }});
    }});
    _equityPoints.push({{v: _cumPnl + floatPnlTotal, label:'ahora', floating:true}});

    // Guardar data en estado global; arrancar rAF si no esta corriendo
    window._eqAnimState = window._eqAnimState || {{
      pulsePhase: 0,
      dashOffset: 0,
      rafActive: false,
      data: null
    }};
    window._eqAnimState.data = {{
      points: _equityPoints,
      cumPnl: _cumPnl,
      floatNow: floatPnlTotal
    }};

    if (!window._eqAnimState.rafActive) {{
      window._eqAnimState.rafActive = true;
      requestAnimationFrame(_drawEquityFrame);
    }}

    // ── Tooltip handlers ─────────────────────────────────────────────────────
    (function() {{
      const canvas = document.getElementById('equity-curve');
      const tooltip = document.getElementById('equity-tooltip');
      if (!canvas || !tooltip) return;
      if (canvas._tooltipBound) return;  // no rebind en cada poll
      canvas._tooltipBound = true;

      canvas.addEventListener('mousemove', (e) => {{
        const s = window._eqAnimState;
        if (!s || !s.data) return;
        const pts = s.data.points;
        if (pts.length < 2) return;

        const rect = canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        const W = canvas.offsetWidth, H = 180;
        const vals = pts.map(p => p.v);
        const minV = Math.min(...vals, 0), maxV = Math.max(...vals, 0);
        const range = Math.max(maxV - minV, 2);
        const px = i => (i / (pts.length-1)) * (W-4) + 2;
        const py = v => H - 6 - ((v - minV) / range) * (H - 14);

        // Encontrar punto mas cercano dentro de 18px
        let best = -1, bestD = 18;
        for (let i = 0; i < pts.length; i++) {{
          const dx = px(i) - mx, dy = py(pts[i].v) - my;
          const d = Math.sqrt(dx*dx + dy*dy);
          if (d < bestD) {{ best = i; bestD = d; }}
        }}

        if (best < 0) {{ tooltip.style.display = 'none'; return; }}

        const p = pts[best];
        let html = '';
        if (p.isStart) {{
          html = '<div style="color:#4e5f90">Inicio</div><div style="color:#dde3f5;font-weight:700">Capital $10,000.00</div>';
        }} else if (p.floating) {{
          const fc = s.data.floatNow >= 0 ? '#00e676' : '#f44336';
          html = '<div style="color:#4e5f90;font-size:9px;text-transform:uppercase;letter-spacing:0.5px">P&amp;L flotante</div>'
               + '<div style="color:'+fc+';font-weight:700;font-size:14px">'+(s.data.floatNow>=0?'+':'')+s.data.floatNow.toFixed(2)+'%</div>'
               + '<div style="color:#7a8db5;font-size:10px;margin-top:3px">Acumulado: '+(p.v>=0?'+':'')+p.v.toFixed(2)+'%</div>'
               + '<div style="color:#4e5f90;font-size:9px;margin-top:4px">(posiciones abiertas, vivo)</div>';
        }} else if (p.trade) {{
          const t = p.trade;
          const isW = t.pnl_pct >= 0;
          const col = isW ? '#00e676' : '#f44336';
          const icon = isW ? '🟢' : '🔴';
          const reasonMap = {{'TP_HIT':'TP alcanzado','SL_HIT':'SL alcanzado','REGIME_CHANGE':'Cambio régimen','MANUAL':'Cierre manual','CERRADO_MANUAL':'Cierre manual'}};
          const reason = reasonMap[t.reason] || t.reason || '?';
          let dur = '';
          try {{
            if (t.opened_at && t.closed_at) {{
              const ms = new Date(t.closed_at) - new Date(t.opened_at);
              const mins = Math.floor(ms / 60000);
              if (mins < 60) dur = mins + 'min';
              else if (mins < 1440) dur = Math.floor(mins/60) + 'h ' + (mins%60) + 'min';
              else dur = Math.floor(mins/1440) + 'd';
            }}
          }} catch(_e) {{}}
          const dir = t.direction === 'short' ? '▼ SHORT' : '▲ LONG';
          html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
                 + '<span style="color:#dde3f5;font-weight:700">'+icon+' '+(t.sym||'?')+' '+(t.tf||'').toUpperCase()+'</span>'
                 + '<span style="color:'+col+';font-weight:700;font-size:13px">'+(isW?'+':'')+(t.pnl_pct||0).toFixed(2)+'%</span>'
                 + '</div>'
                 + '<div style="color:#7a8db5;font-size:10px;margin-bottom:5px">'+dir+' · '+(t.strategy||'?')+' · Grade '+(t.grade||'?')+'</div>'
                 + '<div style="font-size:10px;color:#7a8db5;line-height:1.5">'
                 + 'Entrada: <span style="color:#dde3f5">'+(t.entry||'?')+'</span>'
                 + ' → Salida: <span style="color:#dde3f5">'+(t.exit_price||'?')+'</span><br>'
                 + 'Razón: <span style="color:'+col+'">'+reason+'</span>'
                 + (dur ? ' · Duración: <span style="color:#dde3f5">'+dur+'</span>' : '')
                 + '</div>'
                 + '<div style="color:#4e5f90;font-size:9px;margin-top:5px">Acumulado: '+(p.v>=0?'+':'')+p.v.toFixed(2)+'%</div>';
        }}

        tooltip.innerHTML = html;
        // Posicionar (preferir derecha del punto, si se sale → izquierda)
        const px2 = px(best), py2 = py(p.v);
        tooltip.style.display = 'block';
        const tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
        let tx = px2 + 14, ty = py2 - th/2;
        if (tx + tw > W) tx = px2 - tw - 14;
        if (ty < 0) ty = 6;
        if (ty + th > H) ty = H - th - 6;
        tooltip.style.left = tx + 'px';
        tooltip.style.top = ty + 'px';
      }});

      canvas.addEventListener('mouseleave', () => {{
        tooltip.style.display = 'none';
      }});
    }})();

    // Actualizar P&L flotante en el label HTML
    const _flLabel = document.getElementById('equity-float');
    if(_flLabel) {{
      const _fc = floatPnlTotal>=0?'#00e676':'#f44336';
      _flLabel.style.color = _fc;
      _flLabel.textContent = (floatPnlTotal>=0?'+':'')+floatPnlTotal.toFixed(2)+'%';
    }}
    // Skip rest of original IIFE — todo se hace en _drawEquityFrame
    (function() {{
      // Placeholder vacio — el draw real ocurre en el rAF loop
      return;
      // Codigo original deshabilitado abajo:
      const canvas = document.getElementById('equity-curve');
      if(!canvas) return;
      const dpr = window.devicePixelRatio || 1;
      const W = canvas.offsetWidth || 600; const H = 180;
      canvas.width = W * dpr; canvas.height = H * dpr;
      const ctx = canvas.getContext('2d');
      ctx.scale(dpr, dpr);

      // Serie: trades cerrados + punto flotante actual
      let cumPnl = 0;
      const points = [{{v:0, label:'inicio'}}];
      [...hist].forEach(t => {{
        cumPnl += (t.pnl_pct||0);
        points.push({{v:cumPnl, label:t.sym+' '+t.tf, closed:t.pnl_pct>=0}});
      }});
      // Añadir punto flotante
      const floatNow = floatPnlTotal;
      points.push({{v: cumPnl + floatNow, label:'ahora', floating:true}});

      const vals = points.map(p => p.v);
      const minV = Math.min(...vals, 0); const maxV = Math.max(...vals, 0);
      const range = Math.max(maxV - minV, 2);
      const px = i => (points.length < 2) ? W/2 : (i / (points.length-1)) * (W-4) + 2;
      const py = v => H - 6 - ((v - minV) / range) * (H - 14);

      ctx.clearRect(0, 0, W, H);

      // Grid líneas horizontales
      ctx.strokeStyle = '#1a1f2a'; ctx.lineWidth = 1;
      [-10,-5,0,5,10,15,20,30,40,50].forEach(v => {{
        if(v < minV-2 || v > maxV+2) return;
        const yy = py(v);
        ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(W, yy); ctx.stroke();
        ctx.fillStyle = '#3a4050'; ctx.font = '11px monospace';
        ctx.fillText((v>=0?'+':'')+v+'%', 4, yy-2);
      }});

      // Línea cero destacada
      ctx.strokeStyle = '#1a2240'; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(0, py(0)); ctx.lineTo(W, py(0)); ctx.stroke();

      // Helper: Catmull-Rom -> cubic bezier (curva suave que pasa por todos los puntos)
      function _smoothCurve(ctx, pts, closeArea) {{
        if (pts.length < 2) return;
        ctx.moveTo(pts[0].x, pts[0].y);
        if (pts.length === 2) {{
          ctx.lineTo(pts[1].x, pts[1].y);
          return;
        }}
        for (let i = 0; i < pts.length - 1; i++) {{
          const p0 = pts[Math.max(0, i-1)];
          const p1 = pts[i];
          const p2 = pts[i+1];
          const p3 = pts[Math.min(pts.length-1, i+2)];
          const cp1x = p1.x + (p2.x - p0.x) / 6;
          const cp1y = p1.y + (p2.y - p0.y) / 6;
          const cp2x = p2.x - (p3.x - p1.x) / 6;
          const cp2y = p2.y - (p3.y - p1.y) / 6;
          ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p2.x, p2.y);
        }}
      }}

      if(points.length >= 2) {{
        const lastClosed = cumPnl + floatNow;
        const lineCol = lastClosed >= 0 ? '#00e676' : '#f44336';
        const glowCol = lastClosed >= 0 ? 'rgba(0,230,118,0.55)' : 'rgba(244,67,54,0.55)';

        // Puntos para la curva (incluye flotante - lo dibujamos suave hasta el ultimo cerrado)
        const closedPts = points.slice(0, -1).map((p, i) => ({{x: px(i), y: py(p.v)}}));
        const allPts = points.map((p, i) => ({{x: px(i), y: py(p.v)}}));

        // ── Área rellena con gradiente suave ─────────────────────────────
        const grad = ctx.createLinearGradient(0, 0, 0, H);
        grad.addColorStop(0, lastClosed>=0?'rgba(0,230,118,0.28)':'rgba(244,67,54,0.28)');
        grad.addColorStop(0.6, lastClosed>=0?'rgba(0,230,118,0.05)':'rgba(244,67,54,0.05)');
        grad.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        _smoothCurve(ctx, closedPts);
        // Continuar al ultimo punto (flotante) con linea recta para cerrar el area
        const lastP = allPts[allPts.length - 1];
        ctx.lineTo(lastP.x, lastP.y);
        ctx.lineTo(lastP.x, H);
        ctx.lineTo(closedPts[0].x, H);
        ctx.closePath();
        ctx.fill();

        // ── Linea principal: trades cerrados con curva suave + glow ──────
        ctx.shadowColor = glowCol;
        ctx.shadowBlur = 8;
        ctx.strokeStyle = lineCol;
        ctx.lineWidth = 2.5;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.setLineDash([]);
        ctx.beginPath();
        _smoothCurve(ctx, closedPts);
        ctx.stroke();
        ctx.shadowBlur = 0; // reset glow

        // ── Linea flotante (punteada al final, gris) ─────────────────────
        ctx.setLineDash([5,4]);
        ctx.strokeStyle = '#7d8590';
        ctx.lineWidth = 1.5;
        const lastClosedPt = closedPts[closedPts.length - 1];
        ctx.beginPath();
        ctx.moveTo(lastClosedPt.x, lastClosedPt.y);
        ctx.lineTo(lastP.x, lastP.y);
        ctx.stroke();
        ctx.setLineDash([]);

        // ── Puntos de trades cerrados con halo ───────────────────────────
        points.slice(1,-1).forEach((p,i) => {{
          const x = px(i+1), y = py(p.v);
          const col = p.closed ? '#00e676' : '#f44336';
          // Halo translucido
          ctx.fillStyle = p.closed ? 'rgba(0,230,118,0.35)' : 'rgba(244,67,54,0.35)';
          ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI*2); ctx.fill();
          // Punto central nitido con borde
          ctx.fillStyle = col;
          ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI*2); ctx.fill();
          ctx.strokeStyle = '#050914';
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }});

        // ── Punto final (flotante) con halo grande pulsante ──────────────
        const lpX = px(points.length-1), lpY = py(lastP.v);
        ctx.fillStyle = lastClosed>=0?'rgba(0,230,118,0.25)':'rgba(244,67,54,0.25)';
        ctx.beginPath(); ctx.arc(lpX, lpY, 12, 0, Math.PI*2); ctx.fill();
        ctx.fillStyle = lastClosed>=0?'rgba(0,230,118,0.5)':'rgba(244,67,54,0.5)';
        ctx.beginPath(); ctx.arc(lpX, lpY, 7, 0, Math.PI*2); ctx.fill();
        ctx.fillStyle = lineCol;
        ctx.beginPath(); ctx.arc(lpX, lpY, 4, 0, Math.PI*2); ctx.fill();
        ctx.strokeStyle = '#050914';
        ctx.lineWidth = 2;
        ctx.stroke();

        // ── Label P&L con fondo translucido ──────────────────────────────
        const label = (lastClosed>=0?'+':'')+lastClosed.toFixed(2)+'%';
        ctx.font = 'bold 14px "IBM Plex Mono", monospace';
        const labelW = ctx.measureText(label).width + 12;
        let lx = lpX + 14;
        if (lx + labelW > W) lx = lpX - labelW - 8;
        const ly = lpY - 8;
        ctx.fillStyle = 'rgba(13,17,23,0.85)';
        ctx.fillRect(lx-4, ly-10, labelW, 18);
        ctx.strokeStyle = lineCol;
        ctx.lineWidth = 1;
        ctx.strokeRect(lx-4, ly-10, labelW, 18);
        ctx.fillStyle = lineCol;
        ctx.fillText(label, lx, ly+4);
      }} else {{
        // Sin datos aún
        ctx.fillStyle = '#2a3040'; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
        ctx.fillText('Esperando primer trade cerrado...', W/2, H/2);
        ctx.textAlign = 'left';
      }}

      // Actualizar label flotante
      const fl = document.getElementById('equity-float');
      if(fl) {{
        const fc = floatNow>=0?'#00e676':'#f44336';
        fl.style.color = fc;
        fl.textContent = (floatNow>=0?'+':'')+floatNow.toFixed(2)+'%';
      }}
    }})();

    // Actualizar P&L flotante en header
    const fpEl = document.getElementById('float-pnl');
    if(fpEl) {{
      const fCol = floatPnlTotal>=0?'#00e676':'#f44336';
      fpEl.style.color = fCol;
      fpEl.textContent = (floatPnlTotal>=0?'+':'') + floatPnlTotal.toFixed(2) + '%';
    }}

    // Actualizar Command Center KPI strip + Risk Panel
    try {{
      const sigs = await fetch('/api/signals').then(r => r.json()).catch(()=>null);
      _updateKpiStrip(d, sigs, floatPnlTotal);
      _updateRiskPanel(d);
    }} catch(_e) {{}}
  }} catch(e) {{
    document.getElementById('trades-section').innerHTML =
      `<div style="background:#1a0a0a;border:1px solid #f44336;border-radius:6px;padding:10px;margin:16px 0;font-family:monospace;font-size:11px;color:#f85149">
        Error panel trades: ${{e.message}}<br><small style="color:#555">${{e.stack?.substring(0,200)}}</small>
      </div>`;
  }}
}}

// ── Equity curve animation loop ──────────────────────────────────────────
function _drawEquityFrame() {{
  const s = window._eqAnimState;
  if (!s || !s.data) {{ if(s) s.rafActive = false; return; }}
  const canvas = document.getElementById('equity-curve');
  if (!canvas) {{ s.rafActive = false; return; }}

  const dpr = window.devicePixelRatio || 1;
  const W = canvas.offsetWidth || 600; const H = 180;
  if (canvas.width !== W * dpr || canvas.height !== H * dpr) {{
    canvas.width = W * dpr; canvas.height = H * dpr;
  }}
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  // Avanzar fases de animacion (60fps)
  s.pulsePhase = (s.pulsePhase + 0.05) % (Math.PI * 2);
  s.dashOffset = (s.dashOffset - 0.9);

  const points = s.data.points;
  const floatNow = s.data.floatNow;
  const cumPnl = s.data.cumPnl;
  const vals = points.map(p => p.v);
  const minV = Math.min(...vals, 0); const maxV = Math.max(...vals, 0);
  const range = Math.max(maxV - minV, 2);
  const px = i => (points.length < 2) ? W/2 : (i / (points.length-1)) * (W-4) + 2;
  const py = v => H - 6 - ((v - minV) / range) * (H - 14);

  // Grid horizontal (excluyendo el 0 que se dibuja aparte)
  ctx.strokeStyle = '#1a1f2a'; ctx.lineWidth = 1;
  [-10,-5,5,10,15,20,30,40,50].forEach(v => {{
    if(v < minV-2 || v > maxV+2) return;
    const yy = py(v);
    ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(W, yy); ctx.stroke();
    ctx.fillStyle = '#3a4050'; ctx.font = '11px monospace';
    ctx.fillText((v>=0?'+':'')+v+'%', 4, yy-2);
  }});

  // Linea de referencia $10,000 (breakeven) muy sutil al fondo
  const breakEvenY = py(0);
  ctx.strokeStyle = 'rgba(100,110,130,0.18)';
  ctx.lineWidth = 1;
  ctx.setLineDash([2, 8]);
  ctx.beginPath(); ctx.moveTo(0, breakEvenY); ctx.lineTo(W, breakEvenY); ctx.stroke();
  ctx.setLineDash([]);

  // Helper bezier suave
  function smoothCurve(ctx, pts) {{
    if (pts.length < 2) return;
    ctx.moveTo(pts[0].x, pts[0].y);
    if (pts.length === 2) {{ ctx.lineTo(pts[1].x, pts[1].y); return; }}
    for (let i = 0; i < pts.length - 1; i++) {{
      const p0 = pts[Math.max(0, i-1)];
      const p1 = pts[i];
      const p2 = pts[i+1];
      const p3 = pts[Math.min(pts.length-1, i+2)];
      const cp1x = p1.x + (p2.x - p0.x) / 6;
      const cp1y = p1.y + (p2.y - p0.y) / 6;
      const cp2x = p2.x - (p3.x - p1.x) / 6;
      const cp2y = p2.y - (p3.y - p1.y) / 6;
      ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p2.x, p2.y);
    }}
  }}

  if (points.length >= 2) {{
    const lastClosed = cumPnl + floatNow;
    const lineCol = lastClosed >= 0 ? '#00e676' : '#f44336';
    const glowCol = lastClosed >= 0 ? 'rgba(0,230,118,0.55)' : 'rgba(244,67,54,0.55)';
    const closedPts = points.slice(0, -1).map((p, i) => ({{x: px(i), y: py(p.v)}}));
    const lastP = {{x: px(points.length-1), y: py(points[points.length-1].v)}};

    // Wobble sutil del punto flotante (oscila +-1.5px en Y como respiracion)
    const wobble = Math.sin(s.pulsePhase) * 1.5;
    lastP.y += wobble;

    // Area rellena con gradiente
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, lastClosed>=0?'rgba(0,230,118,0.28)':'rgba(244,67,54,0.28)');
    grad.addColorStop(0.6, lastClosed>=0?'rgba(0,230,118,0.05)':'rgba(244,67,54,0.05)');
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = grad;
    ctx.beginPath();
    smoothCurve(ctx, closedPts);
    ctx.lineTo(lastP.x, lastP.y);
    ctx.lineTo(lastP.x, H);
    ctx.lineTo(closedPts[0].x, H);
    ctx.closePath();
    ctx.fill();

    // ── Linea principal: dashes fluyen como serpiente a lo largo de TODA la linea
    // Capa 1: linea solida base (suave) — para que se vea siempre la curva
    ctx.shadowColor = glowCol;
    ctx.shadowBlur = 8;
    ctx.strokeStyle = lineCol;
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.globalAlpha = 0.4;
    ctx.setLineDash([]);
    ctx.beginPath();
    smoothCurve(ctx, closedPts);
    ctx.stroke();
    // Capa 2: dashes brillantes que FLUYEN por encima (efecto serpiente)
    ctx.globalAlpha = 1.0;
    ctx.lineWidth = 2.8;
    ctx.setLineDash([14, 10]);
    ctx.lineDashOffset = s.dashOffset;
    ctx.beginPath();
    smoothCurve(ctx, closedPts);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.lineDashOffset = 0;
    ctx.shadowBlur = 0;

    // ── Linea flotante: dashes que FLUYEN como serpiente ──────────────
    ctx.setLineDash([6, 5]);
    ctx.lineDashOffset = s.dashOffset;
    ctx.strokeStyle = lineCol;
    ctx.lineWidth = 2;
    ctx.shadowColor = glowCol;
    ctx.shadowBlur = 6;
    const lastClosedPt = closedPts[closedPts.length - 1];
    ctx.beginPath();
    ctx.moveTo(lastClosedPt.x, lastClosedPt.y);
    ctx.lineTo(lastP.x, lastP.y);
    ctx.stroke();
    ctx.shadowBlur = 0;
    ctx.setLineDash([]);
    ctx.lineDashOffset = 0;

    // Puntos de trades cerrados
    points.slice(1,-1).forEach((p,i) => {{
      const x = px(i+1), y = py(p.v);
      const col = p.closed ? '#00e676' : '#f44336';
      ctx.fillStyle = p.closed ? 'rgba(0,230,118,0.35)' : 'rgba(244,67,54,0.35)';
      ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI*2); ctx.fill();
      ctx.fillStyle = col;
      ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI*2); ctx.fill();
      ctx.strokeStyle = '#050914';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }});



    // ── Punto final PULSANTE (halo cambia de tamaño con pulsePhase) ────
    const pulseAmp = (Math.sin(s.pulsePhase) + 1) / 2; // 0..1
    const haloOuter = 10 + pulseAmp * 6;   // 10..16
    const haloMid   = 6 + pulseAmp * 2;    // 6..8
    const haloOpacity = 0.20 + pulseAmp * 0.30; // 0.20..0.50

    ctx.fillStyle = lastClosed>=0?`rgba(0,230,118,${{haloOpacity*0.5}})`:`rgba(244,67,54,${{haloOpacity*0.5}})`;
    ctx.beginPath(); ctx.arc(lastP.x, lastP.y, haloOuter, 0, Math.PI*2); ctx.fill();
    ctx.fillStyle = lastClosed>=0?`rgba(0,230,118,${{haloOpacity}})`:`rgba(244,67,54,${{haloOpacity}})`;
    ctx.beginPath(); ctx.arc(lastP.x, lastP.y, haloMid, 0, Math.PI*2); ctx.fill();
    ctx.fillStyle = lineCol;
    ctx.beginPath(); ctx.arc(lastP.x, lastP.y, 4, 0, Math.PI*2); ctx.fill();
    ctx.strokeStyle = '#050914';
    ctx.lineWidth = 2;
    ctx.stroke();

    // ── Legend box ejecutivo top-right ───────────────────────────────────
    const realizedPct = cumPnl;
    const floatTotalPct = lastClosed;
    const realizedDol = 10000 * (1 + realizedPct/100);
    const floatDol = 10000 * (1 + floatTotalPct/100);
    const realizedCol = realizedPct >= 0 ? '#f1c40f' : '#e67e22';
    const fmtD = v => v.toFixed(0).replace(/\B(?=(\d{{3}})+(?!\d))/g, ',');
    const realizedStr = '$' + fmtD(realizedDol) + '   ' + (realizedPct>=0?'+':'') + realizedPct.toFixed(2) + '%';
    const floatStr    = '$' + fmtD(floatDol)    + '   ' + (floatTotalPct>=0?'+':'') + floatTotalPct.toFixed(2) + '%';
    const r1Label = 'CAPITAL REALIZADO';
    const r2Label = 'VALOR FLOTANTE';

    ctx.font = 'bold 9px "IBM Plex Mono", monospace';
    const tw1 = ctx.measureText(r1Label).width;
    const tw2 = ctx.measureText(r2Label).width;
    ctx.font = 'bold 11px "IBM Plex Mono", monospace';
    const tw3 = ctx.measureText(realizedStr).width;
    const tw4 = ctx.measureText(floatStr).width;
    const colA = Math.max(tw1, tw2);
    const colB = Math.max(tw3, tw4);
    const legW = 20 + colA + 18 + colB + 12;  // line+label+gap+value+pad
    const legH = 46;
    const legX = W - legW - 10;
    const legY = 8;

    // Caja con sombra y borde sutil
    ctx.shadowColor = 'rgba(0,0,0,0.55)';
    ctx.shadowBlur = 10;
    ctx.fillStyle = 'rgba(13,17,23,0.92)';
    ctx.fillRect(legX, legY, legW, legH);
    ctx.shadowBlur = 0;
    ctx.strokeStyle = 'rgba(80,90,110,0.5)';
    ctx.lineWidth = 1;
    ctx.strokeRect(legX, legY, legW, legH);

    // Fila 1: Capital realizado (linea solida amarilla)
    const r1Y = legY + 16;
    ctx.strokeStyle = realizedCol;
    ctx.lineWidth = 2.5;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(legX + 8, r1Y - 3);
    ctx.lineTo(legX + 20, r1Y - 3);
    ctx.stroke();
    // Label
    ctx.font = 'bold 9px "IBM Plex Mono", monospace';
    ctx.fillStyle = '#7a8db5';
    ctx.fillText(r1Label, legX + 24, r1Y);
    // Value
    ctx.font = 'bold 11px "IBM Plex Mono", monospace';
    ctx.fillStyle = realizedCol;
    ctx.textAlign = 'right';
    ctx.fillText(realizedStr, legX + legW - 8, r1Y);
    ctx.textAlign = 'left';

    // Fila 2: Valor flotante (linea dashed verde/rojo)
    const r2Y = legY + 36;
    ctx.strokeStyle = lineCol;
    ctx.lineWidth = 2.5;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(legX + 8, r2Y - 3);
    ctx.lineTo(legX + 20, r2Y - 3);
    ctx.stroke();
    ctx.setLineDash([]);
    // Label
    ctx.font = 'bold 9px "IBM Plex Mono", monospace';
    ctx.fillStyle = '#7a8db5';
    ctx.fillText(r2Label, legX + 24, r2Y);
    // Value
    ctx.font = 'bold 11px "IBM Plex Mono", monospace';
    ctx.fillStyle = lineCol;
    ctx.textAlign = 'right';
    ctx.fillText(floatStr, legX + legW - 8, r2Y);
    ctx.textAlign = 'left';
  }} else {{
    ctx.fillStyle = '#2a3040'; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText('Esperando primer trade cerrado...', W/2, H/2);
    ctx.textAlign = 'left';
  }}

  // Continuar loop
  if (s.rafActive) requestAnimationFrame(_drawEquityFrame);
}}

async function closeTrade(sym, tf, reason) {{
  const price = parseFloat(prompt(`Precio de cierre para ${{sym}} ${{tf}} (${{reason}})?`) || 0);
  if(!price) return;
  await fetch('/api/trades/close', {{method:'POST', body: JSON.stringify({{sym,tf,exit_price:price,reason}})}});
  loadTrades(); loadSignals();
}}

async function openTrade(sym, tf, direction, entry, sl, tp, strategy) {{
  await fetch('/api/trades/open', {{method:'POST', body: JSON.stringify({{sym,tf,direction,entry,sl,tp,strategy}})}});
  loadTrades();
}}

loadTrades();

// Precio en vivo: actualizar cada 10 segundos
let _tradeCountdown = 10;
function _updateTradeTimer() {{
  _tradeCountdown--;
  if(_tradeCountdown <= 0) {{
    _tradeCountdown = 10;
    loadTrades();
    return;
  }}
  const el = document.getElementById('trade-counter');
  if(el) el.textContent = `${{_tradeCountdown}}s`;
}}
setInterval(_updateTradeTimer, 1000);

// ── CAPITAL FLOTANTE — refresh cada 2.5s con precios de Binance ──────────────
async function _refreshFloatingCapital() {{
  try {{
    const open = _openTradesCache;
    const port = _portCache || {{}};
    const capEl = document.getElementById('capital-live');
    if(!capEl) return;

    const baseEquity = parseFloat(capEl.dataset.baseEquity) || 10000;
    let floatPnlPct = 0;

    if(open.length > 0) {{
      const _COM_SYMS2 = new Set(['HG','WTI','XAU','XAG','NG','PL']);
      const pairs = [...new Set(open.filter(t=>!_COM_SYMS2.has(t.sym)).map(t=>t.sym+'USDT'))];
      const prices = {{}};
      try {{ const _m2r2=await fetch('/api/m2_prices'); Object.assign(prices,await _m2r2.json()); }} catch(e) {{}}
      await Promise.all(pairs.map(async p => {{
        try {{
          const r = await fetch(`https://api.binance.com/api/v3/ticker/price?symbol=${{p}}`);
          const j = await r.json();
          if(j.price) prices[p.replace('USDT','')] = parseFloat(j.price);
        }} catch(e) {{}}
      }}));
      open.forEach(t => {{
        const cp = prices[t.sym] || t.entry;
        const isLong = t.direction !== 'short';
        const pnl = isLong ? (cp-t.entry)/t.entry*100*2 : (t.entry-cp)/t.entry*100*2;
        floatPnlPct += pnl;
      }});
    }}

    const liveEquity = baseEquity * (1 + floatPnlPct/100);
    const newVal = liveEquity.toLocaleString('en-US',{{minimumFractionDigits:2,maximumFractionDigits:2}});

    if(_lastCapitalVal !== null && _lastCapitalVal !== newVal) {{
      const up = liveEquity >= (parseFloat(_lastCapitalVal.replace(/,/g,''))||liveEquity);
      capEl.style.color = up ? '#00e676' : '#f44336';
      capEl.style.textShadow = up ? '0 0 12px #00e67660' : '0 0 12px #f4433660';
      setTimeout(() => {{ capEl.style.color='#dde3f5'; capEl.style.textShadow='none'; }}, 600);
    }}
    capEl.textContent = newVal;
    _lastCapitalVal = newVal;

    // sync con float-pnl y equity-float
    const fp = document.getElementById('float-pnl');
    if(fp) {{
      fp.style.color = floatPnlPct >= 0 ? '#00e676' : '#f44336';
      fp.textContent = (floatPnlPct>=0?'+':'') + floatPnlPct.toFixed(2) + '%';
    }}
    const eqFloat = document.getElementById('equity-float');
    if(eqFloat) {{
      eqFloat.style.color = floatPnlPct >= 0 ? '#00e676' : '#f44336';
      eqFloat.textContent = (floatPnlPct>=0?'+':'') + floatPnlPct.toFixed(2) + '%';
    }}
  }} catch(e) {{}}
}}
setInterval(_refreshFloatingCapital, 2500);

// ═══════════════════════════════════════════════════════════
// NOTIFICACIONES — bell panel + badge + toast
// ═══════════════════════════════════════════════════════════
let _seenNotifIds = JSON.parse(localStorage.getItem('sigma_seen_notifs') || '[]');
let _lastNotifEvents = [];

function notifId(e) {{ return (e.ts||'') + '|' + (e.type||'') + '|' + (e.sym||'') + '_' + (e.tf||'') + '_' + (e.strategy||''); }}

function showToast(e) {{
  const c = document.getElementById('toast-container');
  if (!c) return;
  const isShort = (e.direction||'') === 'short';
  const arrow = isShort ? '▼' : '▲';
  const color = isShort ? '#f85149' : '#00e676';
  const t = document.createElement('div');
  t.style.cssText = `background:linear-gradient(180deg,#0d1428,#0a1020);border:1px solid #242f55;border-left:3px solid ${{color}};border-radius:10px;padding:12px 16px;min-width:300px;max-width:400px;box-shadow:0 8px 24px rgba(0,0,0,.6);pointer-events:auto;font-family:'Inter',sans-serif;color:#dde3f5;animation:slideIn .3s ease`;
  t.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
    <div>
      <div style="font-size:11px;font-weight:700;color:#e0bb3a;letter-spacing:.05em;text-transform:uppercase;margin-bottom:4px">📊 Per-Model Trade</div>
      <div style="font-size:14px;font-weight:700;color:#b8c5e0">${{arrow}} <b>${{e.sym}}</b> ${{(e.tf||'').toUpperCase()}} ${{isShort?'SHORT':'LONG'}}</div>
      <div style="font-size:11px;color:#7a8db5;margin-top:2px">${{e.strategy||''}} [${{e.grade||'?'}}]</div>
      
      <div style="font-size:10px;color:#4e5f90;margin-top:2px">RR ${{e.rr}}:1 · Kelly ${{e.kelly_pct}}%</div>
    </div>
    <button onclick="this.parentElement.parentElement.remove()" style="background:none;border:none;color:#4e5f90;cursor:pointer;font-size:16px;line-height:1;padding:0">×</button>
  </div>`;
  c.appendChild(t);
  setTimeout(() => {{ try {{ t.style.opacity = '0'; t.style.transition = 'opacity .4s'; setTimeout(() => t.remove(), 400); }} catch(e){{}} }}, 8000);
}}

function renderBellList(events) {{
  const list = document.getElementById('bell-list');
  if (!list) return;
  if (!events.length) {{ list.innerHTML = '<div style="color:#4e5f90;font-size:11px;text-align:center;padding:14px">Sin eventos aun</div>'; return; }}
  let html = '';
  for (const e of events.slice(0, 30)) {{
    const id = notifId(e);
    const unread = !_seenNotifIds.includes(id);
    const isShort = (e.direction||'') === 'short';
    const arrow = isShort ? '▼' : '▲';
    const color = isShort ? '#f85149' : '#00e676';
    const bg = unread ? '#0d1428' : 'transparent';
    html += `<div style="padding:8px 10px;border-radius:6px;background:${{bg}};border-left:2px solid ${{color}};font-size:11px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="color:#b8c5e0;font-weight:700">${{arrow}} ${{e.sym}} ${{(e.tf||'').toUpperCase()}} ${{e.strategy||''}}</span>
        <span style="color:#4e5f90;font-size:9px;font-family:'IBM Plex Mono',monospace">${{(e.ts||'').substring(11,16)}}</span>
      </div>
      <div style="color:#7a8db5;font-size:10px;margin-top:2px">[${{e.grade||'?'}}] Entry <b>${{e.entry}}</b> · Kelly ${{e.kelly_pct}}% · RR ${{e.rr}}:1</div>
      <div style="font-size:10px;color:#4e5f90;margin-top:5px;font-family:'IBM Plex Mono',monospace">SL <b>${{e.sl||'--'}}</b> &bull; TP <b>${{e.tp||'--'}}</b></div>
    </div>`;
  }}
  list.innerHTML = html;
}}

function updateBellBadge(events) {{
  const badge = document.getElementById('bell-badge');
  if (!badge) return;
  const unread = events.filter(e => !_seenNotifIds.includes(notifId(e))).length;
  if (unread > 0) {{ badge.textContent = unread > 99 ? '99+' : unread; badge.style.display = 'inline-block'; }}
  else {{ badge.style.display = 'none'; }}
}}

function toggleBell() {{
  const p = document.getElementById('bell-panel');
  if (!p) return;
  p.style.display = p.style.display === 'none' ? 'block' : 'none';
}}

function markAllRead() {{
  _seenNotifIds = _lastNotifEvents.map(notifId);
  localStorage.setItem('sigma_seen_notifs', JSON.stringify(_seenNotifIds.slice(-200)));
  updateBellBadge(_lastNotifEvents);
  renderBellList(_lastNotifEvents);
}}

async function pollNotifications() {{
  try {{
    const r = await fetch('/api/notifications');
    const d = await r.json();
    const events = d.events || [];
    // Detectar nuevos eventos (no vistos) y mostrar toast
    const newOnes = events.filter(e => !_seenNotifIds.includes(notifId(e)));
    const isInitial = _lastNotifEvents.length === 0;
    if (!isInitial) {{
      // Solo toast para los NUEVOS desde la ultima vista (no en la primera carga)
      const prevIds = new Set(_lastNotifEvents.map(notifId));
      for (const e of events) {{
        if (!prevIds.has(notifId(e))) showToast(e);
      }}
    }}
    _lastNotifEvents = events;
    updateBellBadge(events);
    renderBellList(events);
  }} catch(e) {{}}
}}

// CSS animation
const _animStyle = document.createElement('style');
_animStyle.textContent = '@keyframes slideIn {{ from {{ transform: translateX(100%); opacity: 0 }} to {{ transform: translateX(0); opacity: 1 }} }}';
document.head.appendChild(_animStyle);

pollNotifications();
setInterval(pollNotifications, 15000);

async function _checkCacheFreshness() {{
  try {{
    const r = await fetch('/api/signals');
    const d = await r.json();
    const stale = d.cache_stale === true;
    const age = d.cache_age_s;
    const banner = document.getElementById('cache-stale-banner');
    const msg = document.getElementById('cache-stale-msg');
    if (stale && banner) {{
      banner.style.display = 'block';
      msg.textContent = 'Cache de señales desactualizado (' + (age ? age.toFixed(0) + 's' : '?') + ')';
      document.body.style.paddingTop = '40px';
    }} else if (banner) {{
      banner.style.display = 'none';
      document.body.style.paddingTop = '';
    }}
  }} catch (e) {{}}
}}
setInterval(_checkCacheFreshness, 30000);
_checkCacheFreshness();

</script>
<div style="background:#1a1a1a; padding:10px; margin:10px 0; border-radius:8px; max-width:980px; margin-left:auto; margin-right:auto;">
  <h3 style="margin:0 0 8px 0; color:#fff; font-size:14px;">Long/Short Ratio (Top Traders Account, 1h)</h3>
  <div id="lsr-grid" style="display:grid; grid-template-columns:repeat(5,1fr); gap:8px;">
    <div style="color:#888">cargando...</div>
  </div>
  <div style="font-size:10px;color:#666;margin-top:6px;">Fuente: Binance Futures /futures/data. Read-only, no integrado al motor de signals.</div>
</div>
<script>
async function _sigmaLoadLsr() {{
  const symbols = ['BTC','ETH','SOL','BNB','LTC'];
  const grid = document.getElementById('lsr-grid');
  if (!grid) return;
  let html = '';
  for (const sym of symbols) {{
    try {{
      const r = await fetch(`/api/lsr/${{sym}}USDT/1h?limit=1`, {{cache:'no-store'}});
      const d = await r.json();
      const latest = (d && d.top_acct && d.top_acct[0]) ? d.top_acct[0] : null;
      if (!latest || latest.lsr == null) {{
        html += `<div style="background:#2a2a2a;padding:8px;border-radius:6px;color:#888">${{sym}}: n/d</div>`;
        continue;
      }}
      const lsr = Number(latest.lsr).toFixed(2);
      const color = lsr > 2 ? '#ff4444' : (lsr < 0.5 ? '#44ff44' : '#bbb');
      const longPct = (Number(latest.long)*100).toFixed(0);
      const shortPct = (Number(latest.short)*100).toFixed(0);
      html += `<div style="background:#2a2a2a;padding:8px;border-radius:6px;">
        <div style="font-weight:bold;color:${{color}};font-size:13px">${{sym}}: ${{lsr}}</div>
        <div style="font-size:11px;color:#aaa">L:${{longPct}}% S:${{shortPct}}%</div>
      </div>`;
    }} catch (e) {{
      html += `<div style="background:#2a2a2a;padding:8px;border-radius:6px;color:#888">${{sym}}: err</div>`;
    }}
  }}
  grid.innerHTML = html;
}}
_sigmaLoadLsr();
setInterval(_sigmaLoadLsr, 300000);
</script>
</body>
</html>"""

    out = OUTPUT_DIR / 'results' / 'charts' / 'dashboard.html'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding='utf-8')
    print(f'[DASHBOARD] {out}')
    return out


if __name__ == '__main__':
    try:
        generate_html()
    except Exception as e:
        import traceback
        print(f'[DASHBOARD ERROR] {e}', flush=True)
        traceback.print_exc()
        raise



"""engine_api.py — Endpoints agregados para el SaaS Next.js.

Expone bajo /api/v2/* la data que necesita squantdesk.com para mostrar
"qué decide el motor cuántico" en cada rincón de la plataforma.

Routes:
  GET /api/v2/engine_status          → snapshot agregado del motor
  GET /api/v2/champions              → lista de champions con métricas, red flags, grade
  GET /api/v2/decisions              → decision stream (filtros: kinds, since, limit, slot)
  GET /api/v2/portfolio              → portfolio del motor (real, no synthetic)
  GET /api/v2/fire                   → progreso FIRE tracker
  GET /api/v2/market_mood            → LSR + FNG + régimen agregado
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path('/opt/sigma')


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


# ── Aggregations ─────────────────────────────────────────────────────────────

def get_engine_status() -> dict:
    """Snapshot agregado del estado del motor cuántico."""
    snap = _read_json(BASE / 'results/reports/port_snapshot.json', {})
    bay  = _read_json(BASE / 'results/reports/bayesian_edges.json', {'strategies': {}})
    fire = _read_json(BASE / 'results/fire_config.json', {})
    ts   = _read_json(BASE / 'results/trade_state.json', {})

    champs = snap.get('champions', {})
    coverage = {
        'active': len(champs),
        'target': 40,
        'progress_pct': round(len(champs) / 40 * 100, 1),
        'n_grade_a': snap.get('n_grade_a', 0),
        'n_pass_live': snap.get('n_pass_live', 0),
        'n_blocked': snap.get('n_blocked', 0),
    }

    portfolio = {
        'cagr_weighted': round(snap.get('port_cagr', 0), 2),
        'cagr_pass_live': round(snap.get('port_cagr_pass_live', 0), 2),
        'cagr_with_kelly': round(snap.get('port_cagr_with_kelly', 0), 2),
        'wr': round(snap.get('port_wr', 0), 2),
        'dd': round(snap.get('port_dd', 0), 2),
        'pf': round(snap.get('port_pf', 0), 2),
        'calmar': round(snap.get('port_calmar', 0), 2),
        'n_trades': snap.get('total_trades', 0),
    }

    strats = bay.get('strategies', {})
    bayesian = {
        'tracked': len(strats),
        'edge_confirmed': sum(1 for s in strats.values() if s.get('edge_confirmed')),
        'watching': sum(1 for s in strats.values() if not s.get('edge_confirmed')),
    }

    start_eq  = fire.get('starting_equity', 10000)
    target_eq = fire.get('target_equity', 100000)
    cur_eq    = ts.get('portfolio', {}).get('equity', start_eq)
    pct       = (cur_eq - start_eq) / max(target_eq - start_eq, 1) * 100
    fire_block = {
        'current_equity': round(cur_eq, 2),
        'starting_equity': start_eq,
        'target_equity': target_eq,
        'progress_pct': round(max(0, min(pct, 100)), 1),
        'btc_virtual': round(cur_eq / 100000, 6),
        'baseline_date': fire.get('baseline_date'),
    }

    # Live counter (total backtests)
    total_runs = 0
    try:
        conn = sqlite3.connect(str(BASE / 'models' / 'sigma.db'))
        total_runs = conn.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        conn.close()
    except Exception:
        pass

    # Decision activity (lazy import to avoid cycle)
    decision_activity = {}
    last_decision_at = None
    try:
        from utils.decisions import count_by_kind, tail_decisions
        decision_activity = count_by_kind(24)
        last = tail_decisions(1)
        if last:
            last_decision_at = last[0].get('ts')
    except Exception:
        pass

    return {
        'portfolio': portfolio,
        'coverage': coverage,
        'bayesian': bayesian,
        'fire': fire_block,
        'backtests_total': total_runs,
        'decision_activity_24h': decision_activity,
        'last_decision_at': last_decision_at,
        'snapshot_at': snap.get('snapshot_at'),
        'snapshot_trigger': snap.get('trigger'),
    }


def _grade_from_cagr(cagr: float) -> str:
    if cagr >= 60: return 'A+'
    if cagr >= 40: return 'A'
    if cagr >= 20: return 'B'
    if cagr > 0:   return 'C'
    return 'D'


def _model_for_slot(sym: str, tf: str, strat: str, direction: str) -> dict | None:
    sym_lower = sym.lower()
    candidates = [
        BASE / 'models' / tf / f'{sym_lower}_{strat}.json',
        BASE / 'models' / tf / f'{sym_lower}_{strat}_{direction}.json',
        BASE / 'models' / tf / f'{sym_lower}_{strat}_short.json' if direction == 'short' else None,
    ]
    for cf in candidates:
        if cf is None: continue
        if cf.exists():
            try:
                return json.loads(cf.read_text(encoding='utf-8'))
            except Exception:
                continue
    return None


def get_champions() -> dict:
    """Champions actuales con metrics, robustness, grade, red flags."""
    snap = _read_json(BASE / 'results/reports/port_snapshot.json', {})
    bay  = _read_json(BASE / 'results/reports/bayesian_edges.json', {'strategies': {}})
    bay_strats = bay.get('strategies', {})

    champions = []
    for slot, val in snap.get('champions', {}).items():
        try:
            sym, tf = slot.split('|')
            strat, direction = val.split('|')
        except Exception:
            continue

        model = _model_for_slot(sym, tf, strat, direction) or {}
        oos = model.get('metrics_oos', {})
        wft = model.get('wft', {})
        mc  = model.get('mc', {})

        red_flags = []
        if oos.get('trades', 0) < 30:           red_flags.append('LOW_N')
        if wft.get('verdict') == 'FAIL':        red_flags.append('WFT_FAIL')
        if oos.get('cagr', 0) > 200:            red_flags.append('CAGR_TOO_HIGH')
        if (mc.get('mc_confidence') or 0) < 50: red_flags.append('LOW_MC')
        if oos.get('dd', 0) < -50:              red_flags.append('DEEP_DD')

        bay_info = bay_strats.get(strat, {})

        champions.append({
            'slot': slot,
            'sym': sym,
            'tf': tf,
            'strategy': strat,
            'direction': direction,
            'grade': _grade_from_cagr(oos.get('cagr', 0)),
            'metrics_oos': {
                'cagr':   round(oos.get('cagr', 0), 2),
                'wr':     round(oos.get('wr', 0), 2),
                'dd':     round(oos.get('dd', 0), 2),
                'pf':     round(oos.get('pf', 0), 2),
                'trades': oos.get('trades', 0),
                'payoff': round(oos.get('payoff', 0), 2),
            },
            'wft': {
                'verdict':      wft.get('verdict', 'N/A'),
                'oos_win_rate': round(wft.get('oos_win_rate', 0), 1),
                'n_windows':    wft.get('n_windows', 0),
            },
            'mc': {
                'confidence':  round(mc.get('mc_confidence', 0), 1),
                'cagr_p05':    round(mc.get('mc_cagr_p05', 0), 1),
                'cagr_p50':    round(mc.get('mc_cagr_p50', 0), 1),
                'cagr_p95':    round(mc.get('mc_cagr_p95', 0), 1),
                'dd_p95':      round(mc.get('mc_dd_p95', 0), 1),
            },
            'bayesian': {
                'edge_confirmed': bool(bay_info.get('edge_confirmed')),
                'n_trades':       bay_info.get('n_trades', 0),
                'live_wr':        round(bay_info.get('posterior_mean_wr', 0) * 100, 1) if bay_info else 0,
            },
            'red_flags': red_flags,
            'risk_pct':  model.get('risk_pct'),
            'saved_at':  model.get('saved_at'),
        })

    grade_rank = {'A+': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4}
    champions.sort(key=lambda c: (grade_rank.get(c['grade'], 9),
                                  -c['metrics_oos']['cagr']))

    return {
        'champions': champions,
        'count': len(champions),
        'snapshot_at': snap.get('snapshot_at'),
    }


def get_decisions(since_iso=None, limit=100, kinds=None, slot=None) -> dict:
    """Decision stream. Soporta filtros."""
    try:
        from utils.decisions import read_decisions, count_by_kind
        return {
            'decisions': read_decisions(since_iso=since_iso, limit=limit,
                                        kinds=kinds, slot=slot),
            'activity_24h': count_by_kind(24),
        }
    except Exception as e:
        return {'decisions': [], 'activity_24h': {}, 'error': str(e)}


def get_portfolio_engine() -> dict:
    """Portfolio del motor (paper trading real)."""
    ts = _read_json(BASE / 'results/trade_state.json', {})
    port = ts.get('portfolio', {})
    snap = _read_json(BASE / 'results/reports/port_snapshot.json', {})
    history = ts.get('history', [])
    open_trades = list(ts.get('open', {}).values())

    return {
        'live': {
            'equity': round(port.get('equity', 10000), 2),
            'initial': port.get('initial_capital', 10000),
            'return_pct': round((port.get('equity', 10000) / max(port.get('initial_capital', 10000), 1) - 1) * 100, 2),
            'max_dd_pct': round(port.get('max_dd_pct', 0), 2),
            'peak': round(port.get('peak_equity', port.get('equity', 10000)), 2),
            'total_commission': round(port.get('total_commission', 0), 2),
            'total_funding': round(port.get('total_funding', 0), 2),
            'start_date': port.get('start_date'),
            'equity_history': port.get('equity_history', [])[-100:],
            'n_trades': len(history),
            'open_trades_count': sum(1 for t in open_trades if t.get('status') == 'open'),
        },
        'backtest': {
            'cagr_weighted': round(snap.get('port_cagr', 0), 2),
            'cagr_with_kelly': round(snap.get('port_cagr_with_kelly', snap.get('port_cagr', 0)), 2),
            'cagr_pass_live': round(snap.get('port_cagr_pass_live', 0), 2),
            'wr': round(snap.get('port_wr', 0), 2),
            'dd': round(snap.get('port_dd', 0), 2),
            'pf': round(snap.get('port_pf', 0), 2),
        },
        'recent_trades': list(reversed(history[-30:])),
        'open_trades': open_trades,
    }


def get_fire() -> dict:
    """FIRE tracker. Misión: $100K → 1 BTC self-custody."""
    fire = _read_json(BASE / 'results/fire_config.json', {})
    ts   = _read_json(BASE / 'results/trade_state.json', {})
    cur  = ts.get('portfolio', {}).get('equity', fire.get('starting_equity', 10000))
    start = fire.get('starting_equity', 10000)
    target = fire.get('target_equity', 100000)
    pct = (cur - start) / max(target - start, 1) * 100

    snap = _read_json(BASE / 'results/reports/port_snapshot.json', {})
    cagr = snap.get('port_cagr', 0) / 100.0
    if cagr > 0 and cur < target:
        import math
        eta_years = math.log(target / cur) / math.log(1 + cagr) if cagr > 0 else None
    else:
        eta_years = None

    milestones = []
    for m_pct in fire.get('milestones', [25, 50, 75, 100]):
        ms_val = start + (target - start) * (m_pct / 100)
        milestones.append({
            'pct': m_pct,
            'value': round(ms_val, 0),
            'hit': cur >= ms_val,
        })

    return {
        'mission': '1 BTC en cold storage — self-custody',
        'current_equity': round(cur, 2),
        'starting_equity': start,
        'target_equity': target,
        'progress_pct': round(max(0, min(pct, 100)), 1),
        'btc_virtual': round(cur / 100000, 6),
        'cagr_weighted': round(snap.get('port_cagr', 0), 2),
        'eta_years_at_current_cagr': round(eta_years, 2) if eta_years else None,
        'baseline_date': fire.get('baseline_date'),
        'deadline_days': fire.get('target_deadline_days'),
        'milestones': milestones,
    }


def get_market_mood() -> dict:
    """Mood ring: LSR + FNG + régimen agregado."""
    out: dict = {'sources': [], 'mood': 'UNKNOWN'}

    # F&G — leer el último valor del CSV
    fg_path = BASE / 'models' / 'data_fear_greed.csv'
    if fg_path.exists():
        try:
            last = fg_path.read_text(encoding='utf-8').strip().splitlines()[-1]
            parts = last.split(',')
            if len(parts) >= 2:
                fg_val = int(parts[1].strip())
                if fg_val <= 25: fg_label = 'Extreme Fear'
                elif fg_val <= 45: fg_label = 'Fear'
                elif fg_val <= 55: fg_label = 'Neutral'
                elif fg_val <= 75: fg_label = 'Greed'
                else: fg_label = 'Extreme Greed'
                out['fear_greed'] = {'value': fg_val, 'label': fg_label}
                out['sources'].append('fear_greed')
        except Exception:
            pass

    # LSR — leer desde sqlite
    lsr_db = BASE / 'results' / 'lsr.db'
    if lsr_db.exists():
        try:
            conn = sqlite3.connect(str(lsr_db))
            cur = conn.cursor()
            try:
                rows = cur.execute(
                    "SELECT symbol, ratio FROM lsr WHERE ts = (SELECT MAX(ts) FROM lsr)"
                ).fetchall()
                out['lsr'] = {sym: round(r, 2) for sym, r in rows}
                out['sources'].append('lsr')
            except Exception:
                # tabla puede llamarse diferente — saltamos silenciosamente
                pass
            conn.close()
        except Exception:
            pass

    # Mood compuesto
    fg = out.get('fear_greed', {}).get('value')
    if fg is not None:
        if fg <= 25:   out['mood'] = 'EXTREME_FEAR'
        elif fg <= 45: out['mood'] = 'CAUTIOUS'
        elif fg <= 55: out['mood'] = 'NEUTRAL'
        elif fg <= 75: out['mood'] = 'OPTIMISTIC'
        else:          out['mood'] = 'EUPHORIC'

    return out


# ── Dispatcher ───────────────────────────────────────────────────────────────

def dispatch_v2(path: str) -> tuple[str, int]:
    """Routea /api/v2/* a la función correcta. Devuelve (body_json, status_code)."""
    parsed = urlparse(path)
    route = parsed.path
    qs = parse_qs(parsed.query)

    def _q(name, default=None):
        v = qs.get(name)
        return v[0] if v else default

    try:
        if route == '/api/v2/engine_status':
            data = get_engine_status()
        elif route == '/api/v2/champions':
            data = get_champions()
        elif route == '/api/v2/decisions':
            kinds = _q('kinds')
            kinds_list = [k.strip() for k in kinds.split(',')] if kinds else None
            data = get_decisions(
                since_iso=_q('since'),
                limit=int(_q('limit', '100')),
                kinds=kinds_list,
                slot=_q('slot'),
            )
        elif route == '/api/v2/portfolio':
            data = get_portfolio_engine()
        elif route == '/api/v2/fire':
            data = get_fire()
        elif route == '/api/v2/market_mood':
            data = get_market_mood()
        else:
            return json.dumps({'error': 'unknown route', 'route': route}), 404

        return json.dumps(data, ensure_ascii=False, default=str), 200

    except Exception as e:
        import traceback
        return json.dumps({
            'error': str(e),
            'type': type(e).__name__,
            'trace': traceback.format_exc(),
        }), 500

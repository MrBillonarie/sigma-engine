#!/usr/bin/env python3
"""
SIGMA ENGINE — Live Trading Readiness Checklist v1.0
Evalua automaticamente si el sistema esta listo para operar con dinero real.

Categorias:
  A. Performance paper trading  (25 pts)
  B. Calidad de modelos         (20 pts)
  C. Sistemas de riesgo         (20 pts)
  D. Infraestructura tecnica    (20 pts)
  E. API y conectividad         (15 pts)

Total: 100 pts — necesario >= 80 para activar live
"""
import json, urllib.request, sys, os
from pathlib import Path
from datetime import datetime, timezone, timedelta

CHILE    = timezone(timedelta(hours=-4))
BASE     = Path('/opt/sigma')
LIVE_MIN = 80   # puntos minimos para activar live

def _now():
    return datetime.now(CHILE)

def _fetch(url, timeout=5):
    try:
        r = urllib.request.urlopen(url, timeout=timeout)
        return json.loads(r.read())
    except:
        return None

def run_checklist():
    checks = []  # lista de (categoria, nombre, ok, valor, pts_max, pts)

    def add(cat, name, ok, valor, pts_max, pts_earned=None):
        pts = pts_earned if pts_earned is not None else (pts_max if ok else 0)
        checks.append({
            'cat': cat, 'name': name, 'ok': ok,
            'valor': valor, 'pts_max': pts_max, 'pts': pts
        })

    # ── Cargar datos ──────────────────────────────────────────────────────────
    ts     = {}
    try:
        ts = json.loads((BASE / 'results/trade_state.json').read_text())
    except: pass

    st     = ts.get('stats', {})
    port   = ts.get('portfolio', {})
    hist   = ts.get('history', [])
    lr     = ts.get('live_readiness', {}) or {}
    sec    = {}
    try:
        sec = json.loads((BASE / 'engine/config/secrets.json').read_text())
    except: pass

    signals = _fetch('http://127.0.0.1:8080/api/signals') or {}
    models  = signals.get('models', [])
    cb      = signals.get('circuit_breaker', False)

    trades  = st.get('total', 0)
    wr      = st.get('win_rate', 0)
    pf      = st.get('profit_factor', 0)
    maxdd   = abs(port.get('max_dd', 0))
    ret     = port.get('return_pct', 0)
    score   = lr.get('score', 0)

    grade_a = [m for m in models if m.get('grade') in ('A+', 'A')]
    wft_ok  = [m for m in models if m.get('wft_pass_rate') and m.get('wft_pass_rate') >= 55]
    decay_w = [m for m in models if m.get('decay_warning')]

    # Ultimos 7 dias sin CB
    from datetime import timedelta
    week_ago = (_now() - timedelta(days=7)).strftime('%Y-%m-%d')
    cb_hist  = [t for t in hist if t.get('reason') == 'CIRCUIT_BREAKER'
                and str(t.get('closed_at', '')) >= week_ago]

    # Timestamp del Pine Script
    pine_path   = BASE / 'results/pine_scripts/SIGMA_v13_COMPLETO.pine'
    pine_hours  = 999
    try:
        mtime      = datetime.fromtimestamp(pine_path.stat().st_mtime,
                                             tz=timezone.utc)
        pine_hours = (_now() - mtime.astimezone(CHILE)).total_seconds() / 3600
    except: pass

    # Sigma-web y sigma-telegram activos
    def svc_ok(name):
        try:
            r = os.popen(f'systemctl is-active {name} 2>/dev/null').read().strip()
            return r == 'active'
        except:
            return False

    # ── A. PERFORMANCE PAPER TRADING (25 pts) ────────────────────────────────
    add('A', 'Trades paper >= 30',       trades >= 30,  f"{trades} trades",   5)
    add('A', 'WR live >= 55%',           wr >= 55,      f"{wr:.1f}%",          6,
        6 if wr >= 65 else 4 if wr >= 55 else 0)
    add('A', 'Profit Factor >= 1.3',     pf >= 1.3,     f"{pf:.2f}",           5,
        5 if pf >= 1.5 else 3 if pf >= 1.3 else 0)
    add('A', 'Max DD < 15%',             maxdd < 15,    f"{maxdd:.1f}%",        5,
        5 if maxdd < 10 else 3 if maxdd < 15 else 0)
    add('A', 'Retorno paper > 0%',       ret > 0,       f"{ret:+.2f}%",         4)

    # ── B. CALIDAD DE MODELOS (20 pts) ───────────────────────────────────────
    add('B', 'Gate score >= 80/100',     score >= 80,   f"{score}/100",         8,
        8 if score >= 85 else 5 if score >= 80 else 0)
    add('B', 'Modelos Grade A+/A >= 4',  len(grade_a) >= 4, f"{len(grade_a)} modelos", 5)
    add('B', 'Walk-Forward OK >= 3',     len(wft_ok) >= 3,  f"{len(wft_ok)} modelos",  4)
    add('B', 'Sin decay warnings',       len(decay_w) == 0,  f"{len(decay_w)} activos", 3)

    # ── C. SISTEMAS DE RIESGO (20 pts) ───────────────────────────────────────
    smart_exit_ok  = (BASE / 'engine/live/smart_exit.py').exists()
    filter_ok      = (BASE / 'engine/live/ai_filter.py').exists()
    executor_ok    = (BASE / 'engine/live/live_executor.py').exists()
    pausa_ok       = not (BASE / 'results/pausa.flag').exists()
    add('C', 'Smart Exit activo',        smart_exit_ok, 'OK' if smart_exit_ok else 'falta',    5)
    add('C', 'Entry Filter activo',      filter_ok,     'OK' if filter_ok else 'falta',        4)
    add('C', 'Live Executor listo',      executor_ok,   'OK' if executor_ok else 'falta',      4)
    add('C', 'Sin circuit breaker 7d',   len(cb_hist)==0 and not cb,
        f"CB: {'activo' if cb else 'inactivo'}",  4)
    add('C', 'Sistema no pausado',       pausa_ok,      'OK' if pausa_ok else 'PAUSADO',       3)

    # ── D. INFRAESTRUCTURA (20 pts) ──────────────────────────────────────────
    api_up   = _fetch('http://127.0.0.1:8080/api/stats') is not None
    tg_up    = svc_ok('sigma-telegram')
    pipe_up  = svc_ok('sigma-pipeline')
    add('D', 'Dashboard/API accesible',  api_up,        'OK' if api_up else 'DOWN',            6)
    add('D', 'sigma-telegram activo',    tg_up,         'OK' if tg_up else 'DOWN',             4)
    add('D', 'sigma-pipeline activo',    pipe_up,       'OK' if pipe_up else 'DOWN',           4)
    add('D', 'Pine Script < 48h',        pine_hours < 48,
        f"{pine_hours:.0f}h" if pine_hours < 999 else 'sin archivo',                           3)
    add('D', 'VPS con modelos activos',  len(models) >= 8, f"{len(models)} modelos",           3)

    # ── E. API Y CONECTIVIDAD (15 pts) ───────────────────────────────────────
    binance_key    = bool(sec.get('BINANCE_API_KEY', ''))
    binance_secret = bool(sec.get('BINANCE_API_SECRET', ''))
    anthropic_key  = bool(sec.get('ANTHROPIC_API_KEY', ''))
    binance_test   = False
    balance_ok     = False
    if binance_key and binance_secret:
        try:
            import ccxt
            ex  = ccxt.binance({
                'apiKey': sec['BINANCE_API_KEY'],
                'secret': sec['BINANCE_API_SECRET'],
                'options': {'defaultType': 'future'},
                'timeout': 8000,
            })
            bal         = ex.fetch_balance()
            usdt_free   = float(bal.get('USDT', {}).get('free', 0))
            binance_test= True
            balance_ok  = usdt_free >= 50
            balance_val = f"${usdt_free:.0f} USDT"
        except Exception as e:
            balance_val = f"error: {e}"
    else:
        balance_val = 'sin API key'

    add('E', 'Binance API key',      binance_key,    'OK' if binance_key else 'pendiente',     4)
    add('E', 'Binance API secret',   binance_secret, 'OK' if binance_secret else 'pendiente',  4)
    add('E', 'Conexion Binance OK',  binance_test,   'OK' if binance_test else 'no probada',   4)
    add('E', 'Balance USDT >= $50',  balance_ok,     balance_val,                              3)

    return checks

def format_report(checks):
    total_pts = sum(c['pts'] for c in checks)
    total_max = sum(c['pts_max'] for c in checks)
    pct       = total_pts / total_max * 100 if total_max else 0
    ready     = total_pts >= LIVE_MIN

    cats = {}
    for c in checks:
        cats.setdefault(c['cat'], []).append(c)

    cat_names = {
        'A': 'Performance Paper Trading',
        'B': 'Calidad de Modelos',
        'C': 'Sistemas de Riesgo',
        'D': 'Infraestructura',
        'E': 'API y Conectividad',
    }

    lines = []
    lines.append(f"{'='*52}")
    lines.append(f"  SIGMA ENGINE — Checklist Live Trading")
    lines.append(f"  {_now().strftime('%d/%m/%Y %H:%M')} (Chile)")
    lines.append(f"{'='*52}")
    lines.append(f"  PUNTAJE TOTAL: {total_pts}/{total_max} ({pct:.0f}%)")
    lines.append(f"  ESTADO: {'✅ LISTO PARA LIVE' if ready else f'⏳ FALTAN {LIVE_MIN-total_pts} PUNTOS'}")
    lines.append(f"{'='*52}")

    for cat, cat_checks in cats.items():
        cat_pts = sum(c['pts'] for c in cat_checks)
        cat_max = sum(c['pts_max'] for c in cat_checks)
        lines.append(f"\n  {cat}. {cat_names[cat]} ({cat_pts}/{cat_max})")
        lines.append(f"  {'-'*48}")
        for c in cat_checks:
            icon  = '✅' if c['ok'] else '❌'
            pts_s = f"+{c['pts']}pts" if c['pts'] > 0 else f"  0pts"
            lines.append(f"  {icon} {c['name']:<35} {c['valor']:<18} {pts_s}")

    lines.append(f"\n{'='*52}")
    if ready:
        lines.append("  ✅ Sistema LISTO — puedes activar LIVE_MODE=True")
        lines.append("     en /opt/sigma/engine/live/live_executor.py")
    else:
        pending = [c for c in checks if not c['ok']]
        lines.append(f"  Pendientes ({len(pending)}):")
        for c in pending[:5]:
            lines.append(f"    • {c['name']}: {c['valor']}")
    lines.append(f"{'='*52}")

    return '\n'.join(lines), total_pts, total_max, ready

def _esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def format_telegram(checks):
    """Formato compacto para Telegram."""
    total_pts = sum(c['pts'] for c in checks)
    total_max = sum(c['pts_max'] for c in checks)
    pct       = total_pts / total_max * 100 if total_max else 0
    ready     = total_pts >= LIVE_MIN

    cats = {}
    for c in checks:
        cats.setdefault(c['cat'], []).append(c)

    cat_names = {
        'A': 'Performance Paper',
        'B': 'Modelos',
        'C': 'Riesgo',
        'D': 'Infraestructura',
        'E': 'API / Binance',
    }

    bar_filled = int(pct / 10)
    bar_str    = '█' * bar_filled + '░' * (10 - bar_filled)

    msg  = f"📋 <b>CHECKLIST LIVE TRADING</b>\n"
    msg += f"<code>{bar_str}</code> {total_pts}/{total_max} pts\n"
    msg += f"{'✅ LISTO PARA LIVE' if ready else f'⏳ Faltan {LIVE_MIN-total_pts} puntos'}\n\n"

    for cat, cat_checks in cats.items():
        cat_pts = sum(c['pts'] for c in cat_checks)
        cat_max = sum(c['pts_max'] for c in cat_checks)
        ok_all  = all(c['ok'] for c in cat_checks)
        icon    = '✅' if ok_all else '⚠️' if cat_pts > 0 else '❌'
        msg += f"{icon} <b>{cat_names[cat]}</b> {cat_pts}/{cat_max}\n"
        for c in cat_checks:
            ci = '  ✅' if c['ok'] else '  ❌'
            msg += f"{ci} {_esc(c['name'])}: <code>{_esc(c['valor'])}</code>\n"
        msg += '\n'

    if not ready:
        pending = [c for c in checks if not c['ok']]
        msg += f"<b>Proximos pasos:</b>\n"
        for c in pending[:4]:
            msg += f"  • {c['name']}\n"

    return msg

if __name__ == '__main__':
    checks = run_checklist()
    report, pts, max_pts, ready = format_report(checks)
    print(report)
    sys.exit(0 if ready else 1)

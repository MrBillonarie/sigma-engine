"""
SIGMA ENGINE - Dashboard Multi-Activo
Genera dashboard.html con matriz completa: 5 activos x 5 TFs
Se regenera cada 2 minutos via web_server.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, sqlite3, re, time
from pathlib import Path
from datetime import datetime, date
try:
    import zoneinfo as _zi_db
    _TZ_DB = _zi_db.ZoneInfo("America/Santiago")
except ImportError:
    from datetime import timezone as _tz_db2, timedelta as _td_db2
    _TZ_DB = _tz_db2(_td_db2(hours=-4))

def _now_chile_dash():
    from datetime import datetime as _dz_db
    return _dz_db.now(_TZ_DB)

import os as _os_db; _os_db.environ['TZ']='America/Santiago'; import time as _t_db; _t_db.tzset()

OUTPUT_DIR = Path(__file__).parent.parent.parent

ASSETS = ['BTC','ETH','LTC','SOL','BNB']
ASSET_EMOJI = {'BTC':'&#8383;','ETH':'&#926;','LTC':'&#321;','SOL':'&#9678;','BNB':'&#11042;'}
ASSET_COLOR = {'BTC':'#f7931a','ETH':'#627eea','LTC':'#345d9d','SOL':'#9945ff','BNB':'#f3ba2f'}
TIMEFRAMES  = ['4h','1h','15m','5m','1m']
TF_LABEL    = {'4h':'4H','1h':'1H','15m':'15m','5m':'5m','1m':'1m (scalp)'}
TF_COLORS_H = {'4h':'#2ecc71','1h':'#58a6ff','15m':'#f1c40f','5m':'#e67e22','1m':'#a78bfa'}


# ── SCORE / GRADE ─────────────────────────────────────────────────────────────

def _compute_score(m):
    if not m: return -9999
    t    = m.get('trades', 0)
    wr   = m.get('wr', m.get('winrate', 0))
    cagr = m.get('cagr', 0)
    dd   = m.get('dd', m.get('max_dd', 0))
    pf   = m.get('pf', m.get('profit_factor', 1))
    ty   = m.get('trades_year', m.get('trades_month', 0) * 12)

    if t < 10 or cagr <= 0: return -9999

    if ty <= 0 and t > 0:
        ty = t * (365.0 / 600)

    if ty < 3: return -9999

    if wr <= 0 and cagr > 0:
        wr = 50.0

    s_freq = min(ty / 12.0, 1.0) * 0.20
    s_cagr = min(cagr, 60) / 60 * 0.40
    s_wr   = min(max(wr / 100 - 0.50, 0) / 0.20, 1.0) * 0.20
    s_cal  = min(cagr / abs(dd) if dd < 0 else 0, 5) / 5 * 0.15
    s_pf   = min(pf, 3) / 3 * 0.05
    return round(s_freq + s_cagr + s_wr + s_cal + s_pf, 4)

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

LONG_STRATEGIES  = {'breakout','tma_bands','tma_breakout','momentum','pullback','mean_rev','regime_adaptive'}
SHORT_STRATEGIES = {'breakdown','pullback_short','momentum_short'}

def load_model(asset, tf, direction='long'):
    """Carga el mejor modelo OOS positivo para asset+tf segun direccion."""
    if tf == '1m':
        return None
    d = OUTPUT_DIR / 'models' / tf
    if not d.exists():
        return None
    sym = asset.lower()

    if direction == 'short':
        candidates = [
            f'{sym}_breakdown.json',
            f'{sym}_pullback_short.json',
            f'{sym}_momentum_short.json',
        ]
    elif asset == 'BTC':
        candidates = [
            'best_bull_breakout.json', 'best_bull_tma_bands.json',
            'best_bull_pullback.json', 'best_validated.json',
            'btc_breakout.json', 'btc_regime_adaptive.json',
            'btc_tma_bands.json', 'btc_momentum.json',
        ]
    else:
        candidates = [
            f'{sym}_regime_adaptive.json',
            f'{sym}_breakout.json',
            f'{sym}_tma_bands.json',
            f'{sym}_tma_breakout.json',
            f'{sym}_momentum.json',
            f'{sym}_pullback.json',
            f'{sym}_mean_rev.json',
        ]

    for fname in candidates:
        p = d / fname
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
            # Verificar que es para este activo
            sym_field = data.get('symbol', '').upper()
            if sym_field and asset not in sym_field and asset != 'BTC':
                continue
            m = data.get('metrics_oos') or {}
            cagr = m.get('cagr', 0)
            if cagr > 0:
                v = data.get('validation', {})
                strat = data.get('strategy', fname.replace('.json','').split('_',1)[-1] if '_' in fname else '')
                is_adaptive = strat == 'regime_adaptive'
                model_dict = {
                    'cagr':       cagr,
                    'wr':         m.get('wr', m.get('winrate', 0)),
                    'dd':         m.get('dd', m.get('max_dd', 0)),
                    'pf':         m.get('pf', m.get('profit_factor', 0)),
                    'trades':     m.get('trades', 0),
                    'cagr_is':    data.get('metrics_is', {}).get('cagr', 0),
                    'source':     fname,
                    'strategy':   strat,
                    'adaptive':   is_adaptive,
                    'confidence': v.get('confidence', ''),
                    'mc_p_pos':   v.get('monte_carlo', {}).get('p_pos', 0),
                    'wft_pct':    v.get('walk_forward', {}).get('pct_positive', 0),
                }
                model_dict['score'] = _compute_score(model_dict)
                return model_dict
        except:
            continue
    return None


def is_running(asset, tf):
    """Devuelve True si el optimizador para asset+tf fue modificado recientemente."""
    name = f'{asset.lower()}_{tf}'
    lp   = OUTPUT_DIR / 'results' / 'reports' / f'{name}.log'
    if not lp.exists():
        return False
    age_min = (time.time() - lp.stat().st_mtime) / 60
    return age_min < 10  # activo si log cambio en <10 min


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
        return {'total': total, 'by_tf': by_tf, 'top3': top3, 'rate_hr': rate}
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

def _row_model(m, direction):
    """HTML de una fila LONG o SHORT dentro de la celda."""
    arrow  = '&#9650;' if direction == 'long' else '&#9660;'
    col    = '#2ecc71' if direction == 'long' else '#e74c3c'
    empty  = f'<div style="color:#30363d;font-size:9px;padding:2px 0">{arrow} <span style="color:#30363d">pendiente</span></div>'
    if not m:
        return empty
    cagr  = m['cagr']; wr = m['wr']; t = m['trades']
    conf  = m.get('confidence','')
    cc    = {'ALTA':'#2ecc71','MEDIA':'#f1c40f','BAJA':'#e67e22'}.get(conf,'#555')
    strat = m.get('strategy','')[:10]
    grade_badge = _score_grade(m.get('score', -9999))
    return (
        f'<div style="display:flex;justify-content:space-between;align-items:center;padding:2px 0 1px">'
        f'<span style="color:{col};font-size:9px">{arrow} {strat}</span>'
        f'<span style="font-family:monospace;color:{c_cagr(cagr)};font-weight:700;font-size:11px">{cagr:+.1f}%</span>'
        f'</div>'
        f'<div style="display:flex;justify-content:space-between;font-size:9px;padding-bottom:1px">'
        f'<span style="color:{cc}">{conf}&nbsp;{grade_badge}</span>'
        f'<span style="color:#8b949e">WR {wr:.0f}% {t}T</span>'
        f'</div>'
    )

def _combined_row(ml, ms, ma):
    """HTML fila COMBINED — usa adaptive si existe, sino estima."""
    sep = '<div style="border-top:1px solid #1c2128;margin:2px 0"></div>'
    if ma:
        # Adaptive ya es el combined real backtestado
        cagr = ma['cagr']; wr = ma['wr']; t = ma['trades']
        conf = ma.get('confidence','')
        cc   = {'ALTA':'#2ecc71','MEDIA':'#f1c40f','BAJA':'#e67e22'}.get(conf,'#555')
        return (
            sep +
            f'<div style="background:rgba(88,166,255,.08);border-radius:3px;padding:2px 3px;margin-top:1px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center">'
            f'<span style="color:#58a6ff;font-size:9px;font-weight:700">&#9670; ADAPTIVE</span>'
            f'<span style="font-family:monospace;color:{c_cagr(cagr)};font-weight:700;font-size:11px">{cagr:+.1f}%</span>'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;font-size:9px">'
            f'<span style="color:{cc}">{conf}</span>'
            f'<span style="color:#8b949e">WR {wr:.0f}% {t}T</span>'
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
            f'<span style="color:#58a6ff;font-size:9px">&#9670; COMBINED ~</span>'
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
            return (
                f'<td class="cell-ok" style="background:linear-gradient(180deg,rgba(88,166,255,.07),rgba(88,166,255,.01))">'
                f'{comb_row.replace("<div style=", "<div style=").replace("margin-top:1px", "margin-top:0")}'
                f'</td>'
            )

        return (
            f'<td class="cell-ok">'
            f'{long_row}'
            f'<div style="border-top:1px solid #1c2128;margin:2px 0"></div>'
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

def generate_html():
    now = _now_chile_dash().strftime('%Y-%m-%d %H:%M')
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

    # Top models for portfolio rule
    all_m = []
    for asset in ASSETS:
        for tf in ['1h','4h','15m','5m']:
            m = load_model(asset, tf)
            if m:
                all_m.append((m['cagr'], asset, tf, m))
    all_m.sort(reverse=True)
    top2 = all_m[:2]

    # Count total ready
    n_ready   = sum(1 for c,a,t,m in all_m)
    n_total   = len(ASSETS) * len(TIMEFRAMES)  # 5 activos x 5 TFs = 25

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

    for asset in ASSETS:
        color  = ASSET_COLOR[asset]
        emoji  = ASSET_EMOJI[asset]
        cells  = ''.join(cell_html(asset, tf) for tf in TIMEFRAMES)
        matrix_rows += f'''
        <tr>
          <td class="asset-col">
            <span class="asset-emoji" style="color:{color}">{emoji}</span>
            <span class="asset-name">{asset}</span>
          </td>
          {cells}
        </tr>'''
        # Collect for summary (long + short)
        for tf in TIMEFRAMES:
            for d in ['long','short']:
                m = load_model(asset, tf, direction=d)
                if m:
                    tf_models[tf].append((
                        m['cagr'], m['wr'], m['trades'], m.get('score', -9999),
                        m.get('dd', 0), m.get('pf', 0)
                    ))

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
                f'<td style="text-align:center;border-top:2px solid #30363d;padding:7px 4px">'
                f'<div style="color:{c_cagr(avg_c)};font-family:\'JetBrains Mono\',monospace;font-size:12px;font-weight:700">{avg_c:+.1f}%</div>'
                f'<div style="font-size:10px;color:{c_wr(w_wr)}">WR {w_wr:.0f}%</div>'
                f'<div style="font-size:10px;color:#8b949e">{total_t}T</div>'
                f'</td>'
            )
        else:
            summary_cells += '<td style="border-top:2px solid #30363d;text-align:center;color:#30363d">—</td>'

    # Overall portfolio summary — ponderado por trades OOS (mas trades = mas confiable)
    if all_cagrs:
        all_models_w = []
        for tf in TIMEFRAMES:
            for m_data in tf_models[tf]:
                all_models_w.append(m_data)  # (cagr, wr, trades, score, dd, pf)
        tot_t = all_trades_sum
        if all_models_w:
            total_trades_w = sum(m[2] for m in all_models_w)
            if total_trades_w > 0:
                port_cagr = sum(m[0]*m[2] for m in all_models_w) / total_trades_w
                port_wr   = sum(m[1]*m[2] for m in all_models_w) / total_trades_w
                port_dd   = sum(m[4]*m[2] for m in all_models_w if len(m)>4) / total_trades_w
                port_pf   = sum(m[5]*m[2] for m in all_models_w if len(m)>5 and m[5]>0) / max(
                    sum(m[2] for m in all_models_w if len(m)>5 and m[5]>0), 1)
            else:
                port_cagr = sum(m[0] for m in all_models_w) / len(all_models_w)
                port_wr   = sum(m[1] for m in all_models_w) / len(all_models_w)
                port_dd   = sum(m[4] for m in all_models_w if len(m)>4) / len(all_models_w)
                port_pf   = sum(m[5] for m in all_models_w if len(m)>5) / len(all_models_w)
            # Calmar ratio del portafolio
            port_calmar = round(port_cagr / abs(port_dd), 2) if port_dd < 0 else 0
        else:
            port_cagr = port_wr = port_dd = port_pf = port_calmar = 0
    else:
        port_cagr = port_wr = port_dd = port_pf = port_calmar = tot_t = 0

    # Contar modelos grado A+/A (score >= 0.55) — operables
    n_grade_a = sum(1 for m in all_models_w if len(m) > 3 and m[3] >= 0.55) if all_cagrs else 0

    matrix_rows += f'''
        <tr style="background:#0d1117">
          <td style="border-top:2px solid #30363d;padding:7px 8px">
            <span style="font-size:10px;font-weight:700;color:#8b949e;text-transform:uppercase;letter-spacing:.05em">Ponderado</span>
          </td>
          {summary_cells}
        </tr>
        <tr style="background:#0a1628">
          <td colspan="6" style="padding:8px 10px;border-top:1px solid #1c2128">
            <span style="font-size:11px;color:#8b949e">Portafolio (ponderado por trades OOS): &nbsp;</span>
            <span style="font-family:\'JetBrains Mono\',monospace;color:{c_cagr(port_cagr)};font-weight:700;font-size:14px">{port_cagr:+.1f}%</span>
            <span style="font-size:11px;color:#8b949e"> CAGR &nbsp;|&nbsp; WR: </span>
            <span style="font-family:\'JetBrains Mono\',monospace;color:{c_wr(port_wr)};font-weight:700;font-size:13px">{port_wr:.1f}%</span>
            <span style="font-size:11px;color:#8b949e"> &nbsp;|&nbsp; DD: </span>
            <span style="font-family:\'JetBrains Mono\',monospace;color:{"#f85149" if port_dd<-20 else "#ff9800" if port_dd<-10 else "#e6edf3"};font-weight:700;font-size:13px">{port_dd:.1f}%</span>
            <span style="font-size:11px;color:#8b949e"> &nbsp;|&nbsp; PF: </span>
            <span style="font-family:\'JetBrains Mono\',monospace;color:{"#00e676" if port_pf>=2 else "#69f0ae" if port_pf>=1.5 else "#ff9800"};font-weight:700;font-size:13px">{port_pf:.2f}</span>
            <span style="font-size:11px;color:#8b949e"> &nbsp;|&nbsp; Calmar: </span>
            <span style="font-family:\'JetBrains Mono\',monospace;color:{"#00e676" if port_calmar>=2 else "#69f0ae" if port_calmar>=1 else "#ff9800"};font-weight:700;font-size:13px">{port_calmar:.2f}</span>
            <span style="font-size:11px;color:#8b949e"> &nbsp;|&nbsp; Trades OOS: </span>
            <span style="font-family:\'JetBrains Mono\',monospace;color:#e6edf3;font-size:12px">{tot_t}</span>
            <span style="font-size:11px;color:#8b949e"> &nbsp;|&nbsp; Activos: </span>
            <span style="font-family:\'JetBrains Mono\',monospace;color:#58a6ff;font-size:12px">{n_ready}/{n_total}</span>
            <span style="font-size:11px;color:#8b949e"> &nbsp;|&nbsp; Grado A+/A: </span>
            <span style="font-family:\'JetBrains Mono\',monospace;color:#69f0ae;font-size:13px;font-weight:700">{n_grade_a}</span>
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
        a_color = ASSET_COLOR.get(asset, '#e6edf3')
        cagr_html = f'<span style="color:{c_cagr(cagr)}">{cagr:+.1f}%</span>' if cagr is not None else ''
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
                f'<div style="background:#21262d;border-radius:6px;padding:10px 14px;min-width:90px;text-align:center">'
                f'<div style="font-size:10px;color:#8b949e;margin-bottom:4px">{a}</div>'
                f'<div style="font-size:18px;font-weight:700;color:{c_cagr(cag)}">{cag:+.1f}%</div>'
                f'<div style="font-size:10px;color:#8b949e">{r.get("trades","-")}T</div></div>'
            )

    # TF counter pills for banner
    TF_COLORS = {'1h':'#58a6ff','4h':'#2ecc71','15m':'#f1c40f','5m':'#e67e22','2h':'#a78bfa','1m':'#8b949e'}
    tf_counter_html = ''
    for tf, cnt in sorted(db.get('by_tf', {}).items()):
        col = TF_COLORS.get(tf, '#e6edf3')
        tf_counter_html += (
            f'<div class="counter-stat">'
            f'<div class="val" style="color:{col}" id="tf-{tf}">{cnt:,}</div>'
            f'<div class="lbl">{tf.upper()}</div>'
            f'</div>'
        )

    top2_pills = ''.join(
        f'<div class="pill" style="border-color:{ASSET_COLOR.get(a,"#58a6ff")}">'
        f'<span style="color:{ASSET_COLOR.get(a,"#58a6ff")}">{ASSET_EMOJI.get(a,a)} {a}</span>'
        f'<span>{tf.upper()}</span>'
        f'<span style="color:{c_cagr(cagr)}">{cagr:+.1f}%</span></div>'
        for cagr, a, tf, m in top2
    ) or '<span style="color:#8b949e">Sin modelos OOS positivos aun</span>'

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<!-- Sin meta refresh — todo actualiza via JavaScript sin parpadeo -->
<title>SIGMA ENGINE</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#e6edf3;font-family:'Inter',sans-serif;font-size:14px}}
.mono{{font-family:'JetBrains Mono',monospace}}
.container{{max-width:1100px;margin:0 auto;padding:20px 14px}}

/* Header */
.hdr{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:22px;padding-bottom:14px;border-bottom:1px solid #30363d}}
.hdr h1{{font-size:20px;font-weight:700;color:#58a6ff;letter-spacing:-.02em}}
.hdr .meta{{font-size:11px;color:#8b949e;text-align:right;line-height:1.8}}

/* Cards */
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:18px}}
.card-title{{font-size:11px;font-weight:700;color:#8b949e;text-transform:uppercase;letter-spacing:.06em;margin-bottom:14px}}

/* Rule box */
.rule{{background:#161b22;border:1px solid #58a6ff33;border-radius:8px;padding:14px 18px;margin-bottom:18px;display:flex;flex-wrap:wrap;gap:16px;align-items:center}}
.rule-title{{font-weight:700;color:#58a6ff;font-size:14px}}
.rule-sub{{color:#8b949e;font-size:12px;margin-top:2px}}
.pills{{display:flex;gap:8px;flex-wrap:wrap}}
.pill{{display:inline-flex;gap:8px;align-items:center;background:#21262d;border:1px solid #30363d;border-radius:20px;padding:4px 12px;font-size:12px}}

/* Progress badge */
.prog{{font-size:12px;color:#8b949e;margin-left:auto;white-space:nowrap}}
.prog strong{{color:#e6edf3}}

/* MATRIX */
.matrix-wrap{{overflow-x:auto}}
.matrix{{width:100%;border-collapse:collapse;table-layout:fixed}}
.matrix th{{padding:8px 6px;font-size:11px;font-weight:600;color:#8b949e;text-align:center;border-bottom:2px solid #30363d;width:80px}}
.matrix th.th-asset{{text-align:left;width:90px}}
.matrix td{{padding:6px;text-align:center;border-bottom:1px solid #1c2128;vertical-align:middle;height:52px}}
.asset-col{{text-align:left!important;padding-left:4px!important}}
.asset-emoji{{font-size:16px;margin-right:4px}}
.asset-name{{font-weight:700;font-size:13px}}

/* Cells */
.cell-ok{{background:#0d2016;border:1px solid #2ecc7130;border-radius:4px}}
.cell-run{{background:#0d1a2e;border:1px solid #58a6ff30;border-radius:4px;color:#58a6ff}}
.cell-neg{{background:#1a0d0d;border:1px solid #e74c3c20;border-radius:4px;color:#e74c3c44}}
.cell-pending{{color:#30363d}}
.cell-na{{color:#21262d;font-size:18px}}
.cell-cagr{{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:600;line-height:1.3}}
.cell-sub{{font-size:10px;color:#8b949e;margin-top:2px}}

@keyframes spin{{to{{transform:rotate(360deg)}}}}
@keyframes pulse{{0%,100%{{opacity:1;box-shadow:0 0 0 0 rgba(0,230,118,0.4)}}50%{{opacity:0.7;box-shadow:0 0 0 4px rgba(0,230,118,0)}}}}
@keyframes flashIn{{from{{opacity:0;transform:translateY(-10px)}}to{{opacity:1;transform:translateY(0)}}}}
.spin{{display:inline-block;animation:spin 1.5s linear infinite;font-size:14px}}

/* Tables */
table.t{{width:100%;border-collapse:collapse}}
table.t th{{padding:7px 10px;font-size:11px;color:#8b949e;font-weight:600;border-bottom:1px solid #30363d;text-align:left}}
table.t td{{padding:8px 10px;font-family:'JetBrains Mono',monospace;font-size:12px;border-bottom:1px solid #21262d}}
table.t tr:last-child td{{border:none}}
table.t tr:hover td{{background:#1c2128}}

/* Badges */
.badge{{display:inline-block;padding:2px 7px;border-radius:10px;font-size:10px;font-weight:600}}
.badge.green{{background:#2ecc7120;color:#2ecc71;border:1px solid #2ecc71}}

/* WFT */
.wft-stats{{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:12px}}
.wft-num{{text-align:center}}
.wft-num .n{{font-size:22px;font-weight:700;font-family:'JetBrains Mono',monospace}}
.wft-num .l{{font-size:10px;color:#8b949e}}
.progress{{background:#21262d;border-radius:4px;height:6px;margin-bottom:12px;overflow:hidden}}
.progress-fill{{height:100%;background:linear-gradient(90deg,#58a6ff,#2ecc71);border-radius:4px}}
.wft-scroll{{max-height:200px;overflow-y:auto}}

/* DB Pills */
.tf-pills{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}}
.tf-pill{{background:#21262d;border:1px solid #30363d;border-radius:4px;padding:3px 10px;font-size:11px;font-family:'JetBrains Mono',monospace}}

/* Halving */
.phase{{display:inline-block;padding:3px 8px;border-radius:4px;font-size:11px;font-weight:600}}

/* Regime panel */
.regime-grid{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}}
.regime-card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;min-width:130px;flex:1;text-align:center}}
.regime-asset{{font-weight:700;font-size:13px;margin-bottom:6px}}
.regime-badge{{display:inline-block;padding:3px 12px;border-radius:12px;font-size:12px;font-weight:700;margin-bottom:6px}}
.regime-bull  {{background:#2ecc7120;color:#2ecc71;border:1px solid #2ecc71}}
.regime-range {{background:#f1c40f20;color:#f1c40f;border:1px solid #f1c40f}}
.regime-bear  {{background:#e74c3c20;color:#e74c3c;border:1px solid #e74c3c}}
.regime-unk   {{background:#55555520;color:#555;border:1px solid #555}}
.regime-rsi   {{font-size:11px;color:#8b949e;font-family:'JetBrains Mono',monospace}}

/* Activity feed */
.feed{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 16px;margin-bottom:18px}}
.feed-title{{font-size:11px;font-weight:700;color:#8b949e;text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px;display:flex;justify-content:space-between}}
.feed-list{{max-height:280px;overflow-y:auto;display:flex;flex-direction:column;gap:4px}}
.feed-item{{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:5px;font-size:12px;background:#0d1117;border-left:3px solid #30363d}}
.feed-item.rec  {{border-left-color:#2ecc71;background:#0d2016}}
.feed-item.neg  {{border-left-color:#e74c3c;background:#1a0d0d}}
.feed-item.pos  {{border-left-color:#f1c40f;background:#1a1800}}
.feed-item.skip {{border-left-color:#555;opacity:.7}}
.feed-ts  {{color:#555;font-family:'JetBrains Mono',monospace;font-size:10px;min-width:38px}}
.feed-asset{{font-weight:700;min-width:52px}}
.feed-strat{{color:#8b949e;min-width:80px}}
.feed-note {{color:#8b949e;flex:1;font-size:11px}}
.feed-cagr {{font-family:'JetBrains Mono',monospace;font-weight:600;min-width:55px;text-align:right}}

/* Counter banner */
.counter-banner{{
  background:linear-gradient(135deg,#0d1f3c 0%,#0d2616 50%,#1a0d2e 100%);
  border:1px solid #30363d;border-radius:10px;
  padding:20px 28px;margin-bottom:18px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px;
  position:relative;overflow:hidden;
}}
.counter-banner::before{{
  content:'';position:absolute;inset:0;
  background:radial-gradient(ellipse at 20% 50%,#58a6ff0a 0%,transparent 60%),
             radial-gradient(ellipse at 80% 50%,#2ecc710a 0%,transparent 60%);
  pointer-events:none;
}}
.counter-main{{display:flex;align-items:baseline;gap:10px}}
.counter-number{{
  font-family:'JetBrains Mono',monospace;font-size:52px;font-weight:700;
  background:linear-gradient(90deg,#58a6ff,#2ecc71);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
  line-height:1;letter-spacing:-2px;
}}
.counter-label{{font-size:13px;color:#8b949e;line-height:1.4}}
.counter-label strong{{color:#e6edf3;display:block;font-size:15px}}
.counter-stats{{display:flex;gap:20px;flex-wrap:wrap}}
.counter-stat{{text-align:center}}
.counter-stat .val{{font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:700;color:#e6edf3}}
.counter-stat .lbl{{font-size:10px;color:#8b949e;margin-top:2px}}
.counter-rate{{
  font-family:'JetBrains Mono',monospace;font-size:12px;color:#2ecc71;
  background:#2ecc7110;border:1px solid #2ecc7130;border-radius:4px;padding:4px 10px;
}}

/* Footer */
.footer{{text-align:center;color:#8b949e;font-size:11px;padding:18px 0;border-top:1px solid #21262d;margin-top:16px}}

/* Responsive */
@media(max-width:600px){{
  .matrix th,.matrix td{{padding:4px 2px}}
  .asset-name{{font-size:11px}}
}}
</style>
</head>
<body>
<div class="container">

<!-- HEADER -->
<div class="hdr">
  <div>
    <h1>&#963; SIGMA ENGINE</h1>
    <div style="color:#8b949e;font-size:11px;margin-top:3px">Multi-Activo &bull; Multi-TF &bull; Futuros BTC/USDT</div>
  </div>
  <div class="meta">
    {now}<br>
    <span class="phase" style="background:{phase_col}18;color:{phase_col};border:1px solid {phase_col}">{phase_txt}</span>
  </div>
</div>

<!-- COUNTER BANNER -->
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
    <div class="counter-rate" id="live-rate">+{db.get("rate_hr",1328):,} / hora</div>
    <div style="font-size:11px;color:#8b949e;margin-top:6px">{n_ready} modelos OOS positivos</div>
    <div style="font-size:11px;color:#8b949e">{n_total - n_ready} pendientes</div>
  </div>
</div>

<!-- TRADING SIGNAL BANNER -->
<div id="trading-signal" style="padding:14px 20px;border-radius:8px;margin-bottom:18px;text-align:center;font-size:15px;font-weight:700;background:#21262d;border:2px solid #30363d">
  Cargando señal de mercado...
</div>

<!-- REGIME PANEL -->
<div style="margin-bottom:18px">
  <div style="font-size:11px;font-weight:700;color:#8b949e;text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px;display:flex;justify-content:space-between">
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
    <div style="font-size:11px;color:#8b949e;margin-bottom:6px">Mejores 2 ahora:</div>
    <div class="pills">{top2_pills}</div>
  </div>
  <div class="prog">Modelos listos: <strong>{n_ready}/{n_total}</strong></div>
</div>

<!-- MATRIX 5x5 -->
<div class="card" id="matrix-section">
  <div class="card-title" style="display:flex;justify-content:space-between">
    <span>Matriz de Modelos — 5 Activos x 5 Timeframes</span>
    <span style="font-weight:400;color:#555">
      <span style="color:#2ecc71">&#9632;</span> Positivo &nbsp;
      <span style="color:#58a6ff">&#9632;</span> Optimizando &nbsp;
      <span style="color:#30363d">&#9632;</span> Pendiente &nbsp;
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
          <th title="Scalping: TP fijo 0.2-0.35% | 5 estrategias | WR>52% requerido">1m &#9889;</th>
        </tr>
      </thead>
      <tbody>
        {matrix_rows}
      </tbody>
    </table>
  </div>
  <div style="font-size:11px;color:#555;margin-top:10px">
    1m = scalping con TP/SL fijo % &bull; 5 estrategias &bull; necesita WR &gt; 52% para cubrir comision 0.08%
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
    <div class="wft-num"><div class="n" style="color:#58a6ff">{wft_done}/97</div><div class="l">Ventanas</div></div>
    <div class="wft-num"><div class="n" style="color:{wft_col}">{wft_pct:.0f}%</div><div class="l">Positivas</div></div>
    <div class="wft-num"><div class="n" style="color:#2ecc71">{wft_pos}</div><div class="l">OK</div></div>
    <div class="wft-num"><div class="n" style="color:#e74c3c">{wft_done-wft_pos}</div><div class="l">Negativas</div></div>
  </div>
  <div class="progress"><div class="progress-fill" style="width:{round(wft_done/97*100,1)}%"></div></div>
  {"<div class='wft-scroll'><table class='t'><thead><tr><th>Ventana</th><th>Trades</th><th>WR</th><th>CAGR</th><th>&#10003;</th></tr></thead><tbody>" + wft_rows + "</tbody></table></div>" if wft_rows else "<div style='color:#8b949e;text-align:center;padding:16px'>Corriendo...</div>"}
</div>

<!-- CROSS-ASSET -->
{f'''
<div class="card">
  <div class="card-title">Cross-Asset &mdash; Params BTC en otros activos</div>
  <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">{ca_cards_html}</div>
  <div style="font-size:12px;color:#8b949e">
    Positivos: <strong style="color:#e6edf3">{len(ca_pos)}/4</strong> &nbsp;&bull;&nbsp;
    <strong style="color:{"#2ecc71" if len(ca_pos)>=3 else "#f1c40f"}">{ca_conf[:50] if ca_conf else "N/D"}</strong>
  </div>
</div>''' if ca_cards_html else ""}

<!-- VPS ACTIVITY -->
<div class="card">
  <div class="card-title">VPS &mdash; Actividad del Optimizador</div>
  <div class="tf-pills">{tf_counts if tf_counts else '<span style="color:#8b949e">Sin datos</span>'}</div>
  {"" if not top3_rows else f'<table class="t"><thead><tr><th>TF</th><th>Estrategia</th><th>CAGR IS</th><th>WR</th><th>Score</th></tr></thead><tbody>{top3_rows}</tbody></table>'}
</div>

<div class="card" style="text-align:center;padding:28px 20px">
  <div class="card-title" style="justify-content:center;margin-bottom:8px">
    &#9660; Descargar Pine Scripts &mdash; TradingView
  </div>
  <p style="color:#8b949e;font-size:13px;margin-bottom:20px;max-width:520px;margin-left:auto;margin-right:auto">
    Carga <strong style="color:#e6edf3">ambos indicadores</strong> en el mismo chart de TradingView.
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
      <div style="font-size:11px;color:#8b949e;margin-top:6px">Senales + SL/TP + Backtest</div>
    </div>
    <div style="text-align:center">
      <a href="/download/terminal" download="SIGMA_v13_COMPLETO.pine"
         style="display:inline-flex;align-items:center;gap:8px;padding:12px 24px;
                background:#21262d;color:#e6edf3;
                border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;
                border:1px solid #30363d;transition:all .2s"
         onmouseover="this.style.borderColor='#58a6ff'" onmouseout="this.style.borderColor='#30363d'">
        &#11015; SIGMA TERMINAL v13.0
      </a>
      <div style="font-size:11px;color:#8b949e;margin-top:6px">Analisis ICT / OFI / CVD / Bayesian</div>
    </div>
  </div>
  <div id="hud-info" style="font-size:12px;color:#8b949e">Cargando info...</div>

  <div style="margin-top:20px;border-top:1px solid #21262d;padding-top:16px">
    <div style="color:#8b949e;font-size:11px;margin-bottom:10px;text-align:center">
      &#9660; Modelos individuales — Pine Script por par/TF/estrategia (19 modelos)
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:6px;justify-content:center">
      
      <a href="/download/model/SIGMA_LTC_4H_momentum_short_CAGR48pct.pine" download="SIGMA_LTC_4H_momentum_short_CAGR48pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#f85149'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#f85149;font-weight:bold">▼</span>
        <span style="color:#8b949e">LTC 4H</span>
        <span style="color:#e6edf3">momentum short</span>
        <span style="color:#ffd700;font-weight:bold;margin-left:4px">A+</span>
        <span style="color:#3fb950;font-size:11px">+48.1%</span>
        <span style="color:#888;font-size:10px">WR 48%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_BTC_1H_momentum_short_CAGR46pct.pine" download="SIGMA_BTC_1H_momentum_short_CAGR46pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#f85149'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#f85149;font-weight:bold">▼</span>
        <span style="color:#8b949e">BTC 1H</span>
        <span style="color:#e6edf3">momentum short</span>
        <span style="color:#ffd700;font-weight:bold;margin-left:4px">A+</span>
        <span style="color:#3fb950;font-size:11px">+46.4%</span>
        <span style="color:#888;font-size:10px">WR 68%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_LTC_1H_breakout_CAGR42pct.pine" download="SIGMA_LTC_1H_breakout_CAGR42pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#00e676'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#00e676;font-weight:bold">▲</span>
        <span style="color:#8b949e">LTC 1H</span>
        <span style="color:#e6edf3">breakout</span>
        <span style="color:#ffd700;font-weight:bold;margin-left:4px">A+</span>
        <span style="color:#3fb950;font-size:11px">+42.8%</span>
        <span style="color:#888;font-size:10px">WR 84%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_SOL_1H_breakdown_CAGR36pct.pine" download="SIGMA_SOL_1H_breakdown_CAGR36pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#f85149'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#f85149;font-weight:bold">▼</span>
        <span style="color:#8b949e">SOL 1H</span>
        <span style="color:#e6edf3">breakdown</span>
        <span style="color:#00c853;font-weight:bold;margin-left:4px">A</span>
        <span style="color:#3fb950;font-size:11px">+36.9%</span>
        <span style="color:#888;font-size:10px">WR 75%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_BTC_1H_momentum_CAGR32pct.pine" download="SIGMA_BTC_1H_momentum_CAGR32pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#00e676'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#00e676;font-weight:bold">▲</span>
        <span style="color:#8b949e">BTC 1H</span>
        <span style="color:#e6edf3">momentum</span>
        <span style="color:#00c853;font-weight:bold;margin-left:4px">A</span>
        <span style="color:#3fb950;font-size:11px">+32.0%</span>
        <span style="color:#888;font-size:10px">WR 58%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_BNB_4H_breakout_CAGR29pct.pine" download="SIGMA_BNB_4H_breakout_CAGR29pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#00e676'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#00e676;font-weight:bold">▲</span>
        <span style="color:#8b949e">BNB 4H</span>
        <span style="color:#e6edf3">breakout</span>
        <span style="color:#00c853;font-weight:bold;margin-left:4px">A</span>
        <span style="color:#3fb950;font-size:11px">+29.0%</span>
        <span style="color:#888;font-size:10px">WR 78%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_SOL_15M_regime_adaptive_CAGR28pct.pine" download="SIGMA_SOL_15M_regime_adaptive_CAGR28pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#00e676'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#00e676;font-weight:bold">▲</span>
        <span style="color:#8b949e">SOL 15M</span>
        <span style="color:#e6edf3">regime adaptive</span>
        <span style="color:#00c853;font-weight:bold;margin-left:4px">A</span>
        <span style="color:#3fb950;font-size:11px">+28.1%</span>
        <span style="color:#888;font-size:10px">WR 58%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_SOL_4H_momentum_short_CAGR24pct.pine" download="SIGMA_SOL_4H_momentum_short_CAGR24pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#f85149'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#f85149;font-weight:bold">▼</span>
        <span style="color:#8b949e">SOL 4H</span>
        <span style="color:#e6edf3">momentum short</span>
        <span style="color:#69f0ae;font-weight:bold;margin-left:4px">B</span>
        <span style="color:#3fb950;font-size:11px">+24.5%</span>
        <span style="color:#888;font-size:10px">WR 57%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_ETH_4H_tma_bands_CAGR18pct.pine" download="SIGMA_ETH_4H_tma_bands_CAGR18pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#00e676'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#00e676;font-weight:bold">▲</span>
        <span style="color:#8b949e">ETH 4H</span>
        <span style="color:#e6edf3">tma bands</span>
        <span style="color:#69f0ae;font-weight:bold;margin-left:4px">B</span>
        <span style="color:#3fb950;font-size:11px">+18.3%</span>
        <span style="color:#888;font-size:10px">WR 79%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_ETH_1H_breakout_CAGR13pct.pine" download="SIGMA_ETH_1H_breakout_CAGR13pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#00e676'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#00e676;font-weight:bold">▲</span>
        <span style="color:#8b949e">ETH 1H</span>
        <span style="color:#e6edf3">breakout</span>
        <span style="color:#ff9800;font-weight:bold;margin-left:4px">C</span>
        <span style="color:#3fb950;font-size:11px">+13.0%</span>
        <span style="color:#888;font-size:10px">WR 69%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_ETH_1H_momentum_short_CAGR11pct.pine" download="SIGMA_ETH_1H_momentum_short_CAGR11pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#f85149'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#f85149;font-weight:bold">▼</span>
        <span style="color:#8b949e">ETH 1H</span>
        <span style="color:#e6edf3">momentum short</span>
        <span style="color:#ff9800;font-weight:bold;margin-left:4px">C</span>
        <span style="color:#3fb950;font-size:11px">+11.5%</span>
        <span style="color:#888;font-size:10px">WR 78%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_BTC_4H_tma_bands_CAGR11pct.pine" download="SIGMA_BTC_4H_tma_bands_CAGR11pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#00e676'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#00e676;font-weight:bold">▲</span>
        <span style="color:#8b949e">BTC 4H</span>
        <span style="color:#e6edf3">tma bands</span>
        <span style="color:#ff9800;font-weight:bold;margin-left:4px">C</span>
        <span style="color:#3fb950;font-size:11px">+11.2%</span>
        <span style="color:#888;font-size:10px">WR 71%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_ETH_4H_momentum_short_CAGR9pct.pine" download="SIGMA_ETH_4H_momentum_short_CAGR9pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#f85149'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#f85149;font-weight:bold">▼</span>
        <span style="color:#8b949e">ETH 4H</span>
        <span style="color:#e6edf3">momentum short</span>
        <span style="color:#ff9800;font-weight:bold;margin-left:4px">C</span>
        <span style="color:#3fb950;font-size:11px">+9.8%</span>
        <span style="color:#888;font-size:10px">WR 73%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_BTC_4H_breakout_CAGR7pct.pine" download="SIGMA_BTC_4H_breakout_CAGR7pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#00e676'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#00e676;font-weight:bold">▲</span>
        <span style="color:#8b949e">BTC 4H</span>
        <span style="color:#e6edf3">breakout</span>
        <span style="color:#666;font-weight:bold;margin-left:4px">D</span>
        <span style="color:#3fb950;font-size:11px">+7.1%</span>
        <span style="color:#888;font-size:10px">WR 62%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_BNB_1H_breakdown_CAGR6pct.pine" download="SIGMA_BNB_1H_breakdown_CAGR6pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#f85149'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#f85149;font-weight:bold">▼</span>
        <span style="color:#8b949e">BNB 1H</span>
        <span style="color:#e6edf3">breakdown</span>
        <span style="color:#666;font-weight:bold;margin-left:4px">D</span>
        <span style="color:#3fb950;font-size:11px">+6.9%</span>
        <span style="color:#888;font-size:10px">WR 73%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_BNB_2H_pullback_short_CAGR5pct.pine" download="SIGMA_BNB_2H_pullback_short_CAGR5pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#f85149'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#f85149;font-weight:bold">▼</span>
        <span style="color:#8b949e">BNB 2H</span>
        <span style="color:#e6edf3">pullback short</span>
        <span style="color:#666;font-weight:bold;margin-left:4px">D</span>
        <span style="color:#3fb950;font-size:11px">+5.8%</span>
        <span style="color:#888;font-size:10px">WR 64%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_SOL_4H_pullback_CAGR5pct.pine" download="SIGMA_SOL_4H_pullback_CAGR5pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#00e676'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#00e676;font-weight:bold">▲</span>
        <span style="color:#8b949e">SOL 4H</span>
        <span style="color:#e6edf3">pullback</span>
        <span style="color:#666;font-weight:bold;margin-left:4px">D</span>
        <span style="color:#3fb950;font-size:11px">+5.2%</span>
        <span style="color:#888;font-size:10px">WR 50%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_SOL_5M_mean_rev_CAGR4pct.pine" download="SIGMA_SOL_5M_mean_rev_CAGR4pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#00e676'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#00e676;font-weight:bold">▲</span>
        <span style="color:#8b949e">SOL 5M</span>
        <span style="color:#e6edf3">mean rev</span>
        <span style="color:#666;font-weight:bold;margin-left:4px">D</span>
        <span style="color:#3fb950;font-size:11px">+4.8%</span>
        <span style="color:#888;font-size:10px">WR 82%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
      <a href="/download/model/SIGMA_LTC_4H_tma_bands_CAGR3pct.pine" download="SIGMA_LTC_4H_tma_bands_CAGR3pct.pine"
         style="display:flex;align-items:center;gap:8px;padding:8px 14px;
                background:#161b22;color:#e6edf3;border-radius:6px;
                text-decoration:none;font-size:12px;border:1px solid #21262d;
                transition:border-color .2s;white-space:nowrap"
         onmouseover="this.style.borderColor='#00e676'" onmouseout="this.style.borderColor='#21262d'">
        <span style="color:#00e676;font-weight:bold">▲</span>
        <span style="color:#8b949e">LTC 4H</span>
        <span style="color:#e6edf3">tma bands</span>
        <span style="color:#666;font-weight:bold;margin-left:4px">D</span>
        <span style="color:#3fb950;font-size:11px">+3.0%</span>
        <span style="color:#888;font-size:10px">WR 87%</span>
        <span style="color:#444;font-size:10px">&#11015;</span>
      </a>
    </div>
  </div>
</div>


<!-- ═══════════════ SIGMA ENGINE BOOK ═══════════════ -->
<div style="background:#0d1117;border:1px solid #21262d;border-radius:12px;margin:20px 0;overflow:hidden;font-family:monospace">
<div style="background:linear-gradient(135deg,#0d1f3c 0%,#161b22 100%);padding:24px 28px;border-bottom:1px solid #21262d">
<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px">
<div><div style="color:#58a6ff;font-size:10px;letter-spacing:3px;font-weight:600;margin-bottom:6px">SIGMA QUANTITATIVE SYSTEMS</div><div style="color:#e6edf3;font-size:22px;font-weight:800;letter-spacing:-0.5px">ENGINE REPORT</div><div style="color:#8b949e;font-size:12px;margin-top:4px">Binance Futures Perpetual &middot; IS/OOS 80/20 &middot; Anti-overfit &middot; WFT &middot; Monte Carlo</div></div>
<div style="text-align:right"><div style="color:#58a6ff;font-size:11px;font-weight:600">Generado</div><div style="color:#e6edf3;font-size:13px">2026-05-10 13:57</div><div style="color:#8b949e;font-size:10px;margin-top:2px">&#218;ltimo modelo: 2026-05-10 17:37</div></div></div>
<div style="display:flex;gap:0;margin-top:20px;border:1px solid #21262d;border-radius:8px;overflow:hidden;flex-wrap:wrap">
<div style="flex:1;padding:14px 18px;border-right:1px solid #21262d;min-width:90px;"><div style="color:#444;font-size:9px;letter-spacing:1px">SLOTS ACTIVOS</div><div style="color:#e6edf3;font-size:24px;font-weight:800;margin-top:2px">12</div><div style="color:#444;font-size:9px;margin-top:2px">19 modelos total</div></div><div style="flex:1;padding:14px 18px;border-right:1px solid #21262d;min-width:90px;background:#0a1628"><div style="color:#444;font-size:9px;letter-spacing:1px">MEJOR SLOT</div><div style="color:#00c853;font-size:24px;font-weight:800;margin-top:2px">+48.1%</div><div style="color:#444;font-size:9px;margin-top:2px">LTC 4H &middot; ver matriz &#8593;</div></div><div style="flex:1;padding:14px 18px;border-right:1px solid #21262d;min-width:90px;"><div style="color:#444;font-size:9px;letter-spacing:1px">PISO WR</div><div style="color:#58a6ff;font-size:24px;font-weight:800;margin-top:2px">50%</div><div style="color:#444;font-size:9px;margin-top:2px">WR m&#237;nimo del portafolio</div></div><div style="flex:1;padding:14px 18px;border-right:1px solid #21262d;min-width:90px;background:#0a1628"><div style="color:#444;font-size:9px;letter-spacing:1px">MC CONFIANZA</div><div style="color:#a78bfa;font-size:24px;font-weight:800;margin-top:2px">94%</div><div style="color:#444;font-size:9px;margin-top:2px">Monte Carlo 2000</div></div><div style="flex:1;padding:14px 18px;border-right:1px solid #21262d;min-width:90px;"><div style="color:#444;font-size:9px;letter-spacing:1px">TRIALS DB</div><div style="color:#8b949e;font-size:24px;font-weight:800;margin-top:2px">592K</div><div style="color:#444;font-size:9px;margin-top:2px">Optuna acumulado</div></div><div style="flex:1;padding:14px 18px;border-right:1px solid #21262d;min-width:90px;background:#0a1628"><div style="color:#444;font-size:9px;letter-spacing:1px">COBERTURA</div><div style="color:#e6edf3;font-size:24px;font-weight:800;margin-top:2px"><span style="color:#00e676">7L</span> / <span style="color:#f85149">5S</span></div><div style="color:#444;font-size:9px;margin-top:2px">5 activos &middot; 4 TFs</div></div>
</div></div>
<div style="padding:12px 28px;background:#0a1628;border-bottom:1px solid #21262d;display:flex;align-items:center;gap:14px;flex-wrap:wrap"><div style="color:#ffd700;font-size:10px;letter-spacing:2px;font-weight:700;white-space:nowrap">&#11088; MEJOR MODELO</div><div style="flex:1;min-width:160px"><span style="color:#e6edf3;font-size:14px;font-weight:700">LTC 4H</span> <span style="color:#333">&#8212;</span> <span style="color:#8b949e;font-size:12px">momentum short</span></div><div style="display:flex;gap:18px;flex-wrap:wrap"><div style="text-align:center"><div style="color:#444;font-size:9px">CAGR</div><div style="color:#00c853;font-weight:700;font-size:15px">+48.1%</div></div><div style="text-align:center"><div style="color:#444;font-size:9px">WR</div><div style="color:#58a6ff;font-weight:700;font-size:15px">48%</div></div><div style="text-align:center"><div style="color:#444;font-size:9px">DD</div><div style="color:#ff9800;font-weight:700;font-size:15px">-16.1%</div></div><div style="text-align:center"><div style="color:#444;font-size:9px">MC</div><div style="color:#a78bfa;font-weight:700;font-size:15px">98%</div></div><div style="text-align:center"><div style="color:#444;font-size:9px">T/AÑO</div><div style="color:#8b949e;font-weight:700;font-size:15px">15</div></div><div style="text-align:center"><div style="color:#444;font-size:9px">OOS</div><div style="color:#8b949e;font-weight:700;font-size:15px">1.7y</div></div></div></div>
<div style="padding:0 28px 20px">
    <div style="color:#8b949e;font-size:11px;letter-spacing:1px;margin-bottom:10px">
      &#11088; ÚLTIMAS MEJORAS <span style="color:#333">&mdash; nuevos récords del optimizador</span></div>
    <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="border-bottom:1px solid #21262d">
        <th style="padding:5px 8px;color:#444;font-size:10px;width:20px"></th>
        <th style="padding:5px 8px;color:#8b949e;font-size:10px;text-align:left">HORA</th>
        <th style="padding:5px 8px;color:#8b949e;font-size:10px;text-align:left">MODELO</th>
        <th style="padding:5px 8px;color:#8b949e;font-size:10px;text-align:left">ESTRATEGIA</th>
        <th style="padding:5px 8px;color:#8b949e;font-size:10px;text-align:right">CAGR OOS</th>
        <th style="padding:5px 8px;color:#8b949e;font-size:10px;text-align:right">WR</th>
        <th style="padding:5px 8px;color:#8b949e;font-size:10px;text-align:left">NOTA</th>
      </tr></thead>
      <tbody><tr><td colspan=7 style=padding:12px;color:#333;text-align:center>Esperando nuevos récords del optimizador...</td></tr></tbody>
    </table></div></div><div style="padding:16px 28px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:6px"><div style="color:#8b949e;font-size:11px;letter-spacing:1px">TODOS LOS MODELOS <span style="color:#333">&middot; alternativos del mismo slot aparecen atenuados</span></div><div style="color:#333;font-size:10px">12 slots &middot; 19 modelos</div></div><div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr style="border-bottom:2px solid #21262d"><th style="padding:5px 10px;color:#444;font-size:10px;text-align:right">#</th><th style="padding:5px 10px;color:#8b949e;font-size:10px;text-align:left">ACTIVO</th><th style="padding:5px 6px;color:#8b949e;font-size:10px;text-align:center">TF</th><th style="padding:5px 10px;color:#8b949e;font-size:10px;text-align:left">ESTRATEGIA</th><th style="padding:5px 8px;color:#8b949e;font-size:10px;text-align:center">GRADE</th><th style="padding:5px 10px;color:#8b949e;font-size:10px;text-align:right">CAGR OOS</th><th style="padding:5px 10px;color:#8b949e;font-size:10px;text-align:right">WR</th><th style="padding:5px 10px;color:#8b949e;font-size:10px;text-align:right">MAX DD</th><th style="padding:5px 10px;color:#8b949e;font-size:10px;text-align:right">T/A&#209;O</th><th style="padding:5px 10px;color:#8b949e;font-size:10px;text-align:center">MC%</th><th style="padding:5px 10px;color:#8b949e;font-size:10px;text-align:right">GUARDADO</th></tr></thead><tbody><tr style="border-bottom:1px solid #161b22;background:#0d1117"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">1</td><td style="padding:6px 10px"><b style="color:#c9d1d9">LTC</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">4H</span></td><td style="padding:6px 10px"><span style="color:#f85149;font-size:10px">&#9660;</span> <span style="color:#8b949e;font-size:12px">momentum short</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#ffd700;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">A+</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-weight:700;font-size:13px">+48.1%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:58px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#8b949e;font-size:12px">48%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:29px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#ff9800;font-size:12px">-16.1%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">15</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">98%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-10 09:52</td></tr><tr style="border-bottom:1px solid #161b22;background:#090c10"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">2</td><td style="padding:6px 10px"><b style="color:#c9d1d9">BTC</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">1H</span></td><td style="padding:6px 10px"><span style="color:#f85149;font-size:10px">&#9660;</span> <span style="color:#8b949e;font-size:12px">momentum short</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#ffd700;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">A+</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-weight:700;font-size:13px">+46.4%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:56px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#69f0ae;font-size:12px">68%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:41px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#ff9800;font-size:12px">-22.2%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">22</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">94%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-10 17:29</td></tr><tr style="border-bottom:1px solid #161b22;background:#0d1117"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">3</td><td style="padding:6px 10px"><b style="color:#c9d1d9">LTC</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">1H</span></td><td style="padding:6px 10px"><span style="color:#00e676;font-size:10px">&#9650;</span> <span style="color:#8b949e;font-size:12px">breakout</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#ffd700;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">A+</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-weight:700;font-size:13px">+42.8%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:51px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-size:12px">84%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:51px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#69f0ae;font-size:12px">-9.4%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">11</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">98%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-09 21:35</td></tr><tr style="border-bottom:1px solid #161b22;background:#090c10"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">4</td><td style="padding:6px 10px"><b style="color:#c9d1d9">SOL</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">1H</span></td><td style="padding:6px 10px"><span style="color:#f85149;font-size:10px">&#9660;</span> <span style="color:#8b949e;font-size:12px">breakdown</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#00c853;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">A</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-weight:700;font-size:13px">+36.9%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:44px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-size:12px">75%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:45px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#69f0ae;font-size:12px">-11.9%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">17</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">99%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-09 18:53</td></tr><tr style="border-bottom:1px solid #161b22;opacity:0.38;background:#0d1117"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">5</td><td style="padding:6px 10px"><b style="color:#c9d1d9">BTC</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">1H</span></td><td style="padding:6px 10px"><span style="color:#00e676;font-size:10px">&#9650;</span> <span style="color:#8b949e;font-size:12px">momentum</span> <span style="color:#2a2a2a;font-size:9px">(alt)</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#00c853;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">A</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-weight:700;font-size:13px">+32.0%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:38px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#69f0ae;font-size:12px">58%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:35px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#ff9800;font-size:12px">-23.1%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">29</td><td style="padding:6px 10px;text-align:center"><span style="color:#69f0ae;font-size:11px;font-weight:600">83%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-10 17:16</td></tr><tr style="border-bottom:1px solid #161b22;background:#090c10"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">6</td><td style="padding:6px 10px"><b style="color:#c9d1d9">BNB</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">4H</span></td><td style="padding:6px 10px"><span style="color:#00e676;font-size:10px">&#9650;</span> <span style="color:#8b949e;font-size:12px">breakout</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#00c853;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">A</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#69f0ae;font-weight:700;font-size:13px">+29.0%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:35px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-size:12px">78%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:47px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#69f0ae;font-size:12px">-7.0%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">11</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">99%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-09 22:04</td></tr><tr style="border-bottom:1px solid #161b22;background:#0d1117"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">7</td><td style="padding:6px 10px"><b style="color:#c9d1d9">SOL</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">15M</span></td><td style="padding:6px 10px"><span style="color:#00e676;font-size:10px">&#9650;</span> <span style="color:#8b949e;font-size:12px">regime adaptive</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#00c853;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">A</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#69f0ae;font-weight:700;font-size:13px">+28.1%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:34px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#69f0ae;font-size:12px">58%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:35px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#f44336;font-size:12px">-36.7%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">122</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">100%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-10 15:33</td></tr><tr style="border-bottom:1px solid #161b22;background:#090c10"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">8</td><td style="padding:6px 10px"><b style="color:#c9d1d9">SOL</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">4H</span></td><td style="padding:6px 10px"><span style="color:#f85149;font-size:10px">&#9660;</span> <span style="color:#8b949e;font-size:12px">momentum short</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#69f0ae;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">B</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#69f0ae;font-weight:700;font-size:13px">+24.5%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:29px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#69f0ae;font-size:12px">57%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:34px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#69f0ae;font-size:12px">-13.5%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">18</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">92%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-10 09:58</td></tr><tr style="border-bottom:1px solid #161b22;background:#0d1117"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">9</td><td style="padding:6px 10px"><b style="color:#c9d1d9">ETH</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">4H</span></td><td style="padding:6px 10px"><span style="color:#00e676;font-size:10px">&#9650;</span> <span style="color:#8b949e;font-size:12px">tma bands</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#69f0ae;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">B</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#69f0ae;font-weight:700;font-size:13px">+18.3%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:22px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-size:12px">79%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:47px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#69f0ae;font-size:12px">-10.7%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">16</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">93%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-10 05:19</td></tr><tr style="border-bottom:1px solid #161b22;background:#090c10"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">10</td><td style="padding:6px 10px"><b style="color:#c9d1d9">ETH</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">1H</span></td><td style="padding:6px 10px"><span style="color:#00e676;font-size:10px">&#9650;</span> <span style="color:#8b949e;font-size:12px">breakout</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#ff9800;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">C</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#8b949e;font-weight:700;font-size:13px">+13.0%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:16px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#69f0ae;font-size:12px">69%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:42px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#69f0ae;font-size:12px">-2.2%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">8</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">98%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-08 22:36</td></tr><tr style="border-bottom:1px solid #161b22;opacity:0.38;background:#0d1117"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">11</td><td style="padding:6px 10px"><b style="color:#c9d1d9">ETH</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">1H</span></td><td style="padding:6px 10px"><span style="color:#f85149;font-size:10px">&#9660;</span> <span style="color:#8b949e;font-size:12px">momentum short</span> <span style="color:#2a2a2a;font-size:9px">(alt)</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#ff9800;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">C</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#8b949e;font-weight:700;font-size:13px">+11.5%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:14px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-size:12px">78%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:47px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#69f0ae;font-size:12px">-12.8%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">13</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">96%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-10 03:02</td></tr><tr style="border-bottom:1px solid #161b22;background:#090c10"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">12</td><td style="padding:6px 10px"><b style="color:#c9d1d9">BTC</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">4H</span></td><td style="padding:6px 10px"><span style="color:#00e676;font-size:10px">&#9650;</span> <span style="color:#8b949e;font-size:12px">tma bands</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#ff9800;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">C</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#8b949e;font-weight:700;font-size:13px">+11.2%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:13px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-size:12px">71%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:42px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#ff9800;font-size:12px">-15.9%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">14</td><td style="padding:6px 10px;text-align:center"><span style="color:#ff9800;font-size:11px;font-weight:600">60%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-10 17:37</td></tr><tr style="border-bottom:1px solid #161b22;opacity:0.38;background:#0d1117"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">13</td><td style="padding:6px 10px"><b style="color:#c9d1d9">ETH</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">4H</span></td><td style="padding:6px 10px"><span style="color:#f85149;font-size:10px">&#9660;</span> <span style="color:#8b949e;font-size:12px">momentum short</span> <span style="color:#2a2a2a;font-size:9px">(alt)</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#ff9800;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">C</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#8b949e;font-weight:700;font-size:13px">+9.8%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:12px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-size:12px">73%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:44px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#69f0ae;font-size:12px">-12.9%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">13</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">90%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-10 05:21</td></tr><tr style="border-bottom:1px solid #161b22;opacity:0.38;background:#090c10"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">14</td><td style="padding:6px 10px"><b style="color:#c9d1d9">BTC</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">4H</span></td><td style="padding:6px 10px"><span style="color:#00e676;font-size:10px">&#9650;</span> <span style="color:#8b949e;font-size:12px">breakout</span> <span style="color:#2a2a2a;font-size:9px">(alt)</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#666;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">D</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#8b949e;font-weight:700;font-size:13px">+7.1%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:9px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#69f0ae;font-size:12px">62%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:38px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#ff9800;font-size:12px">-16.6%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">18</td><td style="padding:6px 10px;text-align:center"><span style="color:#69f0ae;font-size:11px;font-weight:600">76%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-09 19:16</td></tr><tr style="border-bottom:1px solid #161b22;background:#0d1117"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">15</td><td style="padding:6px 10px"><b style="color:#c9d1d9">BNB</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">1H</span></td><td style="padding:6px 10px"><span style="color:#f85149;font-size:10px">&#9660;</span> <span style="color:#8b949e;font-size:12px">breakdown</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#666;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">D</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#8b949e;font-weight:700;font-size:13px">+6.9%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:8px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-size:12px">73%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:44px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#69f0ae;font-size:12px">-11.3%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">6</td><td style="padding:6px 10px;text-align:center"><span style="color:#69f0ae;font-size:11px;font-weight:600">89%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-09 21:11</td></tr><tr style="border-bottom:1px solid #161b22;opacity:0.38;background:#090c10"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">16</td><td style="padding:6px 10px"><b style="color:#c9d1d9">SOL</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">4H</span></td><td style="padding:6px 10px"><span style="color:#00e676;font-size:10px">&#9650;</span> <span style="color:#8b949e;font-size:12px">tma bands</span> <span style="color:#2a2a2a;font-size:9px">(alt)</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#666;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">D</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#8b949e;font-weight:700;font-size:13px">+6.7%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:8px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-size:12px">74%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:45px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#69f0ae;font-size:12px">-1.9%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">34</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">91%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-09 09:01</td></tr><tr style="border-bottom:1px solid #161b22;opacity:0.38;background:#0d1117"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">17</td><td style="padding:6px 10px"><b style="color:#c9d1d9">SOL</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">4H</span></td><td style="padding:6px 10px"><span style="color:#00e676;font-size:10px">&#9650;</span> <span style="color:#8b949e;font-size:12px">pullback</span> <span style="color:#2a2a2a;font-size:9px">(alt)</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#666;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">D</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#8b949e;font-weight:700;font-size:13px">+5.2%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:6px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#8b949e;font-size:12px">50%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:30px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#69f0ae;font-size:12px">-2.1%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">10</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">91%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-08 23:19</td></tr><tr style="border-bottom:1px solid #161b22;background:#090c10"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">18</td><td style="padding:6px 10px"><b style="color:#c9d1d9">SOL</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">5M</span></td><td style="padding:6px 10px"><span style="color:#00e676;font-size:10px">&#9650;</span> <span style="color:#8b949e;font-size:12px">mean rev</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#666;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">D</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#8b949e;font-weight:700;font-size:13px">+4.8%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:6px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-size:12px">82%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:49px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#69f0ae;font-size:12px">-1.5%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">15</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">100%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-09 02:41</td></tr><tr style="border-bottom:1px solid #161b22;opacity:0.38;background:#0d1117"><td style="padding:6px 10px;color:#444;font-size:10px;text-align:right">19</td><td style="padding:6px 10px"><b style="color:#c9d1d9">LTC</b></td><td style="padding:6px 6px;text-align:center"><span style="background:#1c2128;color:#58a6ff;padding:1px 6px;border-radius:10px;font-size:11px">4H</span></td><td style="padding:6px 10px"><span style="color:#00e676;font-size:10px">&#9650;</span> <span style="color:#8b949e;font-size:12px">tma bands</span> <span style="color:#2a2a2a;font-size:9px">(alt)</span></td><td style="padding:6px 8px;text-align:center"><span style="background:#666;color:#000;padding:2px 8px;border-radius:10px;font-weight:800;font-size:11px">D</span></td><td style="padding:6px 10px;text-align:right"><span style="color:#8b949e;font-weight:700;font-size:13px">+3.0%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:4px;height:5px;background:#238636;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right"><span style="color:#00c853;font-size:12px">87%</span><div style="margin-top:2px"><div style="display:inline-block;width:60px;height:5px;background:#1c2128;border-radius:3px;vertical-align:middle"><div style="width:52px;height:5px;background:#1f6feb;border-radius:3px"></div></div></div></td><td style="padding:6px 10px;text-align:right;color:#69f0ae;font-size:12px">-1.1%</td><td style="padding:6px 10px;text-align:right;color:#8b949e;font-size:11px">9</td><td style="padding:6px 10px;text-align:center"><span style="color:#00c853;font-size:11px;font-weight:600">98%</span></td><td style="padding:6px 10px;text-align:right;color:#30363d;font-size:10px">05-09 16:51</td></tr></tbody></table></div><div style="margin-top:12px;padding:10px 14px;background:#161b22;border-radius:6px;border-left:3px solid #1f6feb"><div style="color:#58a6ff;font-size:10px;font-weight:600;letter-spacing:1px;margin-bottom:3px">METODOLOG&#205;A</div><div style="color:#8b949e;font-size:10px;line-height:1.6">Datos: Binance Futures OHLCV &middot; Fee 0.04% RT &middot; Funding hist&#243;rico real &middot; Split IS/OOS 80/20 &middot; Anti-overfit &le;2.5&times; &middot; WFT &ge;55% ventanas &middot; Monte Carlo 2000 sims &middot; Kelly sizing &lt;6% &middot; Leverage 2&times; para retornos estimados</div></div></div>
<div style="padding:0 28px 20px"><div style="color:#8b949e;font-size:11px;letter-spacing:1px;margin-bottom:10px">COBERTURA POR ACTIVO</div><div style="display:flex;gap:10px;flex-wrap:wrap"><div style="background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px 16px;min-width:115px;flex:1"><b style="color:#c9d1d9;font-size:14px">BNB</b><div style="margin:4px 0"><span style="background:#1c2128;color:#58a6ff;padding:1px 5px;border-radius:6px;font-size:9px;margin:1px">4H</span><span style="background:#1c2128;color:#58a6ff;padding:1px 5px;border-radius:6px;font-size:9px;margin:1px">1H</span></div><div style="color:#00c853;font-size:19px;font-weight:800;margin-top:6px">+29.0%</div><div style="color:#8b949e;font-size:10px">WR 78% &middot; DD -7.0%</div><div style="margin-top:6px"><span style="background:#00c853;color:#000;padding:1px 8px;border-radius:8px;font-weight:700;font-size:11px">A</span></div></div><div style="background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px 16px;min-width:115px;flex:1"><b style="color:#c9d1d9;font-size:14px">BTC</b><div style="margin:4px 0"><span style="background:#1c2128;color:#58a6ff;padding:1px 5px;border-radius:6px;font-size:9px;margin:1px">1H</span><span style="background:#1c2128;color:#58a6ff;padding:1px 5px;border-radius:6px;font-size:9px;margin:1px">4H</span></div><div style="color:#00c853;font-size:19px;font-weight:800;margin-top:6px">+46.4%</div><div style="color:#8b949e;font-size:10px">WR 68% &middot; DD -22.2%</div><div style="margin-top:6px"><span style="background:#ffd700;color:#000;padding:1px 8px;border-radius:8px;font-weight:700;font-size:11px">A+</span></div></div><div style="background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px 16px;min-width:115px;flex:1"><b style="color:#c9d1d9;font-size:14px">ETH</b><div style="margin:4px 0"><span style="background:#1c2128;color:#58a6ff;padding:1px 5px;border-radius:6px;font-size:9px;margin:1px">4H</span><span style="background:#1c2128;color:#58a6ff;padding:1px 5px;border-radius:6px;font-size:9px;margin:1px">1H</span></div><div style="color:#00c853;font-size:19px;font-weight:800;margin-top:6px">+18.3%</div><div style="color:#8b949e;font-size:10px">WR 79% &middot; DD -10.7%</div><div style="margin-top:6px"><span style="background:#69f0ae;color:#000;padding:1px 8px;border-radius:8px;font-weight:700;font-size:11px">B</span></div></div><div style="background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px 16px;min-width:115px;flex:1"><b style="color:#c9d1d9;font-size:14px">LTC</b><div style="margin:4px 0"><span style="background:#1c2128;color:#58a6ff;padding:1px 5px;border-radius:6px;font-size:9px;margin:1px">4H</span><span style="background:#1c2128;color:#58a6ff;padding:1px 5px;border-radius:6px;font-size:9px;margin:1px">1H</span></div><div style="color:#00c853;font-size:19px;font-weight:800;margin-top:6px">+48.1%</div><div style="color:#8b949e;font-size:10px">WR 48% &middot; DD -16.1%</div><div style="margin-top:6px"><span style="background:#ffd700;color:#000;padding:1px 8px;border-radius:8px;font-weight:700;font-size:11px">A+</span></div></div><div style="background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px 16px;min-width:115px;flex:1"><b style="color:#c9d1d9;font-size:14px">SOL</b><div style="margin:4px 0"><span style="background:#1c2128;color:#58a6ff;padding:1px 5px;border-radius:6px;font-size:9px;margin:1px">1H</span><span style="background:#1c2128;color:#58a6ff;padding:1px 5px;border-radius:6px;font-size:9px;margin:1px">15M</span><span style="background:#1c2128;color:#58a6ff;padding:1px 5px;border-radius:6px;font-size:9px;margin:1px">4H</span><span style="background:#1c2128;color:#58a6ff;padding:1px 5px;border-radius:6px;font-size:9px;margin:1px">5M</span></div><div style="color:#00c853;font-size:19px;font-weight:800;margin-top:6px">+36.9%</div><div style="color:#8b949e;font-size:10px">WR 75% &middot; DD -11.9%</div><div style="margin-top:6px"><span style="background:#00c853;color:#000;padding:1px 8px;border-radius:8px;font-weight:700;font-size:11px">A</span></div></div></div></div>
<div style="padding:12px 28px;background:#090c10;border-top:1px solid #161b22;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px"><div style="color:#30363d;font-size:10px">SIGMA ENGINE v3 &middot; BNB &middot; BTC &middot; ETH &middot; LTC &middot; SOL &middot; TFs: 15m 1h 4h 5m</div><div style="color:#30363d;font-size:10px">592,912 trials &middot; 19 modelos &middot; 12 slots activos &middot; retrain 24/7</div></div>
</div>
<!-- ═══════════════════════════════════════════════ -->

<div class="footer">
  SIGMA ENGINE &nbsp;&mdash;&nbsp; Counter en vivo cada 5s &nbsp;&mdash;&nbsp; Pagina cada 60s &nbsp;&mdash;&nbsp; {now}
</div>

</div>

<script>
const TF_COLORS = {{'1h':'#58a6ff','4h':'#2ecc71','15m':'#f1c40f','5m':'#e67e22','2h':'#a78bfa','1m':'#8b949e'}};

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
      if (re) re.innerText = '+' + (d.rate_hr || 0).toLocaleString() + ' / hora';

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
    const col  = ASSET_COLORS[asset] || '#e6edf3';
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

function updateTradingSignal(data) {{
  const banner = document.getElementById('trading-signal');
  if (!banner || !data) return;
  const bulls = Object.entries(data).filter(([a,r]) => r.regime === 'BULL').map(([a]) => a);
  const ranges = Object.entries(data).filter(([a,r]) => r.regime === 'RANGE').map(([a]) => a);
  const bears  = Object.entries(data).filter(([a,r]) => r.regime === 'BEAR').map(([a]) => a);

  if (bulls.length >= 2) {{
    banner.style.background = '#0d2016';
    banner.style.borderColor = '#2ecc71';
    banner.style.color = '#2ecc71';
    banner.innerHTML = '&#11088; MERCADO BULL — Operar con size normal | ' + bulls.join(', ') + ' en BULL';
  }} else if (bulls.length === 1) {{
    banner.style.background = '#1a1800';
    banner.style.borderColor = '#f1c40f';
    banner.style.color = '#f1c40f';
    banner.innerHTML = '&#126; MERCADO MIXTO — Solo ' + bulls[0] + ' en BULL | Reducir size al 50%';
  }} else if (ranges.length >= 2) {{
    banner.style.background = '#1a1800';
    banner.style.borderColor = '#f1c40f';
    banner.style.color = '#f1c40f';
    banner.innerHTML = '&#126; MERCADO RANGE — Solo señales muy fuertes | Max 1 par activo';
  }} else {{
    banner.style.background = '#1a0d0d';
    banner.style.borderColor = '#e74c3c';
    banner.style.color = '#e74c3c';
    banner.innerHTML = '&#10060; MERCADO BEAR — NO OPERAR | Esperar recuperacion | ' + bears.length + '/5 pares en BEAR';
  }}
}}

function fetchRegime() {{
  fetch('/api/regime')
    .then(r => r.json())
    .then(d => {{ renderRegime(d); updateTradingSignal(d); }})
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
          '&nbsp;&nbsp;&#9679; <strong style="color:#58a6ff">' + d.models + ' modelos</strong>' +
          '&nbsp;&nbsp;&#9679; actualizado <strong style="color:#e6edf3">' + d.updated + '</strong>';
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

<div id="pine-section" style="margin:16px 0"></div>
<div id="trades-section" style="margin:16px 0"></div>
<div id="signals-section"><p style="color:#888;padding:16px">Cargando señales...</p></div>
<div id="trainer-section" style="margin:16px 0"></div>
<script>
async function loadTrainer(){{
  try{{
    const [tres,pres] = await Promise.all([fetch('/api/trainer_status'),fetch('/api/pine_status')]);
    const d  = await tres.json();
    const pd = await pres.json();
    const lines = d.lines || [];
    const pines = (pd.models||[]).slice(0,10);

    const relevant = lines.filter(l=>
      l.includes('SIGMA')||l.includes('PIPELINE')||l.includes('Probando')||
      l.includes('GANADOR')||l.includes('BEAR')||l.includes('BULL')||
      l.includes('score=')||l.includes('CAGR')||l.includes('OOS')||
      l.includes('CLT]')||l.includes('RÉGIMEN')||l.includes('DEGRADED')||
      l.includes('PINE')||l.includes('ERROR')||l.includes('velas')
    ).slice(-12);

    const sc=s=>s=='BTC'?'#f7931a':s=='ETH'?'#627eea':s=='LTC'?'#bfbbbb':s=='SOL'?'#9945ff':s=='BNB'?'#f3ba2f':'#58a6ff';
    const cc=v=>v>=40?'#00c853':v>=20?'#69f0ae':v>=0?'#8bc48b':'#f44336';
    const sm={{'momentum_short':'MOM↓','breakdown':'BDN↓','pullback_short':'PBK↓','breakout':'BRK↑','tma_bands':'TMA','mean_rev':'MRV','regime_adaptive':'RAD','momentum':'MOM↑','pullback':'PBK↑'}};
    const sn=s=>sm[s]||s;
    const newN=(pd.models||[]).filter(m=>m.is_new).length;

    let pRows='';
    pines.forEach(m=>{{
      const dot=m.is_new
        ?'<span style="display:inline-block;width:7px;height:7px;background:#00c853;border-radius:50%;animation:pulse 1.5s infinite"></span>'
        :'<span style="display:inline-block;width:7px;height:7px;background:#21262d;border-radius:50%"></span>';
      pRows+='<tr style="border-bottom:1px solid #161b22">'
        +'<td style="padding:3px 6px;width:14px">'+dot+'</td>'
        +'<td style="padding:3px 4px;color:#444;font-size:10px;font-family:monospace;white-space:nowrap">'+m.age_str+'</td>'
        +'<td style="padding:3px 6px"><b style="color:'+sc(m.sym)+';font-size:11px">'+m.sym+'</b></td>'
        +'<td style="padding:3px 4px;color:#555;font-size:10px">'+m.tf.toUpperCase()+'</td>'
        +'<td style="padding:3px 6px;color:#555;font-size:10px">'+sn(m.strategy)+'</td>'
        +'<td style="padding:3px 8px;text-align:right"><b style="color:'+cc(m.cagr)+';font-size:11px">'+(m.cagr>0?'+':'')+m.cagr+'%</b></td>'
        +'<td style="padding:3px 8px;text-align:right"><a href="/'+encodeURIComponent(m.fname)+'" download="'+m.fname+'" style="color:#58a6ff;font-size:12px;text-decoration:none">&#11015;</a></td>'
        +'</tr>';
    }});

    let logLines='';
    relevant.forEach(line=>{{
      let col='#555';
      if(line.includes('GANADOR')||line.includes('score=')||line.includes('OOS')) col='#69f0ae';
      else if(line.includes('SIGMA')||line.includes('PIPELINE')) col='#90a8f0';
      else if(line.includes('Probando')) col='#8b949e';
      else if(line.includes('ERROR')) col='#f85149';
      else if(line.includes('CLT]')) col='#6a737d';
      else if(line.includes('BEAR')||line.includes('BULL')||line.includes('RÉGIMEN')) col='#ff9800';
      logLines+='<div style="color:'+col+';white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+line.replace(/</g,'&lt;')+'</div>';
    }});

    const TH='padding:3px 6px;color:#333;font-size:9px;text-transform:uppercase;letter-spacing:.8px;border-bottom:1px solid #21262d;font-weight:500';

    document.getElementById('trainer-section').innerHTML=
      '<div style="background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:14px;margin:16px 0">'
      +'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">'
      +'<span style="color:#c9d1d9;font-weight:700;font-size:13px">⚙ Optimizador VPS — Actividad</span>'
      +'<span style="color:#555;font-size:11px">DB: <b style="color:#8b949e">'+(d.db_total||0).toLocaleString()+'</b> &middot; <b style="color:#69f0ae">'+(d.rate_hr||0)+'</b>/hr</span>'
      +'</div>'
      +'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">'
      +'<span style="color:#8b949e;font-size:11px;font-weight:600">🌲 Pine Scripts — Últimas actualizaciones</span>'
      +(newN>0?'<span style="background:#00c853;color:#000;padding:1px 6px;border-radius:8px;font-size:9px;font-weight:bold">'+newN+' new</span>':'')
      +'<span style="color:#333;font-size:10px;margin-left:auto">'+(pd.updated||'')+'</span>'
      +'</div>'
      +'<table style="width:100%;border-collapse:collapse;margin-bottom:10px">'
      +'<thead><tr>'
      +'<th style="'+TH+';width:14px"></th>'
      +'<th style="'+TH+'">Cuándo</th>'
      +'<th style="'+TH+'">Activo</th>'
      +'<th style="'+TH+'">TF</th>'
      +'<th style="'+TH+'">Estrategia</th>'
      +'<th style="'+TH+';text-align:right">CAGR</th>'
      +'<th style="'+TH+';text-align:right">Pine</th>'
      +'</tr></thead>'
      +'<tbody>'+pRows+'</tbody></table>'
      +'<div style="border-top:1px solid #21262d;padding-top:8px;margin-top:4px">'
      +'<div style="font-family:monospace;font-size:11px;line-height:1.6">'+logLines+'</div>'
      +'</div>'
      +'</div>';
  }} catch(e){{
    document.getElementById('trainer-section').innerHTML='<p style="color:#555;font-size:11px;padding:8px">Optimizador: sin datos</p>';
  }}
}}
loadTrainer();
setInterval(loadTrainer, 30000);
async function loadPineActivity(){{
  try{{
    const res = await fetch('/api/pine_html');
    const html = await res.text();
    document.getElementById('pine-section').innerHTML = html;
  }} catch(e) {{
    document.getElementById('pine-section').innerHTML = '';
  }}
}}
loadPineActivity();
setInterval(loadPineActivity, 60000);





</script>

<script>
function recIcon(r){{
  if(r==='ACTIVAR') return '<span style="background:#00c853;color:#000;padding:2px 7px;border-radius:4px;font-weight:bold;font-size:11px">✅ ACTIVAR</span>';
  if(r==='ESPERAR') return '<span style="background:#ff9800;color:#000;padding:2px 7px;border-radius:4px;font-size:11px">⏸ ESPERAR</span>';
  if(r==='CONDICIONAL') return '<span style="background:#ffeb3b;color:#000;padding:2px 7px;border-radius:4px;font-size:11px">⚠ CONDIC.</span>';
  return '<span style="background:#f44336;color:#fff;padding:2px 7px;border-radius:4px;font-size:11px">❌ NO</span>';
}}
function sigBadge(s, slot){{
  if(s && slot===1) return '<span style="background:#00e676;color:#000;padding:2px 8px;border-radius:4px;font-weight:bold;font-size:12px">🟢 SEÑAL — SLOT 1</span>';
  if(s && slot===2) return '<span style="background:#00e676;color:#000;padding:2px 8px;border-radius:4px;font-weight:bold;font-size:12px">🟢 SEÑAL — SLOT 2</span>';
  if(s && slot===3) return '<span style="background:#26c6da;color:#000;padding:2px 8px;border-radius:4px;font-weight:bold;font-size:12px">🔵 SEÑAL — SLOT 3</span>';
  if(s && slot===0) return '<span style="background:#ffeb3b;color:#000;padding:2px 8px;border-radius:4px;font-size:11px">🟡 SEÑAL — EN COLA</span>';
  return '<span style="color:#666;font-size:11px">— sin señal</span>';
}}
function gradeColor(g){{
  if(g==='A+') return '#00c853'; if(g==='A') return '#69f0ae';
  if(g==='B') return '#ffeb3b'; if(g==='C') return '#ff9800'; return '#f44336';
}}
let _prevSignalKeys = new Set();
let _countdown = 30;

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
  if(_countdown <= 0) {{ _countdown = 30; loadSignals(); return; }}
  const el = document.getElementById('live-counter');
  if(el) el.textContent = `en ${{_countdown}}s`;
}}
setInterval(_updateLive, 1000);

const TD  = 'padding:7px 10px;border-bottom:1px solid #161622;white-space:nowrap;font-size:12px';
const TDC = TD+';text-align:center';
const TDR = TD+';text-align:right';
const TH  = 'padding:6px 10px;text-align:left;color:#444;font-size:10px;text-transform:uppercase;letter-spacing:0.8px;border-bottom:2px solid #21262d;font-weight:500';
const THC = TH+';text-align:center';
const THR = TH+';text-align:right';
const gradeC    = g => g==='A+'?'#00c853':g==='A'?'#69f0ae':g==='B'?'#ffeb3b':g==='C'?'#ff9800':'#f44336';
const stratShort = s => s.replace('momentum_short','MOM↓').replace('breakdown','BDN↓')
  .replace('pullback_short','PBK↓').replace('breakout','BRK↑').replace('tma_bands','TMA')
  .replace('momentum','MOM↑').replace('pullback','PBK↑').replace('mean_rev','MRV')
  .replace('regime_adaptive','RAD').replace('_',' ');

let _portfolioEquity = 10000;  // actualizado desde /api/trades

function _usdCell(kelly, slDist, sl, entry) {{
  try {{
    const eq  = _portfolioEquity || 10000;
    const risk = Math.round(eq * (kelly||3.3) / 100);
    const slPct = (slDist > 0 && entry > 0) ? slDist / entry
                : (sl > 0 && entry > 0 ? Math.abs(entry - sl) / entry : 0);
    if (!slPct || slPct <= 0) return '<span style="color:#2a2a2a">—</span>';
    const not = risk / slPct;
    const rc  = risk < 200 ? '#69f0ae' : risk < 500 ? '#ff9800' : '#f44336';
    return '<div style="color:'+rc+';font-weight:700">$'+risk+'</div>'
         + '<div style="color:#333;font-size:9px">10×:$'+Math.round(not/10)+' · 20×:$'+Math.round(not/20)+'</div>';
  }} catch(e) {{ return '<span style="color:#2a2a2a">—</span>'; }}
}}
async function _fetchPortfolioEquity() {{
  try {{ const r=await fetch('/api/trades'); const d=await r.json();
    _portfolioEquity = d.portfolio?.equity || 10000; }} catch(e) {{}}
}}
_fetchPortfolioEquity(); setInterval(_fetchPortfolioEquity, 60000);

function _ddKellyBadge(models) {{
  try {{
    const dd   = models && models[0] ? (models[0].current_dd_pct || 0) : 0;
    const mult = models && models[0] ? (models[0].dd_kelly_mult  || 1) : 1;
    if (mult < 1.0) {{
      const col = mult <= 0.25 ? '#f44336' : mult <= 0.5 ? '#ff5722' : '#ff9800';
      return '<span style="background:' + col + ';color:#fff;padding:2px 8px;border-radius:4px;font-weight:bold;margin-left:8px;font-size:11px">⚠ DD ' + dd.toFixed(1) + '% Kelly ×' + mult.toFixed(2) + '</span>';
    }}
    return '';
  }} catch(e) {{ return ''; }}
}}
async function loadSignals(){{
  // Update BTC.D badge
  try {{
    const bd = (await (await fetch('/api/signals')).json());
    const btcdVal = bd.models && bd.models[0] ? bd.models[0].btcd_value : 0.5;
    const el = document.getElementById('btcd-badge');
    if(el) {{
      if(btcdVal < 0.35) el.innerHTML = '₿ DOMINA <span style="color:#888">(-30% alts)</span>';
      else if(btcdVal > 0.65) el.innerHTML = '<span style="color:#00e676">₿ DEBIL — alts ×1.15</span>';
      else el.innerHTML = '';
    }}
  }} catch(e){{}}
  _countdown = 30;
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
    const pts = m => m.slot===1?100 : m.slot===2?90 : m.slot===3?80 : m.signal?50 :
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
          flash.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999;background:#0d1117;border:2px solid '+(m.type!=='short'?'#00e676':'#f85149')+';border-radius:8px;padding:14px 20px;font-family:monospace;animation:flashIn 0.3s ease';
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
      if(slot===1||slot===2||slot===3) {{
        const slotC = slot===3?'#26c6da':dirC;
        stateBadge = `<span style="background:${{slotC}};color:#000;padding:2px 9px;border-radius:4px;font-weight:bold;font-size:11px">SLOT ${{slot}} ${{isLong?'▲':'▼'}}${{slot===3?' (65%K)':''}}</span>`;
        const rgbC = slot===3 ? '38,198,218' : (isLong?'0,230,118':'248,81,73');
        rowStyle   = `background:rgba(${{rgbC}},0.05);border-left:3px solid ${{slotC}}`;
      }} else if(rec==='ACTIVAR') {{
        stateBadge = `<span style="background:rgba(96,130,220,0.18);color:#90a8f0;border:1px solid #4060b0;padding:2px 9px;border-radius:4px;font-size:11px;font-weight:600">◉ COLA</span>`;
        rowStyle   = 'background:rgba(64,96,192,0.04);border-left:3px solid #4060b0';
      }} else if(rec==='CONDICIONAL') {{
        stateBadge = `<span style="color:#ff9800;font-size:11px">◈ COND</span>`;
        rowStyle   = 'opacity:0.65;border-left:3px solid #7a4400';
      }} else {{
        stateBadge = `<span style="color:#333;font-size:11px">⏸ PAUSA</span>`;
        rowStyle   = 'opacity:0.35;border-left:3px solid #1c2128';
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

      const cagrCol = m.cagr_2x>20?'#00c853':m.cagr_2x>10?'#69f0ae':m.cagr_2x>0?'#8bc48b':'#f44336';
      const ddCol   = m.dd_2x>-10?'#69f0ae':m.dd_2x>-20?'#ff9800':'#f44336';
      const wrCol   = m.wr>=65?'#00c853':m.wr>=55?'#69f0ae':'#8b949e';
      const corrW   = m.corr_warning ? ' title="Correlacionado — reducir size"' : '';

      rows += `<tr style="${{rowStyle}}">
        <td style="${{TD}}">${{stateBadge}}</td>
        <td style="${{TD}}"><b style="color:#c9d1d9">${{m.sym}}</b></td>
        <td style="${{TD}};color:#6a737d">${{m.tf.toUpperCase()}}</td>
        <td style="${{TD}};color:#ddd">${{stratShort(m.strategy)}}</td>
        <td style="${{TDC}}"><span style="background:${{gc}};color:#000;padding:1px 7px;border-radius:10px;font-weight:bold;font-size:11px">${{m.grade}}</span></td>
        <td style="${{TDC}};font-size:11px">${{conf}}</td>
        <td style="${{TDR}};color:${{cagrCol}};font-weight:600">${{m.cagr_2x>0?'+':''}}${{m.cagr_2x}}%</td>
        <td style="${{TDR}};color:${{ddCol}}">${{m.dd_2x}}%</td>
        <td style="${{TDR}};color:${{wrCol}}">${{m.wr?.toFixed(0)??'—'}}%</td>
        <td style="${{TDC}};color:#555">${{m.trades}}</td>
        <td style="${{TDR}};color:#555;font-size:11px">
          ${{m.eff_risk_pct!=null?m.eff_risk_pct+'%':'—'}}
          ${{m.corr_warning?' <span title="Correlacionado — Kelly -40%">⚠</span>':''}}
          ${{m.ensemble_count>1?' <span style="color:#90a8f0">E'+m.ensemble_count+'</span>':''}}
          ${{m.decay_warning?' <span style="color:#ff5722;font-size:10px" title="Decay: WR live '+m.live_wr+'% — Kelly -50%">⚡</span>':''}}
          ${{m.regime_muted?' <span style="color:#444;font-size:10px" title="Silenciada por régimen">🔇</span>':''}}
          ${{m.htf_penalty?' <span style="color:#ff9800;font-size:10px" title="TF mayor no confirma — Kelly -35%">↑?</span>':''}}
          ${{m.btcd_penalty?' <span style="color:#f7931a;font-size:9px" title="BTC domina — Kelly -30% en alts">₿↑</span>':''}}
          ${{m.btcd_boost?' <span style="color:#00e676;font-size:9px" title="Alts dominan — Kelly +15%">₿↓</span>':''}}
        </td>
        <td style="${{TDC}}">${{dirTag}}</td>
        <td style="${{TDR}};font-family:monospace;color:#8b949e">${{precio}}</td>
        <td style="${{TDR}};font-family:monospace;color:#f85149">${{sl}}</td>
        <td style="${{TDR}};font-family:monospace;color:#00e676">${{tp}}</td>
        <td style="${{TDR}};color:${{rrCol}};font-weight:bold">${{rr!='—'?rr+':1':'—'}}</td>
        <td style="${{TDR}}">
          ${{m.ev!=null
            ? `<span style="color:${{m.ev>=0?'#00c853':'#f44336'}};font-weight:700">${{m.ev>=0?'+':''}}${{m.ev.toFixed(1)}}%</span>`
            : '<span style="color:#2a2a2a">—</span>'}}
        </td>
        <td style="${{TDR}};font-size:10px">
          ${{(()=>{{ if(!m.signal||!m.price||!m.sl||!m.eff_risk_pct) return '<span style="color:#1c2128">—</span>';
            const eq=_portfolioEquity; const risk=eq*m.eff_risk_pct/100;
            const slp=Math.abs(m.price-m.sl)/m.price;
            if(slp<=0) return '—';
            const not=risk/slp; const m10=(not/10).toFixed(0); const m20=(not/20).toFixed(0);
            const rc=risk<200?'#69f0ae':risk<500?'#ff9800':'#f44336';
            return `<b style="color:${{rc}}">${{risk<1000?'$'+risk.toFixed(0):'$'+(risk/1000).toFixed(1)+'K'}}</b>`
                  +`<div style="color:#333;font-size:9px">10×:${{m10}} 20×:${{m20}}</div>`;
          }})()}}
        </td>
      </tr>`;
    }});

    let html = `<div style="background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:14px;margin:16px 0;overflow-x:auto">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <div style="display:flex;align-items:center;gap:10px">
          <span style="color:#c9d1d9;font-weight:700;font-size:14px">📊 Panel de Señales</span>
          <span style="color:#444;font-size:11px">Régimen <b style="color:#ff9800">${{regime}}</b></span>
          <span style="color:#444;font-size:11px" title="Slots activos según régimen y correlación">
            Slots: <b style="color:${{regime==='BEAR'?'#f85149':regime==='BULL'?'#00c853':'#58a6ff'}}">${{regime==='BEAR'?'máx 2':'máx 3'}}</b>
          </span>
          <span style="color:#333;font-size:10px" title="Backtest: Futuros Binance, fee 0.04%, funding histórico real. CAGR/DD = backtest OOS sin apalancamiento. Kelly sizing. EV = WR×TP − (1−WR)×SL.">ⓘ Futuros · EV · Kelly · sin leverage</span>
          <span id="btcd-badge" style="font-size:10px;color:#f7931a;margin-left:4px"></span>
          ${{gradeBar}}
        </div>
        <span style="color:#444;font-size:11px">
          <span style="display:inline-block;width:6px;height:6px;background:#00e676;border-radius:50%;margin-right:4px;animation:pulse 1.5s infinite"></span>
          LIVE · <span id="live-counter">30s</span> · ${{upd}}
          ${{d.circuit_breaker?'<span style="background:#f44336;color:#fff;padding:2px 8px;border-radius:4px;font-weight:bold;margin-left:8px">⛔ CIRCUIT BREAKER</span>':''}}
          ${{_ddKellyBadge(d.models)}}
        </span>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:12px">
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
          <th style="${{THC}}">Dir</th>
          <th style="${{THR}}">Entrada</th>
          <th style="${{THR}}">SL</th>
          <th style="${{THR}}">TP</th>
          <th style="${{THR}}">RR</th>
          <th style="${{THR}}" title="Expected Value por trade (WR×TP − (1−WR)×SL)">EV</th>
          <th style="${{THR}}" title="USD a colocar como margen en Binance Futures">USD Margen</th>
        </tr></thead>
        <tbody>${{rows}}</tbody>
      </table>
    </div>`;


    // ── Panel de Sizing USD ───────────────────────────────────────────────
    const activeSignals = allSorted.filter(m => m.signal && m.slot > 0 && m.price && m.sl && m.eff_risk_pct);
    let sizingHTML = '';
    if(activeSignals.length > 0) {{
      const PORT_EQUITY = _portfolioEquity;  // live from /api/trades
      sizingHTML = `<div style="background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:14px 20px;margin:12px 0">
        <div style="color:#8b949e;font-size:11px;letter-spacing:1px;margin-bottom:12px">
          💰 SIZING EN USD <span style="color:#333">— cuánto colocar en Binance Futures por señal activa</span>
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
        ${{activeSignals.map(m => {{
          const isL    = m.type !== 'short';
          const dc     = isL ? '#00e676' : '#f85149';
          const riskUsd= PORT_EQUITY * m.eff_risk_pct / 100;
          const slPct  = Math.abs(m.price - m.sl) / m.price;
          const notional = slPct > 0 ? riskUsd / slPct : 0;
          const m5  = notional / 5;
          const m10 = notional / 10;
          const m20 = notional / 20;
          const ev   = m.ev != null ? m.ev.toFixed(1) : '—';
          const evC  = (m.ev||0) >= 0 ? '#00c853' : '#f44336';
          const rrN  = (m.sl && m.tp) ? Math.abs(m.tp-m.price)/Math.abs(m.price-m.sl) : 0;
          const slot3note = m.slot===3 ? '<div style="color:#26c6da;font-size:9px">Slot 3 — Kelly 65%</div>' : '';
          return `<div style="background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px 16px;min-width:200px;flex:1;border-top:3px solid ${{m.slot===3?'#26c6da':dc}}">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
              <span style="color:#c9d1d9;font-weight:700">${{m.sym}} <span style="color:#555">${{m.tf.toUpperCase()}}</span></span>
              <span style="background:${{dc}};color:#000;padding:1px 8px;border-radius:4px;font-weight:bold;font-size:11px">SLOT ${{m.slot}} ${{isL?'▲':'▼'}}</span>
            </div>
            ${{slot3note}}
            <div style="font-size:22px;font-weight:800;color:${{dc}};margin-bottom:4px">
              ${{riskUsd < 1000 ? '$'+riskUsd.toFixed(0) : '$'+(riskUsd/1000).toFixed(1)+'K'}}
              <span style="font-size:12px;color:#444;font-weight:400"> máx pérdida</span>
            </div>
            <div style="color:#8b949e;font-size:11px;margin-bottom:8px">
              Kelly ${{m.eff_risk_pct}}% · EV <span style="color:${{evC}}">${{ev}}%</span> · RR ${{rrN>0?rrN.toFixed(1)+':1':'—'}}
            </div>
            <div style="border-top:1px solid #21262d;padding-top:8px">
              <div style="color:#444;font-size:9px;letter-spacing:1px;margin-bottom:4px">MARGEN A DEPOSITAR EN BINANCE</div>
              <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:4px">
                ${{[[5,'5×'],[10,'10×'],[20,'20×']].map(([l,lt])=>{{
                  const mg = notional / l;
                  const mgPct = (mg/PORT_EQUITY*100).toFixed(0);
                  const mgC = mgPct>50?'#f44336':mgPct>25?'#ff9800':'#69f0ae';
                  return `<div style="background:#0d1117;border:1px solid #21262d;border-radius:4px;padding:4px 8px;text-align:center">
                    <div style="color:#444;font-size:9px">${{lt}}</div>
                    <div style="color:${{mgC}};font-weight:700;font-size:13px">$${{mg<1000?mg.toFixed(0):(mg/1000).toFixed(1)+'K'}}</div>
                    <div style="color:#333;font-size:9px">${{mgPct}}% cuenta</div>
                  </div>`;
                }}).join('')}}
              </div>
              <div style="margin-top:6px;color:#444;font-size:9px">
                Notional: <span style="color:#555">${{notional<1000?'$'+notional.toFixed(0):'$'+(notional/1000).toFixed(1)+'K'}}</span>
                · SL dist: <span style="color:#555">${{(slPct*100).toFixed(2)}}%</span>
              </div>
            </div>
          </div>`;
        }}).join('')}}
        </div>
        <div style="margin-top:10px;color:#30363d;font-size:10px">
          ⓘ Margen = Riesgo_USD ÷ (SL% × Leverage) · Verde &lt;25% cuenta · Naranja 25-50% · Rojo &gt;50%
          · El capital del portfolio se ajusta automáticamente al crecer
        </div>
      </div>`;
    }}

    document.getElementById('signals-section').innerHTML = sizingHTML + html;
  }} catch(e){{
    document.getElementById('signals-section').innerHTML = '<p style="color:#888">Cargando señales...</p>';
  }}
}}
loadSignals();

// ── PANEL DE TRADES EN VIVO ──────────────────────────────────────────────────
function _readinessPanel(d) {{
  try {{
    var lr = (d && d.live_readiness) ? d.live_readiness : {{}};
    var score  = lr.score  || 0;
    var level  = lr.level  || 'EARLY';
    var checks = lr.checks || [];
    var col = level==='LISTO'?'#00c853':level==='CASI'?'#ff9800':level==='BUILDING'?'#58a6ff':'#2a2a2a';
    var bg  = level==='LISTO'?'rgba(0,200,83,0.06)':level==='CASI'?'rgba(255,152,0,0.06)':'rgba(20,20,20,0.3)';
    var icon= level==='LISTO'?'&#10003;':level==='CASI'?'&#9889;':level==='BUILDING'?'&#128202;':'&#128300;';
    var msgMap = {{'LISTO':'Sistema listo para capital real','CASI':'Casi listo','BUILDING':'Acumulando estadisticas...','EARLY':'Fase inicial'}};
    var msg = msgMap[level] || 'Validando...';
    var ch2 = '';
    for(var i=0;i<checks.length;i++){{
      var ch=checks[i]; var cc=ch.ok?'#00c853':'#333';
      ch2+='<div style="display:flex;gap:8px;padding:4px 0;border-bottom:1px solid #0d1117">'
          +'<span style="color:'+cc+';font-weight:700;min-width:16px">'+(ch.ok?'&#10003;':'&#9675;')+'</span>'
          +'<span style="color:'+(ch.ok?'#8b949e':'#444')+';font-size:11px;flex:1">'+ch.name+'</span>'
          +'<span style="color:'+(ch.ok?'#58a6ff':'#2a2a2a')+';font-size:11px">'+(ch.val||'')+'</span>'
          +'<span style="color:'+(ch.ok?'#00c853':'#2a2a2a')+';font-size:10px"> +'+ch.pts+'</span>'
          +'</div>';
    }}
    var pw=Math.min(score*1.5,150);
    var footer=level==='LISTO'
      ?'<div style="margin-top:8px;padding:8px;background:rgba(0,200,83,0.1);border-radius:4px;color:#00c853;font-size:11px">&#10003; Puedes conectar la API real de Binance</div>'
      :'<div style="margin-top:8px;color:#30363d;font-size:10px">Acumula 30 trades de paper trading para validar el sistema</div>';
    return '<div style="background:'+bg+';border:1px solid '+col+';border-radius:8px;padding:14px 20px;margin:12px 0">'
          +'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">'
          +'<div><div style="color:'+col+';font-size:10px;letter-spacing:1px;font-weight:600">GATE CAPITAL REAL</div>'
          +'<div style="color:#e6edf3;font-size:14px;font-weight:700">'+icon+' '+msg+'</div></div>'
          +'<div style="color:'+col+';font-size:32px;font-weight:800">'+score+'<span style="font-size:14px;color:#444">/100</span></div></div>'
          +'<div style="background:#1c2128;height:5px;border-radius:3px;margin-bottom:10px">'
          +'<div style="width:'+pw+'px;height:5px;background:'+col+';border-radius:3px"></div></div>'
          +'<div>'+ch2+'</div>'+footer+'</div>';
  }} catch(e) {{ return ''; }}
}}

async function loadTrades() {{
  try {{
    const res = await fetch('/api/trades');
    const d   = await res.json();
    const open  = d.open  || [];
    const cds   = d.cooldowns || [];
    const hist  = d.history || [];
    const st    = d.stats   || {{}};
    const port  = d.portfolio || {{}};
    const mperf = d.model_performance || {{}};

    // ── Precios live desde Binance Futures ───────────────────────────────
    const _lp = {{}};
    try {{
      const pairs = [...new Set(open.map(t=>t.sym+'USDT'))];
      await Promise.all(pairs.map(async p => {{
        const r = await fetch(`https://fapi.binance.com/fapi/v1/ticker/price?symbol=${{p}}`);
        const j = await r.json();
        _lp[p.replace('USDT','')] = parseFloat(j.price);
      }}));
    }} catch(e) {{}}

    // ── Equity flotante total ─────────────────────────────────────────────
    let floatPct = 0;
    open.forEach(t => {{
      const cp = _lp[t.sym] || t.entry;
      const m  = t.direction!=='short' ? 1 : -1;
      if(t.sl_dist && t.sl_dist>0 && t.entry) {{
        const move  = m*(cp-t.entry)/t.entry*100;
        const units = move/(t.sl_dist/t.entry*100);
        floatPct += units*(t.kelly_pct||3.3);
      }} else {{
        floatPct += m*(cp-(t.entry||cp))/(t.entry||1)*100*2;
      }}
    }});

    // ── PANEL PRINCIPAL ───────────────────────────────────────────────────
    const eq       = port.equity || 10000;
    const init     = port.initial || 10000;
    const retPct   = port.return_pct ?? ((eq/init-1)*100).toFixed(2);
    const floatEq  = (eq*(1+floatPct/100)).toFixed(2);
    const retCol   = retPct >= 0 ? '#00c853' : '#f44336';
    const floatCol = floatPct >= 0 ? '#00e676' : '#f85149';
    const ddCol    = (port.max_dd||0) > -10 ? '#69f0ae' : (port.max_dd||0) > -20 ? '#ff9800' : '#f44336';
    const rorCol   = (port.risk_of_ruin||0) < 5 ? '#00c853' : (port.risk_of_ruin||0) < 15 ? '#ff9800' : '#f44336';
    const cagrC    = (port.cagr_live||0) > 20 ? '#00c853' : (port.cagr_live||0) > 0 ? '#69f0ae' : '#f44336';
    const wrC      = st.win_rate >= 60 ? '#00c853' : st.win_rate >= 50 ? '#69f0ae' : '#ff9800';
    const pfC      = st.profit_factor >= 2 ? '#00c853' : st.profit_factor >= 1.3 ? '#69f0ae' : '#ff9800';

    // Equity progress bar
    const barPct   = Math.min(Math.max(retPct, -50), 100);
    const barColor = barPct >= 0 ? '#238636' : '#f44336';
    const barW     = Math.abs(barPct) * 1.2;  // max 120px

    // Comparar live WR vs backtest WR promedio
    const modelKeys = Object.keys(mperf);
    const modelsWithData = modelKeys.filter(k => mperf[k].trades_live >= 5);
    let vsBacktestHTML = '';
    if(modelsWithData.length > 0) {{
      vsBacktestHTML = modelsWithData.map(k => {{
        const m = mperf[k]; const parts = k.split('_');
        const sym=parts[0]; const tf=parts[1]; const strat=parts.slice(2).join('_');
        const oc = m.status==='DEGRADADO'?'#f44336':m.status==='MEJOR'?'#00c853':'#69f0ae';
        const conf = m.confidence_pct;
        const confBar = `<div style="width:${{conf*0.5}}px;height:3px;background:${{conf>=80?'#00c853':conf>=50?'#ff9800':'#555'}};border-radius:2px;display:inline-block;vertical-align:middle"></div>`;
        const needed = m.trades_needed > 0 ? `<span style="color:#444;font-size:9px"> +${{m.trades_needed}} trades</span>` : '';
        return `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid #161b22">
          <span style="color:#8b949e;font-size:11px;min-width:90px">${{sym}} ${{tf.toUpperCase()}} ${{strat.replace(/_/g,' ')}}</span>
          <span style="color:#58a6ff;font-size:11px">BT ${{m.wr_bt.toFixed(0)}}%</span>
          <span style="color:#444">→</span>
          <span style="color:${{oc}};font-weight:700;font-size:11px">live ${{m.wr_live.toFixed(0)}}%</span>
          <span style="color:${{oc}};font-size:10px">(${{m.wr_diff>=0?'+':''}}${{m.wr_diff.toFixed(0)}}pp)</span>
          ${{confBar}}${{needed}}
          <span style="color:${{oc}};font-size:9px;margin-left:4px">${{m.status}}</span>
        </div>`;
      }}).join('');
    }} else {{
      vsBacktestHTML = '<div style="color:#30363d;padding:10px;font-size:11px">Acumulando trades para comparar WR live vs backtest...<br><span style="color:#444">Necesitas ≥5 trades por modelo</span></div>';
    }}

    // Trades abiertos
    let openHTML = open.map(t => {{
      const isL  = t.direction!=='short';
      const dc   = isL?'#00e676':'#f85149';
      const cp   = _lp[t.sym] || t.entry;
      let pnl    = 0;
      if(t.sl_dist && t.sl_dist>0 && t.entry) {{
        const m=isL?1:-1; const move=m*(cp-t.entry)/t.entry*100;
        pnl = move/(t.sl_dist/t.entry*100)*(t.kelly_pct||3.3);
      }} else {{ pnl=(isL?1:-1)*(cp-t.entry)/t.entry*100*2; }}
      const pc  = pnl>=0?'#00e676':'#f44336';
      const rn  = (t.sl_dist&&t.tp_dist&&t.sl_dist>0)?t.rr||t.tp_dist/t.sl_dist:0;
      const ph  = t.trail_phase||0;
      const phTxt = ph===3?'🔒3':ph===2?'🔒2':ph===1?'🔒1':'';
      const slHit= isL?(cp<=t.sl):(cp>=t.sl);
      const tpHit= isL?(cp>=t.tp):(cp<=t.tp);
      const rs = `border-left:3px solid ${{slHit?'#f44336':tpHit?'#00c853':dc}};background:rgba(${{isL?'0,230,118':'248,81,73'}},0.04)`;
      return `<tr style="${{rs}}">
        <td style="${{TD}}"><span style="background:${{dc}};color:#000;padding:1px 7px;border-radius:4px;font-weight:bold;font-size:10px">${{isL?'L':'S'}}</span></td>
        <td style="${{TD}}"><b style="color:#c9d1d9">${{t.sym}}</b></td>
        <td style="${{TD}};color:#555">${{t.tf?.toUpperCase()}}</td>
        <td style="${{TD}};color:#8b949e;font-size:11px">${{stratShort(t.strategy||'')}}</td>
        <td style="${{TDC}}"><span style="background:${{gradeC(t.grade||'D')}};color:#000;padding:1px 6px;border-radius:8px;font-weight:bold;font-size:10px">${{t.grade||'?'}}</span></td>
        <td style="${{TDR}};color:#444;font-size:10px"><b style="color:#8b949e">${{(t.kelly_pct||3.3).toFixed(1)}}%</b></td>
        <td style="padding:6px 8px;text-align:right;font-size:10px">${{_usdCell(t.kelly_pct||3.3, t.sl_dist||0, t.sl||0, t.entry||0)}}</td>
        <td style="${{TDR}};font-family:monospace;color:#666">${{t.entry}}</td>
        <td style="${{TDR}};font-family:monospace;color:#e6edf3;font-weight:bold">${{cp.toFixed(2)}}</td>
        <td style="${{TDR}};color:#f85149;font-family:monospace">${{t.sl}}</td>
        <td style="${{TDR}};color:#00e676;font-family:monospace">${{t.tp}}</td>
        <td style="${{TDR}};color:${{rn>=2?'#00c853':rn>=1.5?'#69f0ae':'#ff9800'}};font-weight:bold">${{rn>0?rn.toFixed(1)+':1':'—'}}</td>
        <td style="${{TDR}};color:${{pc}};font-weight:bold;font-size:12px">${{pnl>=0?'+':''}}${{pnl.toFixed(2)}}%</td>
        <td style="${{TD}};color:#555;font-size:10px">${{t.opened_at?.substring(11,16)||''}} ${{phTxt}}</td>
        <td style="${{TDC}}">
          <button onclick="closeTrade('${{t.sym}}','${{t.tf}}','SL_HIT')" style="background:#2d1010;color:#f85149;border:1px solid #5a1a1a;border-radius:3px;padding:1px 5px;font-size:10px;cursor:pointer">SL</button>
          <button onclick="closeTrade('${{t.sym}}','${{t.tf}}','TP_HIT')" style="background:#0d2010;color:#00e676;border:1px solid #1a5a2a;border-radius:3px;padding:1px 5px;font-size:10px;cursor:pointer">TP</button>
        </td>
      </tr>`;
    }}).join('');

    // Cooldowns
    cds.forEach(c => {{
      const col=c.reason==='SL_HIT'?'#f44336':c.reason==='TP_HIT'?'#00c853':'#555';
      openHTML += `<tr style="opacity:0.4;border-left:3px solid ${{col}}"><td style="${{TD}};color:${{col}}">⏸</td><td style="${{TD}}"><b style="color:#555">${{c.sym}}</b></td><td style="${{TD}};color:#444">${{c.tf?.toUpperCase()}}</td><td colspan="11" style="${{TD}};color:#333">cooldown hasta ${{c.until?.substring(11,16)||'—'}} (${{c.reason}})</td></tr>`;
    }});

    // Historial
    const histHTML = [...hist].slice(0,15).map(t => {{
      const pnl=t.pnl_pct||0; const pc=pnl>=0?'#00e676':'#f44336';
      const icon=t.reason==='TP_HIT'?'✓ TP':t.reason==='SL_HIT'?'✗ SL':t.reason==='REGIME_CHANGE'?'↻ RG':'○';
      const comm=t.commission?`<span style="color:#333;font-size:9px"> -${{t.commission.toFixed(2)}}$ fee</span>`:'';
      const fund=t.funding&&t.funding>0?`<span style="color:#3fb950;font-size:9px"> +${{t.funding.toFixed(2)}}$ fund</span>`:'';
      const hrs=t.hours_open?`<span style="color:#333;font-size:9px"> ${{t.hours_open}}h</span>`:'';
      return `<tr style="border-bottom:1px solid #0d1117">
        <td style="${{TD}};color:${{pc}};font-weight:bold;font-size:11px">${{icon}}</td>
        <td style="${{TD}};color:#666"><b>${{t.sym}}</b></td>
        <td style="${{TD}};color:#444">${{t.tf?.toUpperCase()}}</td>
        <td style="${{TD}};color:#555;font-size:11px">${{stratShort(t.strategy||'')}}</td>
        <td style="${{TDC}}"><span style="background:${{gradeC(t.grade||'D')}};color:#000;padding:1px 5px;border-radius:6px;font-weight:bold;font-size:9px">${{t.grade||'?'}}</span></td>
        <td style="${{TDR}};color:#8b949e;font-size:10px"><b>${{(t.kelly_pct||3.3).toFixed(1)}}%</b></td>
        <td style="${{TDR}};font-family:monospace;color:#555">${{t.entry}}</td>
        <td style="${{TDR}};font-family:monospace;color:#555">${{t.exit_price}}</td>
        <td style="${{TDR}};color:${{pc}};font-weight:bold;font-size:12px">${{pnl>=0?'+':''}}${{pnl.toFixed(2)}}%</td>
        <td style="${{TD}};font-size:9px">${{comm}}${{fund}}${{hrs}}</td>
        <td style="${{TDR}};color:#333;font-size:9px">${{t.equity_after?'$'+(t.equity_after.toFixed(0)):'—'}}</td>
        <td style="${{TD}};color:#333;font-size:9px">${{t.closed_at?.substring(5,16)||''}}</td>
      </tr>`;
    }}).join('');

    // Equity sparkline mini
    const eqPoints = port.equity_history || [];
    let sparkSVG = '';
    if(eqPoints.length >= 3) {{
      const vals = eqPoints.map(p=>p.eq);
      const mn=Math.min(...vals); const mx=Math.max(...vals);
      const range=mx-mn||1;
      const pts = vals.map((v,i)=>{{
        const x=i/(vals.length-1)*200;
        const y=40-((v-mn)/range*36);
        return `${{x.toFixed(1)}},${{y.toFixed(1)}}`;
      }}).join(' ');
      const lastV=vals[vals.length-1]; const firstV=vals[0];
      const sc=lastV>=firstV?'#00c853':'#f44336';
      sparkSVG=`<svg width="200" height="44" style="vertical-align:middle"><polyline points="${{pts}}" fill="none" stroke="${{sc}}" stroke-width="1.5" stroke-linejoin="round"/><circle cx="${{200}}" cy="${{(40-((lastV-mn)/range*36)).toFixed(1)}}" r="3" fill="${{sc}}"/></svg>`;
    }}


    const readinessPanel = _readinessPanel(d);
    const html = `
    <div style="background:#0d1117;border:1px solid #21262d;border-radius:10px;padding:0;margin:16px 0;overflow:hidden">

      ${{readinessPanel}}
      <!-- Header con equity -->
      <div style="background:linear-gradient(135deg,#0a1628 0%,#161b22 100%);padding:18px 24px;border-bottom:1px solid #21262d">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px">
          <div>
            <div style="color:#58a6ff;font-size:10px;letter-spacing:2px;font-weight:600">PAPER TRADING — SIMULACIÓN REAL</div>
            <div style="font-size:26px;font-weight:800;margin-top:4px">
              <span style="color:#e6edf3">$</span><span id="eq-live" style="color:${{retPct>=0?'#00c853':'#f44336'}}">${{floatEq}}</span>
            </div>
            <div style="color:#8b949e;font-size:11px;margin-top:2px">
              ${{init.toLocaleString()}} inicial
              <span style="color:${{retCol}};font-weight:700;margin-left:8px">${{retPct>=0?'+':''}}${{retPct}}%</span>
              total
            </div>
          </div>
          <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center">
            ${{sparkSVG}}
            <div style="text-align:right">
              <div style="color:#444;font-size:9px;letter-spacing:1px">P&L FLOTANTE</div>
              <div style="color:${{floatCol}};font-size:16px;font-weight:700">${{floatPct>=0?'+':''}}${{floatPct.toFixed(2)}}%</div>
            </div>
          </div>
        </div>

        <!-- Métricas portfolio -->
        <div style="display:flex;gap:0;margin-top:16px;border:1px solid #21262d;border-radius:8px;overflow:hidden;flex-wrap:wrap">
          ${{[
            ['CAGR live', port.cagr_live!=null?`${{port.cagr_live>=0?'+':''}}${{port.cagr_live}}%`:'— días<7', cagrC, 'días activos: '+port.days_active],
            ['WR live',   st.win_rate+'%',   wrC,  `${{st.wins||0}}W / ${{st.losses||0}}L`],
            ['Max DD',    (port.max_dd||0)+'%', ddCol,  'máx caída del portfolio'],
            ['Calmar',    port.calmar!=null?port.calmar.toFixed(2):'—', (port.calmar||0)>=2?'#00c853':(port.calmar||0)>=1?'#69f0ae':'#ff9800', 'CAGR / |Max DD|'],
            ['Sharpe',    port.sharpe!=null?port.sharpe.toFixed(2):'—', (port.sharpe||0)>=1.5?'#00c853':(port.sharpe||0)>=0.7?'#69f0ae':'#8b949e', 'retorno ajustado por riesgo'],
            ['Profit F',  st.profit_factor>=0?st.profit_factor.toFixed(2):'—', pfC, 'wins / |losses|'],
            ['Riesgo',    port.risk_of_ruin!=null?port.risk_of_ruin+'%':'—', rorCol, 'probabilidad DD>25%'],
            ['Comisión',  '-$'+(port.commission_paid||0).toFixed(2), '#f44336', 'total pagado'],
          ].map(([l,v,c,s])=>`<div style="flex:1;padding:10px 14px;border-right:1px solid #21262d;min-width:80px">
            <div style="color:#444;font-size:9px;letter-spacing:1px">${{l}}</div>
            <div style="color:${{c}};font-size:16px;font-weight:700;margin-top:2px">${{v}}</div>
            <div style="color:#333;font-size:9px;margin-top:1px">${{s}}</div>
          </div>`).join('')}}
        </div>
      </div>

      <!-- Trades abiertos -->
      <div style="padding:14px 20px;border-bottom:1px solid #161b22">
        <div style="color:#8b949e;font-size:11px;letter-spacing:1px;margin-bottom:8px">POSICIONES ABIERTAS</div>
        <div style="overflow-x:auto">
          <table style="width:100%;border-collapse:collapse;font-size:12px">
            <thead><tr style="border-bottom:1px solid #21262d">
              <th style="${{TH}}">Dir</th><th style="${{TH}}">Activo</th><th style="${{TH}}">TF</th>
              <th style="${{TH}}">Estrategia</th><th style="${{THC}}">Grade</th>
              <th style="${{THR}}">Kelly</th><th style="${{THR}}" title="Riesgo máximo en USD + margen por leverage">USD</th><th style="${{THR}}">Entry</th><th style="${{THR}}">Live</th>
              <th style="${{THR}}">SL</th><th style="${{THR}}">TP</th><th style="${{THR}}">RR</th>
              <th style="${{THR}}">P&L</th><th style="${{TD}}">Hora</th><th style="${{THC}}">Acción</th>
            </tr></thead>
            <tbody>${{openHTML||'<tr><td colspan=14 style="padding:16px;color:#333;text-align:center">Sin posiciones abiertas</td></tr>'}}</tbody>
          </table>
        </div>
      </div>

      <!-- Live WR vs Backtest -->
      <div style="padding:14px 20px;border-bottom:1px solid #161b22">
        <div style="color:#8b949e;font-size:11px;letter-spacing:1px;margin-bottom:8px">
          WR LIVE vs BACKTEST <span style="color:#333">— validando si el modelo funciona en mercado real</span>
        </div>
        <div style="font-size:11px">${{vsBacktestHTML}}</div>
      </div>

      <!-- Historial -->
      <div style="padding:14px 20px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:6px">
          <div style="color:#8b949e;font-size:11px;letter-spacing:1px">HISTORIAL DE TRADES</div>
          <div style="color:#30363d;font-size:10px">fee 0.04%/lado · funding simulado · Kelly sizing real</div>
        </div>
        <div style="overflow-x:auto">
          <table style="width:100%;border-collapse:collapse;font-size:11px">
            <thead><tr style="border-bottom:1px solid #21262d">
              <th style="${{TH}}">Resultado</th><th style="${{TH}}">Par</th><th style="${{TH}}">TF</th>
              <th style="${{TH}}">Estrategia</th><th style="${{THC}}">Grade</th>
              <th style="${{THR}}">Kelly</th><th style="${{THR}}">Entry</th><th style="${{THR}}">Exit</th>
              <th style="${{THR}}">P&L %</th><th style="${{TH}}">Costos</th>
              <th style="${{THR}}">Equity</th><th style="${{THR}}">Hora cierre</th>
            </tr></thead>
            <tbody>${{histHTML||'<tr><td colspan=12 style="padding:16px;color:#333;text-align:center">Sin trades cerrados aún</td></tr>'}}</tbody>
          </table>
        </div>
      </div>
    </div>`;

    document.getElementById('trades-section').innerHTML = html;

    // Update equity live label
    const eqEl = document.getElementById('equity-float');
    if(eqEl) {{ eqEl.style.color=floatPct>=0?'#00e676':'#f44336'; eqEl.textContent=(floatPct>=0?'+':'')+floatPct.toFixed(2)+'%'; }}
    const fpEl = document.getElementById('float-pnl');
    if(fpEl) {{ fpEl.style.color=floatPct>=0?'#00e676':'#f44336'; fpEl.textContent=(floatPct>=0?'+':'')+floatPct.toFixed(2)+'%'; }}

  }} catch(e) {{
    document.getElementById('trades-section').innerHTML = `<div style="background:#1a0a0a;border:1px solid #f44336;border-radius:6px;padding:10px;margin:16px 0;font-family:monospace;font-size:11px;color:#f85149">Error paper trading: ${{e.message}}</div>`;
  }}
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
</script>
</body>
</html>"""

    out = OUTPUT_DIR / 'results' / 'charts' / 'dashboard.html'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding='utf-8')
    print(f'[DASHBOARD] {out}')
    return out


if __name__ == '__main__':
    generate_html()

# --- SIGMA secrets loader (audit 2026-05-13) ---
import sys as _sigma_sys
if "/opt/sigma" not in _sigma_sys.path:
    _sigma_sys.path.insert(0, "/opt/sigma")
from utils.secrets import get_tg_token as _sigma_get_tg_token
# --- end SIGMA secrets loader ---

import sys, os, subprocess, time, sqlite3, random, json
from pathlib import Path
from itertools import cycle
from datetime import datetime

try:
    from utils.parallel_guard import global_slots_available
except Exception as _e_guard:
    print(f'[WARN] No se pudo importar utils.parallel_guard: {_e_guard}. Sin tope global.')
    def global_slots_available(cap=7):
        return cap

OUTPUT_DIR = Path('/opt/sigma')
LOG_PATH   = OUTPUT_DIR / 'results/reports/trainer.log'
MAX_PAR    = 2  # maximo 2+1 manual = 3 total
PYTHON     = '/opt/sigma_env/bin/python'

TF_QUEUE = cycle([
    ('1h',  130), ('15m', 150), ('1h',  130),
    ('4h',  120), ('1h',  130), ('15m', 150),
    ('5m',  55),  ('1h',  130), ('15m', 150),
    ('4h',  120), ('1h',  130), ('15m', 150),
])  # Reducidos (eran 240/200/180) y sin 2h (decision equipo)

ASSETS = ['BTC/USDT', 'ETH/USDT', 'LTC/USDT', 'SOL/USDT', 'BNB/USDT']
_asset_idx = 0


def log(msg):
    from datetime import timezone, timedelta
    _cl = datetime.now(tz=timezone(timedelta(hours=-4)))
    line = '[' + _cl.strftime('%H:%M:%S') + ' CLT] ' + msg
    print(line, flush=True)
    try:
        with open(LOG_PATH, 'a') as f:
            f.write(line + '\n')
    except:
        pass



TOKEN_TG   = _sigma_get_tg_token()  # token activo
CHAT_ID_TG = "-1003787411069"
_MILESTONE_FILE = "/opt/sigma/last_milestone.txt"

def _load_last_milestone():
    try:
        return int(open(_MILESTONE_FILE).read().strip())
    except:
        return 0

def _save_last_milestone(m):
    try:
        open(_MILESTONE_FILE, "w").write(str(m))
    except:
        pass

_last_milestone = [_load_last_milestone()]  # persiste entre reinicios

def _send_milestone(total, rate_hr, regime, best_models):
    """Envia resumen de hito 100K backtests a Telegram."""
    try:
        import urllib.request as _ur, json as _jn, sqlite3 as _sq
        milestone = (total // 100000) * 100000
        if milestone <= _last_milestone[0]:
            return
        _last_milestone[0] = milestone
        _save_last_milestone(milestone)

        # Top 3 modelos por CAGR
        c = _sq.connect('/opt/sigma/models/sigma.db')
        top = c.execute(
            "SELECT symbol, tf, mode, cagr, winrate FROM runs "
            "WHERE cagr > 0 AND winrate >= 42 "
            "ORDER BY score DESC LIMIT 5"
        ).fetchall()
        c.close()
        top_txt = ''
        for r in top:
            top_txt += f'  {r[0].replace("/USDT",""):4} {r[1]:4} {r[2]:15} CAGR={r[3]:+.1f}% WR={r[4]:.0f}%\n'

        hours_to_next = (milestone + 100000 - total) / max(rate_hr, 1)
        msg = (
            f'HITO {milestone:,} BACKTESTS\n\n'
            f'Velocidad: {rate_hr:,}/hr\n'
            f'Regimen: {regime}\n'
            f'Proximo hito: {milestone+100000:,} (~{hours_to_next:.1f}h)\n\n'
            f'Top 5 por score:\n{top_txt}'
        )
        data = _jn.dumps({
            "chat_id": CHAT_ID_TG, "text": msg,
            "parse_mode": ""
        }).encode()
        req = _ur.Request(
            f"https://api.telegram.org/bot{TOKEN_TG}/sendMessage",
            data=data, headers={"Content-Type": "application/json"}
        )
        _ur.urlopen(req, timeout=10)
        log(f'  [HITO] {milestone:,} backtests — notificacion enviada')
    except Exception as _me:
        log(f'  [HITO ERROR] {_me}')


# Tracker de rate de backtests para hitos
_rate_tracker = {"last_n": None, "last_ts": None}
def _compute_rate_hr():
    """Calcula rate de backtests por hora basado en delta desde última lectura."""
    import time as _t
    n = db_count()
    now = _t.time()
    if _rate_tracker["last_n"] is None or _rate_tracker["last_ts"] is None:
        _rate_tracker["last_n"] = n
        _rate_tracker["last_ts"] = now
        return 0
    dn = n - _rate_tracker["last_n"]
    dt = now - _rate_tracker["last_ts"]
    _rate_tracker["last_n"] = n
    _rate_tracker["last_ts"] = now
    return int(dn / max(dt, 1) * 3600)

def db_count():
    try:
        c = sqlite3.connect(str(OUTPUT_DIR / 'models/sigma.db'))
        n = c.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        c.close()
        return n
    except:
        return 0


def get_regime():
    """
    Obtiene régimen mayoritario entre los activos.
    /api/regime devuelve {BTC:{regime:..}, ETH:{regime:..}, ...}
    /api/signals devuelve {regime: 'BEAR', ...} — fallback más simple
    """
    try:
        import urllib.request
        r = urllib.request.urlopen('http://127.0.0.1:8080/api/regime', timeout=3)
        d = json.loads(r.read())
        # d es {BTC:{regime:BEAR}, ETH:{regime:BEAR}, ...}
        regimes = [v.get('regime', 'UNKNOWN') for v in d.values() if isinstance(v, dict)]
        if regimes:
            # Régimen mayoritario
            from collections import Counter
            return Counter(regimes).most_common(1)[0][0]
    except:
        pass
    try:
        import urllib.request
        r = urllib.request.urlopen('http://127.0.0.1:8080/api/signals', timeout=3)
        d = json.loads(r.read())
        return d.get('regime', 'UNKNOWN')
    except:
        return 'UNKNOWN'


def _get_short_models():
    """Retorna set de activos que YA tienen un modelo short decente."""
    import glob
    models_dir = OUTPUT_DIR / 'models'
    has_short = set()
    try:
        from utils.strategies import SHORT_STRATEGIES as SHORT_STRATS
    except Exception:
        SHORT_STRATS = {'breakdown', 'pullback_short', 'momentum_short'}
    for jf in models_dir.glob('*/*.json'):
        try:
            d = json.loads(jf.read_text(encoding='utf-8'))
            sym = d.get('symbol', '').replace('/USDT', '').lower()
            strat = d.get('strategy', '')
            m = d.get('metrics_oos', {})
            if strat in SHORT_STRATS and m.get('cagr', 0) > 10 and m.get('trades', 0) >= 12:
                has_short.add(sym)
        except:
            pass
    return has_short


def get_degraded_assets():
    """Detecta activos sin modelo ganador reciente O con modelo muy antiguo."""
    degraded = []
    try:
        c = sqlite3.connect(str(OUTPUT_DIR / 'models/sigma.db'))
        rows = c.execute("""
            SELECT symbol, MAX(created_at) as last_ts
            FROM runs WHERE cagr > 0 AND symbol != ''
            GROUP BY symbol
        """).fetchall()
        c.close()
        import time as _t
        now = _t.time()
        trained = {r[0] for r in rows}
        for sym in ASSETS:
            if sym not in trained:
                degraded.append(sym)
                continue
            for r in rows:
                if r[0] == sym:
                    try:
                        ts = datetime.fromisoformat(r[1]).timestamp()
                        if now - ts > 60 * 3600:
                            degraded.append(sym)
                    except:
                        pass
    except:
        pass

    # Agregar activos cuyos modelos JSON son muy antiguos (>5 dias)
    try:
        import glob as _g, time as _t
        now = _t.time()
        for sym in ['btc', 'eth', 'ltc', 'sol', 'bnb']:
            files = list((OUTPUT_DIR / 'models').glob(f'*/{sym}_*.json'))
            if not files:
                continue
            newest = max(f.stat().st_mtime for f in files)
            age_days = (now - newest) / 86400
            if age_days > 5:
                asset = next((a for a in ASSETS if sym.upper() in a), None)
                if asset and asset not in degraded:
                    log(f'  [AGE] {asset} modelo tiene {age_days:.1f}d — retrenando')
                    degraded.append(asset)
    except:
        pass


    # Modelos con WR live muy por debajo del backtest → reoptimizar
    try:
        c3 = sqlite3.connect(str(OUTPUT_DIR / 'models/sigma.db'))
        rows3 = c3.execute('''
            SELECT mls.sym
            FROM model_live_stats mls
            JOIN (SELECT symbol, tf, strategy, MAX(wr) as wr
                  FROM runs GROUP BY symbol, tf, strategy) r
              ON r.symbol=mls.sym AND r.tf=mls.tf AND r.strategy=mls.strategy
            WHERE mls.wins+mls.losses >= 5
              AND (CAST(mls.wins AS REAL)/(mls.wins+mls.losses)) < (r.wr/100.0 - 0.15)
            GROUP BY mls.sym
        ''').fetchall()
        c3.close()
        for row in rows3:
            asset = row[0]
            if asset and asset not in degraded:
                log(f'  [LIVE-DECAY] {asset} WR live <<< backtest — reoptimizando')
                degraded.append(asset)
    except Exception:
        pass

    return list(set(degraded))


def _get_asset_scores():
    """Score del mejor modelo por activo. Menor = más prioridad."""
    scores = {}
    models_dir = OUTPUT_DIR / 'models'
    for sym in ['btc', 'eth', 'ltc', 'sol', 'bnb']:
        best = -1.0
        for jf in models_dir.glob(f'*/{sym}_*.json'):
            try:
                d = json.loads(jf.read_text(encoding='utf-8'))
                m = d.get('metrics_oos', {})
                t = m.get('trades', 0); ty = m.get('trades_year', 0)
                wr = m.get('wr', 0); cagr = m.get('cagr', 0)
                dd = m.get('dd', 0); pf = m.get('pf', 1)
                if t < 10 or cagr <= 0: continue
                if ty <= 0 and t > 0: ty = t * (365.0 / 600)
                if ty < 3: continue
                if wr <= 0 and cagr > 0: wr = 50
                s = (min(ty/12, 1)*0.20 + min(cagr, 60)/60*0.40 +
                     min(max(wr/100 - .5, 0)/.20, 1)*0.20 +
                     min(cagr/abs(dd) if dd < 0 else 0, 5)/5*0.15 +
                     min(pf, 3)/3*0.05)
                if s > best: best = s
            except: pass
        scores[sym] = best
    return scores


def _pick_asset():
    global _asset_idx
    try:
        scores = _get_asset_scores()
        weighted = []
        for sym, asset in zip(['btc', 'eth', 'ltc', 'sol', 'bnb'], ASSETS):
            s = scores.get(sym, 0.50)
            weight = 2 if s < 0.45 else 1
            weighted.extend([asset] * weight)
        _asset_idx += 1
        return weighted[_asset_idx % len(weighted)]
    except:
        _asset_idx += 1
        return ASSETS[_asset_idx % len(ASSETS)]


def _ram_libre_mb():
    """Retorna RAM libre en MB leyendo /proc/meminfo."""
    try:
        with open('/proc/meminfo') as _f:
            for _line in _f:
                if _line.startswith('MemAvailable:'):
                    return int(_line.split()[1]) // 1024
    except Exception:
        pass
    return 9999


def _pipeline_ram_mb():
    """RAM total usada por todos los procesos asset_pipeline activos."""
    import subprocess as _sp
    try:
        out = _sp.check_output(['ps', 'aux'], text=True)
        total = 0
        for _l in out.split('\n'):
            if 'asset_pipeline' in _l and 'grep' not in _l:
                parts = _l.split()
                if len(parts) > 5:
                    total += int(parts[5]) // 1024
        return total
    except Exception:
        return 0


def launch(tf, trials, force_asset=None, focus='all'):
    # 2026-05-14: el trainer usa conceptos 'explore'/'new' que asset_pipeline NO acepta.
    # asset_pipeline.argparse solo acepta choices=['long','short','both','all'].
    # Sin esto, ~15% de los lanzamientos crashean en 'invalid choice'.
    _ORIGINAL_FOCUS = focus
    if focus not in ('long', 'short', 'both', 'all'):
        focus = 'both'  # default: busca ambas direcciones (incluye las new strats)
        log(f'  [FOCUS-MAP] {_ORIGINAL_FOCUS!r} -> both (asset_pipeline solo acepta long/short/both/all)')
    symbol = force_asset or _pick_asset()
    # Trials dinámicos: más para modelos fuertes (refinar), menos para débiles (fail fast)
    try:
        scores = _get_asset_scores()
        sym_key = symbol.replace('/USDT', '').lower()
        sc = scores.get(sym_key, 0)
        if sc > 0.65:
            trials = min(int(trials * 1.4), 200)   # modelo top - refinar (cap 200 para prevenir OOM)
        elif sc < 0.20:
            trials = max(int(trials * 0.6), 50)    # modelo debil - fail fast (min 50)
    except:
        pass
    args = ['prlimit', '--as=2684354560', '--',  # cap RAM 2.5GB para prevenir OOM
            PYTHON, '-u',
            str(OUTPUT_DIR / 'engine/optimization/asset_pipeline.py'),
            '--symbol', symbol, '--tf', tf, '--trials', str(trials),
            '--focus', focus]
    env = dict(os.environ)
    env['PYTHONPATH'] = str(OUTPUT_DIR)
    # Tope global compartido con master_pipeline/gap_auto_launcher/commodities
    # (ver utils/parallel_guard.py) -- antes continuous_trainer no veia a los otros 3
    if global_slots_available() <= 0:
        return None, 'global_cap_reached'
    # RAM Guard: no lanzar si RAM insuficiente
    _rl = _ram_libre_mb()
    _pr = _pipeline_ram_mb()
    if _rl < 2000:
        log(f'  [RAM GUARD] {_rl}MB libres — skip lanzamiento')
        return None, f'ram_guard({_rl}MB)'
    if _pr > 3500:
        log(f'  [RAM GUARD] Pipelines usan {_pr}MB — skip')
        return None, f'ram_pipelines({_pr}MB)'
    proc = subprocess.Popen(args, env=env, cwd=str(OUTPUT_DIR))
    focus_tag = f' [{focus.upper()}]' if focus != 'all' else ''
    return proc, f'pipeline {symbol} {tf}{focus_tag} t={trials}'


def _autopsy_failed_strats(n=20):
    """Lee autopsias recientes y retorna dict {strategy: fail_count}."""
    try:
        import json as _jj
        from pathlib import Path as _P
        ap = _P('/opt/sigma/results/reports/autopsy_learnings.json')
        if not ap.exists():
            return {}
        learnings = _jj.loads(ap.read_text())[-n:]
        failed = {}
        for entry in learnings:
            s = entry.get('strategy', '')
            if s:
                failed[s] = failed.get(s, 0) + 1
        return failed
    except Exception:
        return {}


def _new_strats_explored():
    """Cuenta cuantas de las 23 estrategias del sprint 2026-05-14 tienen runs en la DB.
    Updated 2026-05-14: usa NEW_2026_05_14 del módulo central utils.strategies."""
    try:
        import sqlite3 as _sq
        import sys as _sysn
        if '/opt/sigma' not in _sysn.path: _sysn.path.insert(0, '/opt/sigma')
        try:
            from utils.strategies import NEW_2026_05_14
            new_strats = list(NEW_2026_05_14)
        except Exception:
            # Fallback hardcoded por si el import falla
            new_strats = [
                'rsi_overbought_short', 'death_cross_short', 'ema200_rejection_short',
                'macd_bear_cross', 'lower_high_break_short',
                'wedge_breakdown_short', 'supply_zone_rejection', 'bearish_rsi_divergence',
                'volume_climax_top', 'range_break_down', 'macd_zero_cross_down',
                'stoch_rsi_short', 'williams_r_short', 'cci_reversal_short',
                'engulfing_short', 'three_candles_short', 'inside_bar_short',
                'zscore_rich_short', 'heikin_ashi_short', 'roc_negative_short',
                'dmi_bear', 'vwap_overpriced_short', 'keltner_breakdown_short',
            ]
        ph = ','.join(['?'] * len(new_strats))
        conn = _sq.connect('/opt/sigma/models/sigma.db')
        count = conn.execute(
            'SELECT COUNT(DISTINCT strategy) FROM runs WHERE strategy IN (' + ph + ')',
            new_strats
        ).fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 99  # asumir exploradas si falla


def _get_priority_combo():
    """Retorna combo activo+estrategia con mejor historial en DB."""
    try:
        conn = sqlite3.connect(str(OUTPUT_DIR / 'models/sigma.db'))
        # Top asset by avg CAGR in last 500 runs
        row = conn.execute(
            'SELECT symbol FROM runs WHERE cagr>0 AND symbol!="" '
            'ORDER BY cagr DESC LIMIT 500'
        ).fetchone()
        conn.close()
        if row and row[0]:
            return row[0], 'all'
    except Exception:
        pass
    return None, 'all'


def pick_focus_and_asset(regime, degraded):
    """
    Decide qué activo y focus usar según régimen, gaps y meta-aprendizaje.
    Incorpora autopsias de modelos fallidos y conteo de estrategias nuevas.
    """
    has_short = _get_short_models()
    needs_short = [a for a in ASSETS if a.replace('/USDT','').lower() not in has_short]

    # Meta-learning: priorizar nuevas si pocas exploradas
    new_count = _new_strats_explored()
    if new_count < 18:  # 2026-05-14: subido de 6→18 (75% de las 23 nuevas)
        log(f'  [META] Solo {new_count}/23 estrategias nuevas exploradas — focus=new')
        return None, 'new'

    # Leer fallas recientes de modelos
    failed = _autopsy_failed_strats(20)

    if regime == 'BEAR':
        short_fails = sum(failed.get(s, 0) for s in ['breakdown', 'momentum_short', 'pullback_short'])
        if short_fails > 4:
            log(f'  [META] BEAR + {short_fails} fallas short — rotando a explore')
            return None, 'explore'
        if needs_short and random.random() < 0.50:
            asset = random.choice(needs_short)
            log(f'  [BEAR] Priorizando SHORT para {asset}')
            return asset, 'short'
        elif random.random() < 0.45:
            return None, 'explore'
        elif random.random() < 0.05:
            return None, 'long'
        else:
            return None, 'short'

    elif regime == 'BULL':
        long_fails = sum(failed.get(s, 0) for s in ['breakout', 'pullback', 'momentum'])
        if long_fails > 4:
            log(f'  [META] BULL + {long_fails} fallas long — rotando a explore')
            return None, 'explore'
        focus = 'long' if random.random() < 0.55 else ('explore' if random.random() < 0.35 else 'short')
        return None, focus

    else:  # RANGE o UNKNOWN
        choice = random.choice(['new', 'new', 'explore', 'all'])
        log(f'  [META] RANGE — focus={choice}')
        return None, choice


log('=' * 55)
log('SIGMA VPS TRAINER v3 - REGIME-AWARE')
log('CPUs: 4 AMD | MAX_PAR: ' + str(MAX_PAR) + ' | DB: ' + str(db_count()))
log('=' * 55)

active = []
runs = 0
last_status = time.time()
last_regime_log = 0

while True:
    alive = []
    for p, label in active:
        if p.poll() is None:
            alive.append((p, label))
        else:
            log('  [' + label.upper() + '] exit ' + str(p.returncode) +
                ' | DB:' + str(db_count()))
            runs += 1
            # MC en modelos sin validar (cada 3 runs)
            if runs % 3 == 0:
                try:
                    subprocess.Popen(
                        [PYTHON, str(OUTPUT_DIR / 'engine/live/validate_models.py'),
                         '--runs', '1000'],
                        env=dict(os.environ), cwd=str(OUTPUT_DIR)
                    )
                    log('  [MC] Actualizando Monte Carlo...')
                except:
                    pass
            # Regenerar Pine params cada 5 runs
            if runs % 60 == 0:
                try:
                    subprocess.Popen(
                        [PYTHON, str(OUTPUT_DIR / 'engine/live/generate_pine_params.py')],
                        env=dict(os.environ), cwd=str(OUTPUT_DIR)
                    )
                    log('  [PINE] Regenerando parámetros...')
                except:
                    pass
    active = alive

    while len(active) < MAX_PAR:
        regime = get_regime()

        # Log régimen cada 30 min
        now = time.time()
        if now - last_regime_log > 300:
            has_short = _get_short_models()
            _db_n = db_count()
            log(f'  [RÉGIMEN] {regime} | SHORT models: {sorted(has_short)} | DB: {_db_n}')
            last_regime_log = now
            # Hito cada 100K backtests
            _send_milestone(_db_n, _compute_rate_hr(), regime, [])

        degraded = get_degraded_assets()
        focus = 'all'
        force_asset = None

        if degraded:
            force_asset = degraded[0]
            log(f'  [DEGRADED] Priorizando {force_asset} — sin modelo reciente')
            tf_choice = random.choice(['1h', '4h', '15m'])
            trials_choice = 180
        else:
            tf_choice, trials_choice = next(TF_QUEUE)
            # 30% del tiempo: forzar el combo con mejor historial DB
            import random as _r
            if _r.random() < 0.30:
                _pa, _pf = _get_priority_combo()
                force_asset = _pa or None
                focus = _pf
            else:
                force_asset, focus = pick_focus_and_asset(regime, degraded)
            # Explore strategies generan pocas señales en 4H — forzar a 15m/1H
            if focus == 'explore' and tf_choice in ('4h', '2h'):
                tf_choice = random.choice(['1h', '15m', '1h'])  # 1h más probable
                log(f'  [EXPLORE] TF ajustado a {tf_choice} — más señales para estrategias nuevas')

        p, label = launch(tf_choice, trials_choice, force_asset=force_asset, focus=focus)
        if p is None:
            log(f'  [SKIP] {label}')
        else:
            active.append((p, label))
        time.sleep(1.5)

    now = time.time()
    if now - last_status > 1800:
        log('STATUS | DB:' + str(db_count()) +
            ' | Activos:' + str(len(active)) + ' | Runs:' + str(runs))
        last_status = now

    time.sleep(10)

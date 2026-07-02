#!/usr/bin/env python3
"""
SIGMA — Champion Advisor (v2)
=============================
Modo ADVISOR: lee sigma.db, busca candidatos con score alto y los compara con los
campeones actuales (metrics_oos.cagr). Genera reporte y notifica al Telegram.

Por que NO auto-aplica:
- sigma.db tiene `winrate=0` (campo no se llena correctamente)
- sigma.db `direction='long'` siempre (no se setea bien)
- Promover por score solo es riesgoso sin validacion OOS

Solucion:
- ADVISOR muestra candidatos cada N horas
- Usuario o asset_pipeline.py hacen revalidacion OOS
- Solo se promueve manualmente o tras validacion confirmada

Modos:
  --report (default): Solo lista candidatos y diff con champions
  --notify-only: Envia reporte a Telegram sin tocar JSON
  --apply: Promueve solo cuando el nuevo candidato supera a actual por CAGR margin
"""
# --- SIGMA secrets loader (audit 2026-05-13) ---
import sys as _sigma_sys
if "/opt/sigma" not in _sigma_sys.path:
    _sigma_sys.path.insert(0, "/opt/sigma")
from utils.secrets import get_tg_token as _sigma_get_tg_token
# --- end SIGMA secrets loader ---

import sqlite3, json, os, shutil, glob, sys, argparse
from datetime import datetime
from pathlib import Path
import urllib.request as _ur, urllib.parse as _up

# === CONFIG ===
DB = '/opt/sigma/models/sigma.db'
MODELS_DIR = Path('/opt/sigma/models')
RETIRED_DIR = MODELS_DIR / 'retired'
LOG = Path('/opt/sigma/results/reports/champion_elector.log')
TG_TOKEN = _sigma_get_tg_token()
TG_CHAT  = "-1003787411069"

UNIVERSE_SYMS = ['BTC','ETH','SOL','LTC','BNB']
UNIVERSE_TFS  = ['15m','1h','4h']
UNIVERSE_M3_SYMS = ['AAPL','NVDA','TSLA','JPM','XOM']   # Motor 3: S&P 500
UNIVERSE_M3_TFS  = ['15m','1h','4h','1d']               # Motor 3: incluye 1d
STOCK_PREFIX = {'AAPL':'aaplusd','NVDA':'nvdausd','TSLA':'tslausd','JPM':'jpmusd','XOM':'xomusd'}

# Filtros para considerar un candidato
MIN_TRADES   = 30
MIN_SCORE    = 0.55
MIN_CAGR     = 15
MAX_DD       = -25
# Para PROMOVER: el nuevo debe superar al actual EN CAGR por al menos este margen
CAGR_MARGIN_PCT = 30  # 30% mejor CAGR (relativo)

# SHORT_STRATEGIES importado desde utils.strategies (centralizado 2026-05-14)
# Nota: usamos infer_direction() del módulo, pero mantenemos referencia local para compatibilidad
import sys as _sys_imp
if '/opt/sigma' not in _sys_imp.path: _sys_imp.path.insert(0, '/opt/sigma')
from utils.strategies import SHORT_STRATEGIES, infer_direction


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, 'a') as f: f.write(line + '\n')
    print(line, flush=True)


def tg_send(msg):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = _up.urlencode({'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML',
                              'disable_notification':'true','disable_web_page_preview':'true'}).encode()
        _ur.urlopen(_ur.Request(url, data=data), timeout=8)
    except Exception as e:
        log(f"[TG ERROR] {e}")


# infer_direction importado desde utils.strategies



def current_champions():
    """Lee los JSON de modelos activos en /opt/sigma/models/<tf>/."""
    champs = {}
    for tf in UNIVERSE_TFS + ['5m','2h']:
        d = MODELS_DIR / tf
        if not d.exists(): continue
        for f in d.glob('*.json'):
            try:
                data = json.load(open(f))
                if not isinstance(data, dict): continue
                sym = (data.get('symbol') or data.get('sym','')).replace('/USDT','').replace('/USD','')
                strat = data.get('strategy','?')
                direction = data.get('direction') or infer_direction(strat)
                metrics = data.get('metrics_oos', {}) or data.get('metrics', {})
                key = (sym, tf, direction, strat)
                champs[key] = {
                    'data': data, 'path': str(f),
                    'cagr': metrics.get('cagr', 0),
                    'wr': metrics.get('wr', 0),
                    'dd': metrics.get('dd', 0),
                    'trades': metrics.get('trades', 0),
                }
            except Exception as e: pass
    return champs


def candidates_from_db():
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=30)
    conn.execute('PRAGMA busy_timeout=30000')
    c = conn.cursor()

    SEARCH_MODES = ('walk_forward','bayesian','bayesian_aggressive','random','smoke','grid')
    placeholders = ','.join('?'*len(SEARCH_MODES))

    candidates = {}
    for sym in UNIVERSE_SYMS:
        for tf in UNIVERSE_TFS:
            sym_full = f"{sym}/USDT"
            q = f"""
                SELECT mode, params, score, cagr, max_dd, trades, profit_factor, ts
                FROM runs
                WHERE symbol=? AND tf=?
                AND score >= ? AND trades >= ?
                AND cagr >= ? AND max_dd >= ?
                AND mode NOT IN ({placeholders})
                AND mode NOT LIKE 'genetic_gen%'
                AND NOT (COALESCE(max_dd,0) = 0 AND COALESCE(profit_factor,0) = 0)
                ORDER BY score DESC LIMIT 30
            """
            params_q = [sym_full, tf, MIN_SCORE, MIN_TRADES, MIN_CAGR, MAX_DD] + list(SEARCH_MODES)
            try:
                c.execute(q, params_q)
                rows = c.fetchall()
                seen_dir = {}
                _filtered_is_only = 0
                for row in rows:
                    mode, params_json, score, cagr, dd, trades, pf, ts = row
                    # Defensa adicional: IS-only rows (max_dd=0, pf=0 → no OOS calculado)
                    if (dd in (0, None)) and (pf in (0, None)):
                        _filtered_is_only += 1
                        continue
                    direction = infer_direction(mode)
                    if direction in seen_dir: continue
                    try: params = json.loads(params_json) if params_json else {}
                    except: params = {}
                    seen_dir[direction] = {
                        'mode': mode, 'params': params, 'score': score,
                        'cagr': cagr, 'dd': dd, 'trades': trades, 'pf': pf, 'ts': ts
                    }
                    if len(seen_dir) == 2: break
                if _filtered_is_only:
                    log(f"champion_elector: {sym} {tf} — {_filtered_is_only} candidates filtered as IS-only (dd=0 pf=0)")
                for direction, cand in seen_dir.items():
                    candidates[(sym, tf, direction)] = cand
            except Exception as e:
                log(f"[QUERY ERR] {sym} {tf}: {e}")
    conn.close()

    # ── Motor 3: busca candidatos en per_study DBs (AAPL/NVDA/TSLA/JPM/XOM) ────
    import sqlite3 as _sq3
    _PER_STUDY = Path('/opt/sigma/models/optuna_per_study')
    for _m3sym in UNIVERSE_M3_SYMS:
        _pfx = STOCK_PREFIX[_m3sym]
        for _tf3 in UNIVERSE_M3_TFS:
            _seen_dir = {}
            _db_glob = list(_PER_STUDY.glob(f'{_pfx}_{_tf3}_*.db'))
            for _db in _db_glob:
                try:
                    _c3 = _sq3.connect(f'file:{_db}?mode=ro', uri=True, timeout=10)
                    _c3.execute('PRAGMA busy_timeout=5000')
                    # Get best trial by value (score)
                    # M3 per-study DBs guardan score en trial_values (no en
                    # trial_user_attributes, que está vacío). Fix 2026-07-02.
                    _rows3 = _c3.execute(
                        "SELECT t.trial_id, tv.value "
                        "FROM trials t JOIN trial_values tv ON t.trial_id=tv.trial_id "
                        "WHERE t.state='COMPLETE' AND tv.value > -999 "
                        "ORDER BY tv.value DESC LIMIT 1"
                    ).fetchall()
                    if not _rows3:
                        _c3.close(); continue
                    _tid, _score = _rows3[0]
                    if _score < MIN_SCORE:
                        _c3.close(); continue
                    # Extract strategy from DB filename: pfx_tf_STRATEGY.db
                    _strat = _db.stem[len(f'{_pfx}_{_tf3}_'):]
                    # Leer métricas desde el JSON del champion en disco (user_attrs vacíos en M3)
                    _json_path = BASE / 'models' / _tf3 / f'{_pfx}_{_strat}.json'
                    if not _json_path.exists():
                        _c3.close(); continue
                    _jd = json.loads(_json_path.read_text())
                    _moos = _jd.get('metrics_oos') or {}
                    _cagr   = float(_moos.get('cagr', 0) or 0)
                    _dd     = float(_moos.get('dd', 0) or 0)
                    _trades = int(_moos.get('trades', 0) or 0)
                    _pf     = float(_moos.get('pf', 0) or 0)
                    if _cagr < MIN_CAGR or _trades < MIN_TRADES or _dd < MAX_DD:
                        _c3.close(); continue
                    _dir   = infer_direction(_strat)
                    if _dir not in _seen_dir:
                        _seen_dir[_dir] = {
                            'mode': _strat, 'params': {}, 'score': _score,
                            'cagr': _cagr, 'dd': _dd, 'trades': _trades, 'pf': _pf, 'ts': ''
                        }
                    _c3.close()
                except Exception as _e3:
                    try: _c3.close()
                    except: pass
            for _dir3, _cand3 in _seen_dir.items():
                candidates[(_m3sym, _tf3, _dir3)] = _cand3

    return candidates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='Aplicar promociones (no recomendado sin OOS)')
    ap.add_argument('--notify', action='store_true', help='Enviar reporte a Telegram')
    args = ap.parse_args()
    DRY = not args.apply
    NOTIFY = args.notify or args.apply

    log(f"=== ELECTOR run: dry={DRY} notify={NOTIFY} ===")

    champs = current_champions()
    log(f"Champions actuales: {len(champs)}")

    cands = candidates_from_db()
    log(f"Candidatos DB: {len(cands)}")

    # Comparaciones
    upgrades = []      # nuevo CAGR (sigma.db) > actual CAGR (metrics_oos) por margin
    additions = []     # no hay JSON para este sym/tf/dir
    matched_better = [] # mismo strategy ya en prod, pero potencial mejora de params
    behind = []        # candidato existe pero es PEOR que el actual

    for (sym, tf, direction), cand in cands.items():
        existing = None
        for k, v in champs.items():
            if k[0] == sym and k[1] == tf and k[2] == direction:
                existing = v; existing_strat = k[3]
                break

        cand_cagr = cand['cagr']
        if existing:
            old_cagr = existing['cagr']
            ratio = (cand_cagr / max(old_cagr, 1)) if old_cagr > 0 else 99
            if cand['mode'] == existing_strat:
                matched_better.append((sym, tf, direction, cand, existing, ratio))
            elif old_cagr <= 0 or ratio >= (1 + CAGR_MARGIN_PCT/100):
                upgrades.append((sym, tf, direction, cand, existing, ratio))
            else:
                behind.append((sym, tf, direction, cand, existing, ratio))
        else:
            additions.append((sym, tf, direction, cand))

    # Reporte detallado
    print()
    print("=" * 75)
    print(f"REPORTE ELECTOR (modo {'DRY-RUN' if DRY else 'APPLY'})")
    print("=" * 75)

    if upgrades:
        print(f"\n🔄 UPGRADES potenciales ({len(upgrades)}) — diferente strategy, mejor CAGR")
        for sym, tf, dirn, cand, ex, ratio in upgrades:
            print(f"  {sym} {tf} {dirn}:")
            print(f"    Actual: {ex['data'].get('strategy','?'):<22}  CAGR={ex['cagr']:>5.1f}%  WR={ex['wr']:>5.1f}%  DD={ex['dd']:>5.1f}%  trades={ex['trades']}")
            print(f"    Candidato (DB): {cand['mode']:<14}  CAGR={cand['cagr']:>5.1f}%  score={cand['score']:.3f}  trades={cand['trades']}  ratio_cagr={ratio:.2f}x")

    if additions:
        print(f"\n➕ NUEVAS COMBINACIONES ({len(additions)}) — no hay JSON aun")
        for sym, tf, dirn, cand in additions:
            print(f"  + {sym} {tf} {dirn} {cand['mode']:<22}  CAGR={cand['cagr']:>5.1f}%  score={cand['score']:.3f}  trades={cand['trades']}")

    if matched_better:
        print(f"\n♻️ MISMO STRATEGY ({len(matched_better)}) — solo params podrian variar")
        for sym, tf, dirn, cand, ex, ratio in matched_better[:5]:
            print(f"  {sym} {tf} {dirn} {cand['mode']}: actual CAGR={ex['cagr']:.1f}% vs DB CAGR={cand['cagr']:.1f}% ({ratio:.2f}x)")

    if behind:
        print(f"\n⏸ DETRAS DEL ACTUAL ({len(behind)}) — no requieren accion")

    # Cobertura GAPS — sym/tf/dir sin candidato Y sin champion
    print()
    print("📊 COBERTURA")
    coverage_full = set()
    for k in champs.keys():
        coverage_full.add((k[0], k[1], k[2]))
    for k in cands.keys():
        coverage_full.add(k)
    gaps = []
    for sym in UNIVERSE_SYMS:
        for tf in UNIVERSE_TFS:
            for direction in ['long','short']:
                if (sym, tf, direction) not in coverage_full:
                    gaps.append((sym, tf, direction))
    for sym in UNIVERSE_M3_SYMS:
        for tf in UNIVERSE_M3_TFS:
            for direction in ['long','short']:
                if (sym, tf, direction) not in coverage_full:
                    gaps.append((sym, tf, direction))
    print(f"  Combos totales del universo: {len(UNIVERSE_SYMS)*len(UNIVERSE_TFS)*2}")
    print(f"  Cubiertos (en prod o DB): {len(coverage_full)}")
    print(f"  GAPS (sin candidato ni champion): {len(gaps)}")
    if gaps:
        for g in gaps[:10]:
            print(f"    - {g[0]} {g[1]} {g[2]}")

    # Apply?
    if not DRY:
        log(f"\n=== APLICANDO {len(upgrades)} upgrades + {len(additions)} adiciones ===")
        RETIRED_DIR.mkdir(exist_ok=True)
        for sym, tf, dirn, cand, ex, ratio in upgrades + [(s,t,d,c,None,0) for s,t,d,c in additions]:
            # Retirar viejo si existe
            if ex:
                old_path = ex['path']
                retired = RETIRED_DIR / f"{os.path.basename(old_path)}.{datetime.now().strftime('%Y%m%d_%H%M')}.retired"
                shutil.move(old_path, retired)
                log(f"  RETIRED: {old_path} → {retired}")
            # Escribir nuevo
            _sym_sfx = '/USD' if sym in UNIVERSE_M3_SYMS else '/USDT'
            new_data = {
                'symbol': f'{sym}{_sym_sfx}', 'tf': tf, 'strategy': cand['mode'],
                'direction': dirn,
                'params': cand['params'],
                'risk_pct': 5.0,
                'metrics_oos': {
                    'trades': cand['trades'], 'cagr': round(cand['cagr'], 2),
                    'dd': round(cand['dd'], 2),
                    'pf': round(cand['pf'], 3) if cand['pf'] else 0,
                    'score_db': round(cand['score'], 4),
                },
                '_promoted_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                '_promoted_by': 'champion_elector_v2',
                '_promotion_note': 'PENDING_OOS_REVALIDATION — params from DB raw, WR not yet validated'
            }
            tf_dir = MODELS_DIR / tf
            tf_dir.mkdir(exist_ok=True)
            new_path = tf_dir / f"{sym.lower()}_{cand['mode']}.json"
            tmp = str(new_path) + '.tmp'
            with open(tmp, 'w') as f: json.dump(new_data, f, indent=2, default=str)
            os.replace(tmp, str(new_path))
            log(f"  PROMOTED: {sym} {tf} {dirn} {cand['mode']} → {new_path}")

    # Telegram report
    if NOTIFY:
        msg = "📊 <b>SIGMA — Champion Advisor</b>\n"
        msg += f"<i>Auditoria automatica de la matrix vs produccion</i>\n\n"
        msg += f"📂 Champions en prod: <b>{len(champs)}</b>\n"
        msg += f"🔬 Candidatos en DB: <b>{len(cands)}</b>\n\n"
        if upgrades:
            msg += f"🔄 <b>Mejoras potenciales ({len(upgrades)}):</b>\n"
            for sym, tf, dirn, cand, ex, ratio in upgrades[:8]:
                msg += f"• {sym} {tf} {dirn}: {ex['data'].get('strategy','?')} → <b>{cand['mode']}</b> (CAGR {ex['cagr']:.0f}% → {cand['cagr']:.0f}%)\n"
        if additions:
            msg += f"\n➕ <b>Nuevos ({len(additions)}):</b>\n"
            for sym, tf, dirn, cand in additions[:5]:
                msg += f"+ {sym} {tf} {dirn} <b>{cand['mode']}</b> CAGR {cand['cagr']:.0f}%\n"
        if gaps:
            msg += f"\n📉 <b>Gaps de cobertura ({len(gaps)}):</b>\n<code>"
            msg += ', '.join(f'{g[0]} {g[1]} {g[2]}' for g in gaps[:6])
            msg += "</code>"
        msg += "\n\n<i>⚠️ Advisor mode: necesita OOS revalidation antes de promover automaticamente</i>"
        tg_send(msg)
        log("Telegram reporte enviado")

    log("=== Run completo ===\n")


if __name__ == '__main__':
    main()

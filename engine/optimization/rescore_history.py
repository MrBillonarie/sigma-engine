#!/usr/bin/env python3
"""
SIGMA — Historical Re-Evaluation by Score
==========================================
Re-evalúa todos los modelos guardados y todos los runs del DB con
el nuevo criterio de score (40% CAGR + 20% WR + 20% freq + 15% Cal + 5% PF).

Responde: ¿Hubieron mejores modelos en el pasado que fueron descartados
           porque tenían CAGR menor pero score mayor?

Uso:
  python engine/optimization/rescore_history.py
  python engine/optimization/rescore_history.py --apply   # guarda nuevos best
"""
import sys, os, json, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from datetime import datetime

BASE      = Path(__file__).parent.parent.parent
MODELS    = BASE / 'models'
DB_PATH   = MODELS / 'sigma.db'


# ── Score formula (mismo que asset_pipeline.py) ──────────────────────────────

def score(m, min_t=15):
    if m is None: return -9999
    t  = m.get('trades', 0)
    ty = m.get('trades_year', m.get('trades_month', 0) * 12)
    wr = m.get('wr', m.get('winrate', 0))
    cagr = m.get('cagr', 0)
    dd   = m.get('dd', m.get('max_dd', 0))
    pf   = m.get('pf', m.get('profit_factor', 1))

    if t < min_t or cagr <= 0: return -9999
    if ty < 5: return -9999

    s_freq = min(ty / 12.0, 1.0) * 0.20
    s_cagr = min(cagr, 60) / 60 * 0.40
    s_wr   = max(wr / 100 - 0.50, 0) / 0.20 * 0.20
    s_cal  = min(cagr / abs(dd) if dd < 0 else 0, 5) / 5 * 0.15
    s_pf   = min(pf, 3) / 3 * 0.05
    return round(s_freq + s_cagr + s_wr + s_cal + s_pf, 4)


def score_label(s):
    if s >= 0.70: return 'A+'
    if s >= 0.55: return 'A'
    if s >= 0.40: return 'B'
    if s >= 0.25: return 'C'
    if s > -100:  return 'D'
    return '—'


# ── Scan saved JSON models ────────────────────────────────────────────────────

def scan_models():
    """Lee todos los .json de modelos y calcula su score."""
    rows = []
    if not MODELS.exists():
        return rows

    for tf_dir in sorted(MODELS.iterdir()):
        if not tf_dir.is_dir() or tf_dir.name == '__pycache__': continue
        tf = tf_dir.name
        for jf in sorted(tf_dir.glob('*.json')):
            try:
                data = json.loads(jf.read_text(encoding='utf-8'))
                m = data.get('metrics_oos') or {}
                if not m: continue

                # trades_year: calcular desde trades + oos_days si disponible
                trades    = m.get('trades', 0)
                oos_days  = data.get('oos_days', 0) or 0
                trades_yr = m.get('trades_year', 0)
                if not trades_yr and oos_days > 0:
                    trades_yr = round(trades / max(oos_days / 365.25, 0.1), 1)
                if not trades_yr:
                    # fallback: trades_month from metrics
                    trades_yr = m.get('trades_month', 0) * 12

                m2 = dict(m)
                m2['trades_year'] = trades_yr
                s  = score(m2)

                rows.append({
                    'file':       jf.name,
                    'tf':         tf,
                    'path':       str(jf),
                    'score':      s,
                    'grade':      score_label(s),
                    'cagr':       m.get('cagr', 0),
                    'wr':         m.get('wr', m.get('winrate', 0)),
                    'dd':         m.get('dd', m.get('max_dd', 0)),
                    'pf':         m.get('pf', m.get('profit_factor', 0)),
                    'trades':     trades,
                    'trades_yr':  trades_yr,
                    'strategy':   data.get('strategy', '—'),
                    'symbol':     data.get('symbol', '—'),
                    'saved_at':   data.get('saved_at', '—'),
                })
            except Exception as e:
                print(f'  [WARN] {jf.name}: {e}')

    return sorted(rows, key=lambda r: r['score'], reverse=True)


# ── Scan DB runs ──────────────────────────────────────────────────────────────

def scan_db_top(limit=50):
    """Top runs del DB por score para cada (tf, mode)."""
    if not DB_PATH.exists():
        print(f'  [WARN] DB no encontrada: {DB_PATH}')
        return []

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Top N runs por (tf, mode) con score más alto — excluye los ya marcados is_best
    rows = conn.execute("""
        SELECT id, tf, mode, params, trades, winrate, cagr, max_dd,
               profit_factor, calmar, trades_month, score, is_best, ts
        FROM runs
        WHERE score > 0.10
          AND trades >= 15
          AND cagr > 0
        ORDER BY score DESC
        LIMIT ?
    """, (limit,)).fetchall()

    # También busca el mejor por score en cada (tf, mode) aunque sea bajo
    best_by_group = conn.execute("""
        SELECT tf, mode,
               MAX(score)  AS best_score,
               MAX(cagr)   AS best_cagr,
               COUNT(*)    AS n_runs,
               SUM(CASE WHEN score > 0.10 AND cagr > 0 THEN 1 ELSE 0 END) AS n_positive
        FROM runs
        GROUP BY tf, mode
        ORDER BY tf, best_score DESC
    """).fetchall()

    # Conteo general
    total = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    pos   = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE cagr > 0 AND score > 0.10"
    ).fetchone()[0]
    validated = conn.execute("SELECT COUNT(*) FROM runs WHERE validated=1").fetchone()[0]

    conn.close()
    return [dict(r) for r in rows], [dict(r) for r in best_by_group], total, pos, validated


# ── Compare: JSON models vs DB best ──────────────────────────────────────────

def compare(models, db_top):
    """
    Para cada modelo guardado, busca si hay un run en DB con mayor score.
    (Nota: el DB no tiene el simbolo, así que la comparación es por tf+mode)
    """
    # Indexar DB top por (tf, mode) → max score
    db_idx = {}
    for r in db_top:
        key = (r['tf'], r['mode'])
        if key not in db_idx or r['score'] > db_idx[key]['score']:
            db_idx[key] = r

    gaps = []
    for m in models:
        key = (m['tf'], m.get('strategy', ''))
        if key not in db_idx: continue
        db_run = db_idx[key]

        delta = db_run['score'] - m['score']
        if delta > 0.02:  # diferencia significativa
            gaps.append({
                'symbol':    m['symbol'],
                'tf':        m['tf'],
                'strategy':  m.get('strategy', ''),
                'file':      m['file'],
                'saved_score':  m['score'],
                'saved_cagr':   m['cagr'],
                'saved_wr':     m['wr'],
                'db_best_score': db_run['score'],
                'db_best_cagr':  db_run['cagr'],
                'db_best_wr':    db_run['winrate'],
                'delta':     round(delta, 4),
                'db_run_id': db_run['id'],
                'db_params': db_run['params'],
            })

    return sorted(gaps, key=lambda g: g['delta'], reverse=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help='Sobreescribe modelos si DB tiene score mayor')
    args = parser.parse_args()

    print('\n' + '='*70)
    print('  SIGMA — HISTORICAL RE-SCORE')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('='*70)

    # 1. Modelos guardados
    print('\n[1] MODELOS GUARDADOS (JSON) — ordenados por score')
    print(f'    {"Archivo":<35} {"TF":<6} {"Score":>7} {"Grado":>5} '
          f'{"CAGR%":>7} {"WR%":>6} {"DD%":>6} {"PF":>5} {"T/año":>6}')
    print('    ' + '-'*80)

    models = scan_models()
    if not models:
        print('    Sin modelos guardados.')
    for m in models:
        print(f'    {m["file"]:<35} {m["tf"]:<6} {m["score"]:>7.4f} {m["grade"]:>5} '
              f'{m["cagr"]:>6.1f}% {m["wr"]:>5.1f}% {m["dd"]:>5.1f}% '
              f'{m["pf"]:>4.2f} {m["trades_yr"]:>6.1f}')

    # 2. DB summary
    print('\n[2] BASE DE DATOS — resumen')
    try:
        db_top, best_by_group, total, pos, validated = scan_db_top(limit=100)

        print(f'    Total runs: {total:,} | Positivos (score>0.10): {pos:,} | Validados: {validated}')

        print(f'\n    Top 20 runs por score en DB:')
        print(f'    {"ID":>8} {"TF":<6} {"Mode":<20} {"Score":>7} {"CAGR%":>7} '
              f'{"WR%":>6} {"DD%":>6} {"PF":>5} {"Best":>5}')
        print('    ' + '-'*75)
        for r in db_top[:20]:
            best_flag = '★' if r['is_best'] else ''
            print(f'    {r["id"]:>8} {r["tf"]:<6} {r["mode"]:<20} {r["score"]:>7.4f} '
                  f'{r["cagr"]:>6.1f}% {r["winrate"]:>5.1f}% {r["max_dd"]:>5.1f}% '
                  f'{r["profit_factor"]:>4.2f} {best_flag:>5}')

        print(f'\n    Mejor score por (TF, Mode):')
        print(f'    {"TF":<6} {"Mode":<20} {"BestScore":>10} {"BestCAGR":>9} {"N":>7} {"Pos":>6}')
        print('    ' + '-'*65)
        for r in best_by_group:
            if r['best_score'] and r['best_score'] > 0:
                print(f'    {r["tf"]:<6} {r["mode"]:<20} {r["best_score"]:>10.4f} '
                      f'{r["best_cagr"]:>8.1f}% {r["n_runs"]:>7,} {r["n_positive"]:>6,}')

    except Exception as e:
        print(f'    ERROR leyendo DB: {e}')
        db_top, best_by_group = [], []

    # 3. Gaps: DB tiene mejor score que modelo guardado
    print('\n[3] GAPS — Runs en DB con score MAYOR que modelo guardado')
    print('    (Modelos que podrían ser mejores y no fueron guardados por CAGR-only)')
    gaps = compare(models, db_top)
    if not gaps:
        print('    Sin gaps significativos. Los modelos guardados son óptimos por score.')
    else:
        print(f'    {"Symbol":<8} {"TF":<6} {"Strategy":<20} '
              f'{"Guardado":>10} {"DB_Best":>8} {"Delta":>7}')
        print('    ' + '-'*65)
        for g in gaps:
            print(f'    {g["symbol"]:<8} {g["tf"]:<6} {g["strategy"]:<20} '
                  f'score={g["saved_score"]:.4f} → {g["db_best_score"]:.4f} '
                  f'(+{g["delta"]:.4f})')
            print(f'       CAGR guardado: {g["saved_cagr"]:+.1f}%  | DB CAGR: {g["db_best_cagr"]:+.1f}%  '
                  f'DB_WR: {g["db_best_wr"]:.1f}%')
            print(f'       DB run_id={g["db_run_id"]} | params={g["db_params"][:80]}...')

        if args.apply:
            print('\n  [APPLY] Aplicando cambios...')
            print('  [WARN] --apply requiere re-correr backtest con esos params para generar modelo completo.')
            print('  [INFO] Guarda los run_ids aquí para re-testear manualmente:')
            for g in gaps:
                print(f'         run_id={g["db_run_id"]}  {g["symbol"]} {g["tf"]} {g["strategy"]}')

    # 4. Ranking final de modelos por score
    print('\n[4] RANKING GLOBAL — mejores modelos guardados por score')
    top = [m for m in models if m['score'] > 0][:15]
    for i, m in enumerate(top, 1):
        sym = m.get('symbol', '').replace('/USDT', '') or m['file'].split('_')[0].upper()
        print(f'  #{i:>2} {sym:<5} {m["tf"]:<5} {m.get("strategy",""):<20} '
              f'score={m["score"]:.4f} [{m["grade"]}] '
              f'CAGR={m["cagr"]:+.1f}% WR={m["wr"]:.0f}% DD={m["dd"]:.1f}% '
              f'PF={m["pf"]:.2f}')

    print('\n' + '='*70)
    print('  DONE')
    print('='*70 + '\n')


if __name__ == '__main__':
    main()

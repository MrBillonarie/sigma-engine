#!/usr/bin/env python3
"""
SIGMA DB Report — análisis histórico por símbolo
Uso: python engine/optimization/db_report.py
     python engine/optimization/db_report.py --symbol BTC --tf 1h
"""
import sys, os, json, sqlite3, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path
from datetime import datetime

BASE    = Path(__file__).parent.parent.parent
DB_PATH = BASE / 'models' / 'sigma.db'

GRADES = [(0.70,'A+'),(0.55,'A'),(0.40,'B'),(0.25,'C'),(0,'D')]

def grade(s):
    if s <= -100: return '—'
    for thresh, g in GRADES:
        if s >= thresh: return g
    return 'D'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default='', help='BTC, ETH, SOL...')
    parser.add_argument('--tf',     default='', help='1h, 4h, 15m...')
    parser.add_argument('--top',    type=int, default=20)
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Stats globales
    total = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    syms  = conn.execute("SELECT DISTINCT symbol FROM runs WHERE symbol!='' ORDER BY symbol").fetchall()
    print(f'\n{"="*70}')
    print(f'  SIGMA DB REPORT — {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'  Total runs: {total:,} | Símbolos: {[r[0] for r in syms]}')
    print('='*70)

    # Builds query filters
    where = []
    params = []
    if args.symbol:
        where.append("symbol LIKE ?")
        params.append(f'%{args.symbol.upper()}%')
    if args.tf:
        where.append("tf=?")
        params.append(args.tf)
    where.append("score > 0.10 AND trades >= 15 AND cagr > 0")
    where_str = ' AND '.join(where)

    # Top runs
    rows = conn.execute(f"""
        SELECT symbol, tf, mode, trades, winrate, cagr, max_dd,
               profit_factor, score, ts
        FROM runs WHERE {where_str}
        ORDER BY score DESC LIMIT ?
    """, params + [args.top]).fetchall()

    print(f'\n  TOP {args.top} RUNS (filtro: symbol={args.symbol or "todos"} tf={args.tf or "todos"})')
    print(f'  {"Symbol":<12} {"TF":<6} {"Mode":<20} {"Score":>7} {"Gr":>3} '
          f'{"CAGR%":>7} {"WR%":>6} {"DD%":>6} {"PF":>5} {"Fecha"}')
    print('  ' + '-'*80)
    for r in rows:
        g = grade(r['score'])
        sym = (r['symbol'] or '').replace('/USDT','')
        print(f'  {sym:<12} {r["tf"]:<6} {r["mode"]:<20} {r["score"]:>7.4f} {g:>3} '
              f'{r["cagr"]:>6.1f}% {r["winrate"]:>5.1f}% {r["max_dd"]:>5.1f}% '
              f'{r["profit_factor"]:>4.2f} {(r["ts"] or "")[:10]}')

    # Por símbolo+TF resumen
    print(f'\n  RESUMEN POR SÍMBOLO + TF')
    print(f'  {"Symbol":<12} {"TF":<6} {"Runs":>6} {"BestScore":>10} {"BestCAGR":>9} {"BestWR%":>8}')
    print('  ' + '-'*55)
    summary = conn.execute("""
        SELECT symbol, tf, COUNT(*) as n,
               MAX(score) as best_score,
               MAX(cagr)  as best_cagr,
               MAX(winrate) as best_wr
        FROM runs
        WHERE score > 0.10 AND cagr > 0 AND symbol != ''
        GROUP BY symbol, tf
        ORDER BY symbol, tf
    """).fetchall()
    for r in summary:
        sym = (r['symbol'] or '').replace('/USDT','')
        g = grade(r['best_score'])
        print(f'  {sym:<12} {r["tf"]:<6} {r["n"]:>6,} {r["best_score"]:>10.4f} [{g}] '
              f'{r["best_cagr"]:>8.1f}% {r["best_wr"]:>7.1f}%')

    conn.close()
    print()

if __name__ == '__main__':
    main()

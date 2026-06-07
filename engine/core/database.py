"""
SIGMA ENGINE — Database Layer
SQLite persistente: guarda TODOS los resultados, aprende del historial.
Nunca empieza desde cero — cada run mejora sobre el anterior.
"""

import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "models" / "sigma.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Crea tablas si no existen."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT DEFAULT (datetime('now')),
            tf          TEXT,
            mode        TEXT,
            symbol      TEXT DEFAULT '',
            params      TEXT,
            trades      INTEGER,
            winrate     REAL,
            cagr        REAL,
            pnl_pct     REAL,
            sharpe      REAL,
            max_dd      REAL,
            profit_factor REAL,
            calmar      REAL,
            trades_month REAL,
            score       REAL,
            is_best     INTEGER DEFAULT 0,
            validated   INTEGER DEFAULT 0,
            notes       TEXT
        );

        CREATE TABLE IF NOT EXISTS best_per_tf (
            tf          TEXT PRIMARY KEY,
            run_id      INTEGER,
            params      TEXT,
            trades      INTEGER,
            winrate     REAL,
            cagr        REAL,
            pnl_pct     REAL,
            sharpe      REAL,
            max_dd      REAL,
            profit_factor REAL,
            score       REAL,
            updated_at  TEXT DEFAULT (datetime('now')),
            pine_script TEXT
        );

        CREATE TABLE IF NOT EXISTS wft_results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT DEFAULT (datetime('now')),
            tf          TEXT,
            run_id      INTEGER,
            oos_win_rate REAL,
            avg_efficiency REAL,
            fdr_pct     REAL,
            mc_ruin_pct REAL,
            mc_dd_p95   REAL,
            edge_score  INTEGER,
            verdict     TEXT
        );

        CREATE TABLE IF NOT EXISTS regime_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT DEFAULT (datetime('now')),
            tf          TEXT,
            regime      TEXT,
            adx         REAL,
            hurst       REAL,
            vol_pct     REAL,
            action      TEXT
        );

        CREATE TABLE IF NOT EXISTS live_trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT DEFAULT (datetime('now')),
            tf          TEXT,
            direction   TEXT,
            quality     TEXT,
            entry       REAL,
            sl          REAL,
            tp1         REAL,
            exit_price  REAL,
            pnl_usd     REAL,
            pnl_pct     REAL,
            result      TEXT,
            score_hud   INTEGER,
            regime      TEXT,
            session     TEXT,
            notes       TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_runs_tf     ON runs(tf);
        CREATE INDEX IF NOT EXISTS idx_runs_score  ON runs(score DESC);
        CREATE INDEX IF NOT EXISTS idx_runs_ts     ON runs(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_runs_symbol ON runs(symbol);
        """)
    print(f"  [DB] Inicializada: {DB_PATH}")


def migrate_db():
    """Agrega columnas nuevas si no existen (para DBs existentes)."""
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()]
        if 'symbol' not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN symbol TEXT DEFAULT ''")
            print("  [DB] Migración: columna 'symbol' agregada")
        if 'direction' not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN direction TEXT DEFAULT 'long'")
            print("  [DB] Migración: columna 'direction' agregada")


def save_run(tf, mode, params, metrics, score, symbol='', direction='long', pine_script=None):
    """Guarda un resultado de optimization."""
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO runs (tf, mode, symbol, direction, params, trades, winrate, cagr, pnl_pct,
                              sharpe, max_dd, profit_factor, calmar, trades_month, score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            tf, mode, symbol, direction, json.dumps(params),
            metrics.get("trades", 0),
            metrics.get("winrate", metrics.get("wr", 0)),
            metrics.get("cagr", metrics.get("pnl_pct", 0)),
            metrics.get("pnl_pct", 0),
            metrics.get("sharpe", 0),
            metrics.get("max_dd", 0),
            metrics.get("profit_factor", 0),
            metrics.get("calmar", 0),
            metrics.get("trades_month", 0),
            score
        ))
        run_id = cur.lastrowid

        # Actualizar best_per_tf si es mejor
        existing = conn.execute(
            "SELECT score FROM best_per_tf WHERE tf=?", (tf,)
        ).fetchone()

        if existing is None or score > existing["score"]:
            conn.execute("""
                INSERT OR REPLACE INTO best_per_tf
                (tf, run_id, params, trades, winrate, cagr, pnl_pct, sharpe,
                 max_dd, profit_factor, score, updated_at, pine_script)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),?)
            """, (
                tf, run_id, json.dumps(params),
                metrics.get("trades", 0),
                metrics.get("winrate", metrics.get("wr", 0)),
                metrics.get("cagr", metrics.get("pnl_pct", 0)),
                metrics.get("pnl_pct", 0),
                metrics.get("sharpe", 0),
                metrics.get("max_dd", 0),
                metrics.get("profit_factor", 0),
                score, pine_script or ""
            ))
            conn.execute(
                "UPDATE runs SET is_best=1 WHERE id=?", (run_id,)
            )
            return run_id, True  # es nuevo mejor

        return run_id, False


def get_best(tf):
    """Retorna la mejor config conocida para un TF."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM best_per_tf WHERE tf=?", (tf,)
        ).fetchone()
        if row:
            d = dict(row)
            d["params"] = json.loads(d["params"])
            return d
    return None


def get_top_runs(tf, n=20, min_trades=30):
    """Top N runs para un TF — base para Bayesian optimization."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT params, trades, winrate, cagr, sharpe, max_dd, profit_factor, score
            FROM runs
            WHERE tf=? AND trades>=? AND score > -100
            ORDER BY score DESC LIMIT ?
        """, (tf, min_trades, n)).fetchall()
        return [dict(r) for r in rows]


def get_top_runs_by_strategy(tf, strategy, n=10, min_trades=10):
    """Top N runs para un TF+estrategia específica — warm-start preciso para Optuna."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT params, trades, winrate, cagr, sharpe, max_dd, profit_factor, score
            FROM runs
            WHERE tf=? AND mode=? AND trades>=? AND score > -100
            ORDER BY score DESC LIMIT ?
        """, (tf, strategy, min_trades, n)).fetchall()
        return [dict(r) for r in rows]


def get_param_importance(tf, n=50):
    """
    Analiza correlacion de parametros con el score.
    Retorna dict {param: importancia} para guiar la busqueda.
    """
    runs = get_top_runs(tf, n=n, min_trades=10)
    if len(runs) < 5:
        return {}

    import numpy as np
    scores = np.array([r["score"] for r in runs])
    importance = {}

    for run in runs:
        params = json.loads(run["params"]) if isinstance(run["params"], str) else run["params"]
        for key, val in params.items():
            if isinstance(val, (int, float, bool)):
                importance.setdefault(key, []).append((float(val), run["score"]))

    result = {}
    for key, pairs in importance.items():
        if len(pairs) < 3:
            continue
        vals   = np.array([p[0] for p in pairs])
        scores = np.array([p[1] for p in pairs])
        if vals.std() > 0:
            corr = np.corrcoef(vals, scores)[0, 1]
            result[key] = abs(corr)

    return dict(sorted(result.items(), key=lambda x: x[1], reverse=True))


def save_wft(tf, run_id, wft_metrics):
    """Guarda resultado de Walk-Forward."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO wft_results
            (tf, run_id, oos_win_rate, avg_efficiency, fdr_pct,
             mc_ruin_pct, mc_dd_p95, edge_score, verdict)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            tf, run_id,
            wft_metrics.get("oos_win_rate", 0),
            wft_metrics.get("avg_efficiency", 0),
            wft_metrics.get("fdr_pct", 100),
            wft_metrics.get("mc_ruin_pct", 100),
            wft_metrics.get("mc_dd_p95", -50),
            wft_metrics.get("edge_score", 0),
            wft_metrics.get("verdict", "UNKNOWN")
        ))
        if wft_metrics.get("edge_score", 0) >= 7:
            conn.execute(
                "UPDATE runs SET validated=1 WHERE id=?", (run_id,)
            )


def save_live_trade(tf, trade_dict):
    """Registra un trade real."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO live_trades
            (tf, direction, quality, entry, sl, tp1, exit_price,
             pnl_usd, pnl_pct, result, score_hud, regime, session, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            tf,
            trade_dict.get("direction", ""),
            trade_dict.get("quality", ""),
            trade_dict.get("entry", 0),
            trade_dict.get("sl", 0),
            trade_dict.get("tp1", 0),
            trade_dict.get("exit_price", 0),
            trade_dict.get("pnl_usd", 0),
            trade_dict.get("pnl_pct", 0),
            trade_dict.get("result", ""),
            trade_dict.get("score_hud", 0),
            trade_dict.get("regime", ""),
            trade_dict.get("session", ""),
            trade_dict.get("notes", "")
        ))


def db_summary():
    """Resumen de todo lo guardado en la DB."""
    with get_conn() as conn:
        total  = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        best_n = conn.execute("SELECT COUNT(*) FROM best_per_tf").fetchone()[0]
        wft_n  = conn.execute("SELECT COUNT(*) FROM wft_results").fetchone()[0]
        live_n = conn.execute("SELECT COUNT(*) FROM live_trades").fetchone()[0]
        val_n  = conn.execute("SELECT COUNT(*) FROM runs WHERE validated=1").fetchone()[0]

        print(f"  [DB] {DB_PATH.name}")
        print(f"  Runs totales:    {total:,}")
        print(f"  TFs con best:    {best_n}")
        print(f"  WFT realizados:  {wft_n}")
        print(f"  Trades validados:{val_n}")
        print(f"  Live trades:     {live_n}")

        rows = conn.execute("""
            SELECT tf, trades, winrate, cagr, profit_factor, score, updated_at
            FROM best_per_tf ORDER BY tf
        """).fetchall()
        if rows:
            print(f"\n  {'TF':<6} {'T/mes':>5} {'WR%':>7} {'CAGR%':>8} {'PF':>6} {'Score':>7}")
            print(f"  {'-'*45}")
            for r in rows:
                print(f"  {r['tf']:<6} {'?':>5} {r['winrate']:>6.1f}% "
                      f"{r['cagr']:>7.1f}% {r['profit_factor']:>6.2f} {r['score']:>7.4f}")


# Auto-init
init_db()
migrate_db()

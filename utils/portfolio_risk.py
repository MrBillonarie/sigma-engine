"""
Portfolio risk module — hedge fund style metrics.
Calculates: strategy correlation matrix, portfolio VaR, concentration by asset,
alpha/beta vs market, diversification score.
"""
import json, math, time, glob, sys
from pathlib import Path
from collections import defaultdict

BASE     = Path("/opt/sigma")
TS_FILE  = BASE / "results/trade_state.json"
OUT_FILE = BASE / "results/reports/portfolio_risk.json"

sys.path.insert(0, str(BASE))

# Beta vs BTC por activo para el stress test de escenario (asset_beta_to_btc).
# Aproximacion documentada, no medida en vivo: BTC mueve el mercado crypto
# completo en un selloff y los altcoins históricamente caen mas (beta>1);
# los commodities no siguen a BTC, se asume beta 0 (sin beneficio de hedge
# asumido, conservador en la dirección de no subestimar el riesgo).
ASSET_BETA_TO_BTC = {
    "BTC": 1.0, "ETH": 1.2, "SOL": 1.4, "BNB": 1.3, "LTC": 1.4,
    "XAU": 0.0, "XAG": 0.0, "WTI": 0.0, "NG": 0.0, "PL": 0.0, "HG": 0.0,
}
STRESS_SHOCKS_BTC_PCT = [-10.0, -20.0, -30.0]


def _pearson(a, b):
    """Pearson correlation between two lists of equal length."""
    n = len(a)
    if n < 3:
        return None
    ma = sum(a)/n; mb = sum(b)/n
    num = sum((a[i]-ma)*(b[i]-mb) for i in range(n))
    da  = math.sqrt(sum((x-ma)**2 for x in a))
    db  = math.sqrt(sum((x-mb)**2 for x in b))
    if da == 0 or db == 0:
        return None
    return round(num/(da*db), 3)


def _var_historical(returns, confidence=0.95):
    """Historical VaR at given confidence level."""
    if not returns:
        return None
    s = sorted(returns)
    idx = int((1 - confidence) * len(s))
    return round(s[max(0,idx)], 4)


def _cvar_historical(returns, confidence=0.95):
    """Conditional VaR (Expected Shortfall)."""
    if not returns:
        return None
    s = sorted(returns)
    n_tail = max(1, int((1-confidence)*len(s)))
    tail = s[:n_tail]
    return round(sum(tail)/len(tail), 4)


def _simple_beta(strat_returns, mkt_returns):
    """OLS beta: cov(S,M)/var(M)."""
    n = min(len(strat_returns), len(mkt_returns))
    if n < 3:
        return None
    s = strat_returns[:n]; m = mkt_returns[:n]
    ms = sum(s)/n; mm = sum(m)/n
    cov = sum((s[i]-ms)*(m[i]-mm) for i in range(n))/n
    var = sum((m[i]-mm)**2 for i in range(n))/n
    return round(cov/var, 3) if var > 0 else None



def _compute_factor_decomposition(trades):
    """
    Decompose portfolio returns into:
    - Market beta (exposure to BTC direction)
    - Net alpha (return after removing market contribution)
    - Information ratio (alpha / tracking error)
    """
    import sqlite3 as _sq, os as _os, time as _tm
    if len(trades) < 5:
        return {}

    # Build trade return series
    rets = [t.get("pnl_pct", 0) or 0 for t in trades]
    n    = len(rets)

    # Approximate market return: BTC daily close returns from OHLCV cache
    # If we can't get market data, use a simplified approach
    mkt_returns = []
    try:
        _lsr_db = "/opt/sigma/results/lsr.db"
        if _os.path.exists(_lsr_db):
            # Use BTC LSR as market proxy (longs > shorts = bullish market)
            conn = _sq.connect(_lsr_db, timeout=2)
            rows = conn.execute(
                "SELECT ls_ratio FROM lsr WHERE symbol='BTCUSDT' AND tf='1h' AND kind='global' "
                "ORDER BY ts DESC LIMIT 50"
            ).fetchall()
            conn.close()
            if rows:
                lsr_vals = [r[0] for r in rows]
                mkt_returns = [lsr_vals[i] - lsr_vals[i+1] for i in range(len(lsr_vals)-1)]
                mkt_returns = mkt_returns[:n]
    except Exception:
        pass

    # If no market data, return basic stats
    if len(mkt_returns) < 5:
        avg_r = sum(rets) / n
        std_r = (sum((r - avg_r)**2 for r in rets) / max(n-1, 1)) ** 0.5
        info_r = avg_r / max(std_r, 0.001) * (252**0.5)  # annualized
        return {
            "beta_market": None,
            "net_alpha_pct": round(avg_r, 3),
            "avg_pnl_pct": round(avg_r, 3),
            "std_pnl_pct": round(std_r, 3),
            "info_ratio_annualized": round(info_r, 2),
            "method": "no_market_data",
        }

    # Pearson beta calculation
    min_n = min(len(rets), len(mkt_returns))
    r_s = rets[:min_n]
    m_s = mkt_returns[:min_n]
    mean_r = sum(r_s) / min_n
    mean_m = sum(m_s) / min_n
    cov = sum((r_s[i]-mean_r)*(m_s[i]-mean_m) for i in range(min_n)) / max(min_n-1, 1)
    var_m = sum((x-mean_m)**2 for x in m_s) / max(min_n-1, 1)
    beta  = cov / max(var_m, 0.0001)

    # Alpha = portfolio return - beta * market return
    alpha_series = [r_s[i] - beta * m_s[i] for i in range(min_n)]
    avg_alpha = sum(alpha_series) / min_n
    std_alpha = (sum((a - avg_alpha)**2 for a in alpha_series) / max(min_n-1, 1)) ** 0.5
    info_ratio = avg_alpha / max(std_alpha, 0.001) * (252**0.5)

    return {
        "beta_market": round(beta, 3),
        "net_alpha_pct": round(avg_alpha, 3),
        "avg_pnl_pct": round(mean_r, 3),
        "std_pnl_pct": round((sum((r-mean_r)**2 for r in r_s)/max(min_n-1,1))**0.5, 3),
        "info_ratio_annualized": round(info_ratio, 2),
        "method": "beta_adjusted",
        "n_used": min_n,
    }


def stress_scenarios(open_trades, equity):
    """Forward-looking: ¿qué pasa con las posiciones REALES abiertas hoy si
    BTC cae -10%/-20%/-30% en un día? Usa la misma fórmula de pnl que el resto
    del sistema (pnl_pct = move_a_favor_pct * kelly_pct/sl_dist_pct_at_open) y
    el beta por activo de ASSET_BETA_TO_BTC. Cada posición se clipea a su
    propio -kelly_pct (el peor caso real ya es "toca SL"), así el escenario
    nunca exagera por encima de lo que la protección de SL permite.
    """
    scenarios = {}
    for btc_shock in STRESS_SHOCKS_BTC_PCT:
        total_pnl_pct = 0.0
        per_position = []
        for t in open_trades:
            sym   = (t.get("sym") or "").upper()
            direc = (t.get("direction") or "long").lower()
            kelly = t.get("kelly_pct_used", t.get("kelly_pct", 2.2)) or 2.2
            sl_dist = t.get("sl_dist_pct_at_open") or 5.0
            beta  = ASSET_BETA_TO_BTC.get(sym, 0.6)  # default conservador para activos no listados
            asset_move_pct = btc_shock * beta
            # long sufre cuando el precio cae (move_in_favor negativo); short se
            # beneficia de la misma caida (move_in_favor positivo) -- signo invertido.
            move_in_favor_pct = asset_move_pct if direc == "long" else -asset_move_pct
            pnl_pct = move_in_favor_pct * (kelly / max(sl_dist, 0.1))
            pnl_pct = max(pnl_pct, -kelly)  # floor: nunca peor que el SL real
            total_pnl_pct += pnl_pct
            per_position.append({
                "sym": sym, "tf": t.get("tf"), "direction": direc,
                "kelly_pct": round(kelly, 2), "pnl_pct": round(pnl_pct, 3),
            })
        scenarios[f"btc_{int(btc_shock)}pct"] = {
            "btc_shock_pct": btc_shock,
            "portfolio_pnl_pct": round(total_pnl_pct, 3),
            "portfolio_pnl_usd": round(equity * total_pnl_pct / 100, 2),
            "positions": per_position,
        }
    return scenarios


def compute():
    ts   = json.load(open(TS_FILE))
    hist = ts.get("history",[]) or []
    port = ts.get("portfolio",{}) or {}
    open_raw = ts.get("open", {}) or {}
    open_trades = list(open_raw.values()) if isinstance(open_raw, dict) else (open_raw or [])
    equity  = port.get("equity",10000)
    initial = port.get("initial_capital",10000)

    if not hist:
        result = {"computed_at": time.strftime("%Y-%m-%d %H:%M"),
                  "error":"no_trade_history", "n_trades":0}
        json.dump(result, open(OUT_FILE,"w"), indent=2)
        return result

    # ── Build trade series per strategy ────────────────────────────────────
    by_strat  = defaultdict(list)   # strat_key → [pnl_pct, ...]
    by_asset  = defaultdict(list)   # sym → [pnl_pct, ...]
    by_dir    = defaultdict(list)   # direction → [pnl_pct, ...]
    all_pnl   = []
    all_pnl_ts= []  # (timestamp, pnl_pct)

    for t in hist:
        strat = t.get("strategy","?")
        sym   = t.get("sym","") or t.get("symbol","").replace("/USDT","").replace("/USD","")
        tf    = t.get("tf","")
        pnl   = t.get("pnl_pct",0) or 0
        direc = t.get("direction","?")
        ts_raw= t.get("closed_at") or t.get("opened_at","")
        key   = f"{sym}/{tf}/{strat}"

        by_strat[key].append(pnl)
        by_asset[sym].append(pnl)
        by_dir[direc].append(pnl)
        all_pnl.append(pnl)
        all_pnl_ts.append((ts_raw, pnl))

    n_trades = len(all_pnl)

    # ── Correlation matrix between strategies ───────────────────────────────
    strat_keys = list(by_strat.keys())
    corr_matrix = {}
    for i, k1 in enumerate(strat_keys):
        for k2 in strat_keys[i+1:]:
            s1 = by_strat[k1]; s2 = by_strat[k2]
            n  = min(len(s1),len(s2))
            if n >= 3:
                c = _pearson(s1[:n], s2[:n])
                if c is not None:
                    corr_matrix[f"{k1}↔{k2}"] = c

    # High correlation pairs (|r| > 0.5)
    high_corr = {k:v for k,v in corr_matrix.items() if abs(v) > 0.5}

    # ── Portfolio VaR ───────────────────────────────────────────────────────
    var_95  = _var_historical(all_pnl, 0.95)
    cvar_95 = _cvar_historical(all_pnl, 0.95)
    var_99  = _var_historical(all_pnl, 0.99)

    # ── Per-asset concentration ─────────────────────────────────────────────
    asset_stats = {}
    for sym, pnls in by_asset.items():
        n = len(pnls)
        wins  = sum(1 for p in pnls if p > 0)
        pnl   = sum(pnls)
        asset_stats[sym] = {
            "n":         n,
            "weight_pct":round(n/n_trades*100,1),
            "wr":        round(wins/n*100,1) if n>0 else None,
            "total_pnl": round(pnl,2),
            "avg_pnl":   round(pnl/n,2) if n>0 else None,
            "var_95":    _var_historical(pnls, 0.95),
        }

    # Concentration (Herfindahl index: 1/n = perfectly diversified)
    weights = [v["n"]/n_trades for v in asset_stats.values()]
    hhi = sum(w*w for w in weights)
    n_eff = round(1/hhi, 2) if hhi > 0 else None  # effective number of assets
    concentration_ok = hhi < 0.35  # below 35% HHI = reasonably diversified

    # ── Direction split ─────────────────────────────────────────────────────
    dir_stats = {}
    for direc, pnls in by_dir.items():
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        dir_stats[direc] = {
            "n": n,
            "weight_pct": round(n/n_trades*100,1),
            "wr": round(wins/n*100,1) if n>0 else None,
            "total_pnl": round(sum(pnls),2),
        }

    # ── Alpha vs "buy and hold" proxy ───────────────────────────────────────
    # Proxy: if trade is LONG, market return = +mean(all_long_pnl)
    # Simple alpha = strategy_avg - direction_avg (measures timing skill)
    strat_alpha = {}
    for key, pnls in by_strat.items():
        direction = "short" if "short" in key.split("/")[-1] else "long"
        dir_pnls  = by_dir.get(direction, [])
        if len(pnls) >= 2 and dir_pnls:
            dir_avg   = sum(dir_pnls)/len(dir_pnls)
            strat_avg = sum(pnls)/len(pnls)
            alpha     = round(strat_avg - dir_avg, 3)
            strat_alpha[key] = {
                "n": len(pnls),
                "avg_pnl": round(strat_avg,3),
                "dir_avg": round(dir_avg,3),
                "alpha":   alpha,
                "positive_alpha": alpha > 0,
            }

    # ── Drawdown sequence ───────────────────────────────────────────────────
    equity_curve = [initial]
    for ts_raw, pnl in sorted(all_pnl_ts):
        last = equity_curve[-1]
        # approximate: pnl_pct applied to current equity
        equity_curve.append(last * (1 + pnl/100))

    peak = initial
    max_dd = 0
    for eq in equity_curve:
        if eq > peak: peak = eq
        dd = (eq - peak) / peak * 100
        if dd < max_dd: max_dd = dd

    # Longest losing streak
    streak = 0; max_streak = 0
    for p in all_pnl:
        if p < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    result = {
        "computed_at": time.strftime("%Y-%m-%d %H:%M"),
        "n_trades":    n_trades,
        "portfolio": {
            "equity":     equity,
            "initial":    initial,
            "return_pct": round((equity/initial-1)*100,2),
            "var_95_pct": var_95,
            "cvar_95_pct":cvar_95,
            "var_99_pct": var_99,
            "max_dd_pct": round(max_dd,2),
            "max_losing_streak": max_streak,
        },
        "concentration": {
            "hhi":              round(hhi,4),
            "n_effective_assets":n_eff,
            "ok":               concentration_ok,
            "assets":           asset_stats,
        },
        "direction_split": dir_stats,
        "correlations": {
            "all":       corr_matrix,
            "high_pairs":high_corr,
            "n_high":    len(high_corr),
        },
        "alpha_per_strategy": strat_alpha,
        "stress_test": stress_scenarios(open_trades, equity),
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(result, open(OUT_FILE,"w"), indent=2)
    return result


if __name__ == "__main__":
    r = compute()
    p = r["portfolio"]
    c = r["concentration"]
    print(f"Portfolio: ${p['equity']:.0f} | return={p['return_pct']:+.1f}%")
    print(f"VaR 95%: {p['var_95_pct']}% | CVaR 95%: {p['cvar_95_pct']}% | MaxDD: {p['max_dd_pct']}%")
    print(f"Max losing streak: {p['max_losing_streak']}")
    print()
    print(f"Concentration: HHI={c['hhi']:.3f} | Effective assets={c['n_effective_assets']} | OK={c['ok']}")
    print("Asset breakdown:")
    for sym, a in sorted(c["assets"].items()):
        print(f"  {sym}: {a['n']} trades ({a['weight_pct']}%) | WR={a['wr']}% | PnL={a['total_pnl']:+.1f}%")
    print()
    ds = r["direction_split"]
    print("Direction split:")
    for d, v in ds.items():
        print(f"  {d}: {v['n']} trades ({v['weight_pct']}%) | WR={v['wr']}% | PnL={v['total_pnl']:+.1f}%")
    print()
    print("High correlation pairs:", r["correlations"]["high_pairs"])
    print()
    print("Alpha per strategy (vs direction avg):")
    for k, v in sorted(r["alpha_per_strategy"].items(), key=lambda x:-x[1]["alpha"]):
        sign = "+" if v["positive_alpha"] else "-"
        print(f"  {k:<40} alpha={v['alpha']:+.2f}% (strat={v['avg_pnl']:+.2f}% dir_avg={v['dir_avg']:+.2f}%)")
    print()
    print("Stress test (posiciones reales abiertas hoy):")
    for key, sc in r["stress_test"].items():
        print(f"  BTC {sc['btc_shock_pct']:+.0f}%: portafolio {sc['portfolio_pnl_pct']:+.2f}% (${sc['portfolio_pnl_usd']:+,.2f})")


"""SIGMA Quant Toolkit — metodologia de hedge funds aplicada a nuestra escala.

Creado 2026-05-19 tras demanda explicita del user: pensar como Two Sigma/Renaissance,
no como bot de Telegram.

Funciones:
- sharpe_with_ci: Sharpe ratio con intervalo de confianza (lower bound es el verdadero edge)
- bayesian_edge: posterior probabilidad de edge real dada N trades observados (Beta-Binomial)
- decay_signal: detecta degradacion de edge comparando live Sharpe vs backtest Sharpe
- position_correlation_gate: detecta redundancia entre champions (mismo trade efectivo)
"""

import math
from statistics import mean, stdev


# ========================================================================
# 1. SHARPE RATIO CON CI - Renaissance-style robustness
# ========================================================================

def sharpe_with_ci(returns_list, confidence=0.95, periods_per_year=252):
    """Sharpe ratio con intervalo de confianza usando Lo (2002).

    Lower CI bound = Sharpe verdadero mas conservador.
    Strategy con Sharpe 2.5 (N=20) tiene lower_ci ~0.5 (poco confiable).
    Strategy con Sharpe 1.2 (N=200) tiene lower_ci ~1.0 (mas reliable).
    """
    n = len(returns_list)
    if n < 5:
        return {"sharpe": None, "lower_ci": None, "upper_ci": None, "n": n, "reason": "n<5"}

    mu = mean(returns_list)
    sigma = stdev(returns_list) if n > 1 else 0
    if sigma == 0:
        return {"sharpe": None, "lower_ci": None, "upper_ci": None, "n": n, "reason": "zero_vol"}

    sharpe = mu / sigma * math.sqrt(periods_per_year)
    sharpe_se = math.sqrt((1 + (sharpe ** 2) / 2) / n)
    z_alpha = 1.96 if confidence == 0.95 else (1.645 if confidence == 0.90 else 2.576)
    lower = sharpe - z_alpha * sharpe_se
    upper = sharpe + z_alpha * sharpe_se

    return {
        "sharpe": round(sharpe, 3),
        "sharpe_se": round(sharpe_se, 3),
        "lower_ci": round(lower, 3),
        "upper_ci": round(upper, 3),
        "n": n,
        "confidence": confidence,
    }


def sharpe_from_trades(trades_list, periods_per_year=252):
    """Helper: convierte lista de trades (con pnl_pct) en Sharpe-CI."""
    rets = [(t.get("pnl_pct", 0) or 0) / 100.0 for t in trades_list if t.get("pnl_pct") is not None]
    return sharpe_with_ci(rets, periods_per_year=periods_per_year)


# ========================================================================
# 1b. SORTINO RATIO CON CI — solo penaliza volatilidad a la baja
# ========================================================================

def sortino_with_ci(returns_list, confidence=0.95, periods_per_year=252, target=0.0):
    """Sortino ratio: como Sharpe pero el denominador es downside deviation
    (solo retornos por debajo de `target`), no la std completa.

    Mas relevante que Sharpe para esta cartera porque las estrategias tienen
    SL/TP asimetrico (ganancias y perdidas no son simetricas) — penalizar
    la volatilidad al alza junto con la de abajo (como hace Sharpe) castiga
    de mas a una estrategia que solo tiene "sorpresas" positivas.

    El CI usa la misma aproximacion delta-method que sharpe_with_ci (no hay
    forma cerrada estandar para Sortino) — tratarlo como guia, no como un
    intervalo exacto.
    """
    n = len(returns_list)
    if n < 5:
        return {"sortino": None, "lower_ci": None, "upper_ci": None, "n": n, "reason": "n<5"}

    mu = mean(returns_list)
    downside = [min(0.0, r - target) for r in returns_list]
    downside_var = sum(d * d for d in downside) / n
    downside_dev = math.sqrt(downside_var)
    if downside_dev == 0:
        return {"sortino": None, "lower_ci": None, "upper_ci": None, "n": n, "reason": "zero_downside_vol"}

    sortino = (mu - target) / downside_dev * math.sqrt(periods_per_year)
    sortino_se = math.sqrt((1 + (sortino ** 2) / 2) / n)
    z_alpha = 1.96 if confidence == 0.95 else (1.645 if confidence == 0.90 else 2.576)
    lower = sortino - z_alpha * sortino_se
    upper = sortino + z_alpha * sortino_se

    return {
        "sortino": round(sortino, 3),
        "sortino_se": round(sortino_se, 3),
        "lower_ci": round(lower, 3),
        "upper_ci": round(upper, 3),
        "n": n,
        "confidence": confidence,
    }


def sortino_from_trades(trades_list, periods_per_year=252):
    """Helper: convierte lista de trades (con pnl_pct) en Sortino-CI."""
    rets = [(t.get("pnl_pct", 0) or 0) / 100.0 for t in trades_list if t.get("pnl_pct") is not None]
    return sortino_with_ci(rets, periods_per_year=periods_per_year)


# ========================================================================
# 2. BAYESIAN EDGE PROBABILITY
# ========================================================================

def bayesian_edge(wins, losses, target_wr=0.50, prior_alpha=1.0, prior_beta=1.0):
    """Posterior Beta-Binomial: dado N trades, P(WR_real > target)?"""
    a = prior_alpha + wins
    b = prior_beta + losses
    n = wins + losses

    posterior_mean = a / (a + b)
    posterior_var = (a * b) / ((a + b) ** 2 * (a + b + 1))

    if n >= 15:
        sd = math.sqrt(posterior_var)
        z = (posterior_mean - target_wr) / sd if sd > 0 else 0
        prob_above = 1.0 - _normal_cdf(z * -1)
    else:
        prob_above = posterior_mean if posterior_mean > target_wr else 0.5 * posterior_mean

    sd = math.sqrt(posterior_var)
    ci_lower = max(0, posterior_mean - 1.96 * sd)
    ci_upper = min(1, posterior_mean + 1.96 * sd)

    return {
        "wins": wins, "losses": losses, "n": n,
        "posterior_mean": round(posterior_mean, 4),
        "posterior_var": round(posterior_var, 6),
        "credible_lower_95": round(ci_lower, 4),
        "credible_upper_95": round(ci_upper, 4),
        "prob_above_target": round(prob_above, 4),
        "target_wr": target_wr,
        "edge_confirmed": prob_above > 0.80,
    }


def _normal_cdf(x):
    """Aproximacion de la CDF normal estandar."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ========================================================================
# 3. DECAY DETECTOR
# ========================================================================

def decay_signal(live_returns, expected_sharpe, periods_per_year=252, threshold_pct=0.50):
    """Detecta degradacion comparando live Sharpe vs backtest Sharpe."""
    if len(live_returns) < 8:
        return {"status": "INSUFFICIENT_N", "n": len(live_returns), "min_n": 8}

    live = sharpe_with_ci(live_returns, periods_per_year=periods_per_year)
    if live.get("sharpe") is None:
        return {"status": "ERROR", "reason": live.get("reason")}

    live_sharpe = live["sharpe"]
    ratio = live_sharpe / max(expected_sharpe, 0.1) if expected_sharpe else 0

    if ratio >= 0.80: status = "HEALTHY"
    elif ratio >= threshold_pct: status = "WARNING"
    else: status = "DECAY"

    return {
        "live_sharpe": live_sharpe,
        "live_sharpe_lower_ci": live["lower_ci"],
        "expected_sharpe": expected_sharpe,
        "ratio": round(ratio, 3),
        "status": status,
        "n": len(live_returns),
    }


# ========================================================================
# 4. CORRELATION GATE
# ========================================================================

def position_correlation_gate(open_positions, new_signal, cluster_map=None, max_per_cluster=2):
    """Decide si abrir una nueva posicion segun saturacion de cluster correlacionado."""
    if cluster_map is None:
        cluster_map = {"BTC": 1, "ETH": 1, "LTC": 1, "SOL": 2, "BNB": 2}
    new_sym = new_signal.get("sym", "")
    new_dir = new_signal.get("type", "long")
    new_cluster = cluster_map.get(new_sym, 0)
    if not new_cluster:
        return {"allow": True, "reason": "no_cluster_info"}

    same_cluster_same_dir = 0
    for pos in open_positions:
        if pos.get("sym", "") in cluster_map and cluster_map[pos["sym"]] == new_cluster:
            if pos.get("direction") == new_dir:
                same_cluster_same_dir += 1

    if same_cluster_same_dir >= max_per_cluster:
        return {
            "allow": False,
            "reason": "cluster_" + str(new_cluster) + "_saturated_" + new_dir,
            "current_cluster_count": same_cluster_same_dir,
            "max": max_per_cluster,
        }
    return {
        "allow": True,
        "current_cluster_count": same_cluster_same_dir,
        "max": max_per_cluster,
    }


# ========================================================================
# 5. SELECTION BIAS / MULTIPLE-TESTING DIAGNOSTIC
# ========================================================================
# Agregado 2026-06-20 — el robustness gate (utils/robustness.py) ya filtra
# overfit IS/OOS por modelo individual, pero no corrige por el hecho de que
# cada champion es "el mejor de N intentos" de busqueda Optuna (N tipico
# 1000-7000 trials por slot). Con N grande, el maximo esperado por pura
# varianza de muestreo ya es alto aunque ninguna config tenga skill real
# diferenciado — hay que comparar el mejor valor contra ESE piso, no contra
# cero. Metodologia: extreme value approx de Lopez de Prado/Bailey (2014),
# aplicada aqui sobre la distribucion empirica de scores Optuna en vez de
# Sharpe puro (no tenemos retornos trade-a-trade por trial).

def expected_max_order_statistic(mu, sigma, n):
    """E[max de n variables iid ~ N(mu,sigma)] -- aproximacion extreme value.

    Formula de Bailey & Lopez de Prado (2014): el maximo esperado de n draws
    independientes de una normal crece con log(n), no con n -- pero crece, y
    hay que restarlo antes de creer que "el mejor de N" tiene skill real.
    """
    if n < 2 or sigma <= 0:
        return mu
    from scipy.stats import norm
    gamma = 0.5772156649015329  # constante de Euler-Mascheroni
    z1 = float(norm.ppf(1 - 1.0 / n))
    z2 = float(norm.ppf(1 - 1.0 / (n * math.e)))
    return mu + sigma * ((1 - gamma) * z1 + gamma * z2)


def selection_bias_test(trial_values, best_value=None, invalid_floor=-9000):
    """Dado el conjunto completo de scores Optuna de una busqueda (incluye
    trials descalificados con valor invalid_floor, se filtran), testea si el
    mejor resultado (el que se convirtio en champion) es un outlier genuino
    o esta dentro de lo que se esperaria por pura varianza de muestreo dado
    cuantas configuraciones se probaron.

    No es un test de "skill vs no-skill" clasico -- las configuraciones SI
    difieren en performance real. Es un diagnostico de cuanto del "mejor
    resultado" ya se explica solo por haber probado muchas configuraciones
    parecidas, vs. ser un outlier que sobresale incluso contra esa varianza.
    """
    vals = [v for v in trial_values if v is not None and v > invalid_floor]
    n = len(vals)
    if n < 10:
        return {"status": "INSUFFICIENT_TRIALS", "n": n}

    if best_value is None:
        best_value = max(vals)

    rest = [v for v in vals if v != best_value]
    if len(rest) < 5:
        rest = vals  # poblacion "resto" muy chica -> usa la poblacion completa

    mu = mean(rest)
    sigma = stdev(rest) if len(rest) > 1 else 0.0
    if sigma == 0:
        return {"status": "ZERO_VARIANCE", "n": n, "mu": round(mu, 4)}

    expected_max = expected_max_order_statistic(mu, sigma, n)
    z_above_expected = (best_value - expected_max) / sigma

    if z_above_expected < 0.0:
        verdict = "SELECTION_NOISE_LIKELY"
    elif z_above_expected < 0.5:
        verdict = "WEAK_SIGNAL"
    elif z_above_expected < 1.5:
        verdict = "MODERATE_SIGNAL"
    else:
        verdict = "GENUINE_STANDOUT"

    return {
        "status": "OK",
        "n_trials": n,
        "best_value": round(best_value, 4),
        "population_mean": round(mu, 4),
        "population_std": round(sigma, 4),
        "expected_max_by_luck": round(expected_max, 4),
        "z_above_expected_luck": round(z_above_expected, 3),
        "verdict": verdict,
    }


if __name__ == "__main__":
    print("=" * 60)
    print(" SIGMA QUANT TOOLKIT - Self-tests")
    print("=" * 60)

    print("\n[1] Sharpe-CI:")
    rets_vol = [0.05, -0.04, 0.06, -0.03, 0.04, -0.05, 0.05]
    rets_stab = [0.012, 0.010, 0.014, -0.003, 0.011, 0.013, 0.009, 0.011, 0.012, 0.010] * 5
    print("  Volatile (N=7):", sharpe_with_ci(rets_vol))
    print("  Stable (N=50):", sharpe_with_ci(rets_stab))

    print("\n[1b] Sortino-CI:")
    print("  Volatile (N=7):", sortino_with_ci(rets_vol))
    print("  Stable (N=50):", sortino_with_ci(rets_stab))

    print("\n[2] Bayesian Edge:")
    print("  5W 1L:", bayesian_edge(5, 1, target_wr=0.50))
    print("  20W 10L:", bayesian_edge(20, 10, target_wr=0.55))
    print("  3W 5L:", bayesian_edge(3, 5, target_wr=0.50))

    print("\n[3] Decay Signal:")
    healthy = [0.01, 0.012, 0.008, 0.015, -0.003, 0.011, 0.013, 0.010, 0.012, 0.009]
    decay = [-0.005, -0.008, 0.003, -0.012, 0.001, -0.007, -0.004, 0.002, -0.009, -0.005]
    print("  Healthy (exp Sharpe 2.0):", decay_signal(healthy, 2.0))
    print("  Decaying (exp Sharpe 2.0):", decay_signal(decay, 2.0))

    print("\n[4] Correlation Gate:")
    opens = [{"sym": "BTC", "direction": "long"}, {"sym": "ETH", "direction": "long"}]
    print("  LTC long w/ BTC+ETH long:", position_correlation_gate(opens, {"sym": "LTC", "type": "long"}))
    print("  SOL long w/ BTC+ETH long:", position_correlation_gate(opens, {"sym": "SOL", "type": "long"}))

    print("\n[5] Selection Bias Test:")
    import random
    random.seed(42)
    noise_only = [random.gauss(0.30, 0.05) for _ in range(2000)]
    print("  2000 trials, sin outlier real:", selection_bias_test(noise_only))
    real_edge = noise_only[:-1] + [0.30 + 0.05 * 4]  # un trial 4-sigma por encima
    print("  2000 trials, 1 outlier 4-sigma:", selection_bias_test(real_edge))


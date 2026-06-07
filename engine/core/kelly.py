"""
SIGMA ENGINE — Kelly Dinámico

Kelly fraction = (WR * RR - (1-WR)) / RR
Fractional Kelly = Kelly * 0.5  (standard para safety)

USO en backtest:
  from core.kelly import KellyDynamic
  kelly = KellyDynamic(window=20, fraction=0.5)
  risk = kelly.get_risk(cap, trade_history)

USO en autonomo:
  kelly.update(pnl, won)
  risk_pct = kelly.current_risk()
"""

import numpy as np
from collections import deque


class KellyDynamic:
    """
    Kelly dinámico con ventana rodante de los últimos N trades.
    - Fracción Kelly 50% (half-Kelly) para safety
    - Límites: min 0.3%, max 3.0% del capital
    - Si WR < 35% → reduce a mínimo automáticamente
    """

    def __init__(self, window=20, fraction=0.5, min_risk=0.3, max_risk=3.0,
                 default_risk=1.5, min_trades=10):
        self.window       = window
        self.fraction     = fraction
        self.min_risk     = min_risk
        self.max_risk     = max_risk
        self.default_risk = default_risk
        self.min_trades   = min_trades
        self.history      = deque(maxlen=window)  # cada elem = (pnl_pct, won)

    def update(self, pnl_pct: float, won: bool):
        """Registra un trade terminado."""
        self.history.append((pnl_pct, won))

    def calc_kelly(self):
        """Calcula Kelly fraction con los trades en la ventana."""
        if len(self.history) < self.min_trades:
            return self.default_risk

        wins   = [abs(p) for p, w in self.history if w]
        losses = [abs(p) for p, w in self.history if not w]

        if not wins or not losses:
            return self.default_risk

        wr  = len(wins) / len(self.history)
        rr  = np.mean(wins) / np.mean(losses) if losses else 2.0

        # Kelly formula
        kelly = (wr * rr - (1 - wr)) / rr if rr > 0 else 0

        # Fractional Kelly
        f_kelly = kelly * self.fraction

        # Convertir a % de capital (asumiendo RR=2 como base)
        risk_pct = f_kelly * 100

        return round(max(self.min_risk, min(self.max_risk, risk_pct)), 2)

    def current_risk(self):
        """Devuelve el riesgo actual en % de capital."""
        return self.calc_kelly()

    def summary(self):
        """Resumen del estado actual del Kelly."""
        if len(self.history) < self.min_trades:
            return {
                "trades": len(self.history),
                "status": f"Insuficientes datos ({len(self.history)}/{self.min_trades})",
                "risk_pct": self.default_risk
            }

        wins   = [abs(p) for p, w in self.history if w]
        losses = [abs(p) for p, w in self.history if not w]
        wr     = len(wins) / len(self.history)
        rr     = np.mean(wins) / np.mean(losses) if losses else 2.0
        kelly  = (wr * rr - (1 - wr)) / rr if rr > 0 else 0

        return {
            "trades":    len(self.history),
            "winrate":   round(wr * 100, 1),
            "avg_rr":    round(rr, 2),
            "kelly_raw": round(kelly * 100, 2),
            "kelly_half":round(kelly * self.fraction * 100, 2),
            "risk_pct":  self.calc_kelly(),
            "status":    "OK" if kelly > 0 else "EDGE NEGATIVO — no operar"
        }


def calc_kelly_static(winrate, avg_win_pct, avg_loss_pct, fraction=0.5):
    """
    Kelly estático para análisis rápido.

    Args:
        winrate:      WR como decimal (ej: 0.534)
        avg_win_pct:  promedio de ganancia en % del capital por trade ganador
        avg_loss_pct: promedio de pérdida en % del capital por trade perdedor
        fraction:     fracción de Kelly a usar (default 50%)

    Returns:
        dict con Kelly fraction y riesgo recomendado
    """
    if avg_loss_pct == 0: return {"error": "avg_loss_pct no puede ser 0"}

    rr    = avg_win_pct / avg_loss_pct
    kelly = (winrate * rr - (1 - winrate)) / rr
    half  = kelly * fraction
    risk  = max(0.1, min(5.0, half * 100))

    return {
        "winrate":      round(winrate * 100, 1),
        "avg_rr":       round(rr, 2),
        "kelly_full":   round(kelly * 100, 2),
        "kelly_half":   round(half * 100, 2),
        "risk_pct":     round(risk, 2),
        "edge_per_trade": round((winrate * avg_win_pct - (1-winrate) * avg_loss_pct), 4),
        "verdict":      "POSITIVO" if kelly > 0 else "NEGATIVO — no operar"
    }


def analyze_current_models():
    """Calcula Kelly para todos los modelos actuales."""
    import json
    from pathlib import Path

    models = {
        "1h":  {"wr": 0.534, "avg_win": 2.5,  "avg_loss": 1.8},
        "4h":  {"wr": 0.500, "avg_win": 2.0,  "avg_loss": 1.5},
        "15m": {"wr": 0.455, "avg_win": 3.5,  "avg_loss": 1.2},
    }

    print("\n" + "="*60)
    print("  KELLY ANALYSIS — Modelos actuales")
    print("="*60)
    print(f"  {'TF':<6} {'WR':>6} {'RR':>6} {'Kelly':>8} {'Half':>8} {'Risk%':>8} {'Edge':>8}")
    print("  " + "-"*55)

    for tf, m in models.items():
        k = calc_kelly_static(m["wr"], m["avg_win"], m["avg_loss"])
        print(f"  {tf:<6} {k['winrate']:>5.1f}% {k['avg_rr']:>6.2f} "
              f"{k['kelly_full']:>7.2f}% {k['kelly_half']:>7.2f}% "
              f"{k['risk_pct']:>7.2f}% {k['edge_per_trade']:>+7.4f}")

    print("\n  Notas:")
    print("  - Half-Kelly es el riesgo recomendado por trade")
    print("  - Si Kelly < 0: la estrategia pierde dinero en expectativa")
    print("  - Risk% = half-Kelly convertido a % del capital")
    print("="*60)


if __name__ == "__main__":
    analyze_current_models()

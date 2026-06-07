"""
SIGMA ENGINE — Core Risk Layer
Kelly dinamico, stop rules, position sizing, auto-bloqueo.
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"
with open(CONFIG_PATH) as f:
    CFG = json.load(f)


class RiskManager:
    """
    Gestiona el riesgo en tiempo real durante el trading.
    Tracker de estado: capital, DD, losses consecutivos, WR rolling.
    """

    def __init__(self, initial_capital=None):
        r = CFG["risk"]
        self.capital           = initial_capital or CFG["capital"]["initial"]
        self.initial_capital   = self.capital
        self.peak_capital      = self.capital

        self.max_dd_pct        = r["max_dd_global_pct"] / 100
        self.max_trades_day    = r["max_trades_per_day"]
        self.max_consec_losses = r["max_consec_losses"]
        self.stop_daily_usd    = r["stop_daily_usd"]
        self.stop_weekly_usd   = r["stop_weekly_usd"]
        self.stop_monthly_usd  = r["stop_monthly_usd"]
        self.kelly_fraction    = r["kelly_fraction"]
        self.kelly_after       = r["kelly_activate_after_trades"]

        self.trades_today      = 0
        self.consec_losses     = 0
        self.pnl_today         = 0.0
        self.pnl_week          = 0.0
        self.pnl_month         = 0.0
        self.total_trades      = 0
        self.all_trades        = []
        self.blocked           = False
        self.block_reason      = ""

    # ── Status ────────────────────────────────────────────────────────────────
    def can_trade(self):
        """True si el sistema puede abrir un nuevo trade."""
        if self.blocked:
            return False, self.block_reason
        if self.trades_today >= self.max_trades_day:
            return False, f"Max trades/dia alcanzado ({self.max_trades_day})"
        if self.consec_losses >= self.max_consec_losses:
            return False, f"Losses consecutivos: {self.consec_losses}"
        if self.pnl_today <= -self.stop_daily_usd:
            return False, f"Stop diario alcanzado (${self.pnl_today:.1f})"
        if self.pnl_week <= -self.stop_weekly_usd:
            return False, f"Stop semanal alcanzado (${self.pnl_week:.1f})"
        dd = self._current_dd_pct()
        if dd >= self.max_dd_pct:
            self.blocked = True
            self.block_reason = f"DD global {dd*100:.1f}% >= limite {self.max_dd_pct*100:.0f}%"
            return False, self.block_reason
        return True, "OK"

    def _current_dd_pct(self):
        if self.capital > self.peak_capital:
            self.peak_capital = self.capital
        return (self.peak_capital - self.capital) / self.peak_capital

    # ── Position Sizing ───────────────────────────────────────────────────────
    def position_size(self, entry_price, sl_price, quality="EXECUTE"):
        """
        Calcula el tamano de la posicion en unidades del activo.
        Usa Kelly si hay suficientes trades, sino riesgo fijo.
        """
        can, reason = self.can_trade()
        if not can:
            return 0, reason

        risk_usd = self._risk_usd(quality)
        sl_dist  = abs(entry_price - sl_price)
        if sl_dist <= 0:
            return 0, "SL invalido"

        size = risk_usd / sl_dist
        return size, "OK"

    def _risk_usd(self, quality="EXECUTE"):
        """USD en riesgo por trade, ajustado por calidad y estado."""
        base_pct = CFG["risk"]["risk_per_trade_pct"] / 100

        # Kelly si hay suficientes datos
        if self.total_trades >= self.kelly_after:
            kelly_pct = self._kelly_quarter()
            if kelly_pct > 0:
                base_pct = kelly_pct

        # DD Scalar: reduce sizing si hay perdidas
        scalar = self._dd_scalar()

        # Bonus por calidad elite
        quality_mult = {"ELITE_ICT": 1.0, "ELITE": 1.0,
                        "EXECUTE": 0.8, "DUAL_TREND": 0.9,
                        "DUAL_RANGE": 0.7, "WATCH": 0.5}.get(quality, 0.8)

        return self.capital * base_pct * scalar * quality_mult

    def _kelly_quarter(self):
        """Calcula Quarter Kelly desde historial real."""
        if len(self.all_trades) < self.kelly_after:
            return CFG["risk"]["risk_per_trade_pct"] / 100
        recent = self.all_trades[-self.kelly_after:]
        wins   = [t for t in recent if t["pnl"] > 0]
        losses = [t for t in recent if t["pnl"] <= 0]
        if not wins or not losses:
            return CFG["risk"]["risk_per_trade_pct"] / 100
        wr   = len(wins) / len(recent)
        b    = abs(np.mean([t["pnl"] for t in wins])) / abs(np.mean([t["pnl"] for t in losses]))
        f    = wr - (1 - wr) / b if b > 0 else 0
        f    = max(0, min(f * self.kelly_fraction, 0.05))  # cap 5%
        return f

    def _dd_scalar(self):
        """Reduce sizing segun losses consecutivos."""
        r = CFG["risk"]
        l1, l2, l3 = r["dd_losses_l1"], r["dd_losses_l2"], r["dd_losses_l3"]
        if self.consec_losses >= l3: return 0.25
        if self.consec_losses >= l2: return 0.50
        if self.consec_losses >= l1: return 0.75
        return 1.0

    # ── Registro de trades ────────────────────────────────────────────────────
    def register_trade(self, trade_dict):
        """Registra el resultado de un trade cerrado."""
        pnl = trade_dict.get("pnl", 0)
        self.capital    += pnl
        self.pnl_today  += pnl
        self.pnl_week   += pnl
        self.pnl_month  += pnl
        self.trades_today += 1
        self.total_trades += 1
        self.all_trades.append(trade_dict)

        if pnl > 0:
            self.consec_losses = 0
        else:
            self.consec_losses += 1

        if self.capital > self.peak_capital:
            self.peak_capital = self.capital

    def reset_daily(self):
        """Llamar al inicio de cada dia."""
        self.trades_today = 0
        self.pnl_today    = 0.0

    def reset_weekly(self):
        self.pnl_week = 0.0

    def reset_monthly(self):
        self.pnl_month = 0.0

    # ── Status report ─────────────────────────────────────────────────────────
    def status(self):
        can, reason = self.can_trade()
        dd_pct = self._current_dd_pct() * 100
        wr_rolling = self._rolling_wr(20)
        return {
            "capital":        round(self.capital, 2),
            "pnl_usd":        round(self.capital - self.initial_capital, 2),
            "pnl_pct":        round((self.capital - self.initial_capital) / self.initial_capital * 100, 2),
            "dd_pct":         round(dd_pct, 2),
            "total_trades":   self.total_trades,
            "trades_today":   self.trades_today,
            "consec_losses":  self.consec_losses,
            "can_trade":      can,
            "reason":         reason,
            "wr_rolling_20":  round(wr_rolling * 100, 1),
            "risk_scalar":    self._dd_scalar(),
            "phase":          self._current_phase(),
        }

    def _rolling_wr(self, n=20):
        recent = self.all_trades[-n:]
        if not recent: return 0
        return sum(1 for t in recent if t["pnl"] > 0) / len(recent)

    def _current_phase(self):
        t = self.total_trades
        if t < 30: return 0
        if t < 50: return 1
        if t < 100: return 2
        return 3

    def print_status(self):
        s = self.status()
        traffic = "🟢" if s["can_trade"] else "🔴"
        print(f"  {traffic} Capital: ${s['capital']:,.2f} | PnL: {s['pnl_pct']:+.1f}%")
        print(f"  DD actual: {s['dd_pct']:.1f}% | WR rolling 20T: {s['wr_rolling_20']:.1f}%")
        print(f"  Trades: {s['total_trades']} total | {s['trades_today']} hoy | {s['consec_losses']} losses consec.")
        print(f"  Fase: {s['phase']} | Scalar: {s['risk_scalar']:.2f}")
        if not s["can_trade"]:
            print(f"  ⛔ BLOQUEADO: {s['reason']}")


# ── Funciones standalone ──────────────────────────────────────────────────────
def kelly_criterion(win_rate, avg_win, avg_loss, fraction=0.25):
    """Calcula Kelly Criterion."""
    if avg_loss == 0: return 0
    b = avg_win / abs(avg_loss)
    f = win_rate - (1 - win_rate) / b
    return max(0, f * fraction)


def stop_rules_summary():
    """Imprime las stop rules configuradas."""
    r = CFG["risk"]
    print("  STOP RULES:")
    print(f"  Daily loss limit:  ${r['stop_daily_usd']:.2f}")
    print(f"  Weekly loss limit: ${r['stop_weekly_usd']:.2f}")
    print(f"  Monthly DD limit:  ${r['stop_monthly_usd']:.2f} ({r['max_dd_global_pct']:.0f}%)")
    print(f"  Max trades/dia:    {r['max_trades_per_day']}")
    print(f"  Max consec losses: {r['max_consec_losses']}")
    print(f"  Kelly activa en:   trade #{r['kelly_activate_after_trades']}")

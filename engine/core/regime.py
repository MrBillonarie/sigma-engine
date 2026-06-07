"""
SIGMA ENGINE — Regime Adaptive Parameters
El sistema cambia SL/TP/riesgo automaticamente segun el regimen de mercado.

Logica:
  TREND BULL/BEAR → SL amplio, TP lejano, mas riesgo (el movimiento es mas limpio)
  RANGE           → SL ajustado, TP cercano, menos riesgo (mas wicks, menos recorrido)
  VOLATILE        → SL muy amplio, TP conservador, minimo riesgo (mercado impredecible)
  TRANSITION      → parametros medios, precaucion
"""

import json
from pathlib import Path
import numpy as np

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"
with open(CONFIG_PATH) as f:
    CFG = json.load(f)


# ─── MULTIPLICADORES POR REGIMEN ──────────────────────────────────────────────
REGIME_PARAMS = {
    "TREND_BULL": {
        "sl_mult":      1.0,    # SL normal
        "tp_mult":      1.2,    # TP mas ambicioso (tendencia limpia)
        "risk_mult":    1.0,    # Riesgo normal
        "cooldown_mult":0.8,    # Cooldown menor (mas señales en tendencia)
        "min_adx":      25,     # Requiere ADX alto
        "description":  "Tendencia alcista — SL normal, TP ambicioso",
    },
    "TREND_BEAR": {
        "sl_mult":      1.0,
        "tp_mult":      1.2,
        "risk_mult":    1.0,
        "cooldown_mult":0.8,
        "min_adx":      25,
        "description":  "Tendencia bajista — SL normal, TP ambicioso",
    },
    "RANGE": {
        "sl_mult":      0.85,   # SL mas ajustado (menos wicks en rango)
        "tp_mult":      0.75,   # TP mas cercano (no esperar gran recorrido)
        "risk_mult":    0.75,   # Menos riesgo (edge menor en rango)
        "cooldown_mult":1.2,    # Cooldown mayor (evitar overtrading)
        "min_adx":      0,      # Cualquier ADX en rango
        "description":  "Mercado lateral — SL ajustado, TP conservador",
    },
    "VOLATILE": {
        "sl_mult":      1.4,    # SL muy amplio (wicks extremos)
        "tp_mult":      0.9,    # TP conservador (no dejar correr en volatilidad)
        "risk_mult":    0.5,    # Mitad de riesgo
        "cooldown_mult":1.5,    # Cooldown largo
        "min_adx":      0,
        "description":  "Alta volatilidad — SL amplio, riesgo reducido 50%",
    },
    "TRANSITION": {
        "sl_mult":      1.1,
        "tp_mult":      1.0,
        "risk_mult":    0.8,
        "cooldown_mult":1.1,
        "min_adx":      0,
        "description":  "Transicion — parametros conservadores",
    },
}


def detect_regime(df_row_or_series):
    """
    Detecta el regimen de mercado actual.
    Acepta una fila de DataFrame o dict con indicadores.
    Retorna: 'TREND_BULL' | 'TREND_BEAR' | 'RANGE' | 'VOLATILE' | 'TRANSITION'
    """
    if hasattr(df_row_or_series, "to_dict"):
        r = df_row_or_series.to_dict()
    else:
        r = dict(df_row_or_series)

    bull      = r.get("bull", False)
    bear      = r.get("bear", False)
    adx       = r.get("adx", 0)
    hurst     = r.get("hurst", 0.5)
    vol_pct   = r.get("vol_pct", 50)
    atr_ratio = r.get("atr_ratio", 1.0)
    close     = r.get("close", 0)
    ema50     = r.get("ema50", 0)
    ema200    = r.get("ema200", 0)

    # Volatile: ATR elevado o percentil alto
    if vol_pct > 75 or atr_ratio > 1.5:
        return "VOLATILE"

    # Trending: Hurst > 0.52 y ADX > 22
    is_trending = hurst > 0.52 and adx > 22

    if is_trending:
        if bull and close > ema50 and ema50 > ema200:
            return "TREND_BULL"
        if bear and close < ema50 and ema50 < ema200:
            return "TREND_BEAR"

    # Range: Hurst < 0.50 y ADX < 20
    if hurst < 0.50 and adx < 20 and vol_pct < 50:
        return "RANGE"

    return "TRANSITION"


def get_adaptive_params(base_params, regime):
    """
    Ajusta los parametros base segun el regimen detectado.
    Retorna un nuevo dict con los parametros modificados.
    """
    r_cfg = REGIME_PARAMS.get(regime, REGIME_PARAMS["TRANSITION"])
    params = dict(base_params)

    # Ajustar SL/TP
    for key in ["elite_sl_mult", "exec_sl_mult", "watch_sl_mult"]:
        if key in params:
            params[key] = round(params[key] * r_cfg["sl_mult"], 2)

    for key in ["elite_tp_mult", "exec_tp_mult", "watch_tp_mult"]:
        if key in params:
            params[key] = round(params[key] * r_cfg["tp_mult"], 2)

    # Ajustar riesgo
    if "risk_pct" in params:
        params["risk_pct"] = round(params["risk_pct"] * r_cfg["risk_mult"], 2)

    # Ajustar cooldown
    if "signal_cooldown" in params:
        params["signal_cooldown"] = max(2, int(params["signal_cooldown"] * r_cfg["cooldown_mult"]))

    # ADX minimo
    if r_cfg["min_adx"] > 0 and "adx_min" in params:
        params["adx_min"] = max(params["adx_min"], r_cfg["min_adx"])

    params["_regime"]      = regime
    params["_regime_desc"] = r_cfg["description"]
    return params


def regime_backtest_analysis(df, signals, quality, base_params):
    """
    Analiza el performance por regimen de mercado.
    Util para entender en que condiciones funciona mejor la estrategia.
    """
    import pandas as pd
    sys_path = str(Path(__file__).parent.parent)
    import sys; sys.path.insert(0, sys_path)
    from core.backtest import calc_metrics

    if "regime" not in df.columns:
        df = df.copy()
        df["regime"] = df.apply(lambda r: detect_regime(r), axis=1)

    results = {}
    for regime in ["TREND_BULL", "TREND_BEAR", "RANGE", "VOLATILE", "TRANSITION"]:
        mask     = df["regime"] == regime
        n_bars   = mask.sum()
        if n_bars < 50:
            continue

        df_r  = df[mask]
        sig_r = signals[mask]
        qual_r= quality[mask]

        # Reconstruir trades en este regimen
        n_trades = (sig_r != 0).sum()
        if n_trades < 3:
            results[regime] = {"n_bars": n_bars, "n_trades": 0}
            continue

        # Calcular metricas simples
        trade_pnls = []
        for i in range(len(sig_r)):
            if sig_r.iloc[i] != 0:
                # PnL aproximado (simplificado para analisis)
                entry = df_r["close"].iloc[i] if i < len(df_r) else 0
                if i+1 < len(df_r):
                    exit_ = df_r["close"].iloc[i+1]
                    pnl   = (exit_ - entry) / entry * sig_r.iloc[i] * 100
                    trade_pnls.append(pnl)

        if trade_pnls:
            wins = [p for p in trade_pnls if p > 0]
            wr   = len(wins)/len(trade_pnls)*100
            avg  = np.mean(trade_pnls)
            results[regime] = {
                "n_bars":   n_bars,
                "n_trades": len(trade_pnls),
                "winrate":  round(wr, 1),
                "avg_pnl":  round(avg, 2),
                "pct_time": round(n_bars/len(df)*100, 1),
            }

    return results


def print_regime_analysis(analysis):
    """Imprime el analisis de performance por regimen."""
    print(f"\n  {'Regimen':<14} {'% tiempo':>9} {'Trades':>7} {'WR%':>7} {'Avg PnL':>9} {'Accion'}")
    print(f"  {'-'*60}")
    for regime, r in analysis.items():
        if r.get("n_trades", 0) < 3:
            action = "Sin datos"
        elif r.get("winrate", 0) >= 55:
            action = "OPERAR"
        elif r.get("winrate", 0) >= 45:
            action = "REDUCIR"
        else:
            action = "EVITAR"

        print(f"  {regime:<14} {r.get('pct_time',0):>8.1f}% "
              f"{r.get('n_trades',0):>7} "
              f"{r.get('winrate',0):>6.1f}% "
              f"{r.get('avg_pnl',0):>9.2f} "
              f"{action}")


class AdaptiveStrategy:
    """
    Wrapper que aplica parametros adaptativos por regimen en tiempo real.
    Para usar en el motor de backtest o live.
    """
    def __init__(self, base_params):
        self.base_params    = base_params
        self.current_regime = "TRANSITION"
        self.current_params = base_params

    def update(self, df_row):
        """Actualiza el regimen y los parametros segun la barra actual."""
        new_regime = detect_regime(df_row)
        if new_regime != self.current_regime:
            self.current_regime = new_regime
            self.current_params = get_adaptive_params(self.base_params, new_regime)
        return self.current_params

    def get_sl_tp(self, quality="EXECUTE"):
        """Retorna SL/TP ajustados al regimen actual."""
        key_sl = f"{quality.lower().replace('_ict','')}_sl_mult"
        key_tp = f"{quality.lower().replace('_ict','')}_tp_mult"
        sl = self.current_params.get(key_sl,
             self.current_params.get("exec_sl_mult", 1.5))
        tp = self.current_params.get(key_tp,
             self.current_params.get("exec_tp_mult", 2.0))
        return sl, tp

    def get_risk_pct(self):
        return self.current_params.get("risk_pct",
               CFG["risk"]["risk_per_trade_pct"])

    def status(self):
        r = REGIME_PARAMS.get(self.current_regime, {})
        return {
            "regime":      self.current_regime,
            "description": r.get("description",""),
            "sl_mult":     r.get("sl_mult", 1.0),
            "tp_mult":     r.get("tp_mult", 1.0),
            "risk_mult":   r.get("risk_mult", 1.0),
        }

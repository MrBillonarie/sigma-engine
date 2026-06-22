"""SIGMA — Strategies registry (canonical single source of truth).
Centraliza las listas de estrategias para evitar duplicación en múltiples archivos.
Creado 2026-05-14 tras detectar SHORT_STRATEGIES hardcoded en 5+ lugares.
"""

# 26 SHORT REALES (3 originales + 23 agregadas 2026-05-14)
SHORT_STRATEGIES = frozenset([
    "breakdown", "pullback_short", "momentum_short",
    "rsi_overbought_short", "death_cross_short", "ema200_rejection_short",
    "macd_bear_cross", "lower_high_break_short", "lower_high_structure_short",
    "wedge_breakdown_short", "supply_zone_rejection", "bearish_rsi_divergence",
    "volume_climax_top", "range_break_down", "macd_zero_cross_down",
    "stoch_rsi_short", "williams_r_short", "cci_reversal_short",
    "engulfing_short", "three_candles_short", "inside_bar_short",
    "zscore_rich_short", "heikin_ashi_short", "roc_negative_short",
    "dmi_bear", "vwap_overpriced_short", "keltner_breakdown_short",
    # 2026-05-14 noche: +5 mirrors para balance 31L=31S
    "atr_channel_short", "supertrend_short", "bb_squeeze_short",
    "donchian_breakdown", "rsi_trend_short",
    # 2026-05-15: paridad 58L = 58S (32 nuevas inversiones naturales)
    "aroon_cross_bear", "bb_bandwidth_short", "break_of_structure_down", "chaikin_mf_short", "consecutive_wick_top", "elder_impulse_bear", "ema_ribbon_bear", "ema_triple_bear", "htf_divergence_bear", "ichimoku_bear", "linear_reg_break_down", "lower_lows_short", "macd_divergence_bear", "mean_rev_short", "mfi_overbought_short", "micro_momentum_short", "obv_divergence_bear", "open_close_cross_short", "pin_bar_short", "pivot_rejection", "psar_flip_down", "range_scalp_short", "session_open_short", "squeeze_pro_short", "tema_cross_down", "tick_follow_short", "tma_bands_short", "trend_strength_short", "volatility_breakdown", "volume_exhaustion_top", "vwap_rejection", "wma_momentum_short",
    # 2026-06-19: estrategias de Motor 2 (commodity_strategies.py) que nunca se agregaron
    # aqui -- causaba que champion_watcher.py etiquetara WTI/4h como "vwap_deviation_short|long"
    # (contradiccion nombre/direccion) porque el check era membership directo, no infer_direction().
    "turtle_breakout_short", "zscore_revert_short", "ny_session_breakout_short",
    "london_open_short", "macro_momentum_short", "keltner_trend_short",
    "energy_seasonal_short", "dxy_strength_short", "copper_cycle_short",
    "atr_compression_short", "risk_on_short", "volume_climax_short",
    "bollinger_squeeze_short", "vwap_deviation_short", "fibonacci_retracement_short",
    "supply_shock_short", "ng_seasonal_short", "cot_proxy_short",
])

NEW_2026_05_14 = frozenset([
    "rsi_overbought_short", "death_cross_short", "ema200_rejection_short",
    "macd_bear_cross", "lower_high_break_short", "lower_high_structure_short",
    "wedge_breakdown_short", "supply_zone_rejection", "bearish_rsi_divergence",
    "volume_climax_top", "range_break_down", "macd_zero_cross_down",
    "stoch_rsi_short", "williams_r_short", "cci_reversal_short",
    "engulfing_short", "three_candles_short", "inside_bar_short",
    "zscore_rich_short", "heikin_ashi_short", "roc_negative_short",
    "dmi_bear", "vwap_overpriced_short", "keltner_breakdown_short",
])


def infer_direction(strategy):
    """short si está en SHORT_STRATEGIES o keywords (short/breakdown/bear), sino long."""
    if not strategy: return "long"
    s = strategy.lower()
    if s in SHORT_STRATEGIES: return "short"
    if "short" in s or "breakdown" in s or "bear" in s: return "short"
    return "long"


def is_fresh_2026_05_14(strategy):
    """True si fue agregada en el sprint del 2026-05-14."""
    return strategy in NEW_2026_05_14


if __name__ == "__main__":
    print(f"SHORT_STRATEGIES: {len(SHORT_STRATEGIES)}")
    print(f"NEW_2026_05_14:   {len(NEW_2026_05_14)}")
    tests = [
        ("supply_zone_rejection", "short"),
        ("volume_climax_top", "short"),
        ("range_break_down", "short"),
        ("macd_zero_cross_down", "short"),
        ("breakout", "long"),
        ("momentum_short", "short"),
        ("regime_adaptive", "long"),
        ("consecutive_wick", "long"),  # IMPORTANTE: sistema la usa como LONG
    ]
    for strat, exp in tests:
        actual = infer_direction(strat)
        print(f"  [{('OK' if actual==exp else 'FAIL')}] {strat:25} -> {actual} (exp {exp})")

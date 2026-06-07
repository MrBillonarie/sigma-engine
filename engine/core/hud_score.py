"""
SIGMA ENGINE — HUD Score Replicator
Replica el sistema de scoring 0-18 del HUD v12.9.5-BTC en Python.
Este es el filtro que hace que el CAMPEON tenga 90% WR — es muy selectivo.

Las 18 condiciones del HUD:
  Setup (estructura):
    1. EMA50 > EMA200 (bull/bear)          → 1 pt
    2. Strong trend (trend_power > ATR)    → 1 pt
    3. HTF 1h aligned                      → 1 pt
    4. HTF 4h aligned                      → 1 pt
    5. Sin fake move                        → 1 pt
    6. Vol expanding                        → 1 pt

  Timing (momentum):
    7. MACD aligned                        → 1 pt
    8. RSI > 50 (long) / < 50 (short)     → 1 pt
    9. Volume > 1.5x media                 → 1 pt
    10. CVD bull/bear                      → 1 pt
    11. In session                         → 1 pt
    12. OFI accel                          → 1 pt

  Bonus (ICT confluence):
    13. TF4 aligned (daily)               → 1 pt
    14. In OB o FVG                       → 1 pt
    15. Above/below AVWAP                 → 1 pt
    16. OFI threshold                     → 1 pt
    17. Regime bull/bear                  → 1 pt
    18. Premium/Discount zone             → 1 pt

Score >= 10 = OPERABLE
Score >= 14 = ELITE+
Score 18 = MAXIMO
"""

import numpy as np
import pandas as pd


def compute_hud_score(df, direction='auto'):
    """
    Calcula el score HUD 0-18 para cada barra.
    direction: 'long', 'short', o 'auto' (usa bull/bear del df)

    Retorna: Series con el score para cada barra (0-18)
    """
    scores_long  = pd.Series(0, index=df.index)
    scores_short = pd.Series(0, index=df.index)

    c = df['close']

    # ── SETUP SCORE (6 puntos) ────────────────────────────────────────────────
    # 1. Tendencia principal
    scores_long  += df['bull'].astype(int)
    scores_short += df['bear'].astype(int)

    # 2. Strong trend
    strong = df['trend_power'] > df['atr'] * 0.5 if 'trend_power' in df.columns else pd.Series(False, index=df.index)
    scores_long  += strong.astype(int)
    scores_short += strong.astype(int)

    # 3. HTF1 aligned
    if 'htf1_long' in df.columns:
        scores_long  += df['htf1_long'].astype(int)
        scores_short += df['htf1_short'].astype(int) if 'htf1_short' in df.columns else 0

    # 4. HTF2 aligned
    if 'htf2_long' in df.columns:
        scores_long  += df['htf2_long'].astype(int)
        scores_short += (~df['htf2_long']).astype(int)

    # 5. Sin fake move
    if 'fake_move' in df.columns:
        scores_long  += (~df['fake_move']).astype(int)
        scores_short += (~df['fake_move']).astype(int)

    # 6. Volumen expandiendo
    if 'vol_expand' in df.columns:
        scores_long  += df['vol_expand'].astype(int)
        scores_short += df['vol_expand'].astype(int)

    # ── TIMING SCORE (6 puntos) ───────────────────────────────────────────────
    # 7. MACD aligned
    if 'macd' in df.columns and 'macd_signal' in df.columns:
        scores_long  += (df['macd'] > df['macd_signal']).astype(int)
        scores_short += (df['macd'] < df['macd_signal']).astype(int)
    elif 'macd_line' in df.columns and 'signal_line' in df.columns:
        scores_long  += (df['macd_line'] > df['signal_line']).astype(int)
        scores_short += (df['macd_line'] < df['signal_line']).astype(int)

    # 8. RSI
    if 'rsi' in df.columns:
        scores_long  += (df['rsi'] > 50).astype(int)
        scores_short += (df['rsi'] < 50).astype(int)

    # 9. Volume OK
    if 'vol_ok' in df.columns:
        scores_long  += df['vol_ok'].astype(int)
        scores_short += df['vol_ok'].astype(int)

    # 10. Taker bull (real) o CVD (derivado como fallback)
    if 'taker_bull' in df.columns:
        scores_long  += df['taker_bull'].astype(int)
        scores_short += df['taker_bear'].astype(int) if 'taker_bear' in df.columns else (~df['taker_bull']).astype(int)
    elif 'cvd_bull' in df.columns:
        scores_long  += df['cvd_bull'].astype(int)
        scores_short += (~df['cvd_bull']).astype(int)

    # 11. In session
    if 'in_session' in df.columns:
        scores_long  += df['in_session'].astype(int)
        scores_short += df['in_session'].astype(int)
    elif 'in_london' in df.columns:
        in_sess = df['in_london'] | df.get('in_new_york', pd.Series(False, index=df.index))
        scores_long  += in_sess.astype(int)
        scores_short += in_sess.astype(int)

    # 12. Taker acceleration (real) o OFI accel (derivado como fallback)
    if 'taker_accel' in df.columns:
        scores_long  += (df['taker_accel'] > 0).astype(int)
        scores_short += (df['taker_accel'] < 0).astype(int)
    elif 'ofi_accel' in df.columns:
        scores_long  += (df['ofi_accel'] > 0).astype(int)
        scores_short += (df['ofi_accel'] < 0).astype(int)

    # ── BONUS ICT (6 puntos) ──────────────────────────────────────────────────
    # 13. TF3/TF4 bull (todos los HTFs alineados)
    if 'tf3_bull' in df.columns:
        scores_long  += df['tf3_bull'].astype(int)
        scores_short += df['tf3_bear'].astype(int) if 'tf3_bear' in df.columns else 0

    # 14. In OB o FVG
    in_ict_long = pd.Series(False, index=df.index)
    in_ict_short= pd.Series(False, index=df.index)
    for col in ['in_bull_ob', 'fill_bull_fvg']:
        if col in df.columns:
            in_ict_long = in_ict_long | df[col]
    for col in ['in_bear_ob', 'fill_bear_fvg']:
        if col in df.columns:
            in_ict_short = in_ict_short | df[col]
    scores_long  += in_ict_long.astype(int)
    scores_short += in_ict_short.astype(int)

    # 15. AVWAP alignment
    if 'above_avwap' in df.columns:
        scores_long  += df['above_avwap'].astype(int)
        scores_short += (~df['above_avwap']).astype(int)

    # 16. OFI threshold
    if 'ofi_bull' in df.columns:
        scores_long  += df['ofi_bull'].astype(int)
        scores_short += df['ofi_bear'].astype(int) if 'ofi_bear' in df.columns else 0

    # 17. Regime
    if 'regime_bull' in df.columns:
        scores_long  += df['regime_bull'].astype(int)
        scores_short += df['regime_bear'].astype(int) if 'regime_bear' in df.columns else 0

    # 18. Premium/Discount
    if 'in_discount' in df.columns:
        scores_long  += df['in_discount'].astype(int)
    if 'in_premium' in df.columns:
        scores_short += df['in_premium'].astype(int)

    # ── BONUS FUTUROS (hasta +2 puntos extra) ────────────────────────────────
    # 19. VPOC alignment (precio sobre/bajo el nivel de mayor volumen historico)
    if 'above_vpoc' in df.columns:
        scores_long  += df['above_vpoc'].astype(int)
        scores_short += (~df['above_vpoc']).astype(int)

    # 20. Funding neutral (mercado sin sesgo extremo = mejor risk/reward)
    if 'funding_neutral' in df.columns:
        scores_long  += df['funding_neutral'].astype(int)
        scores_short += df['funding_neutral'].astype(int)

    # 21. OI confirma direccion (nueva posicion abierta en nuestra direccion)
    if 'oi_confirm_bull' in df.columns:
        scores_long  += df['oi_confirm_bull'].astype(int)
        scores_short += df['oi_confirm_bear'].astype(int) if 'oi_confirm_bear' in df.columns else 0

    df['hud_score_long']  = scores_long.clip(0, 21)
    df['hud_score_short'] = scores_short.clip(0, 21)

    # Score activo segun direccion del mercado
    if direction == 'long':
        df['hud_score'] = df['hud_score_long']
    elif direction == 'short':
        df['hud_score'] = df['hud_score_short']
    else:
        # Auto: usa el score de la direccion del mercado actual
        df['hud_score'] = np.where(df['bull'], df['hud_score_long'], df['hud_score_short'])

    return df


def get_hud_signals(df, min_score=10, elite_score=14):
    """
    Genera señales SOLO cuando el score HUD supera el umbral.
    Esto replica exactamente el filtro del CAMPEON.

    min_score=10:  minimo para operar (configurable)
    elite_score=14: ELITE+ (setups de maxima calidad)
    """
    df = compute_hud_score(df)

    # Señales base (smart_long/short deben existir en df)
    if 'smart_long' not in df.columns or 'smart_short' not in df.columns:
        raise ValueError("Necesitas compute features primero (build_features)")

    # Filtrar por score
    long_operable  = df['smart_long']  & (df['hud_score_long']  >= min_score)
    short_operable = df['smart_short'] & (df['hud_score_short'] >= min_score)

    long_elite  = df['smart_long']  & (df['hud_score_long']  >= elite_score)
    short_elite = df['smart_short'] & (df['hud_score_short'] >= elite_score)

    # ICT confluence boost
    long_ict  = long_elite  & (df.get('in_bull_ob', False) | df.get('fill_bull_fvg', False) | df.get('above_avwap', False))
    short_ict = short_elite & (df.get('in_bear_ob', False) | df.get('fill_bear_fvg', False) | ~df.get('above_avwap', pd.Series(True, index=df.index)))

    df['hud_sig_long']  = long_operable
    df['hud_sig_short'] = short_operable
    df['hud_elite_long']  = long_elite
    df['hud_elite_short'] = short_elite
    df['hud_ict_long']  = long_ict
    df['hud_ict_short'] = short_ict

    # Score stats
    operable_l = long_operable.sum()
    operable_s = short_operable.sum()
    elite_l    = long_elite.sum()
    elite_s    = short_elite.sum()

    return df, {
        'operable_long':  int(operable_l),
        'operable_short': int(operable_s),
        'elite_long':     int(elite_l),
        'elite_short':    int(elite_s),
        'avg_score':      round(df['hud_score'].mean(), 1),
        'pct_above_min':  round((df['hud_score'] >= min_score).mean() * 100, 1),
        'pct_elite':      round((df['hud_score'] >= elite_score).mean() * 100, 1),
    }


def hud_score_analysis(df):
    """Analisis de la distribucion de scores."""
    df = compute_hud_score(df)

    print(f"\n  HUD SCORE DISTRIBUTION (0-18)")
    print(f"  {'Score':<8} {'Long':>6} {'Short':>6} {'% barras':>10}")
    print(f"  {'-'*35}")
    for score in [18, 17, 16, 15, 14, 13, 12, 11, 10, 9, 8, 7]:
        n_l = (df['hud_score_long']  == score).sum()
        n_s = (df['hud_score_short'] == score).sum()
        pct = ((df['hud_score_long'] == score) | (df['hud_score_short'] == score)).mean() * 100
        tag = " ← ELITE+" if score >= 14 else " ← OPERABLE" if score >= 10 else ""
        print(f"  {score:<8} {n_l:>6} {n_s:>6} {pct:>9.2f}%{tag}")

    print(f"\n  Score promedio: {df['hud_score'].mean():.1f}")
    print(f"  >= 10 (operable): {(df['hud_score'] >= 10).mean()*100:.1f}% de barras")
    print(f"  >= 14 (elite+):   {(df['hud_score'] >= 14).mean()*100:.1f}% de barras")

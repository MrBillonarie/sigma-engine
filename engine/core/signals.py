"""
SIGMA ENGINE — Core Signals Layer
Genera señales de entrada con calidad asignada.
Soporta: ELITE_ICT / ELITE / EXECUTE / WATCH / DUAL_TREND / DUAL_RANGE
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"
with open(CONFIG_PATH) as f:
    CFG = json.load(f)

QUALITY_ORDER = ["ELITE_ICT", "ELITE", "EXECUTE", "DUAL_TREND", "DUAL_RANGE", "WATCH"]


def get_signals(df, params=None):
    """
    Genera señales de entrada con calidad asignada.

    params: dict con overrides de configuracion. Si None, usa settings.json.
    Retorna: (signals: Series[int], quality: Series[str])
      signals: 1=long, -1=short, 0=flat
      quality: 'ELITE_ICT', 'ELITE', 'EXECUTE', 'DUAL_TREND', 'DUAL_RANGE', 'WATCH', 'NONE'
    """
    p = _merge_params(params)

    # ── Filtros base ──────────────────────────────────────────────────────────
    hr  = df["hour"]
    dow = df["dow"]

    sess = CFG["sessions_utc"]
    in_london   = (hr >= sess["london"]["start"])   & (hr < sess["london"]["end"])
    in_new_york = (hr >= sess["new_york"]["start"]) & (hr < sess["new_york"]["end"])
    in_asia     = (hr >= sess["asia"]["start"])     & (hr < sess["asia"]["end"])

    use_sess_b = p.get("use_sess_b", True)
    use_asia   = p.get("use_asia", True)
    in_sess    = in_london | (in_new_york if use_sess_b else pd.Series(False, index=df.index)) | \
                 (in_asia if use_asia else pd.Series(False, index=df.index))

    allowed_days = [1, 2, 3]  # Mar, Mie, Jue siempre
    if p.get("allow_friday", True):  allowed_days.append(4)
    if p.get("allow_monday", False): allowed_days.append(0)
    day_ok = dow.isin(allowed_days)

    temp_ok = (df["mkt_temp"] >= p.get("temp_min", 10)) & \
              (df["mkt_temp"] <= p.get("temp_max", 90))

    base = (~df["fake_move"] & ~df["is_spike"] & day_ok &
            df["gap_ok"] & temp_ok)

    # HTF requirements
    req_htf2  = p.get("req_htf2", True)
    htf_l = df["htf1_long"]  & (df["htf2_long"] if req_htf2 else pd.Series(True, index=df.index))
    htf_s = df["htf1_short"] & (~df["htf2_long"] if req_htf2 else pd.Series(True, index=df.index))

    # ── Filtros de futuros (activos solo si las columnas existen) ─────────────
    # Funding: evitar longs cuando longs muy sobreextendidos y viceversa
    if p.get("use_funding_filter", True):
        htf_l = htf_l & ~df.get("funding_extreme_long",  pd.Series(False, index=df.index))
        htf_s = htf_s & ~df.get("funding_extreme_short", pd.Series(False, index=df.index))

    # Funding Rate z-score extremo (del historico descargado)
    if "fr_ext_l" in df.columns and p.get("use_funding_filter", True):
        htf_l = htf_l & ~df["fr_ext_l"].astype(bool)   # evitar long cuando todos estan long
        htf_s = htf_s & ~df["fr_ext_s"].astype(bool)

    # OI divergence: precio sube pero OI baja = short squeeze sin conviccion
    if p.get("use_oi_filter", True):
        htf_l = htf_l & ~df.get("oi_div_bearish", pd.Series(False, index=df.index))
        htf_s = htf_s & ~df.get("oi_div_bullish", pd.Series(False, index=df.index))

    # ── Filtro Macro: Fear & Greed + SPY risk-on ─────────────────────────────
    # Fear & Greed extremo: cuando hay panico extremo, solo LONG
    # Cuando hay codicia extrema, solo SHORT o flat
    if p.get("use_macro_filter", False):
        if "fg_extreme_greed" in df.columns:
            htf_s = htf_s | df["fg_extreme_fear"].astype(bool)   # panico → señal short bloqueada
            htf_l = htf_l | df["fg_extreme_greed"].astype(bool)  # codicia → señal long bloqueada
        # SPY risk-off: reducir longs
        if "spy_risk_on" in df.columns and p.get("use_spy_filter", False):
            htf_l = htf_l & df["spy_risk_on"].astype(bool)
        # VIX muy alto: mercado en pánico, evitar trades
        if "vix_high" in df.columns and p.get("use_vix_filter", False):
            vix_ok = ~df["vix_high"].astype(bool)
            htf_l  = htf_l & vix_ok
            htf_s  = htf_s & vix_ok

    # ── Señales por calidad ───────────────────────────────────────────────────
    sig_l = pd.Series(False, index=df.index)
    sig_s = pd.Series(False, index=df.index)

    # Mascaras por categoria (para asignacion de calidad)
    eit_l = eit_s = pd.Series(False, index=df.index)
    elt_l = elt_s = pd.Series(False, index=df.index)
    exc_l = exc_s = pd.Series(False, index=df.index)
    tl    = ts    = pd.Series(False, index=df.index)
    rl    = rs    = pd.Series(False, index=df.index)
    wat_l = wat_s = pd.Series(False, index=df.index)

    # ELITE_ICT
    if p.get("use_elite_ict", True):
        eit_l = df["eit_long"]  & base & htf_l
        eit_s = df["eit_short"] & base & htf_s
        sig_l = sig_l | eit_l
        sig_s = sig_s | eit_s

    # ELITE
    if p.get("use_elite", True):
        elt_l = df["elite_long"]  & ~df["eit_long"]  & base & htf_l
        elt_s = df["elite_short"] & ~df["eit_short"] & base & htf_s
        sig_l = sig_l | elt_l
        sig_s = sig_s | elt_s

    # EXECUTE
    if p.get("use_execute", True):
        adx_min = p.get("adx_min", 18)
        exc_l = (df["smart_long"]  & ~df["elite_long"]  &
                 (df["adx"] > adx_min) & base & htf_l)
        exc_s = (df["smart_short"] & ~df["elite_short"] &
                 (df["adx"] > adx_min) & base & htf_s)
        sig_l = sig_l | exc_l
        sig_s = sig_s | exc_s

    # DUAL_TREND
    if p.get("use_trend", True):
        hurst_t = p.get("hurst_t", 0.55)
        adx_t   = p.get("adx_t",   25)
        is_tu   = (df["hurst"] > hurst_t) & (df["adx"] > adx_t) & df["bull"]
        is_td   = (df["hurst"] > hurst_t) & (df["adx"] > adx_t) & df["bear"]
        tl = (is_tu & (df["low"] <= df["ema20"] * 1.005) &
              (df["close"] > df["ema20"]) & (df["close"] > df["open"]) &
              (df["macd"] > df["macd_signal"]) & ~df["fake_move"] & in_sess)
        ts = (is_td & (df["high"] >= df["ema20"] * 0.995) &
              (df["close"] < df["ema20"]) & (df["close"] < df["open"]) &
              (df["macd"] < df["macd_signal"]) & ~df["fake_move"] & in_sess)
        if req_htf2:
            tl = tl & htf_l; ts = ts & htf_s
        tl = tl & base; ts = ts & base
        sig_l = sig_l | tl; sig_s = sig_s | ts

    # DUAL_RANGE
    if p.get("use_range", True):
        is_wr = (df["hurst"] < p.get("hurst_r", 0.50)) & (df["adx"] < p.get("adx_r", 20))
        rl = (is_wr & (df["low"] <= df["bb_lower"]) &
              (df["close"] > df["bb_lower"]) & (df["rsi"] < 30) &
              df["bull_div"] & ~df["fake_move"] & in_sess)
        rs = (is_wr & (df["high"] >= df["bb_upper"]) &
              (df["close"] < df["bb_upper"]) & (df["rsi"] > 70) &
              df["bear_div"] & ~df["fake_move"] & in_sess)
        rl = rl & base; rs = rs & base
        sig_l = sig_l | rl; sig_s = sig_s | rs

    # WATCH
    if p.get("use_watch", False):
        wat_l = df["smart_long"]  & ~df["elite_long"]  & base
        wat_s = df["smart_short"] & ~df["elite_short"] & base
        sig_l = sig_l | wat_l; sig_s = sig_s | wat_s

    # Aplicar filtros comunes finales
    sig_l = sig_l & base
    sig_s = sig_s & base

    # ── Asignar calidad por prioridad (orden inverso: mas alto sobreescribe) ──
    quality = pd.Series("NONE", index=df.index)
    if p.get("use_watch", False):
        quality[wat_l | wat_s] = "WATCH"
    if p.get("use_range", True):
        quality[rl | rs] = "DUAL_RANGE"
    if p.get("use_trend", True):
        quality[tl | ts] = "DUAL_TREND"
    if p.get("use_execute", True):
        quality[exc_l | exc_s] = "EXECUTE"
    if p.get("use_elite", True):
        quality[elt_l | elt_s] = "ELITE"
    if p.get("use_elite_ict", True):
        quality[eit_l | eit_s] = "ELITE_ICT"

    # ── Cooldown ──────────────────────────────────────────────────────────────
    cd = p.get("signal_cooldown", CFG["signals"]["signal_cooldown_bars"])
    final   = pd.Series(0,      index=df.index)
    qual_f  = pd.Series("NONE", index=df.index)
    last_i  = -cd - 1

    for i in range(len(df)):
        if (i - last_i) < cd:
            continue
        if sig_l.iloc[i]:
            final.iloc[i]  = 1
            qual_f.iloc[i] = quality.iloc[i] if quality.iloc[i] != "NONE" else "ELITE"
            last_i = i
        elif sig_s.iloc[i]:
            final.iloc[i]  = -1
            qual_f.iloc[i] = quality.iloc[i] if quality.iloc[i] != "NONE" else "ELITE"
            last_i = i

    return final, qual_f


def signals_summary(signals, quality):
    """Resumen de señales generadas."""
    longs  = (signals == 1).sum()
    shorts = (signals == -1).sum()
    total  = longs + shorts
    print(f"  Señales: {total} total ({longs}L / {shorts}S)")
    if total > 0:
        for q in QUALITY_ORDER:
            n = (quality == q).sum()
            if n > 0:
                print(f"    {q}: {n} ({n/total*100:.1f}%)")


def _merge_params(params=None):
    """Merge de params con defaults de config."""
    defaults = {
        "use_elite_ict":  True,
        "use_elite":      True,
        "use_execute":    True,
        "use_trend":      True,
        "use_range":      True,
        "use_watch":      False,
        "use_sess_b":     True,
        "use_asia":       True,
        "allow_friday":   True,
        "allow_monday":   False,
        "req_htf2":       True,
        "adx_min":        18,
        "hurst_t":        0.55,
        "adx_t":          25,
        "hurst_r":        0.50,
        "adx_r":          20,
        "temp_min":       10,
        "temp_max":       90,
        "signal_cooldown":    CFG["signals"]["signal_cooldown_bars"],
        "use_funding_filter": True,
        "use_oi_filter":      True,
    }
    if params:
        defaults.update(params)
    return defaults

#!/usr/bin/env python3
"""
SIGMA ENGINE — Rule-Based Entry Filter v1.0
Analiza cada senal antes de abrir trade usando reglas deterministicas.
Sin API externa — costo cero, latencia cero.

Sistema de puntaje 0-100:
  >= 65 → ENTRAR
  45-64 → ESPERAR  (señal dudosa)
  <  45 → NO_ENTRAR
"""
import json, time
from pathlib import Path
from datetime import datetime, timezone, timedelta

CHILE     = timezone(timedelta(hours=-4))
MIN_CONF  = 65
CACHE_TTL = 300   # 5 min cache por señal

BASE     = Path('/opt/sigma')
LOG_PATH = BASE / 'results' / 'reports' / 'ai_filter.log'

_cache = {}

def _log(msg):
    try:
        cl   = datetime.now(CHILE)
        line = f"[{cl:%H:%M:%S}] {msg}"
        print(line, flush=True)
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except:
        pass

def _cache_key(signal):
    return f"{signal.get('sym')}_{signal.get('tf')}_{signal.get('type','long')}"

def _from_cache(signal):
    key   = _cache_key(signal)
    entry = _cache.get(key)
    if entry and (time.time() - entry['ts']) < CACHE_TTL:
        return entry['result']
    return None

def _to_cache(signal, result):
    _cache[_cache_key(signal)] = {'result': result, 'ts': time.time()}

# ── Clusters de correlacion (mismo cluster = alta correlacion) ────────────────
CORR_CLUSTERS = [
    {'BTC', 'ETH'},
    {'SOL', 'BNB', 'LTC'},
]

def _same_cluster(sym_a, sym_b):
    for cl in CORR_CLUSTERS:
        if sym_a in cl and sym_b in cl:
            return True
    return False

def analyze(signal: dict, regime: str, open_trades: list) -> dict:
    """
    Analiza la senal con reglas deterministicas.
    Retorna: {ok, confidence, action, reason}
    """
    cached = _from_cache(signal)
    if cached:
        return cached

    sym   = signal.get('sym', '?')
    tf    = signal.get('tf', '?')
    direc = signal.get('type', 'long')
    grade = signal.get('grade', 'D')
    wr    = float(signal.get('wr', 0) or 0)
    cagr  = float(signal.get('cagr', 0) or 0)
    rr    = float(signal.get('rr_ratio', 0) or 0)
    ev    = float(signal.get('ev', 0) or 0)
    kelly = float(signal.get('eff_risk_pct', 3.3) or 3.3)
    conf  = signal.get('val_confidence', '')
    ens   = int(signal.get('ensemble_count', 1) or 1)
    htf   = signal.get('htf_confirms', True)
    strat = signal.get('strategy', '?')
    wft   = signal.get('wft_pass_rate')
    decay = bool(signal.get('decay_warning', False))
    dd_k  = float(signal.get('dd_kelly_mult', 1.0) or 1.0)
    rec   = signal.get('recommendation', '')

    score  = 50
    blocks = []   # razones de bloqueo duro
    warns  = []   # advertencias (restan puntos)

    # ── BLOQUEOS DUROS (rechazo inmediato) ───────────────────────────────────

    # Grade D siempre fuera
    if grade == 'D':
        blocks.append("Grade D sin edge")

    # CAGR insuficiente
    if cagr > 0 and cagr < 12:
        blocks.append(f"CAGR {cagr:.1f}% < minimo 12%")

    # WR muy bajo sin RR que compense
    if wr > 0 and wr < 42:
        blocks.append(f"WR {wr:.0f}% demasiado bajo")
    elif wr > 0 and wr < 50 and rr < 2.5:
        blocks.append(f"WR {wr:.0f}% < 50% requiere RR >= 2.5 (actual {rr:.1f}:1)")

    # EV negativo
    if ev < -1.0:
        blocks.append(f"EV neto negativo ({ev:+.1f}%)")

    # Regimen fuertemente adverso
    if regime == 'BEAR' and direc == 'long' and ens < 2:
        blocks.append(f"BEAR + LONG sin ensemble minimo (E{ens})")
    if regime == 'BULL' and direc == 'short' and ens < 2:
        blocks.append(f"BULL + SHORT sin ensemble minimo (E{ens})")

    # Decay activo con DD alto
    if decay and dd_k <= 0.7:
        blocks.append(f"Decay activo + DD Kelly reducido a {dd_k:.0%}")

    # Correlacion: misma direccion + mismo cluster
    for ot in open_trades:
        if (ot.get('status') == 'open'
                and ot.get('direction') == direc
                and _same_cluster(sym, ot.get('sym', ''))):
            blocks.append(
                f"Correlacion alta con {ot.get('sym')} {ot.get('tf')} {direc}"
            )
            break

    # Si hay bloqueo duro → rechazar
    if blocks:
        reason = blocks[0]
        result = {'ok': False, 'confidence': 20, 'action': 'NO_ENTRAR', 'reason': reason}
        _log(f"BLOQUEADO {sym} {tf} {direc.upper()} — {reason}")
        _to_cache(signal, result)
        return result

    # ── SCORING (suma/resta puntos) ───────────────────────────────────────────

    # WR
    if wr >= 75:   score += 15
    elif wr >= 65: score += 10
    elif wr >= 55: score += 5
    elif wr < 50:  score -= 10

    # CAGR
    if cagr >= 35:   score += 15
    elif cagr >= 20: score += 10
    elif cagr >= 12: score += 5

    # RR
    if rr >= 3.0:   score += 10
    elif rr >= 2.0: score += 6
    elif rr >= 1.5: score += 3
    elif rr < 1.0:  score -= 10
    warns.append(f"RR {rr:.1f}:1") if rr < 1.0 else None

    # EV
    if ev >= 2.0:   score += 8
    elif ev >= 0.5: score += 4
    elif ev < 0:    score -= 5

    # Ensemble
    if ens >= 3:   score += 12
    elif ens == 2: score += 6

    # HTF
    if htf:         score += 8
    else:           score -= 8; warns.append("HTF no confirma")

    # Grade
    if grade == 'A+': score += 10
    elif grade == 'A': score += 6
    elif grade == 'B': score += 2
    elif grade == 'C': score -= 8

    # Confianza validacion
    if conf == 'ALTA':   score += 8
    elif conf == 'MEDIA': score += 3
    elif conf == 'BAJA':  score -= 5

    # WFT
    if wft is not None:
        if wft >= 60:   score += 8
        elif wft >= 50: score += 4
        elif wft < 40:  score -= 8; warns.append(f"WFT {wft:.0f}%")

    # Decay
    if decay: score -= 6; warns.append("decay activo")

    # DD kelly
    if dd_k < 0.8: score -= 5; warns.append(f"DD kelly x{dd_k:.2f}")

    # Regimen alineado (bonus)
    if regime == 'BULL' and direc == 'long':  score += 5
    if regime == 'BEAR' and direc == 'short': score += 5

    # Clamp 0-100
    score = max(0, min(100, score))

    if score >= MIN_CONF:
        action = 'ENTRAR'
        ok     = True
    elif score >= 45:
        action = 'ESPERAR'
        ok     = False
        warns.append(f"score borderline {score}/100")
    else:
        action = 'NO_ENTRAR'
        ok     = False

    reason = (
        f"Grade {grade} WR {wr:.0f}% RR {rr:.1f}:1 EV {ev:+.1f}% E{ens}"
        + (f" | {', '.join(warns)}" if warns else "")
    )

    result = {'ok': ok, 'confidence': score, 'action': action, 'reason': reason}
    _log(f"{sym} {tf} {direc.upper()} → {action} conf={score} — {reason}")
    _to_cache(signal, result)
    return result

def should_enter(signal: dict, regime: str, open_trades: list) -> tuple:
    """Retorna (bool, reason_str)."""
    result = analyze(signal, regime, open_trades)
    return result.get('ok', True), result.get('reason', '')

#!/usr/bin/env python3
"""SIGMA — Guard de pre-compromisos (PROTOCOLO_PRECOMPROMISO.md hecho código).

Un documento exige que alguien se acuerde de él bajo presión; este guard lo hace
mecánico. Corre cada 6h via cron (+ resumen semanal lunes con --weekly):

1. CAMBIO DE RÉGIMEN (§1 del protocolo): si global_regime_m1 gira, activa
   restricción (Kelly ×0.5, máx 2 slots LIVE) que live_executor APLICA leyendo
   precommitment_state.json. Checkpoint a los 10 trades cerrados en el régimen
   nuevo: WR≥45% y PF≥1.0 → fase de relajación (×0.75, 14 días); si no, alerta
   para pasar a paper los modelos sin evidencia (esa parte es manual, por diseño).
2. KILL CRITERIA (§3): calcula la distancia a cada umbral (PF<1.0 con n≥60 en
   ≥2 regímenes; equity < piso $385) y alerta cuando algo se acerca. La decisión
   de apagar sigue siendo humana; la vigilancia deja de serlo.
3. DEMOCIÓN DE CHAMPIONS (§2): por modelo con ≥10 trades LIVE cerrados, si
   WR live < WR backtest − 20pts o PF live < 0.8 → alerta de democión a
   PAPER_ONLY (alerta-primero mientras ningún modelo llega a n=10; endurecer
   a automático cuando haya volumen).

Fail-safe: cualquier error se loguea y NO rompe nada. NUNCA importa web_server.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHILE  = timezone(timedelta(hours=-4))
BASE   = Path('/opt/sigma')
STATE  = BASE / 'results' / 'reports' / 'precommitment_state.json'
REGIME = BASE / 'results' / 'reports' / 'regime_matrix.json'
TRADES = BASE / 'results' / 'trade_state.json'

EQUITY_FLOOR_USD   = 385.0   # 70% del capital de referencia $550.51 (§3). Solo sube.
KILL_PF_THRESHOLD  = 1.0
KILL_PF_MIN_TRADES = 60
DEMOTION_MIN_N     = 10
DEMOTION_WR_GAP    = 20.0
DEMOTION_PF        = 0.8


def _log(msg):
    print(f"[{datetime.now(CHILE):%H:%M:%S}] [PRECOMMIT] {msg}", flush=True)


def _tg(msg):
    try:
        sys.path.insert(0, str(BASE / 'engine' / 'live'))
        import telegram_notifier
        telegram_notifier.send(msg)
    except Exception as e:
        _log(f"telegram fallo: {e}")


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def _save_state(st):
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, indent=2), encoding='utf-8')
    tmp.replace(STATE)


def _live_closed(trades):
    return [t for t in trades.get('history', []) if t.get('mode') == 'LIVE']


def _pf(rows):
    wins   = sum(t.get('pnl_dollar', 0) for t in rows if t.get('pnl_dollar', 0) > 0)
    losses = sum(-t.get('pnl_dollar', 0) for t in rows if t.get('pnl_dollar', 0) < 0)
    return (wins / losses) if losses > 0 else float('inf'), wins, losses


def check_regime(st, trades):
    matrix = _load(REGIME, {})
    regime = matrix.get('global_regime_m1')
    if not regime:
        _log("regime_matrix sin global_regime_m1 -- skip")
        return
    last = st.get('last_global_regime')
    if last is None:
        st['last_global_regime'] = regime
        _log(f"primer registro de regimen: {regime}")
        return
    restr = st.get('restriction') or {}

    if regime != last:
        st['last_global_regime'] = regime
        st['regime_changed_at']  = datetime.now(CHILE).isoformat()
        st['restriction'] = {'active': True, 'kelly_mult': 0.5, 'max_live_slots': 2,
                             'phase': 'fase1', 'reason': f'regimen {last}->{regime}',
                             'activated_at': datetime.now(CHILE).isoformat()}
        _save_state(st)
        _tg(f"🔄🛡 <b>CAMBIO DE RÉGIMEN: {last} → {regime}</b>\n\n"
            f"Protocolo de pre-compromiso ACTIVADO automáticamente "
            f"(escrito en frío el 2026-07-02, hoy solo se ejecuta):\n"
            f"• Kelly global ×0.5\n• Máximo 2 slots LIVE simultáneos\n"
            f"• Checkpoint al trade 10 cerrado en {regime}\n\n"
            f"La evidencia live del sistema era 100% del régimen anterior. "
            f"Esto no es miedo — es el plan.")
        _log(f"REGIMEN CAMBIO {last}->{regime}: restriccion activada")
        return

    # regimen estable: si hay restriccion activa, evaluar checkpoint / expiracion
    if not restr.get('active'):
        return
    changed_at = st.get('regime_changed_at')
    since = datetime.fromisoformat(changed_at) if changed_at else None
    nuevos = []
    for t in _live_closed(trades):
        ca = str(t.get('closed_at', ''))[:19]
        try:
            if since and datetime.fromisoformat(ca).replace(tzinfo=CHILE) >= since:
                nuevos.append(t)
        except Exception:
            pass

    if restr.get('phase') == 'fase1' and len(nuevos) >= 10:
        pf, _, _ = _pf(nuevos)
        wr = 100 * sum(1 for t in nuevos if t.get('pnl_dollar', 0) > 0) / len(nuevos)
        if wr >= 45 and pf >= 1.0:
            st['restriction'] = {'active': True, 'kelly_mult': 0.75, 'max_live_slots': 4,
                                 'phase': 'fase2', 'reason': restr.get('reason'),
                                 'activated_at': datetime.now(CHILE).isoformat()}
            _tg(f"🛡✅ <b>Checkpoint de régimen SUPERADO</b> (n=10: WR {wr:.0f}%, PF {pf:.2f})\n"
                f"Restricción relajada a Kelly ×0.75 por 14 días. Después, normal.")
        else:
            _tg(f"🛡🚨 <b>Checkpoint de régimen FALLADO</b> (n=10: WR {wr:.0f}%, PF {pf:.2f})\n"
                f"Según protocolo: LIVE OFF para modelos sin evidencia en este régimen "
                f"(quedan en paper hasta 20 trades paper con PF≥1.2).\n"
                f"⚠️ Acción MANUAL requerida — el guard mantiene Kelly ×0.5 mientras tanto.")
        _save_state(st)
    elif restr.get('phase') == 'fase2':
        act = datetime.fromisoformat(restr['activated_at'])
        if datetime.now(CHILE) - act >= timedelta(days=14):
            st['restriction'] = {'active': False}
            _save_state(st)
            _tg("🛡 Restricción de cambio de régimen LEVANTADA (fase 2 completó 14 días). "
                "Sizing vuelve a normal.")


def check_kill_criteria(st, trades, weekly):
    lives = _live_closed(trades)
    n = len(lives)
    pf, wins, losses = _pf(lives)
    eq = next((t['equity_after'] for t in reversed(lives) if t.get('equity_after')), None)

    # alertas duras (🚨): maximo 1/dia para no gastar la atencion que protegen.
    # advertencias blandas (⚠️): solo en el resumen semanal.
    hard, soft = [], []
    if n >= KILL_PF_MIN_TRADES and pf < KILL_PF_THRESHOLD:
        hard.append(f"🚨 KILL CRITERIA: PF live {pf:.2f} < {KILL_PF_THRESHOLD} con n={n}. "
                    f"Según protocolo: LIVE OFF + post-mortem. (Verificar ≥2 regímenes.)")
    elif n >= 30 and pf < 1.2:
        soft.append(f"⚠️ PF live {pf:.2f} acercándose al umbral kill ({KILL_PF_THRESHOLD}) — n={n}.")
    if eq is not None:
        if eq < EQUITY_FLOOR_USD:
            hard.append(f"🚨 KILL CRITERIA: equity ${eq:.0f} < piso ${EQUITY_FLOOR_USD:.0f}. "
                        f"Según protocolo: LIVE OFF inmediato.")
        elif eq < EQUITY_FLOOR_USD * 1.15:
            soft.append(f"⚠️ Equity ${eq:.0f} a <15% del piso kill (${EQUITY_FLOOR_USD:.0f}).")
    hoy = datetime.now(CHILE).strftime('%Y-%m-%d')
    if hard and st.get('last_kill_alert_date') != hoy:
        for a in hard:
            _tg(a); _log(a)
        st['last_kill_alert_date'] = hoy
        _save_state(st)
    if weekly:
        for a in soft:
            _tg(a); _log(a)

    if weekly:
        margen_eq = f"${eq:.0f} (piso ${EQUITY_FLOOR_USD:.0f}, margen {100*(eq/EQUITY_FLOOR_USD-1):.0f}%)" if eq else "s/d"
        pf_txt = f"{pf:.2f}" if pf != float('inf') else "∞ (sin pérdidas)"
        _tg(f"🛡 <b>PRE-COMPROMISOS — estado semanal</b>\n"
            f"• Régimen M1: {st.get('last_global_regime','?')} "
            f"(restricción: {'ACTIVA' if (st.get('restriction') or {}).get('active') else 'no'})\n"
            f"• Kill PF: live {pf_txt} vs umbral {KILL_PF_THRESHOLD} (n={n}/{KILL_PF_MIN_TRADES})\n"
            f"• Kill equity: {margen_eq}\n"
            f"• Distancias vigiladas cada 6h. El protocolo completo: PROTOCOLO_PRECOMPROMISO.md")


def check_demotions(trades, st):
    por_modelo = {}
    for t in _live_closed(trades):
        k = (t.get('sym'), t.get('tf'), t.get('strategy'))
        if all(k):
            por_modelo.setdefault(k, []).append(t)
    for (sym, tf, strat), rows in por_modelo.items():
        if len(rows) < DEMOTION_MIN_N:
            continue
        wr_live = 100 * sum(1 for t in rows if t.get('pnl_dollar', 0) > 0) / len(rows)
        pf_live, _, _ = _pf(rows)
        wr_bt = None
        for prefix in (sym.lower(), sym.lower() + 'usd'):
            mj = _load(BASE / 'models' / tf / f'{prefix}_{strat}.json', None)
            if mj:
                wr_bt = (mj.get('metrics_oos') or {}).get('wr')
                break
        gap = (wr_bt - wr_live) if wr_bt is not None else None
        if (gap is not None and gap > DEMOTION_WR_GAP) or pf_live < DEMOTION_PF:
            # dedupe: maximo 1 alerta por modelo por semana
            dkey = f'{sym}_{tf}_{strat}'
            last = (st.get('demotion_alerts') or {}).get(dkey)
            try:
                if last and (datetime.now(CHILE) - datetime.fromisoformat(last)).days < 7:
                    continue
            except Exception:
                pass
            st.setdefault('demotion_alerts', {})[dkey] = datetime.now(CHILE).isoformat()
            _save_state(st)
            _tg(f"⚖️ <b>DEMOCIÓN REQUERIDA</b> — {sym}/{tf} {strat}\n"
                f"n={len(rows)} LIVE: WR {wr_live:.0f}% vs backtest {wr_bt}% "
                f"(gap {gap:.0f}pts) | PF {pf_live:.2f}\n"
                f"Según §2 del protocolo: PAPER_ONLY. Re-promoción solo por gate completo.\n"
                f"⚠️ Acción manual (auto-democión pendiente de integrar al elector).")
            _log(f"DEMOCION {sym}/{tf} {strat}: wr_live={wr_live:.0f} wr_bt={wr_bt} pf={pf_live:.2f}")


def main():
    weekly = '--weekly' in sys.argv
    st = _load(STATE, {})
    trades = _load(TRADES, {})
    if not trades:
        _log("trade_state ilegible -- abort (fail-safe)")
        return
    try:
        check_regime(st, trades)
    except Exception as e:
        _log(f"check_regime error: {e}")
    try:
        check_kill_criteria(st, trades, weekly)
    except Exception as e:
        _log(f"check_kill_criteria error: {e}")
    try:
        check_demotions(trades, st)
    except Exception as e:
        _log(f"check_demotions error: {e}")
    if not STATE.exists():
        _save_state(st)


if __name__ == '__main__':
    main()

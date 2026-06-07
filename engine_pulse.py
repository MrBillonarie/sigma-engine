"""engine_pulse.py — Formatter compartido del pulso del motor.

Single source of truth para los mensajes "pulso del motor" que se publican
en Dashboard / Telegram / Discord. Todos leen del mismo /api/v2/engine_status
y formatean según el canal destino.

Uso:
    from engine_pulse import get_pulse_telegram, get_pulse_discord, get_pulse_dict

    msg_tg = get_pulse_telegram()
    embed  = get_pulse_discord()
    raw    = get_pulse_dict()
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

BASE = Path('/opt/sigma')


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def get_pulse_dict() -> dict:
    """Lee directamente de los JSON canónicos (no via HTTP) para evitar dependencia del Flask."""
    snap = _read_json(BASE / 'results/reports/port_snapshot.json', {})
    bay  = _read_json(BASE / 'results/reports/bayesian_edges.json', {'strategies': {}})
    fire = _read_json(BASE / 'results/fire_config.json', {})
    ts   = _read_json(BASE / 'results/trade_state.json', {})

    # Decision activity
    decisions_24h = {}
    last_decision_ts: str | None = None
    try:
        df = BASE / 'data' / 'decisions.jsonl'
        if df.exists():
            cutoff = datetime.now(timezone.utc).timestamp() - 24 * 3600
            with open(df, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try:
                        rec = json.loads(line)
                        rec_ts = datetime.fromisoformat(rec['ts'].replace('Z', '+00:00'))
                        if rec_ts.timestamp() < cutoff:
                            continue
                        k = rec.get('kind', 'unknown')
                        decisions_24h[k] = decisions_24h.get(k, 0) + 1
                        if last_decision_ts is None or rec.get('ts', '') > last_decision_ts:
                            last_decision_ts = rec.get('ts')
                    except Exception:
                        continue
    except Exception:
        pass

    strats = bay.get('strategies', {})
    cur_eq = ts.get('portfolio', {}).get('equity', fire.get('starting_equity', 10000))
    start  = fire.get('starting_equity', 10000)
    target = fire.get('target_equity', 100000)
    fire_pct = max(0.0, min(100.0, (cur_eq - start) / max(target - start, 1) * 100))

    return {
        'cagr_weighted':    round(snap.get('port_cagr', 0), 2),
        'cagr_pass_live':   round(snap.get('port_cagr_pass_live', 0), 2),
        'wr':               round(snap.get('port_wr', 0), 1),
        'dd':               round(snap.get('port_dd', 0), 1),
        'pf':               round(snap.get('port_pf', 0), 2),
        'n_trades':         snap.get('total_trades', 0),
        'coverage_active':  len(snap.get('champions', {})),
        'coverage_target':  40,
        'n_grade_a':        snap.get('n_grade_a', 0),
        'n_pass_live':      snap.get('n_pass_live', 0),
        'n_blocked':        snap.get('n_blocked', 0),
        'bayesian_total':   len(strats),
        'bayesian_edge':    sum(1 for s in strats.values() if s.get('edge_confirmed')),
        'fire_equity':      round(cur_eq, 2),
        'fire_target':      target,
        'fire_pct':         round(fire_pct, 1),
        'btc_virtual':      round(cur_eq / 100000, 6),
        'decisions_24h':    decisions_24h,
        'last_decision_at': last_decision_ts,
        'snapshot_at':      snap.get('snapshot_at'),
    }


def get_pulse_telegram() -> str:
    """Formato Markdown V2-safe para Telegram (sin caracteres especiales sin escapar)."""
    p = get_pulse_dict()
    decisions_count = sum(p['decisions_24h'].values()) if p['decisions_24h'] else 0
    lines = [
        '<b>⚡ SIGMA · pulso del motor</b>',
        '',
        f"📈 <b>CAGR</b> {p['cagr_weighted']}% (weighted) · <b>WR</b> {p['wr']}% · <b>PF</b> {p['pf']}",
        f"📉 <b>Max DD</b> {p['dd']}% · <b>{p['n_trades']}</b> trades acumulados",
        '',
        f"🎯 <b>Cobertura</b> {p['coverage_active']}/{p['coverage_target']} slots",
        f"⭐ {p['n_grade_a']} grade A · ✅ {p['n_pass_live']} PASS_LIVE · ⛔ {p['n_blocked']} blocked",
        '',
        f"🧪 <b>Bayesian</b> {p['bayesian_edge']}/{p['bayesian_total']} edge confirmado",
        f"🔥 <b>FIRE</b> {p['fire_pct']}% · ${p['fire_equity']:,.0f} / ${p['fire_target']:,.0f}",
        f"₿ <b>BTC virtual</b> {p['btc_virtual']:.6f}",
        '',
        f"📡 <b>{decisions_count}</b> decisiones en últimas 24h",
    ]
    return '\n'.join(lines)


def get_pulse_discord() -> dict:
    """Discord embed dict, compatible con discord.py Embed(**dict)."""
    p = get_pulse_dict()
    decisions_count = sum(p['decisions_24h'].values()) if p['decisions_24h'] else 0
    color = 0xd4af37  # gold

    fields = [
        {'name': '📈 Portfolio', 'value': f"CAGR **{p['cagr_weighted']}%** · WR {p['wr']}% · PF {p['pf']}\nMax DD {p['dd']}% · {p['n_trades']} trades", 'inline': False},
        {'name': '🎯 Cobertura', 'value': f"**{p['coverage_active']}/{p['coverage_target']}** slots\n⭐ {p['n_grade_a']} A · ✅ {p['n_pass_live']} PASS · ⛔ {p['n_blocked']} blocked", 'inline': True},
        {'name': '🧪 Bayesian', 'value': f"**{p['bayesian_edge']}/{p['bayesian_total']}** edge\nconfirmados", 'inline': True},
        {'name': '🔥 FIRE', 'value': f"**{p['fire_pct']}%**\n${p['fire_equity']:,.0f} → ${p['fire_target']:,.0f}\n₿ {p['btc_virtual']:.6f}", 'inline': True},
        {'name': '📡 Actividad 24h', 'value': f"**{decisions_count}** decisiones del motor", 'inline': False},
    ]

    return {
        'title': '⚡ SIGMA · Quantum Decision Engine',
        'description': 'Pulso en vivo del motor cuántico',
        'color': color,
        'fields': fields,
        'footer': {'text': f"snapshot {p['snapshot_at'] or '—'}"},
    }

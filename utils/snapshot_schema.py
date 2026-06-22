"""Esquema compartido para port_snapshot.json -- escrito por 2 procesos
independientes (champion_watcher.py y engine/live/dashboard.py) que no se
coordinan entre si.

Antes de este modulo, cada campo nuevo (ej. champions_secondary, agregado
2026-06-20) necesitaba un parche ad-hoc de preservacion en AMBOS escritores,
o desaparecia silenciosamente cuando el otro proceso regeneraba el snapshot
desde cero -- bug real, encontrado en produccion minutos despues del deploy
del dual-champion (dashboard.py:_write_snapshot() reconstruia `snap` desde
cero con un dict literal, no via dict(existing), y solo preservaba campos
que conocia de antes).

Regla unica: el snapshot final es siempre `{**existing, **updates}` -- el
escritor pasa solo los campos que el mismo calcula en `updates`; cualquier
otro campo del snapshot anterior (de este escritor o del otro) sobrevive
automaticamente, sin necesidad de acordarse de preservarlo explicitamente.

OWNED_FIELDS es documentacion (que campos calcula cada escritor hoy), no
algo que la funcion necesite para decidir que preservar -- la preservacion
es automatica para TODO lo que no este en `updates`.
"""

OWNED_FIELDS = {
    "champion_watcher": {
        "champions", "champions_secondary", "snapshot_at", "trigger",
        "port_cagr", "port_cagr_kelly_boost", "port_cagr_with_kelly",
        "port_wr", "port_dd", "port_pf", "port_calmar", "total_trades",
        "n_grade_a", "port_cagr_operational", "port_cagr_pass_live",
        "port_cagr_all_inc_blocked", "n_pass_live", "n_blocked",
    },
    "dashboard": {
        "port_cagr", "port_cagr_kelly_boost", "port_cagr_with_kelly",
        "port_wr", "port_dd", "port_pf", "port_calmar", "total_trades",
        "n_grade_a", "snapshot_at", "trigger", "champions",
    },
}


def merge_snapshot(existing: dict, updates: dict, owner: str = None) -> dict:
    """snap final = existing con `updates` aplicado encima. Todo lo que no
    este en `updates` se preserva tal cual venga de `existing`, sin importar
    quien lo haya escrito originalmente. `owner` no se usa para gating (ver
    docstring del modulo) -- queda como parametro solo para dejar explicito
    en cada call site quien esta escribiendo, a fin de mantener OWNED_FIELDS
    como documentacion viva.
    """
    merged = dict(existing or {})
    merged.update(updates or {})
    return merged

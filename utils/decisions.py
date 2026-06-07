"""utils/decisions.py — Decision Stream del motor SIGMA.

Single source of truth para "qué decidió el motor". Append-only JSONL.
Cada decisión se escribe acá y se expone por /api/decisions a Dashboard, Telegram, Discord.

Tipos de decisión soportados:
  champion_promoted     — un nuevo modelo gana un slot
  champion_blocked      — robustness gate rechaza un candidato
  push_started          — adaptive launcher arranca un push
  push_finished         — push termina (con resultado)
  gap_closed            — sub-portfolio gap cubierto
  signal_opened         — paper trade abierto
  signal_closed         — paper trade cerrado
  bayesian_transition   — strategy pasa de WATCHING a EDGE_CONFIRMED (o reverso)
  milestone_hit         — backtest milestone (100K, 200K, ...) alcanzado
  regime_change         — BULL ↔ BEAR ↔ RANGE en algún asset
  pine_updated          — Pine Script regenerado tras nuevo champion
  circuit_breaker       — CB activado/desactivado

Cada decisión queda con: id, ts, kind, slot, payload, meta.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Iterable, Optional

BASE = Path('/opt/sigma')
DATA_DIR = BASE / 'data'
DECISIONS_FILE = DATA_DIR / 'decisions.jsonl'
MAX_BYTES = 10 * 1024 * 1024  # 10 MB — rotate at this size
_WRITE_LOCK = Lock()

VALID_KINDS = {
    'champion_promoted', 'champion_blocked',
    'push_started', 'push_finished',
    'gap_closed',
    'signal_opened', 'signal_closed',
    'bayesian_transition',
    'milestone_hit',
    'regime_change',
    'pine_updated',
    'circuit_breaker',
}


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _rotate_if_needed() -> None:
    try:
        if DECISIONS_FILE.exists() and DECISIONS_FILE.stat().st_size > MAX_BYTES:
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
            archive = DATA_DIR / f'decisions.{stamp}.jsonl'
            DECISIONS_FILE.rename(archive)
    except Exception:
        pass


def log_decision(kind: str, payload: dict, slot: Optional[str] = None,
                 meta: Optional[dict] = None) -> dict:
    """Append a decision atomically. Returns the full record.

    Silent on failure — never blocks the caller, even if disk is full.
    """
    if kind not in VALID_KINDS:
        # Permitir kinds nuevos para no romper futuros hooks, pero loguear warning
        # (acá no hay logger, simplemente seguimos)
        pass

    record = {
        'id': uuid.uuid4().hex[:12],
        'ts': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'kind': kind,
        'slot': slot,
        'payload': payload or {},
        'meta': meta or {},
    }

    try:
        _ensure_dir()
        with _WRITE_LOCK:
            _rotate_if_needed()
            with open(DECISIONS_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        pass

    return record


def read_decisions(since_iso: Optional[str] = None,
                   limit: int = 100,
                   kinds: Optional[Iterable[str]] = None,
                   slot: Optional[str] = None) -> list[dict]:
    """Devuelve decisiones más recientes primero. Filtros opcionales."""
    if not DECISIONS_FILE.exists():
        return []

    kinds_set = set(kinds) if kinds else None
    out: list[dict] = []

    try:
        with open(DECISIONS_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception:
        return []

    # iteramos de la más nueva a la más vieja
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if since_iso and rec.get('ts', '') <= since_iso:
            continue
        if kinds_set and rec.get('kind') not in kinds_set:
            continue
        if slot and rec.get('slot') != slot:
            continue
        out.append(rec)
        if len(out) >= limit:
            break

    return out


def tail_decisions(n: int = 20) -> list[dict]:
    return read_decisions(limit=n)


def count_by_kind(hours: int = 24) -> dict[str, int]:
    """Cuenta decisiones de las últimas N horas, agrupadas por kind."""
    if not DECISIONS_FILE.exists():
        return {}
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    counts: dict[str, int] = {}
    try:
        with open(DECISIONS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                try:
                    rec_ts = datetime.fromisoformat(rec['ts'].replace('Z', '+00:00')).timestamp()
                    if rec_ts < cutoff:
                        continue
                except Exception:
                    continue
                k = rec.get('kind', 'unknown')
                counts[k] = counts.get(k, 0) + 1
    except Exception:
        pass
    return counts

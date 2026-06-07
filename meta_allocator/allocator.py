#!/usr/bin/env python3
"""
SIGMA Meta-Allocator — rota capital entre los N motores según performance.

Estado: SKELETON. Algoritmo descrito pero no implementado.

Modelo de allocation (a implementar):
  - Tactical: ponderar por Sharpe rolling 30d, capado por max_alloc del motor
  - Conservative: equal weight inicial, deviar solo si un motor degrada
  - Risk-parity: inverse-vol weighting (cada motor contribuye igual al riesgo)

Frecuencia recomendada de rebalance:
  - Diario para volatil (crypto-only)
  - Semanal para multi-asset (con motores stable)

Constraints:
  - Cada motor tiene min/max alloc en su motor.json
  - Suma de allocs = 100%
  - Reserve cash mínimo 5-10%
"""
import json
from pathlib import Path
from datetime import datetime

MOTORS_DIR = Path("/opt/sigma/motors")
STATE_FILE = Path("/opt/sigma/meta_allocator/state/current_allocation.json")
LOG_FILE   = Path("/opt/sigma/meta_allocator/logs/allocator.log")


def log(msg):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {msg}\n")
    print(f"[{ts}] {msg}")


def load_motor_configs():
    """Lee config de cada motor disponible."""
    configs = {}
    if not MOTORS_DIR.exists():
        return configs
    for motor_dir in MOTORS_DIR.iterdir():
        if not motor_dir.is_dir():
            continue
        cfg_path = motor_dir / "config" / "motor.json"
        if cfg_path.exists():
            try:
                configs[motor_dir.name] = json.loads(cfg_path.read_text())
            except Exception as e:
                log(f"WARN: cant load {motor_dir.name}: {e}")
    return configs


def compute_motor_performance(motor_name):
    """STUB — devuelve métricas live por motor.

    TODO: leer port_snapshot equivalente de cada motor:
      sharpe_30d, sortino, max_dd, return_30d, vol_30d
    """
    return {
        "sharpe_30d": 0,
        "vol_30d":    0,
        "return_30d": 0,
        "max_dd_30d": 0,
        "n_trades":   0,
        "status":     "no_data",
    }


def equal_weight_allocation(configs):
    """Fallback inicial: equal weight entre los motores con status ACTIVE."""
    active = [name for name, cfg in configs.items() if cfg.get("status") == "ACTIVE"]
    if not active:
        return {}
    weight = round(100.0 / len(active), 2)
    return {name: weight for name in active}


def sharpe_weighted_allocation(configs, perf):
    """STUB — pondera por Sharpe positivo, respetando min/max por motor."""
    # TODO: implementar cuando haya datos
    return equal_weight_allocation(configs)


def main():
    configs = load_motor_configs()
    log(f"Motores disponibles: {list(configs.keys())}")

    active_count = sum(1 for cfg in configs.values() if cfg.get("status") == "ACTIVE")
    log(f"Motores ACTIVE: {active_count}")

    if active_count == 0:
        log("No hay motores ACTIVE - skip rebalance")
        return

    # Compute perf for each
    perf = {name: compute_motor_performance(name) for name in configs}

    # Allocate (equal weight for now, sharpe-weighted cuando haya data)
    allocation = sharpe_weighted_allocation(configs, perf)

    # Save state
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "as_of": datetime.now().isoformat(),
        "method": "equal_weight" if all(p["status"] == "no_data" for p in perf.values()) else "sharpe_weighted",
        "allocation_pct": allocation,
        "performance": perf,
        "motors_configs": {k: {"status": v.get("status"), "asset_class": v.get("asset_class")} for k, v in configs.items()},
    }
    STATE_FILE.write_text(json.dumps(state, indent=2))
    log(f"Allocation saved: {allocation}")


if __name__ == "__main__":
    main()

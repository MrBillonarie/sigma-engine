#!/usr/bin/env python3
"""
SIGMA CPU Scaler — run after each CPU upgrade.

Usage:
    python3 /opt/sigma/utils/cpu_scaler.py          # auto-detect CPU
    python3 /opt/sigma/utils/cpu_scaler.py --cpu 8  # force tier

What it does:
  1. Detects current CPU count
  2. Selects the right scaling tier
  3. Updates master_pipeline.py MAX_PARALLEL
  4. Updates asset_pipeline.py N_TRIALS and MC_SIMS
  5. Updates adaptive_push_launcher.py PUSH_TRIALS
  6. Sends Telegram notification
  7. Saves applied config to scaling_profile.json
"""
import os, sys, re, json, subprocess
from pathlib import Path

BASE    = Path("/opt/sigma")
CONFIG  = BASE / "config/scaling_profile.json"

def _get_cpu_count():
    try:
        return os.cpu_count() or 2
    except Exception:
        return 2

def _select_tier(n_cpu, tiers):
    """Select best matching tier (largest tier <= n_cpu)."""
    valid = [int(k) for k in tiers if int(k) <= n_cpu]
    if not valid:
        return tiers[min(tiers.keys(), key=lambda x: int(x))]
    best = str(max(valid))
    return tiers[best]

def _patch_file(path, pattern, replacement, label):
    """Regex patch a Python file. Returns True if changed."""
    if not Path(path).exists():
        print(f"  SKIP (not found): {path}")
        return False
    txt = Path(path).read_text()
    new_txt, n = re.subn(pattern, replacement, txt)
    if n == 0:
        print(f"  SKIP (pattern not found): {label}")
        return False
    if txt == new_txt:
        print(f"  UNCHANGED: {label}")
        return False
    Path(path).write_text(new_txt)
    print(f"  UPDATED: {label}")
    return True

def _tg_send(msg):
    try:
        import urllib.request, json as _j
        tok = Path("/opt/sigma/config/tg_token.txt").read_text().strip()
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data=_j.dumps({"chat_id":"-1003787411069","text":msg,"parse_mode":"Markdown"}).encode(),
            headers={"Content-Type":"application/json"}), timeout=10)
    except Exception as e:
        print(f"TG error: {e}")

def apply(n_cpu=None):
    cfg = json.loads(CONFIG.read_text())
    tiers = cfg["cpu_tiers"]

    if n_cpu is None:
        n_cpu = _get_cpu_count() if cfg.get("auto_detect", True) else cfg.get("last_applied_cpu", 2)

    tier = _select_tier(n_cpu, tiers)
    print(f"\nCPU: {n_cpu} cores → Tier: {tier['label']}")
    print(f"  max_parallel={tier['max_parallel']} trials={tier['optuna_trials']} "
          f"mc={tier['mc_sims']} wft={tier['wft_windows']} push={tier['push_trials']}")

    prev_cpu = cfg.get("last_applied_cpu", 2)
    changed = []

    # 1. master_pipeline.py
    if _patch_file(
        BASE / "engine/live/master_pipeline.py",
        r"MAX_PARALLEL\s*=\s*\d+",
        f"MAX_PARALLEL = {tier['max_parallel']}",
        f"master_pipeline MAX_PARALLEL → {tier['max_parallel']}"
    ): changed.append(f"MAX_PARALLEL={tier['max_parallel']}")

    # 2. asset_pipeline.py — N_TRIALS
    if _patch_file(
        BASE / "engine/optimization/asset_pipeline.py",
        r"N_TRIALS\s*=\s*\d+",
        f"N_TRIALS = {tier['optuna_trials']}",
        f"asset_pipeline N_TRIALS → {tier['optuna_trials']}"
    ): changed.append(f"N_TRIALS={tier['optuna_trials']}")

    # 3. asset_pipeline.py — MC_SIMS
    if _patch_file(
        BASE / "engine/optimization/asset_pipeline.py",
        r"MC_SIMS\s*=\s*\d+",
        f"MC_SIMS = {tier['mc_sims']}",
        f"asset_pipeline MC_SIMS → {tier['mc_sims']}"
    ): changed.append(f"MC_SIMS={tier['mc_sims']}")

    # 4. asset_pipeline.py — WFT_WINDOWS
    if _patch_file(
        BASE / "engine/optimization/asset_pipeline.py",
        r"WFT_WINDOWS\s*=\s*\d+",
        f"WFT_WINDOWS = {tier['wft_windows']}",
        f"asset_pipeline WFT_WINDOWS → {tier['wft_windows']}"
    ): changed.append(f"WFT_WINDOWS={tier['wft_windows']}")

    # 5. adaptive_push_launcher — push trials
    for fname in ["scripts/adaptive_push_launcher.py", "scripts/gap_launcher.py"]:
        if _patch_file(
            BASE / fname,
            r"PUSH_TRIALS\s*=\s*\d+",
            f"PUSH_TRIALS = {tier['push_trials']}",
            f"{fname} PUSH_TRIALS → {tier['push_trials']}"
        ): changed.append(f"push_trials={tier['push_trials']}")

    # Save applied config
    from datetime import datetime
    cfg["last_applied_cpu"]  = n_cpu
    cfg["last_applied_date"] = datetime.now().strftime("%Y-%m-%d")
    CONFIG.write_text(json.dumps(cfg, indent=2))

    # Telegram notification
    if changed:
        msg = (
            f"\U0001f4bb *SIGMA — CPU Upgrade Aplicado*\n\n"
            f"CPU: {prev_cpu} → {n_cpu} cores ({tier['label']})\n\n"
            f"*Parámetros actualizados:*\n"
            + "\n".join(f"  • {c}" for c in changed) +
            f"\n\nReinicia sigma-pipeline y sigma-trainer para aplicar."
        )
        _tg_send(msg)
        print(f"\nTelegram sent: {len(changed)} changes")
    else:
        print("\nNo changes — already at correct tier")

    return tier

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--cpu", type=int, default=None, help="Force CPU count")
    args = p.parse_args()
    apply(args.cpu)

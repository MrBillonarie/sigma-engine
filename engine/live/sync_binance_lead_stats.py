#!/usr/bin/env python3
"""Sincroniza AUM, copy traders activos y capital propio desde el endpoint
publico ("friendly", sin login) que usa la propia pagina de Binance para
renderizar el perfil de Copy Trading. No existe API autenticada equivalente
(SAPI no expone follower count/AUM) y la pagina HTML esta detras de un
challenge anti-bot, pero este endpoint bapi/.../friendly/... responde
directo via HTTP -- probado 2026-06-26."""
import json
import os
import sys
from datetime import datetime

import requests

PORTFOLIO_ID = "5096369356136167936"
URL = (
    "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/"
    f"lead-portfolio/detail?portfolioId={PORTFOLIO_ID}"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": f"https://www.binance.com/es-LA/copy-trading/lead-details/{PORTFOLIO_ID}",
}

AUM_PATH = "/opt/sigma/results/reports/aum.json"
COPYTRADERS_PATH = "/opt/sigma/results/reports/copytraders.json"


def _write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def main():
    resp = requests.get(URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()["data"]

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    aum_total = float(data["aumAmount"])
    own_capital = float(data["marginBalance"])
    copytraders_total = int(data["currentCopyCount"])

    _write(AUM_PATH, {
        "aum_total": aum_total,
        "own_capital": own_capital,
        "updated_at": now,
        "source": "binance_public_api",
    })
    _write(COPYTRADERS_PATH, {
        "copytraders_total": copytraders_total,
        "updated_at": now,
        "source": "binance_public_api",
    })

    print(f"[sync_binance_lead_stats] aum={aum_total} own={own_capital} "
          f"copytraders={copytraders_total} at {now}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[sync_binance_lead_stats] ERROR: {e}", file=sys.stderr)
        sys.exit(1)

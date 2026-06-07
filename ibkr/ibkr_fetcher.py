#!/usr/bin/env python3
"""
ibkr_fetcher.py — Lee posiciones IBKR via ib_insync, filtra datos sensibles, persiste JSON.
Cron */5 min cuando IB Gateway esté autenticado.

Privacy contract:
  Solo expone: symbol, direction, pct_portfolio, pnl_pct, days_open, sector
  Nunca expone: cantidad, precio absoluto, USD value, total equity, cost basis
"""
import json, os, sys, time
from pathlib import Path
from datetime import datetime, timezone

GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 4001    # IB Gateway live default (4001 si paper, ajustar)
CLIENT_ID    = 17       # único, no usado por otros bots
TIMEOUT_SEC  = 15

OUTPUT_FILE = Path("/opt/sigma/ibkr/positions.json")
LOG_FILE    = Path("/opt/sigma/ibkr/logs/fetcher.log")


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = f'[{ts}] {msg}\n'
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line)
    print(line, end='', flush=True)


def fetch_and_publish():
    """Connect to gateway, fetch positions, redact sensitive fields, save JSON."""
    try:
        from ib_insync import IB, util
    except ImportError as e:
        log(f"FATAL: ib_insync not installed: {e}")
        return 1

    ib = IB()
    try:
        ib.connect(GATEWAY_HOST, GATEWAY_PORT, clientId=CLIENT_ID, timeout=TIMEOUT_SEC)
        # 3 = delayed market data (free, 15-min delay)
        # 4 = delayed-frozen, 1 = live (requires subscription)
        ib.reqMarketDataType(3)
    except Exception as e:
        log(f"connect err: {e}")
        return 2

    try:
        positions = ib.positions()
        # We also need total equity to compute pct_portfolio (but we WON'T expose it)
        account = ib.accountSummary()
        total_equity = 0.0
        for av in account:
            if av.tag == "NetLiquidation":
                total_equity = float(av.value)
                break

        # Build sanitized output
        sanitized = []
        for p in positions:
            sym = p.contract.symbol
            qty = p.position
            avg_cost = p.avgCost or 0
            mkt_price = 0
            mkt_value = 0
            # Try to get current price (ticker subscription)
            try:
                ticker = ib.reqMktData(p.contract, '', False, False)
                ib.sleep(1)
                mkt_price = ticker.marketPrice() or ticker.last or 0
                if mkt_price:
                    mkt_value = mkt_price * abs(qty)
                ib.cancelMktData(p.contract)
            except Exception:
                pass

            # Direction
            direction = "long" if qty > 0 else "short"

            # PnL % vs entry (only %, NO absolute)
            pnl_pct = 0
            if avg_cost and mkt_price:
                if direction == "long":
                    pnl_pct = round((mkt_price - avg_cost) / avg_cost * 100, 2)
                else:
                    pnl_pct = round((avg_cost - mkt_price) / avg_cost * 100, 2)

            # % portfolio (ratio only, no USD)
            pct_portfolio = round(mkt_value / total_equity * 100, 2) if total_equity > 0 else 0

            # NaN-safe: si los calculos dan NaN/inf, usar 0
            def _safe(v):
                import math
                if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                    return 0
                return v
            sanitized.append({
                "symbol":         sym,
                "direction":      direction,
                "pct_portfolio":  _safe(pct_portfolio),
                "pnl_pct":        _safe(pnl_pct),
                "has_price":      bool(mkt_price and not (isinstance(mkt_price, float) and (mkt_price != mkt_price))),
                # Note: deliberately NO qty, NO USD value, NO cost basis, NO total_equity
            })

        # Save
        out = {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "positions": sanitized,
            "n_positions": len(sanitized),
            "_privacy_note": "Only ratios and percentages exposed. Absolute USD values intentionally redacted.",
        }
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        log(f"OK · {len(sanitized)} positions published")
        return 0

    except Exception as e:
        import traceback
        log(f"fetch err: {e}\n{traceback.format_exc()}")
        return 3
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(fetch_and_publish())

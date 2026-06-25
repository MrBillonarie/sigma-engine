"""SIGMA Futures Indices & Bonds (IBKR) -- micro-futuros CME, fase 1 (US-only)."""
UNIVERSE = ["MES", "MNQ", "MYM", "ZN", "ZB"]

DESCRIPTIONS = {
    "MES": "Micro E-mini S&P 500 - 1/10 del contrato full-size",
    "MNQ": "Micro E-mini Nasdaq 100",
    "MYM": "Micro E-mini Dow Jones",
    "ZN":  "10-Year Treasury Note future",
    "ZB":  "30-Year Treasury Bond future",
}

# Internacionales (DAX/FTSE/Nikkei/IBOVESPA) quedan para fase 2 - ver
# config/motor.json -> deferred_fase2. Requieren margen multi-moneda.

def get_universe():
    return UNIVERSE

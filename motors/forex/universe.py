"""SIGMA Forex (IBKR) -- universo de 4 pares majors, via IDEALPRO."""
UNIVERSE = ["EUR.USD", "GBP.USD", "USD.JPY", "USD.CHF"]

DESCRIPTIONS = {
    "EUR.USD":  "Euro vs Dollar - mas liquido del mundo",
    "GBP.USD":  "Pound vs Dollar - cable, volatil pre/post UK news",
    "USD.JPY":  "Dollar vs Yen - safe-haven flow indicator",
    "USD.CHF":  "Dollar vs Franco Suizo - safe-haven puro, baja correlacion con riesgo",
}


def get_universe():
    return UNIVERSE

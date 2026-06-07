"""SIGMA Forex — universo de 5 pares majors."""
UNIVERSE = ["EUR.USD", "GBP.USD", "USD.JPY", "AUD.USD", "USD.CAD"]

DESCRIPTIONS = {
    "EUR.USD":  "Euro vs Dollar - mas liquido del mundo",
    "GBP.USD":  "Pound vs Dollar - cable, volatil pre/post UK news",
    "USD.JPY":  "Dollar vs Yen - safe-haven flow indicator",
    "AUD.USD":  "Aussie vs Dollar - commodity proxy",
    "USD.CAD":  "Dollar vs Loonie - oil correlation",
}


def get_universe():
    return UNIVERSE

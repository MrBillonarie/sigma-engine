"""SIGMA LATAM — universo de 5 ETFs país emerging."""
UNIVERSE = ["EWZ", "ECH", "EWW", "EEM", "ILF"]

DESCRIPTIONS = {
    "EWZ":  "iShares MSCI Brazil - LATAM commodity-heavy",
    "ECH":  "iShares MSCI Chile - copper + finance",
    "EWW":  "iShares MSCI Mexico - manufacturing exporter",
    "EEM":  "iShares MSCI Emerging Markets - broad EM exposure",
    "ILF":  "iShares Latin America 40 - LATAM broad",
}


def get_universe():
    return UNIVERSE

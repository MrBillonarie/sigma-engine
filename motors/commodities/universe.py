"""SIGMA Commodities — universo de 5 ETFs commodity."""
UNIVERSE = ["GLD", "SLV", "USO", "UNG", "DBA"]

DESCRIPTIONS = {
    "GLD":  "Gold - safe haven, hedge inflation",
    "SLV":  "Silver - industrial + monetary",
    "USO":  "Oil - cyclical, geopolitical",
    "UNG":  "Natural Gas - seasonal, volatile",
    "DBA":  "Agriculture - inflation pass-through",
}


def get_universe():
    return UNIVERSE

"""SIGMA Stocks US — universo de 5 ETFs macro."""
UNIVERSE = ["SPY", "QQQ", "IWM", "GLD", "TLT"]

DESCRIPTIONS = {
    "SPY":  "S&P 500 broad market - core risk-on",
    "QQQ":  "Nasdaq 100 tech - high beta tech",
    "IWM":  "Russell 2000 small cap - cyclical",
    "GLD":  "SPDR Gold - safe haven, inflation hedge",
    "TLT":  "20+ Year Treasury - duration hedge, risk-off",
}

CORRELATIONS_EXPECTED = {
    ("SPY", "QQQ"): 0.85,
    ("SPY", "IWM"): 0.75,
    ("SPY", "GLD"): -0.10,
    ("SPY", "TLT"): -0.25,
    ("QQQ", "TLT"): -0.30,
    ("GLD", "TLT"):  0.20,
}


def get_universe():
    return UNIVERSE

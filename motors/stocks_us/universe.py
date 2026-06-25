"""SIGMA Equities & ETF (IBKR) -- universo de 8 ETFs macro.

Regla: solo ETFs, nunca acciones individuales (NVDA/AAPL quedan fuera a
proposito -- idiosyncratic risk no encaja con el modelo sistematico de SIGMA).
"""
UNIVERSE = ["SPY", "QQQ", "IWM", "GLD", "SLV", "TLT", "HYG", "TBT"]

DESCRIPTIONS = {
    "SPY":  "S&P 500 broad market - core risk-on",
    "QQQ":  "Nasdaq 100 tech - high beta tech",
    "IWM":  "Russell 2000 small cap - cyclical",
    "GLD":  "SPDR Gold - safe haven, inflation hedge",
    "SLV":  "iShares Silver - safe haven, mas volatil que GLD",
    "TLT":  "20+ Year Treasury - duration hedge, risk-off",
    "HYG":  "High Yield Corporate Bonds - risk-on credito, proxy de apetito por riesgo",
    "TBT":  "Inverso 2x de TLT - apuesta a tasas subiendo",
}

CORRELATIONS_EXPECTED = {
    ("SPY", "QQQ"): 0.85,
    ("SPY", "IWM"): 0.75,
    ("SPY", "GLD"): -0.10,
    ("SPY", "TLT"): -0.25,
    ("QQQ", "TLT"): -0.30,
    ("GLD", "SLV"):  0.85,
    ("TLT", "TBT"): -0.95,
    ("TLT", "HYG"):  0.30,
}


def get_universe():
    return UNIVERSE

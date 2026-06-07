import ccxt, json
ex = ccxt.okx({'timeout': 20000})
ex.load_markets()
xau = [k for k in ex.markets if 'XAU' in k]
print('XAU on OKX:', xau[:10])

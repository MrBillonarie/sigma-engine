import sys
sys.path.insert(0, 'engine')
from core.data import fetch_ohlcv
from core.features import build_features
df = fetch_ohlcv(tf='4h', days=100)
print('4H rows:', len(df))
df2 = build_features(df, {})
print('Features OK:', df2.shape)

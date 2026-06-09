#!/usr/bin/env python3
"""
SIGMA Motor 2 — Macro Features Fetcher
Descarga DXY (DX-Y.NYB) y Tasa 10Y USA (^TNX) desde yfinance.
Usa como features macro para estrategias de commodities (XAU/XAG).
"""
import yfinance as yf
import pandas as pd
from pathlib import Path

LOG_FILE = Path('/opt/sigma/results/reports/commodities_update.log')


def _log(msg):
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def _get_close(raw):
    """Extrae columna close de output yfinance (maneja MultiIndex)."""
    if raw is None or len(raw) == 0:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        for col in raw.columns:
            if str(col[0]).lower() == 'close':
                return raw[col]
        return None
    for name in ['Close', 'close']:
        if name in raw.columns:
            return raw[name]
    return None


def download_macro(tfs=None):
    """
    Descarga DXY + 10Y yield para los TFs indicados.
    Retorna dict {tf: DataFrame} con columnas dxy, yield_10y.
    """
    tfs = tfs or ['1h', '4h', '1d']
    result = {}

    # Determinar parametros de descarga por TF base
    base_data = {}
    for interval, period, start in [
        ('1h', '729d', None),
        ('1d', None,   '2015-01-01'),
    ]:
        key = interval
        for ticker, col in [('DX-Y.NYB', 'dxy'), ('^TNX', 'yield_10y')]:
            kw = dict(tickers=ticker, interval=interval, progress=False, auto_adjust=True)
            if period: kw['period'] = period
            else:      kw['start']  = start
            try:
                raw = yf.download(**kw)
                s = _get_close(raw)
                if s is not None and len(s) > 10:
                    s.index = pd.to_datetime(s.index, utc=True)
                    base_data[(key, col)] = s.rename(col)
            except Exception as e:
                _log(f"  macro {ticker} {interval}: WARN {e}")

    # Construir DataFrames por TF
    for tf in tfs:
        base = '1h' if tf in ('1h', '4h') else '1d'
        parts = {}
        for col in ('dxy', 'yield_10y'):
            s = base_data.get((base, col))
            if s is not None:
                parts[col] = s
        if len(parts) < 2:
            _log(f"  macro {tf}: datos insuficientes")
            continue
        df = pd.DataFrame(parts).ffill(limit=5).dropna()
        if tf == '4h':
            df = df.resample('4h').last().ffill(limit=3).dropna()
        df.index.name = 'timestamp'
        result[tf] = df
        _log(f"  macro {tf}: {len(df):,} filas | dxy={df['dxy'].iloc[-1]:.2f} | 10y={df['yield_10y'].iloc[-1]:.2f}%")

    return result


if __name__ == '__main__':
    _log('=== MACRO FETCHER START ===')
    data = download_macro()
    for tf, df in data.items():
        out = Path(f'/opt/sigma/models/data_MACRO_{tf}_max.csv')
        df.to_csv(out)
        _log(f"  Guardado {out} ({len(df)} filas)")
    _log('=== MACRO FETCHER DONE ===')

"""Data fetcher stub — REEMPLAZAR con implementación real.

Status: SKELETON. No fetch real implementado.

TODO próxima sesión:
1. Conectar al data source apropiado (Yahoo Finance / IBKR delayed / Oanda)
2. Implementar fetch_ohlcv(symbol, tf, days) → DataFrame
3. Cachear en /opt/sigma/motors/{motor}/data/cache_*.pkl
4. Manejar timezones (NYSE 9:30-16:00 ET, holidays, etc.)
5. Filtrar fines de semana y holidays
"""
import logging

logger = logging.getLogger(__name__)


def fetch_ohlcv(symbol, timeframe, days=365):
    """Stub — devuelve None hasta que se implemente."""
    logger.warning(f"data_fetcher STUB called: {symbol} {timeframe} {days}d")
    return None


if __name__ == "__main__":
    print("STUB — implementar fetch_ohlcv() para empezar a operar este motor")

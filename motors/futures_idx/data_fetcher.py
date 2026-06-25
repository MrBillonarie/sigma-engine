"""Data fetcher stub -- REEMPLAZAR con implementacion real.

Status: SKELETON. No fetch real implementado.

TODO proxima sesion:
1. Conectar via ib_async (NO ib_insync, sin mantenimiento desde 2024)
2. Implementar fetch_ohlcv(symbol, tf, days) -> DataFrame para contratos CME
3. Manejar rollover de contrato (vencimientos trimestrales)
4. Cachear en /opt/sigma/motors/futures_idx/data/cache_*.pkl
"""
import logging

logger = logging.getLogger(__name__)


def fetch_ohlcv(symbol, timeframe, days=365):
    """Stub -- devuelve None hasta que se implemente."""
    logger.warning(f"data_fetcher STUB called: {symbol} {timeframe} {days}d")
    return None


if __name__ == "__main__":
    print("STUB -- implementar fetch_ohlcv() para empezar a operar este motor")

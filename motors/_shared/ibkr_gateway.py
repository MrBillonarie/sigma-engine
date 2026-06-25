"""Auth/gateway compartido para los 3 sub-universos IBKR
(stocks_us, futures_idx, forex) -- todos usan la MISMA cuenta IBKR,
por lo tanto el mismo gateway y la misma sesion autenticada.

Status: SKELETON. No implementado.

HALLAZGO CRITICO (investigado 2026-06-24, ver memoria
project_motor3_ibkr_consolidado o equivalente):

- IBKR retail NO tiene API key estatica como Binance. Las cuentas retail
  estan obligadas a 2FA via celular para autenticar, sesion expira a las
  24h (antes si hay mantenimiento), y hay que mandar /tickle cada <5 min
  para no perder la sesion.
- OAuth 1.0a (que evitaria el 2FA diario, como hace una API key de
  Binance) esta reservado a cuentas INSTITUCIONALES. No esta disponible
  para cuentas retail individuales.
- Salida conocida: librerias comunitarias IBeam / ibind automatizan el
  login del Client Portal Gateway y pueden interceptar 2FA por SMS, pero
  no son 100% confiables -- hay dias que van a necesitar intervencion
  manual.
- Libreria de trading: usar ib_async (sucesor activo de ib_insync, que
  quedo sin mantenimiento tras la muerte de su autor en 2024).

REGLA antes de pasar cualquier sub-universo IBKR a PAPER:
correr este gateway desnudo (IBeam/ibind) durante semanas en la cuenta
paper de IBKR y confirmar que la sesion sobrevive sin intervencion
humana. Si no sobrevive, el diseno debe asumir "alguien revisa el login
1 vez al dia" como parte del sistema, no como bug a eliminar -- no
prometer automatizacion 100% zero-touch como la de Binance hasta que
esto este probado.

TODO proxima sesion:
1. Instalar ib_async + ibeam (o ibind) en entorno de pruebas
2. Conectar contra cuenta PAPER de IBKR (gratis, misma API)
3. Medir cuantos dias seguidos aguanta sin intervencion manual
4. Decidir handler de 2FA (SMS / push / manual) segun lo que de la cuenta real
"""
import logging

logger = logging.getLogger(__name__)


def get_connection():
    """Stub -- devuelve None hasta que se implemente con ib_async."""
    logger.warning("ibkr_gateway STUB called -- no hay conexion real implementada")
    return None


if __name__ == "__main__":
    print("STUB -- implementar get_connection() via ib_async antes de usar este motor")

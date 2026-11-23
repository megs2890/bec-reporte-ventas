"""Configuracion leida de variables de entorno."""
import os
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Panama")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


DATA_DIR = env("DATA_DIR", "./data_local")
DB_PATH = os.path.join(DATA_DIR, "bec.db")
SEED_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "seed.json.gz")

IFX_URL = env("IFX_URL", "https://app.interfuerza.com/api/v4/")
# Estados de factura que no cuentan como venta
IFX_EXCLUDE_STATUS = {s.strip().upper() for s in env(
    "IFX_EXCLUDE_STATUS", "ANULADA,ANULADO,VOID,VOIDED,CANCELADA,CANCELADO,CANCELLED,BORRADOR,DRAFT").split(",") if s.strip()}
# Pausa entre llamadas: el API permite 20 llamadas cada 10 s y bloquea la IP una hora si se excede
IFX_PAUSE = float(env("IFX_PAUSE", "0.8"))

SYNC_HOUR = _int("SYNC_HOUR", 7)
SYNC_MINUTE = _int("SYNC_MINUTE", 0)
SYNC_DAYS_BACK = _int("SYNC_DAYS_BACK", 5)

PUBLIC_URL = env("PUBLIC_URL").rstrip("/")
EMAIL_PROVIDER = env("EMAIL_PROVIDER", "resend").lower()
EMAIL_FROM = env("EMAIL_FROM")
EMAIL_TO = [e.strip() for e in env("EMAIL_TO").split(",") if e.strip()]
# A quien avisar si la actualizacion falla, y a quien va el correo de prueba (no a los directores)
EMAIL_AVISOS = [e.strip() for e in env("EMAIL_AVISOS").split(",") if e.strip()]

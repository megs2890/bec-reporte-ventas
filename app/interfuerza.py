"""Cliente del API v4 de InterFuerza.

Documentacion: https://help.interfuerza.com/docs/api_v4-endpoints-tipo-get
- Todas las llamadas van a IFX_URL con el encabezado X-IFX-Token y un cuerpo JSON {class, action, ...}.
- Las listas devuelven 25 registros por pagina.
- Limite: 20 llamadas cada 10 segundos; si se excede, InterFuerza bloquea la IP por una hora.
"""
import time

import httpx

from . import config


class InterfuerzaError(RuntimeError):
    pass


class Interfuerza:
    def __init__(self, token: str, url: str | None = None, pause: float | None = None, client=None):
        if not token:
            raise InterfuerzaError("Falta IFX_TOKEN")
        self.url = url or config.IFX_URL
        self.pause = config.IFX_PAUSE if pause is None else pause
        self._last = 0.0
        self._client = client or httpx.Client(timeout=60, headers={"X-IFX-Token": token})

    def call(self, body: dict) -> dict:
        wait = self.pause - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        r = self._client.post(self.url, json=body)
        if r.status_code >= 400:
            raise InterfuerzaError(f"InterFuerza respondio HTTP {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
        except ValueError as e:
            raise InterfuerzaError(f"Respuesta no es JSON: {r.text[:300]}") from e
        if isinstance(data, dict) and (data.get("error") or data.get("Error")):
            raise InterfuerzaError(f"InterFuerza devolvio error: {str(data)[:300]}")
        return data

    @staticmethod
    def _records(data) -> list:
        """La clave de la lista varia segun la accion (la doc de 'invoices' la muestra como 'quotes')."""
        if isinstance(data, list):
            return data
        best = []
        for k, v in (data or {}).items():
            if k in ("class", "action"):
                continue
            if isinstance(v, list) and len(v) >= len(best) and all(isinstance(i, dict) for i in v):
                best = v
        return best

    def documents(self, action: str, d_from: str, d_to: str, max_pages: int = 5000):
        """Recorre todas las paginas de 'invoices' o 'credit-notes' en un rango de fechas."""
        seen_first = None
        for page in range(1, max_pages + 1):
            # Probado contra el API real (nov 2026): el filtro por fecha que funciona es
            # "filters" sobre el campo Date; "timestamp" como lista devuelve cero registros.
            data = self.call({"class": "GET", "action": action, "page": str(page),
                              "filters": [{"field": "Date", "type": ">=", "value": d_from},
                                          {"field": "Date", "type": "<=", "value": d_to}]})
            recs = self._records(data)
            if not recs:
                return
            first = str(recs[0])[:400]
            if page > 1 and first == seen_first:
                # el API ignoro 'page' y repite la misma pagina: evitar un bucle
                return
            if page == 1:
                seen_first = first
            for r in recs:
                yield r
            if len(recs) < 25:
                return
        raise InterfuerzaError(f"'{action}' supero {max_pages} paginas; se detuvo por seguridad")

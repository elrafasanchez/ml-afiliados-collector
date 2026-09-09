"""Cliente de la API oficial de Dentalink.

A diferencia de Mercado Libre, aquí sí existe un mecanismo soportado: token
durable emitido por la cuenta ADMIN en Configuración → API. No hay cookies, no
hay sesión que caduque y no hace falta navegador. El token solo deja de servir
si alguien lo revoca.

Documentación: https://api.dentalink.healthatom.com/docs/
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any, Callable, Iterator

from .errors import AuthError, SchemaError, TransientError

BASE_URL = "https://api.dentalink.healthatom.com/api/v1"

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_AUTH_STATUSES = {401, 403}

#: Techo de páginas por recurso. Un cambio en la paginación no debe convertirse
#: en un bucle infinito contra un servicio de terceros.
_MAX_PAGES = 200


class DentalinkAPI:
    """Lee recursos de Dentalink con paginación por cursor.

    :param token: token de la API, emitido en el panel de administración.
    :param min_interval: segundos mínimos entre peticiones. La documentación no
        publica límites de tasa, así que el recolector se autolimita en lugar de
        descubrirlos a base de errores.
    """

    def __init__(
        self,
        token: str,
        *,
        max_attempts: int = 4,
        min_interval: float = 0.35,
        timeout: float = 45.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        cleaned = (token or "").strip()
        if not cleaned:
            raise AuthError("No se proporcionó el token de la API de Dentalink.")
        self._token = cleaned
        self._max_attempts = max_attempts
        self._min_interval = min_interval
        self._timeout = timeout
        self._sleep = sleep
        self._last_request_at = 0.0
        self.requests_made = 0

    # ---------------------------------------------------------------- público

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Una sola página de un recurso."""
        query = urllib.parse.urlencode(params or {})
        url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
        return self._request(url)

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_pages: int = _MAX_PAGES,
    ) -> Iterator[dict[str, Any]]:
        """Recorre un recurso completo siguiendo ``links.next``.

        Dentalink pagina por cursor: la respuesta trae la URL siguiente ya
        construida. Seguirla es más seguro que recomponerla, porque el cursor va
        codificado y su formato no está garantizado.
        """
        url: str | None = None
        page = 0

        while page < max_pages:
            payload = self._request(url) if url else self.get(path, params=params)
            data = payload.get("data")
            if not isinstance(data, list):
                raise SchemaError(f"{path} no devolvió una lista en `data`.")
            for row in data:
                if not isinstance(row, dict):
                    raise SchemaError(f"{path} devolvió un elemento que no es objeto.")
                yield row

            links = payload.get("links") or {}
            nxt = links.get("next") if isinstance(links, dict) else None
            if not nxt or not data:
                return
            url = nxt
            page += 1

        raise SchemaError(
            f"{path} superó {max_pages} páginas; la paginación no está terminando."
        )

    def collect(self, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.paginate(path, **kwargs))

    # ------------------------------------------------------------- utilidades

    @staticmethod
    def date_filter(field: str, start: date, end: date) -> dict[str, str]:
        """Filtro ``q`` por rango de fechas, inclusivo en ambos extremos."""
        return {
            "q": json.dumps(
                {field: {"gte": start.isoformat(), "lte": end.isoformat()}},
                separators=(",", ":"),
            )
        }

    # ---------------------------------------------------------------- interno

    def _request(self, url: str) -> dict[str, Any]:
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            self._throttle()
            try:
                return self._open(url)
            except AuthError:
                raise
            except TransientError as error:
                last_error = error
                if attempt == self._max_attempts:
                    break
                self._sleep(self._backoff(attempt))

        raise last_error or TransientError("Dentalink no respondió.")

    def _open(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Token {self._token}",
                "Accept": "application/json",
                "User-Agent": "dental-amigo-collector/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
            self.requests_made += 1
        except urllib.error.HTTPError as error:
            if error.code in _AUTH_STATUSES:
                raise AuthError(
                    f"Dentalink rechazó el token (HTTP {error.code}). "
                    "Hay que regenerarlo en Configuración → API."
                ) from None
            if error.code in _RETRY_STATUSES:
                raise TransientError(f"Dentalink respondió HTTP {error.code}.") from None
            raise SchemaError(f"Dentalink respondió HTTP {error.code}.") from None
        except urllib.error.URLError as error:
            raise TransientError(f"No se pudo alcanzar Dentalink: {error.reason}") from None
        except TimeoutError:
            raise TransientError("Dentalink agotó el tiempo de espera.") from None

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SchemaError("Dentalink devolvió algo que no es JSON.") from error
        if not isinstance(payload, dict):
            raise SchemaError("Dentalink devolvió una respuesta que no es un objeto.")
        return payload

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self._min_interval:
            self._sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Retroceso exponencial con dispersión."""
        return min(30.0, (2 ** attempt) * 0.6) + random.uniform(0, 0.4)

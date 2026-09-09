"""Cliente HTTP contra el dashboard de Afiliados de Mercado Libre.

Mercado Libre no publica una API de afiliados ni un mecanismo OAuth para estos
datos. La única credencial disponible es la cookie de sesión del navegador, que
se recibe por variable de entorno y nunca se registra ni se imprime.

Esta capa es la única que conoce la red y las credenciales.
"""

from __future__ import annotations

import gzip
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import date
from typing import Any, Callable

from .errors import AuthenticationError, SchemaError, TransientSourceError
from .timeframe import filter_time_range

_HOST = "https://www.mercadolibre.com.mx"
_DASHBOARD_PATH = "/afiliados/dashboard"
_SALES_PATH = "/affiliate-program/api/dashboard/sales/general"

# Un navegador real; la fuente sirve marcado distinto a clientes desconocidos.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_AUTH_STATUSES = {401, 403}


class MercadoLibreSource:
    """Lee documentos del panel de Afiliados para rangos de fechas arbitrarios.

    :param cookie: cabecera ``Cookie`` completa de una sesión válida.
    :param max_attempts: intentos por petición antes de rendirse.
    :param min_interval: segundos mínimos entre peticiones, para no saturar la
        fuente cuando se rellena el histórico.
    """

    def __init__(
        self,
        cookie: str,
        *,
        max_attempts: int = 4,
        min_interval: float = 0.7,
        timeout: float = 45.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        cleaned = (cookie or "").strip()
        if not cleaned:
            raise AuthenticationError(
                "No se proporcionó la cookie de sesión de Mercado Libre."
            )
        self._cookie = cleaned
        self._max_attempts = max_attempts
        self._min_interval = min_interval
        self._timeout = timeout
        self._sleep = sleep
        self._last_request_at = 0.0

    # ---------------------------------------------------------------- público

    def fetch_dashboard_html(self, start: date, end_exclusive: date) -> str:
        """Documento hidratado del panel para el rango dado."""
        query = urllib.parse.urlencode(
            {"filter_time_range": filter_time_range(start, end_exclusive)}
        )
        return self._request(f"{_HOST}{_DASHBOARD_PATH}?{query}", expect_json=False)

    def fetch_sales_page(
        self,
        start: date,
        end_exclusive: date,
        *,
        page: int = 1,
        items_per_page: int = 50,
    ) -> dict[str, Any]:
        """Una página del detalle transaccional del rango dado."""
        query = urllib.parse.urlencode(
            {
                "filter_time_range": filter_time_range(start, end_exclusive),
                "items_per_page": items_per_page,
                "order_by": "ord_date_created",
                "page": page,
                "sort": "desc",
                "type": "GENERAL",
                "_t": int(time.time() * 1000),
            }
        )
        payload = self._request(f"{_HOST}{_SALES_PATH}?{query}", expect_json=True)
        if not isinstance(payload, dict):
            raise SchemaError("El detalle de ventas no devolvió un objeto.")
        return payload

    def fetch_all_sales(
        self,
        start: date,
        end_exclusive: date,
        *,
        items_per_page: int = 50,
        max_pages: int = 200,
    ) -> list[dict[str, Any]]:
        """Recorre la paginación completa del detalle transaccional.

        Se detiene por total declarado y por página vacía, y además impone un
        techo de páginas para que un cambio en la fuente no produzca un bucle
        infinito.
        """
        collected: list[dict[str, Any]] = []
        page = 1
        total = None

        while page <= max_pages:
            payload = self.fetch_sales_page(
                start, end_exclusive, page=page, items_per_page=items_per_page
            )
            items = payload.get("item_list")
            if not isinstance(items, list):
                raise SchemaError("El detalle de ventas no expuso `item_list`.")
            collected.extend(items)

            if total is None:
                declared = payload.get("total_results")
                total = declared if isinstance(declared, int) else None
            if not items:
                break
            if total is not None and len(collected) >= total:
                break
            page += 1

        if total is not None and len(collected) != total:
            raise SchemaError(
                f"El detalle transaccional devolvió {len(collected)} filas "
                f"pero declaró {total}."
            )
        return collected

    # ---------------------------------------------------------------- interno

    def _request(self, url: str, *, expect_json: bool) -> Any:
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            self._throttle()
            try:
                body = self._open(url, expect_json=expect_json)
            except AuthenticationError:
                raise
            except TransientSourceError as error:
                last_error = error
                if attempt == self._max_attempts:
                    break
                self._sleep(self._backoff_delay(attempt))
                continue

            if not expect_json:
                return body
            try:
                return json.loads(body)
            except json.JSONDecodeError as error:
                raise SchemaError(
                    "La fuente respondió algo que no es JSON en un extremo JSON."
                ) from error

        raise last_error or TransientSourceError("La fuente no respondió.")

    def _open(self, url: str, *, expect_json: bool) -> str:
        request = urllib.request.Request(
            url,
            headers={
                "Cookie": self._cookie,
                "User-Agent": _USER_AGENT,
                "Accept": (
                    "application/json, text/plain, */*"
                    if expect_json
                    else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "es-MX,es;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Cache-Control": "no-cache",
            },
            method="GET",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
                encoding = (response.headers.get("Content-Encoding") or "").lower()
                text = _decode(raw, encoding)
                final_url = response.geturl()
        except urllib.error.HTTPError as error:
            if error.code in _AUTH_STATUSES:
                raise AuthenticationError(
                    f"Mercado Libre rechazó la sesión (HTTP {error.code})."
                ) from error
            if error.code in _RETRY_STATUSES:
                raise TransientSourceError(
                    f"La fuente respondió HTTP {error.code}."
                ) from error
            raise SchemaError(f"La fuente respondió HTTP {error.code}.") from error
        except urllib.error.URLError as error:
            raise TransientSourceError(f"No se pudo alcanzar la fuente: {error.reason}") from error
        except TimeoutError as error:
            raise TransientSourceError("La fuente agotó el tiempo de espera.") from error

        if "/login" in final_url or "/jms/" in final_url:
            raise AuthenticationError(
                "Mercado Libre redirigió al acceso: la sesión caducó."
            )
        return text

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self._min_interval:
            self._sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """Retroceso exponencial con dispersión, para no reintentar en bloque."""
        return min(30.0, (2 ** attempt) * 0.75) + random.uniform(0, 0.5)


def _decode(raw: bytes, encoding: str) -> str:
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode("utf-8", errors="replace")

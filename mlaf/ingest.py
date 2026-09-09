"""Publicación de los datos recolectados en la API del dashboard.

El token de ingesta se recibe por variable de entorno y nunca se escribe en los
registros. Los mensajes de error se depuran antes de propagarse para que una
traza publicada en los registros de CI no filtre credenciales.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .errors import AuthenticationError, IngestError

_SECRET_PATTERN = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)


def redact(message: str) -> str:
    """Elimina credenciales de un texto antes de registrarlo."""
    return _SECRET_PATTERN.sub(r"\1[oculto]", message)


class DashboardIngest:
    """Cliente del extremo de ingesta del dashboard."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        max_attempts: int = 3,
        timeout: float = 45.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not endpoint.strip():
            raise IngestError("No se configuró la URL de ingesta.")
        if not token.strip():
            raise IngestError("No se configuró el token de ingesta.")
        self._endpoint = endpoint.strip()
        self._token = token.strip()
        self._max_attempts = max_attempts
        self._timeout = timeout
        self._sleep = sleep

    def publish(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Envía un lote y devuelve la respuesta del servidor.

        La operación es idempotente por contrato: el servidor decide mediante
        claves estables si cada registro es alta o actualización, así que
        reintentar tras un fallo de red no duplica nada.
        """
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._post(body)
            except AuthenticationError:
                raise
            except IngestError as error:
                last_error = error
                if attempt == self._max_attempts:
                    break
                self._sleep(min(20.0, 2 ** attempt))

        raise last_error or IngestError("La ingesta no pudo completarse.")

    def _post(self, body: bytes) -> dict[str, Any]:
        request = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "mlaf-collector/2.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            detail = _error_detail(error)
            if error.code == 401:
                raise AuthenticationError(
                    f"El destino rechazó el token de ingesta (401). {detail}"
                ) from error
            if error.code in {400, 409, 413, 415, 422}:
                # Reintentar no cambiaría nada: el lote es inaceptable tal cual.
                raise IngestError(
                    f"El destino rechazó el lote (HTTP {error.code}). {detail}"
                ) from None
            raise IngestError(f"El destino falló con HTTP {error.code}. {detail}") from None
        except urllib.error.URLError as error:
            raise IngestError(f"No se pudo alcanzar el destino: {redact(str(error.reason))}") from None

        if status not in {200, 201}:
            raise IngestError(f"El destino devolvió HTTP {status}.")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise IngestError("El destino devolvió una respuesta ilegible.") from error


def _error_detail(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read())
    except Exception:
        return ""
    message = payload.get("error") if isinstance(payload, dict) else None
    return redact(str(message)) if message else ""

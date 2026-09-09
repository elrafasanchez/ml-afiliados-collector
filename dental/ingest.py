"""Publicación de la instantánea en el dashboard.

El token de ingesta se recibe por entorno y nunca se escribe en los registros.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .errors import AuthError, IngestError

_SECRET = re.compile(r"(Bearer|Token)\s+\S+", re.IGNORECASE)


def redact(message: str) -> str:
    return _SECRET.sub(r"\1 [oculto]", message)


class DentalIngest:
    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        max_attempts: int = 3,
        timeout: float = 60.0,
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
        """Envía la instantánea. La ruta es idempotente por `id`."""
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        last: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._post(body)
            except AuthError:
                raise
            except IngestError as error:
                last = error
                if attempt == self._max_attempts:
                    break
                self._sleep(min(20.0, 2 ** attempt))

        raise last or IngestError("La ingesta no pudo completarse.")

    def _post(self, body: bytes) -> dict[str, Any]:
        request = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "dental-amigo-collector/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw, status = response.read(), response.status
        except urllib.error.HTTPError as error:
            detail = _detail(error)
            if error.code in {401, 403}:
                raise AuthError(f"El destino rechazó el token de ingesta ({error.code}). {detail}") from None
            if error.code in {400, 409, 413, 415, 422}:
                # Reintentar no cambiaría nada: el lote es inaceptable tal cual.
                raise IngestError(f"El destino rechazó el lote (HTTP {error.code}). {detail}") from None
            raise IngestError(f"El destino falló con HTTP {error.code}. {detail}") from None
        except urllib.error.URLError as error:
            raise IngestError(f"No se pudo alcanzar el destino: {redact(str(error.reason))}") from None

        if status not in {200, 201}:
            raise IngestError(f"El destino devolvió HTTP {status}.")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise IngestError("El destino devolvió una respuesta ilegible.") from error


def _detail(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read())
    except Exception:
        return ""
    message = payload.get("error") if isinstance(payload, dict) else None
    return redact(str(message)) if message else ""

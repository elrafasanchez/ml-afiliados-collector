"""Configuración por entorno. Ningún secreto vive en el código ni en el repo."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .errors import CollectorError

ENV_COOKIE = "ML_SESSION_COOKIE"
ENV_INGEST_URL = "ML_INGEST_URL"
ENV_INGEST_TOKEN = "ML_INGEST_TOKEN"


@dataclass(frozen=True)
class Settings:
    cookie: str
    ingest_url: str
    ingest_token: str
    request_interval: float
    dry_run: bool

    @property
    def redacted(self) -> dict[str, str]:
        """Vista segura para registrar al arrancar."""
        return {
            "ingest_url": self.ingest_url,
            "cookie": f"<{len(self.cookie)} caracteres>",
            "ingest_token": "<configurado>" if self.ingest_token else "<ausente>",
            "dry_run": str(self.dry_run),
        }


def load_settings(*, require_ingest: bool = True) -> Settings:
    cookie = os.environ.get(ENV_COOKIE, "").strip()
    if not cookie:
        raise CollectorError(
            f"Falta {ENV_COOKIE}. Es la cabecera Cookie de una sesión válida de "
            "Mercado Libre; debe vivir en el almacén de secretos, nunca en el repo."
        )

    ingest_url = os.environ.get(ENV_INGEST_URL, "").strip()
    ingest_token = os.environ.get(ENV_INGEST_TOKEN, "").strip()
    dry_run = os.environ.get("ML_DRY_RUN", "").strip().lower() in {"1", "true", "yes"}

    if require_ingest and not dry_run:
        missing = [
            name
            for name, value in ((ENV_INGEST_URL, ingest_url), (ENV_INGEST_TOKEN, ingest_token))
            if not value
        ]
        if missing:
            raise CollectorError("Faltan variables de entorno: " + ", ".join(missing))

    try:
        interval = float(os.environ.get("ML_REQUEST_INTERVAL", "0.7"))
    except ValueError:
        interval = 0.7

    return Settings(
        cookie=cookie,
        ingest_url=ingest_url,
        ingest_token=ingest_token,
        request_interval=max(0.0, interval),
        dry_run=dry_run,
    )

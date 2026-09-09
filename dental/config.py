"""Configuración por entorno. Ningún secreto vive en el código ni en el repo."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .errors import DentalError

ENV_TOKEN = "DENTALINK_API_TOKEN"
ENV_INGEST_URL = "DENTAL_INGEST_URL"
ENV_INGEST_TOKEN = "DENTAL_INGEST_TOKEN"


@dataclass(frozen=True)
class Settings:
    dentalink_token: str
    ingest_url: str
    ingest_token: str
    dry_run: bool

    @property
    def redacted(self) -> dict[str, str]:
        return {
            "ingest_url": self.ingest_url or "«sin configurar»",
            "dentalink_token": f"<{len(self.dentalink_token)} caracteres>",
            "ingest_token": "<configurado>" if self.ingest_token else "<ausente>",
            "dry_run": str(self.dry_run),
        }


def load_settings() -> Settings:
    token = os.environ.get(ENV_TOKEN, "").strip()
    if not token:
        raise DentalError(
            f"Falta {ENV_TOKEN}. Es el token de la API de Dentalink, que genera la "
            "cuenta ADMIN en Configuración → API. Debe vivir en los secretos del "
            "repositorio, nunca en el código."
        )

    dry_run = os.environ.get("DENTAL_DRY_RUN", "").strip().lower() in {"1", "true", "yes"}
    ingest_url = os.environ.get(ENV_INGEST_URL, "").strip()
    ingest_token = os.environ.get(ENV_INGEST_TOKEN, "").strip()

    if not dry_run:
        missing = [n for n, v in ((ENV_INGEST_URL, ingest_url), (ENV_INGEST_TOKEN, ingest_token)) if not v]
        if missing:
            raise DentalError("Faltan variables de entorno: " + ", ".join(missing))

    return Settings(
        dentalink_token=token,
        ingest_url=ingest_url,
        ingest_token=ingest_token,
        dry_run=dry_run,
    )

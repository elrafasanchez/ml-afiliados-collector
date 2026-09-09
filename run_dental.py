#!/usr/bin/env python3
"""Punto de entrada del recolector de Dental Amigo.

    python3 run_dental.py operations
    python3 run_dental.py month
    python3 run_dental.py discover        # reconocimiento de la API, sin publicar

Códigos de salida:
    0  el trabajo terminó y se publicó
    1  fallo recuperable (red, 5xx)
    2  fallo de autenticación: hay que regenerar el token de Dentalink
    3  fallo de datos: la API cambió de forma o no superó la validación
"""

from __future__ import annotations

import argparse
import json
import sys

from dental.clinic_time import iso_utc
from dental.config import load_settings
from dental.dentalink import DentalinkAPI
from dental.discover import run as run_discovery
from dental.errors import (
    AuthError,
    DentalError,
    IngestError,
    SchemaError,
    TransientError,
    ValidationError,
)
from dental.ingest import DentalIngest
from dental.jobs import DentalCollector, validate_snapshot
from dental.logging_setup import log

EXIT_OK, EXIT_TRANSIENT, EXIT_AUTH, EXIT_DATA = 0, 1, 2, 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("job", choices=["operations", "month", "discover"])
    parser.add_argument("--days-back", type=int, default=7, help="Ventana del reconocimiento.")
    arguments = parser.parse_args(argv)

    try:
        settings = load_settings()
    except DentalError as error:
        log("configuration_failed", error=str(error))
        return EXIT_DATA

    log("collector_started", job=arguments.job, **settings.redacted)
    api = DentalinkAPI(settings.dentalink_token)

    # El reconocimiento no publica nada: solo describe la forma de la API.
    if arguments.job == "discover":
        try:
            print(run_discovery(api, days_back=arguments.days_back))
        except AuthError as error:
            log("authentication_failed", error=str(error))
            return EXIT_AUTH
        return EXIT_OK

    collector = DentalCollector(api)
    try:
        result = collector.run_operations() if arguments.job == "operations" else collector.run_month()
        validate_snapshot(result)
    except AuthError as error:
        log("authentication_failed", error=str(error), action="regenerar DENTALINK_API_TOKEN")
        return EXIT_AUTH
    except TransientError as error:
        log("source_unavailable", error=str(error))
        return EXIT_TRANSIENT
    except (SchemaError, ValidationError) as error:
        log("data_rejected", error=str(error))
        return EXIT_DATA
    except DentalError as error:
        log("collection_failed", error=str(error))
        return EXIT_DATA

    log("collection_finished", job=result.job, requests=result.requests_made, **result.counts())
    for warning in result.warnings:
        log("warning", detail=warning)
    if result.reconciliation:
        log("reconciliation", **result.reconciliation)

    payload = result.snapshot.as_payload(iso_utc())

    if settings.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=1)[:8000])
        log("dry_run_complete", note="no se publicó nada")
        return EXIT_OK

    try:
        response = DentalIngest(settings.ingest_url, settings.ingest_token).publish(payload)
    except AuthError as error:
        log("ingest_unauthorized", error=str(error))
        return EXIT_AUTH
    except IngestError as error:
        log("ingest_failed", error=str(error))
        return EXIT_TRANSIENT

    log("ingest_succeeded", **{k: v for k, v in response.items() if k != "error"})
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Punto de entrada del recolector.

Uso:

    python3 run_sync.py today
    python3 run_sync.py recent --days 7
    python3 run_sync.py backfill --start 2026-03-13 --end 2026-09-09
    python3 run_sync.py audit --start 2026-03-13 --end 2026-09-09

Códigos de salida:
    0  el trabajo terminó y se publicó
    1  fallo recuperable (red, fuente temporalmente caída)
    2  fallo de autenticación: hace falta renovar la sesión
    3  fallo de datos: la fuente cambió o no superó la validación
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from mlaf.config import load_settings
from mlaf.errors import (
    AuthenticationError,
    CollectorError,
    IngestError,
    SchemaError,
    TransientSourceError,
    ValidationError,
)
from mlaf.ingest import DashboardIngest
from mlaf.jobs import Collector
from mlaf.logging_setup import log
from mlaf.source import MercadoLibreSource
from mlaf.timeframe import today_in_hermosillo

EXIT_OK = 0
EXIT_TRANSIENT = 1
EXIT_AUTH = 2
EXIT_DATA = 3


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "job", choices=["today", "recent", "backfill", "audit"], help="Trabajo a ejecutar."
    )
    parser.add_argument("--days", type=int, default=7, help="Días hacia atrás para `recent`.")
    parser.add_argument("--start", type=date.fromisoformat, help="Inicio inclusivo (YYYY-MM-DD).")
    parser.add_argument("--end", type=date.fromisoformat, help="Fin exclusivo (YYYY-MM-DD).")
    return parser.parse_args(argv)


def resolve_span(arguments: argparse.Namespace) -> tuple[date, date]:
    """Resuelve el rango sin codificar fechas: por omisión, los últimos 180 días."""
    end = arguments.end or (today_in_hermosillo() + timedelta(days=1))
    start = arguments.start or (end - timedelta(days=180))
    if start >= end:
        raise CollectorError(f"El rango {start}..{end} está invertido o vacío.")
    return start, end


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)

    try:
        settings = load_settings()
    except CollectorError as error:
        log("configuration_failed", error=str(error))
        return EXIT_DATA

    log("collector_started", job=arguments.job, **settings.redacted)

    source = MercadoLibreSource(settings.cookie, min_interval=settings.request_interval)
    collector = Collector(source)

    try:
        if arguments.job == "today":
            result = collector.run_today()
        elif arguments.job == "recent":
            result = collector.run_recent(days_back=arguments.days)
        elif arguments.job == "backfill":
            start, end = resolve_span(arguments)
            result = collector.run_backfill(start, end)
        else:
            start, end = resolve_span(arguments)
            result = collector.run_audit(start, end)
    except AuthenticationError as error:
        log("authentication_failed", error=str(error), action="renovar ML_SESSION_COOKIE")
        return EXIT_AUTH
    except TransientSourceError as error:
        log("source_unavailable", error=str(error))
        return EXIT_TRANSIENT
    except (SchemaError, ValidationError) as error:
        log("data_rejected", error=str(error))
        return EXIT_DATA
    except CollectorError as error:
        log("collection_failed", error=str(error))
        return EXIT_DATA

    payload = result.as_payload()
    log(
        "collection_finished",
        job=result.job,
        days=len(result.days),
        sales=len(result.sales),
        requests=result.requests_made,
        warnings=len(result.warnings),
    )
    for warning in result.warnings:
        log("warning", detail=warning)
    if result.reconciliation:
        log("reconciliation", **result.reconciliation)

    if not result.days and arguments.job != "recent":
        log("nothing_collected", note="no se publica un lote vacío")
        return EXIT_DATA

    if settings.dry_run:
        import json

        print(json.dumps(payload, ensure_ascii=False, indent=2))
        log("dry_run_complete", note="no se publicó nada")
        return EXIT_OK

    try:
        response = DashboardIngest(settings.ingest_url, settings.ingest_token).publish(payload)
    except AuthenticationError as error:
        log("ingest_unauthorized", error=str(error))
        return EXIT_AUTH
    except IngestError as error:
        log("ingest_failed", error=str(error))
        return EXIT_TRANSIENT

    log(
        "ingest_succeeded",
        daysWritten=response.get("daysWritten"),
        salesWritten=response.get("salesWritten"),
        revisions=response.get("revisions"),
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Importa un histórico ya cosechado hacia la API del tablero.

Existe para cargar el histórico de una sola vez sin esperar a que el flujo
programado recorra 180 días. El archivo de entrada contiene los bloques
`generalKpis` tal cual los devolvió Mercado Libre, y aquí pasan por **el mismo**
código de normalización y validación que usa el recolector en producción: si un
día no supera las invariantes, no se publica.

    python3 tools/importar_historico.py historico.json [--dry-run] [--chunk 30]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mlaf.config import load_settings
from mlaf.errors import CollectorError
from mlaf.ingest import DashboardIngest
from mlaf.logging_setup import log
from mlaf.normalize import normalize_daily_metrics
from mlaf.timeframe import utc_now_iso
from mlaf.validate import validate_daily_metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archivo", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--chunk", type=int, default=30, help="Días por lote.")
    arguments = parser.parse_args()

    raw = json.loads(arguments.archivo.read_text())
    rows = raw.values() if isinstance(raw, dict) else raw

    accepted, rejected = [], []
    for row in rows:
        date_key = row.get("d") or row.get("dateKey")
        if "kpis" not in row:
            rejected.append((date_key, row.get("error", "sin KPIs")))
            continue
        try:
            metrics = normalize_daily_metrics(row["kpis"], date_key)
            validate_daily_metrics(metrics)
            accepted.append(metrics)
        except CollectorError as error:
            rejected.append((date_key, str(error)))

    accepted.sort(key=lambda day: day.date_key)
    log("historico_preparado", aceptados=len(accepted), rechazados=len(rejected))
    for date_key, reason in rejected[:20]:
        log("dia_rechazado", date=date_key, motivo=reason)

    if not accepted:
        log("nada_que_importar")
        return 1

    print(f"Rango: {accepted[0].date_key} .. {accepted[-1].date_key}")
    print(f"Ventas estimadas acumuladas: {sum(day.estimated_sales for day in accepted):,.2f}")
    print(f"Comisiones acumuladas:       {sum(day.total_commission for day in accepted):,.2f}")

    if arguments.dry_run:
        log("dry_run", nota="no se publicó nada")
        return 0

    settings = load_settings(require_ingest=True)
    ingest = DashboardIngest(settings.ingest_url, settings.ingest_token)

    written = 0
    for index in range(0, len(accepted), arguments.chunk):
        batch = accepted[index : index + arguments.chunk]
        payload = {
            "job": "backfill",
            "collectedAt": utc_now_iso(),
            "days": [day.as_payload() for day in batch],
            "run": {
                "startedAt": utc_now_iso(),
                "finishedAt": utc_now_iso(),
                "requestsMade": len(batch),
                "recordsRead": len(batch),
                "warnings": [],
            },
        }
        response = ingest.publish(payload)
        written += response.get("daysWritten", 0)
        log(
            "lote_publicado",
            desde=batch[0].date_key,
            hasta=batch[-1].date_key,
            escritos=response.get("daysWritten"),
            revisiones=response.get("revisions"),
            total=response.get("totalDailyRecords"),
        )

    log("importacion_completa", diasEscritos=written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

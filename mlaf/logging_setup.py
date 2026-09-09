"""Registro estructurado en una línea JSON por evento.

Los registros van a stderr para no contaminar la salida de datos, y todo texto
pasa por la depuración de credenciales antes de escribirse.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from .ingest import redact


def log(event: str, **fields: Any) -> None:
    record = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "event": event,
    }
    for key, value in fields.items():
        record[key] = redact(value) if isinstance(value, str) else value
    print(json.dumps(record, ensure_ascii=False), file=sys.stderr, flush=True)

"""Registro estructurado, una línea JSON por evento, con secretos depurados."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from .ingest import redact


def log(event: str, **fields: Any) -> None:
    record: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "event": event,
    }
    for key, value in fields.items():
        record[key] = redact(value) if isinstance(value, str) else value
    print(json.dumps(record, ensure_ascii=False), file=sys.stderr, flush=True)

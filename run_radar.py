"""Wake the radar inside the existing Site. No browser or new scheduler."""
from __future__ import annotations
import json
import os
import sys
from urllib.parse import urlsplit, urlunsplit
from mlaf.ingest import DashboardIngest


def radar_endpoint(ingest_url: str) -> str:
    parsed = urlsplit(ingest_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('ML_INGEST_URL must be an HTTPS Site URL')
    return urlunsplit((parsed.scheme, parsed.netloc, '/api/mercadolibre/radar/scan', '', ''))


def main() -> int:
    try:
        client = DashboardIngest(radar_endpoint(os.environ.get('ML_INGEST_URL', '')),
                                 os.environ.get('ML_INGEST_TOKEN', ''), timeout=240)
        result = client.publish({})
        # Log status only; never affiliate sales, credentials, or request bodies.
        print(json.dumps({k: result.get(k) for k in ('status', 'scanned', 'error', 'nextAt', 'skipped')}, ensure_ascii=False))
        return 1 if result.get('error') else 0
    except Exception:
        print('Radar unavailable; last valid data is preserved.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

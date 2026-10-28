"""Official payment synchronization with fixed-code failure reporting."""
from datetime import datetime, timezone
from urllib.parse import urlsplit
from mlaf.config import load_settings
from mlaf.source import MercadoLibreSource
from mlaf.ingest import DashboardIngest
from mlaf.finance import collect_finance
from mlaf.errors import AuthenticationError


def main():
    settings = load_settings()
    origin = urlsplit(settings.ingest_url)
    writer = DashboardIngest(f'{origin.scheme}://{origin.netloc}/api/mercadolibre/finance', settings.ingest_token)
    try:
        payload = collect_finance(MercadoLibreSource(settings.cookie, max_attempts=3, min_interval=max(1, settings.request_interval)))
        if not settings.dry_run:
            writer.publish(payload)
        print(f"Pagos oficiales leídos: {len(payload.get('payments') or [])}; incentivos: {len(payload.get('incentives') or [])}")
        failures = [payload.get('paymentsFailure'), payload.get('incentivesFailure')]
        if 'SESSION_EXPIRED' in failures:
            return 2
        if any(failures):
            return 1
    except Exception as exc:
        code = 'SESSION_EXPIRED' if isinstance(exc, AuthenticationError) else 'FINANCE_SYNC_FAILED'
        if not settings.dry_run:
            try:
                writer.publish({'failure': {'code': code, 'message': 'No se pudo actualizar el saldo oficial; se conserva el último dato válido.'},
                                'observedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')})
            except Exception:
                pass
        print(code)
        return 2 if code == 'SESSION_EXPIRED' else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Cloud-only video synchronization; never publishes invented zero balances."""
from urllib.parse import urlsplit
from mlaf.config import load_settings
from mlaf.source import MercadoLibreSource
from mlaf.ingest import DashboardIngest
from mlaf.videos import collect_videos
from mlaf.errors import AuthenticationError

def main():
    settings = load_settings()
    origin = urlsplit(settings.ingest_url)
    endpoint = f'{origin.scheme}://{origin.netloc}/api/mercadolibre/videos'
    writer = DashboardIngest(endpoint, settings.ingest_token)
    try:
        payload = collect_videos(MercadoLibreSource(settings.cookie, max_attempts=3, min_interval=max(1, settings.request_interval)))
        if not settings.dry_run:
            result = writer.publish(payload)
            print(f"Videos sincronizados: {len(payload['videos'])}; aceptado: {result.get('ok') is True}")
    except Exception as exc:
        code = 'SESSION_EXPIRED' if isinstance(exc, AuthenticationError) else 'VIDEO_SYNC_FAILED'
        if not settings.dry_run:
            try: writer.publish({'failure': code})
            except Exception: pass
        print(code)
        return 2 if code == 'SESSION_EXPIRED' else 1
    return 0

if __name__ == '__main__': raise SystemExit(main())

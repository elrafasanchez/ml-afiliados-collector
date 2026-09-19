"""Refresh rolling 180-day product detail using the established affiliate session.
Each bounded range is fully fetched/validated before the existing normalized sync
accepts it. No historical prices are used for live deal verification.
"""
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit
from mlaf.config import load_settings
from mlaf.source import MercadoLibreSource
from mlaf.jobs import Collector, CollectionResult
from mlaf.ingest import DashboardIngest
from mlaf.errors import SchemaError
from mlaf.timeframe import today_in_hermosillo, utc_now_iso

class CompleteSalesSource(MercadoLibreSource):
    def fetch_sales_page(self, *args, **kwargs):
        payload = super().fetch_sales_page(*args, **kwargs)
        if not isinstance(payload.get('total_results'), int) or payload['total_results'] < 0:
            raise SchemaError('Audience source omitted its declared result count; no range is replaced.')
        return payload

def main():
    settings = load_settings()
    collector = Collector(CompleteSalesSource(settings.cookie, min_interval=1.0))
    sync = DashboardIngest(settings.ingest_url, settings.ingest_token)
    origin = urlsplit(settings.ingest_url)
    radar = DashboardIngest(urlunsplit((origin.scheme, origin.netloc, '/api/mercadolibre/radar/ingest', '', '')), settings.ingest_token)
    end = today_in_hermosillo() + timedelta(days=1)
    start = end - timedelta(days=180)
    while start < end:
        stop = min(end, start + timedelta(days=7))
        started = utc_now_iso()
        sales = collector.read_sales(start, stop)
        result = CollectionResult(job='audit', started_at=started, sales=sales, sales_range=(start, stop))
        if not settings.dry_run:
            sync.publish(result.as_payload())
            radar.publish({'kind': 'coverage', 'start': start.isoformat(), 'end': stop.isoformat(), 'records': len(sales), 'sourceTimestamp': utc_now_iso()})
        print(f'Audience range {start}..{stop}: {len(sales)} validated records; dry_run={settings.dry_run}')
        start = stop

if __name__ == '__main__':
    main()

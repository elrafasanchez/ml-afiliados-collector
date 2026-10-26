"""Discover enrolled video campaigns using the existing authenticated source."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from .parse import extract_page_props
from .errors import SchemaError

HOST = 'https://www.mercadolibre.com.mx'
TZ = ZoneInfo('America/Hermosillo')

def source_time(value):
    if not isinstance(value, str):
        raise SchemaError('Video sin fecha de fuente.')
    try:
        # The source returns UTC without a suffix (16:04 UTC renders 10:04 Mexico).
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError as exc:
        raise SchemaError('Fecha de video inválida.') from exc

def cents(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0: raise ValueError()
        return int((amount * 100).quantize(Decimal('1')))
    except (ValueError, InvalidOperation):
        raise SchemaError('Ganancia de video inválida.')

def normalize_video(campaign, media, days):
    if not isinstance(media.get('id'), str) or not isinstance(media.get('views'), int) or media['views'] < 0:
        raise SchemaError('Identidad o vistas de video inválidas.')
    updated = source_time(media.get('last_sync_time'))
    if media.get('linked_date'):
        linked = source_time(media['linked_date'])
    else:
        # Event campaigns have no linked product/date. Their video creation
        # timestamp is the start displayed by ML (e.g. Eliot: Oct 24–Nov 23).
        created = media.get('create_time')
        if isinstance(created, bool) or not isinstance(created, (int, float)):
            raise SchemaError('Video sin inicio de monetización verificable.')
        try:
            linked = datetime.fromtimestamp(created / 1000, timezone.utc)
        except (ValueError, OverflowError, OSError) as exc:
            raise SchemaError('Inicio de monetización inválido.') from exc
        if linked.year < 2020 or linked > updated:
            raise SchemaError('Inicio de monetización fuera de rango.')
    products = media.get('linked_products') or []
    return {
        'id': media['id'], 'campaignId': campaign['id_campaign'],
        'campaignTitle': campaign['title_reward'].strip(),
        'campaignStatus': campaign['status'],
        'submissionEnd': campaign['end_date'],
        'product': products[0].get('product_name', '') if products else '',
        'thumbnail': media.get('url_thumbnail') or (products[0].get('thumbnail', '') if products else ''),
        'url': media.get('url_video') or '', 'account': media.get('user_name') or '',
        'network': media.get('social_network') or '', 'status': media.get('status') or 'UNKNOWN',
        'monetizing': media.get('monetization') is True,
        'views': media['views'], 'earningsCents': cents(media.get('earnings')),
        'linkedDate': linked.astimezone(TZ).date().isoformat(),
        'monetizationEnd': (linked.astimezone(TZ).date() + timedelta(days=days)).isoformat(),
        'sourceAt': updated.isoformat().replace('+00:00', 'Z'),
        'sourceMonth': updated.astimezone(TZ).strftime('%Y-%m'),
    }

def collect_videos(source, now=None):
    now = now or datetime.now(timezone.utc)
    props = extract_page_props(source._request(HOST + '/afiliados/influencers', expect_json=False))
    campaigns = props.get('influencerCampaignsData')
    if not isinstance(campaigns, list): raise SchemaError('Catálogo de campañas no disponible.')
    selected = []
    for c in campaigns:
        if not isinstance(c, dict) or not c.get('id_campaign'): raise SchemaError('Campaña inválida.')
        # Continue revisions after enrollment closes; do not import old lifetime earnings into this month.
        recent_end = c.get('end_date', '') >= (now.astimezone(TZ).date() - timedelta(days=60)).isoformat()
        if c.get('status') == 'ENROLLED' or (c.get('postulation') and recent_end): selected.append(c)
    if len(selected) > 50: raise SchemaError('Demasiadas campañas; revisión requerida.')
    videos = []
    for c in selected:
        detail = extract_page_props(source._request(HOST + '/afiliados/influencers/campaigns-detail?' + urlencode({'campaign_id':c['id_campaign']}), expect_json=False))
        payload = detail.get('videos')
        days = detail.get('monetizeElapsedDays')
        if not isinstance(days, int) or not 1 <= days <= 365 or not isinstance(payload, dict):
            raise SchemaError('Detalle de campaña incompleto.')
        items = payload.get('linked_media')
        total = payload.get('total_items')
        if not isinstance(items, list) or not isinstance(total, int): raise SchemaError('Videos incompletos.')
        page = 1
        while len(items) < total and page < 20:
            page += 1
            data = source._request(HOST + '/affiliate-program/api/dashboard/influencer/videos?' + urlencode({'campaign_id': c['id_campaign'], 'page': page, 'items_per_page': 100}), expect_json=True)
            more = data.get('linked_media')
            if not isinstance(more, list) or not more: break
            items.extend(more)
        if len(items) != total: raise SchemaError('Paginación de videos incompleta.')
        videos.extend(normalize_video(c, media, days) for media in items)
    if len({v['id'] for v in videos}) != len(videos): raise SchemaError('Video duplicado entre campañas.')
    return {'videos': videos, 'observedAt': now.isoformat().replace('+00:00','Z')}

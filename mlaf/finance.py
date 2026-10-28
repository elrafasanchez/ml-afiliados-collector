"""Read official payment balances from visible server-rendered content.

No amount is reconstructed from sales and no observation time is presented as a
source cut. A layout change rejects the batch and preserves stored balances.
"""
from datetime import datetime, timezone
from html.parser import HTMLParser
import re
from zoneinfo import ZoneInfo
from .errors import SchemaError

HOST = 'https://www.mercadolibre.com.mx'
MONTHS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
          'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
MONTH = '(?:' + '|'.join(MONTHS) + ')'
MONEY = r'\$\s*(\d{1,3}(?:,\d{3})*|\d+)\s*(?:[,.]\s*|\s+)(\d{2})(?!\d)'


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript') and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def visible_text(html):
    parser = VisibleText()
    parser.feed(html)
    return ' '.join(' '.join(parser.parts).split())


def _amount(match):
    return int(match.group(1).replace(',', '')) * 100 + int(match.group(2))


def parse_payments(html, now):
    text = visible_text(html)
    # The current-period anchor makes the implicit year unambiguous. Never use
    # the current year for every row (December/January crosses a year boundary).
    anchor = re.search(r'Detalle del saldo\s+(' + MONTH + r')\s+Per[ií]odo actual\b', text, re.I)
    local = now.astimezone(ZoneInfo('America/Hermosillo'))
    if not anchor or MONTHS.index(anchor.group(1).lower()) + 1 != local.month:
        raise SchemaError('No se pudo verificar el período actual de pagos.')
    body = text[anchor.start():]
    pattern = re.compile(r'(' + MONTH + r')\s+(?:Per[ií]odo actual\s+)?'
                         r'(En revisión hasta fin de\s+' + MONTH + r'|Pagado en Mercado Pago)\s*' + MONEY, re.I)
    rows = list(pattern.finditer(body))
    headings = re.findall(r'(' + MONTH + r')\s+(?:Per[ií]odo actual\s+)?(?:En revisión hasta fin de\s+' + MONTH + r'|Pagado en Mercado Pago)', body, re.I)
    if not rows or len(rows) > 10 or len(rows) != len(headings):
        raise SchemaError('El historial de pagos no tiene una estructura verificable.')
    payments = []
    ordinal = local.year * 12 + local.month - 1
    for index, match in enumerate(rows):
        expected = ordinal - index
        year, zero_month = divmod(expected, 12)
        if MONTHS.index(match.group(1).lower()) != zero_month:
            raise SchemaError('Los meses de pagos están duplicados, omitidos o desordenados.')
        payments.append({'month': f'{year:04d}-{zero_month + 1:02d}',
                         'status': match.group(2),
                         'amountCents': int(match.group(3).replace(',', '')) * 100 + int(match.group(4)),
                         'sourceAt': None})
    next_match = re.search(r'A pagar el\s+(\d{1,2}/[a-záéíóú]{3})\s*' + MONEY, text, re.I)
    next_payment = None
    if next_match:
        next_payment = {'dateLabel': next_match.group(1),
                        'amountCents': int(next_match.group(2).replace(',', '')) * 100 + int(next_match.group(3)),
                        'sourceAt': None}
    elif re.search(r'A pagar el', text, re.I):
        raise SchemaError('No se pudo leer el próximo pago oficial.')
    return payments, next_payment


def collect_finance(source, now=None):
    from .errors import AuthenticationError, CollectorError
    now = now or datetime.now(timezone.utc)
    payload = {'observedAt': now.isoformat().replace('+00:00', 'Z'), 'sourceAt': None}
    try:
        payments, next_payment = parse_payments(source._request(HOST + '/afiliados/payments', expect_json=False), now)
        payload.update(payments=payments, nextPayment=next_payment)
    except (CollectorError, ValueError) as error:
        payload.update(payments=None, nextPayment=None)
        payload['paymentsFailure'] = 'SESSION_EXPIRED' if isinstance(error, AuthenticationError) else 'FINANCE_SYNC_FAILED'
    try:
        payload['incentives'] = parse_incentives(source._request(HOST + '/afiliados/incentives', expect_json=False))
    except (CollectorError, ValueError) as error:
        payload['incentives'] = None
        payload['incentivesFailure'] = 'SESSION_EXPIRED' if isinstance(error, AuthenticationError) else 'FINANCE_SYNC_FAILED'
    return payload


def parse_incentives(html):
    import hashlib
    text = visible_text(html)
    cut = re.search(r'Datos actualizados al\s+(\d{1,2})\s+(' + MONTH + r')\s+(\d{4})', text, re.I)
    if 'Campañas disponibles' not in text or not cut:
        raise SchemaError('No se pudo verificar el corte de incentivos.')
    from datetime import date
    source_date = date(int(cut.group(3)), MONTHS.index(cut.group(2).lower()) + 1, int(cut.group(1)))
    # Only the available campaigns section; upcoming offers are not earnings.
    section = text.split('Campañas disponibles', 1)[1].split('Próximas campañas', 1)[0]
    pattern = re.compile(r'Del\s+(\d{1,2})\s+de\s+(' + MONTH + r')\s+al\s+(\d{1,2})\s+de\s+(' + MONTH + r')', re.I)
    dates = list(pattern.finditer(section))
    if not dates:
        # Absence of a recognizable card must not erase a previously stored one.
        raise SchemaError('No hay campañas con vigencia verificable.')
    campaigns = []
    for index, match in enumerate(dates):
        card = section[match.end():dates[index + 1].start() if index + 1 < len(dates) else len(section)]
        reward = re.search(r'Gana\s+\$\s*(\d[\d,]*)(?:\s*[.]\s*(\d{2}))?\s*extra\s*Al generar\s+(\d+)\s+ventas', card, re.I)
        progress = re.search(r'(Meta alcanzada|En progreso)\s+(\d[\d,]*)\s*/\s*(\d[\d,]*)', card, re.I)
        verification = re.search(r'verificación de\s+(\d+)\s+días', card, re.I)
        if not reward or not progress or not verification:
            raise SchemaError('No se pudo leer la meta, progreso o verificación del incentivo.')
        target = int(reward.group(3))
        if target <= 0 or target != int(progress.group(3).replace(',', '')):
            raise SchemaError('La meta del incentivo no coincide con su progreso.')
        sm = MONTHS.index(match.group(2).lower()) + 1
        em = MONTHS.index(match.group(4).lower()) + 1
        # Infer the year only for a campaign whose month contains the source cut.
        start = date(source_date.year, sm, int(match.group(1)))
        end = date(source_date.year + (em < sm), em, int(match.group(3)))
        if not start <= source_date <= end:
            raise SchemaError('La vigencia del incentivo no contiene su corte; año ambiguo.')
        amount = int(reward.group(1).replace(',', '')) * 100 + int(reward.group(2) or '0')
        title = f'Gana ${reward.group(1)} extra al generar {target} ventas'
        identity = f'{start.isoformat()}|{end.isoformat()}|{title}'
        campaigns.append({'id': 'ml-incentive-' + hashlib.sha256(identity.encode()).hexdigest()[:20],
                          'title': title, 'start': start.isoformat(), 'end': end.isoformat(),
                          'rewardCents': amount, 'target': target,
                          'progress': int(progress.group(2).replace(',', '')),
                          'status': progress.group(1), 'sourceDate': source_date.isoformat(),
                          'verificationDays': int(verification.group(1))})
    return campaigns

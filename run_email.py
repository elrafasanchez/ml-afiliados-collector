"""Read-only Gmail communications ingestion. No email is modified.
The interactive Codex Gmail connection does not expose deployable credentials.
A server-authorized readonly refresh token enables this existing cloud workflow.
"""
import base64
import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from mlaf.ingest import DashboardIngest


def plain_parts(part):
    data = part.get('body', {}).get('data')
    text = ''
    if data and part.get('mimeType') in ('text/plain', 'text/html'):
        text = base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)).decode('utf-8', errors='replace')
        text = re.sub(r'<(?:style|script)\b[^>]*>[\s\S]*?</(?:style|script)>', '', text, flags=re.I)
        text = html.unescape(re.sub(r'<[^>]*>', ' ', text))
    return ' '.join([text] + [plain_parts(p) for p in part.get('parts', [])])


def main():
    names = ('GMAIL_RADAR_CLIENT_ID', 'GMAIL_RADAR_CLIENT_SECRET', 'GMAIL_RADAR_REFRESH_TOKEN')
    if not all(os.environ.get(n) for n in names):
        print('Gmail server monitoring: CONNECTION REQUIRED. Interactive connector remains read-only.')
        return
    form = urllib.parse.urlencode({'client_id': os.environ[names[0]], 'client_secret': os.environ[names[1]], 'refresh_token': os.environ[names[2]], 'grant_type': 'refresh_token'}).encode()
    request = urllib.request.Request('https://oauth2.googleapis.com/token', data=form, headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(request, timeout=30) as response:
        token = json.load(response)['access_token']
    def gmail(path):
        request = urllib.request.Request('https://gmail.googleapis.com/gmail/v1/users/me/' + path, headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    q = 'newer_than:14d (from:mercadolibre.com OR from:mercadolibre.com.mx) (cupón OR cupon OR afiliados OR rally OR campaña)'
    page = gmail('messages?' + urllib.parse.urlencode({'q': q, 'maxResults': 20}))
    origin = urllib.parse.urlsplit(os.environ['ML_INGEST_URL'])
    ingest = DashboardIngest(urllib.parse.urlunsplit((origin.scheme, origin.netloc, '/api/mercadolibre/radar/ingest', '', '')), os.environ['ML_INGEST_TOKEN'])
    count = 0
    for item in page.get('messages', []):
        message = gmail('messages/' + urllib.parse.quote(item['id']) + '?format=full')
        headers = {h['name'].lower(): h['value'] for h in message['payload'].get('headers', [])}
        # Discovery only; sender/auth headers never authorize a coupon.
        sender = headers.get('from', '')
        if not re.search(r'@(?:[a-z0-9-]+\.)*mercadolibre\.com(?:\.mx)?(?:>|\s|$)', sender, re.I):
            continue
        text = re.sub(r'\s+', ' ', plain_parts(message['payload'])).strip()[:40000]
        received = datetime.fromtimestamp(int(message['internalDate']) / 1000, timezone.utc).isoformat()
        ingest.publish({'kind': 'communication', 'id': item['id'], 'subject': headers.get('subject', ''), 'text': text, 'receivedAt': received, 'source': 'https://mail.google.com/mail/#all/' + item['id']})
        count += 1
    print(f'Gmail communications ingested: {count}; no emails modified; no coupons auto-verified.')

if __name__ == '__main__':
    main()

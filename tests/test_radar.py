import unittest
from run_radar import radar_endpoint
class RadarEndpointTests(unittest.TestCase):
    def test_reuses_site_origin_without_changing_existing_ingestion(self):
        self.assertEqual(radar_endpoint('https://site.example/api/mercadolibre/sync'), 'https://site.example/api/mercadolibre/radar/scan')
    def test_rejects_insecure_or_embedded_credentials(self):
        for u in ('http://site.example', 'https://user:secret@site.example', '', 'file:///tmp/site'):
            with self.assertRaises(ValueError): radar_endpoint(u)

class ReadOnlyEmailTests(unittest.TestCase):
    def test_plain_extraction_never_executes_html(self):
        import base64
        from run_email import plain_parts
        encoded = base64.urlsafe_b64encode(b'<script>evil()</script><b>Coupon</b>').decode()
        text = plain_parts({'mimeType': 'text/html', 'body': {'data': encoded}})
        self.assertIn('Coupon', text)
        self.assertNotIn('evil', text)

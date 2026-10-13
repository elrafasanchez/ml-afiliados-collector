import unittest
from unittest.mock import patch
from mlaf.config import Settings
from mlaf.errors import AuthenticationError, TransientSourceError
from run_sync import main, report_failure

class FailureReportingTests(unittest.TestCase):
    settings = Settings("secret-cookie", "https://example.test/api/mercadolibre/sync", "secret-token", .7, False)

    def test_failure_sends_code_without_raw_exception_or_empty_sales(self):
        for error, code, exit_code in [(AuthenticationError("secret-cookie"), "AUTH_EXPIRED", 2), (TransientSourceError("secret-cookie"), "SOURCE_UNAVAILABLE", 1)]:
            with self.subTest(code=code), patch('run_sync.load_settings', return_value=self.settings), patch('run_sync.Collector') as collector, patch('run_sync.DashboardIngest') as ingest, patch('run_sync.log'):
                collector.return_value.run_today.side_effect = error
                self.assertEqual(main(['today']), exit_code)
                payload = ingest.return_value.publish.call_args.args[0]
                self.assertEqual(payload['code'], code)
                self.assertNotIn('secret-cookie', str(payload))
                self.assertNotIn('days', payload)
                self.assertEqual(ingest.call_args.kwargs['max_attempts'], 1)

    def test_reporting_failure_preserves_original_exit_code(self):
        with patch('run_sync.load_settings', return_value=self.settings), patch('run_sync.Collector') as collector, patch('run_sync.DashboardIngest') as ingest, patch('run_sync.log'):
            collector.return_value.run_today.side_effect = AuthenticationError('expired')
            ingest.return_value.publish.side_effect = RuntimeError('offline')
            self.assertEqual(main(['today']), 2)

    def test_dry_run_does_not_send(self):
        with patch('run_sync.DashboardIngest') as ingest:
            report_failure(Settings('cookie','','',.7,True),'today','2026-10-13T12:00:00Z','AUTH_EXPIRED')
            ingest.assert_not_called()

    def test_recent_and_backfill_do_not_swallow_expired_session(self):
        from datetime import date
        from mlaf.jobs import Collector
        for job in ['recent', 'backfill']:
            with self.subTest(job=job), patch('mlaf.jobs.Collector.read_day',side_effect=AuthenticationError('expired')):
                collector=Collector(None)
                with self.assertRaises(AuthenticationError):
                    if job=='recent': collector.run_recent(days_back=1)
                    else: collector.run_backfill(date(2026,10,12),date(2026,10,13))

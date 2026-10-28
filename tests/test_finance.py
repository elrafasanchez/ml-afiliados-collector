import unittest
from datetime import datetime, timezone
from mlaf.errors import SchemaError, AuthenticationError
from mlaf.finance import parse_payments, parse_incentives, collect_finance, visible_text

NOW = datetime(2026, 10, 28, 15, tzinfo=timezone.utc)
PAYMENTS = '''<script>Octubre Período actual $0,00</script><style>fake</style>
<h1>Detalle del saldo</h1><div>Octubre</div><div>Período actual</div>
<div>En revisión hasta fin de diciembre</div><span>$</span><span>149,520</span><span>,</span><span>99</span>
<div>Septiembre En revisión hasta fin de noviembre $ 112,148 , 16</div>
<div>Agosto Pagado en Mercado Pago $ 102,114 , 69</div>
<div>Julio Pagado en Mercado Pago $ 56,297 , 19</div>
<div>Monto pendiente $ 261,669 , 15</div><div>A pagar el 12/nov $ 112,148 , 15</div>'''
INCENTIVES = '''<h1>Campañas disponibles</h1><div>Datos actualizados al 27 octubre 2026</div>
<div>Del 1 de octubre al 31 de octubre</div><div>Gana $100 extraAl generar 2 ventas</div>
<div>Aplica a cualquier categoría</div><div>Tu pago por alcanzar la meta estará disponible después de la verificación de 60 días.</div>
<div>Tu progreso</div><div>Estás participando desde el 1 de octubre</div>
<div>Contaremos el valor de las unidades vendidas durante la vigencia de la campaña.</div>
<div>Meta alcanzada</div><div>2431</div><span>/</span><span>2</span><h2>Próximas campañas</h2>'''


class FinanceTest(unittest.TestCase):
    def test_visible_text_ignores_scripts_and_styles(self):
        self.assertNotIn('fake', visible_text(PAYMENTS))
        self.assertNotIn('$0,00', visible_text(PAYMENTS))

    def test_official_payment_and_month_balance_preserve_source_cent_difference(self):
        rows, next_payment = parse_payments(PAYMENTS, NOW)
        self.assertEqual(rows[1]['amountCents'], 11214816)
        self.assertEqual(next_payment['amountCents'], 11214815)
        self.assertEqual(rows[2]['month'], '2026-08')
        self.assertEqual(rows[2]['status'], 'Pagado en Mercado Pago')
        self.assertIsNone(rows[0]['sourceAt'])

    def test_year_rollover(self):
        html = 'Detalle del saldo Enero Período actual En revisión hasta fin de marzo $ 10 , 00 Diciembre En revisión hasta fin de febrero $ 20 , 50'
        rows, _ = parse_payments(html, datetime(2027, 1, 3, 15, tzinfo=timezone.utc))
        self.assertEqual([row['month'] for row in rows], ['2027-01', '2026-12'])

    def test_wrong_current_month_or_duplicate_month_rejected(self):
        with self.assertRaises(SchemaError):
            parse_payments(PAYMENTS, datetime(2026, 11, 2, 15, tzinfo=timezone.utc))
        with self.assertRaises(SchemaError):
            parse_payments(PAYMENTS.replace('Septiembre', 'Octubre'), NOW)

    def test_malformed_last_month_cannot_silently_truncate_history(self):
        with self.assertRaises(SchemaError):
            parse_payments(PAYMENTS.replace('$ 56,297 , 19', 'importe no disponible'), NOW)

    def test_invalid_next_payment_rejected(self):
        with self.assertRaises(SchemaError):
            parse_payments(PAYMENTS.replace('12/nov $ 112,148 , 15', '12/nov importe no disponible'), NOW)

    def test_incentive_identity_and_verification_are_source_based(self):
        campaign = parse_incentives(INCENTIVES)[0]
        self.assertEqual(campaign['rewardCents'], 10000)
        self.assertEqual(campaign['progress'], 2431)
        self.assertEqual(campaign['target'], 2)
        self.assertEqual(campaign['sourceDate'], '2026-10-27')
        self.assertEqual(campaign['verificationDays'], 60)
        self.assertEqual(campaign['id'], parse_incentives(INCENTIVES.replace('2431', '2432'))[0]['id'])

    def test_unknown_or_inconsistent_incentive_does_not_publish_empty_list(self):
        for html in ('<div>Login</div>', INCENTIVES.replace('<span>2</span>', '<span>3</span>')):
            with self.assertRaises(SchemaError):
                parse_incentives(html)

    def test_component_failure_keeps_other_component(self):
        class Source:
            def _request(self, url, **kwargs):
                return PAYMENTS if url.endswith('/payments') else '<div>Unknown layout</div>'
        payload = collect_finance(Source(), NOW)
        self.assertEqual(len(payload['payments']), 4)
        self.assertIsNone(payload['incentives'])
        self.assertEqual(payload['incentivesFailure'], 'FINANCE_SYNC_FAILED')

    def test_expired_session_cannot_replace_component_data(self):
        class Source:
            def _request(self, *args, **kwargs):
                raise AuthenticationError('expired')
        payload = collect_finance(Source(), NOW)
        self.assertIsNone(payload['payments'])
        self.assertIsNone(payload['incentives'])
        self.assertEqual(payload['paymentsFailure'], 'SESSION_EXPIRED')

"""Pruebas de los trabajos de sincronización, sin red."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from mlaf.errors import AuthenticationError, TransientSourceError
from mlaf.jobs import Collector
from mlaf.timeframe import filter_time_range

FIXTURES = Path(__file__).parent / "fixtures"
# Media tarde del 8 de septiembre en Hermosillo (UTC-7, sin horario de verano).
NOW = datetime(2026, 9, 8, 18, 30, tzinfo=timezone(timedelta(hours=-7)))


def kpis(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def document(payload: dict, orders: dict | None = None) -> str:
    context = {"appProps": {"pageProps": {"generalKpis": payload, "generalOrders": orders or {"item_list": [], "total_results": 0}}}}
    return (
        '<script id="__NORDIC_RENDERING_CTX__" nonce="">_n.ctx.r='
        + json.dumps(context)
        + ";_n.ctx.r.assets.manifest=new Map([]);</script>"
    )


class FakeSource:
    """Fuente de mentira que registra qué rangos se pidieron."""

    def __init__(self, *, day_payload=None, sales=None, failures=None):
        self.day_payload = day_payload or kpis("kpis_2026_09_08.json")
        self.sales = sales if sales is not None else kpis("sales_page.json")["item_list"]
        self.failures = failures or {}
        self.dashboard_calls: list[tuple[date, date]] = []
        self.sales_calls: list[tuple[date, date]] = []

    def fetch_dashboard_html(self, start, end_exclusive):
        self.dashboard_calls.append((start, end_exclusive))
        failure = self.failures.get(start.isoformat())
        if failure:
            raise failure
        return document(self.day_payload)

    def fetch_all_sales(self, start, end_exclusive, **kwargs):
        self.sales_calls.append((start, end_exclusive))
        return self.sales


class TodayJobTest(unittest.TestCase):
    def test_uses_the_current_calendar_date_and_never_a_fixed_one(self):
        source = FakeSource()
        result = Collector(source).run_today(now=NOW)
        self.assertEqual(result.days[0].date_key, "2026-09-08")

    def test_month_query_ends_tomorrow_so_the_source_does_not_drop_today(self):
        """La consulta del mes termina mañana, no el 1 del mes siguiente.

        Si el rango solicitado se extiende más allá del día actual, Mercado
        Libre lo trunca en ayer y descarta el día en curso. Verificado con datos
        reales el 2026-09-08:

            2026-09-01 .. 2026-10-01  →  232,556.70   (sin el día 8)
            2026-09-01 .. 2026-09-09  →  264,756.96   (con el día 8)

        Pedir el mes natural devolvería el mes incompleto sin ningún aviso.
        """
        source = FakeSource()
        result = Collector(source).run_today(now=NOW)

        self.assertIn(
            (date(2026, 9, 1), date(2026, 9, 9)),
            source.dashboard_calls,
            "la consulta del mes debe terminar mañana",
        )
        self.assertNotIn(
            (date(2026, 9, 1), date(2026, 10, 1)),
            source.dashboard_calls,
            "pedir el mes natural completo perdería el día en curso",
        )

    def test_the_published_month_range_is_still_the_natural_month(self):
        """Lo que se recorta es la consulta, no la identidad del periodo.

        El mes se almacena identificado por su rango natural; si se guardara con
        el rango recortado, cada día crearía un periodo distinto en la base.
        """
        result = Collector(FakeSource()).run_today(now=NOW)
        self.assertEqual(result.month_range, (date(2026, 9, 1), date(2026, 10, 1)))

    def test_the_month_aggregate_is_published_apart_from_the_days(self):
        """El agregado del mes ya incluye el día en curso.

        Por eso el recolector no lo suma aparte: hacerlo contaría dos veces el
        día vigente.
        """
        result = Collector(FakeSource()).run_today(now=NOW)
        payload = result.as_payload()
        self.assertEqual(len(payload["days"]), 1)
        self.assertIn("monthAggregate", payload)

    def test_requests_stay_within_the_thirty_minute_budget(self):
        source = FakeSource()
        result = Collector(source).run_today(now=NOW)
        self.assertLessEqual(
            result.requests_made, 5, "una corrida de 30 min debe ser barata"
        )

    def test_reconciliation_gap_is_reported_not_hidden(self):
        source = FakeSource()
        result = Collector(source).run_today(now=NOW)
        self.assertEqual(result.reconciliation["scope"], "today")
        # El fixture trae 2 ventas frente a 57 unidades agregadas.
        self.assertEqual(result.reconciliation["unitsDifference"], 2 - 57)
        self.assertTrue(result.warnings, "la diferencia debe quedar advertida")

    def test_a_stale_source_keeps_the_data_and_warns(self):
        """Un corte viejo no se descarta ni se convierte en cero: se advierte."""
        payload = kpis("kpis_2026_09_08.json")
        payload["last_update"] = "2026-09-08T00:00:00Z"
        source = FakeSource(day_payload=payload)
        result = Collector(source).run_today(now=NOW)
        self.assertEqual(len(result.days), 1)
        self.assertTrue(any("antigüedad" in w for w in result.warnings))


class FailureIsNeverZeroTest(unittest.TestCase):
    def test_an_authentication_failure_propagates_instead_of_producing_zero(self):
        source = FakeSource(failures={"2026-09-08": AuthenticationError("sesión caducada")})
        with self.assertRaises(AuthenticationError):
            Collector(source).run_today(now=NOW)

    def test_a_transient_failure_propagates_instead_of_producing_zero(self):
        source = FakeSource(failures={"2026-09-08": TransientSourceError("502")})
        with self.assertRaises(TransientSourceError):
            Collector(source).run_today(now=NOW)


class ProductRollupSampleTest(unittest.TestCase):
    """El documento incrusta solo la primera página del corte por producto."""

    def _source_with_rollup(self, rollup):
        source = FakeSource()
        original = source.fetch_dashboard_html

        def patched(start, end):
            original(start, end)
            return document(source.day_payload, rollup)

        source.fetch_dashboard_html = patched
        return source

    def test_a_partial_sample_does_not_raise_a_false_alarm(self):
        # 10 productos de 42: comparar sus unidades contra el total del día
        # produciría una advertencia en cada ejecución sin significar nada.
        rollup = {"total_results": 42, "item_list": [
            {"entity_id": "MLM1", "product": "x", "quantity": 5,
             "quantity_direct": 5, "quantity_not_direct": 0,
             "total_sales": 100.0, "earnings": 14.0, "fee": 0.14}
        ]}
        result = Collector(self._source_with_rollup(rollup)).run_today(now=NOW)
        self.assertFalse(result.reconciliation["productRollupComplete"])
        self.assertNotIn("rollupVsAggregateUnits", result.reconciliation)
        self.assertFalse(
            any("corte por producto" in warning for warning in result.warnings),
            "una muestra parcial no debe advertir",
        )

    def test_a_complete_rollup_that_disagrees_does_warn(self):
        rollup = {"total_results": 1, "item_list": [
            {"entity_id": "MLM1", "product": "x", "quantity": 5,
             "quantity_direct": 5, "quantity_not_direct": 0,
             "total_sales": 100.0, "earnings": 14.0, "fee": 0.14}
        ]}
        result = Collector(self._source_with_rollup(rollup)).run_today(now=NOW)
        self.assertTrue(result.reconciliation["productRollupComplete"])
        self.assertEqual(result.reconciliation["rollupVsAggregateUnits"], 5 - 57)
        self.assertTrue(any("corte por producto" in w for w in result.warnings))


class RecentJobTest(unittest.TestCase):
    def test_reads_one_request_per_day(self):
        source = FakeSource()
        result = Collector(source).run_recent(days_back=7, now=NOW)
        self.assertEqual(len(source.dashboard_calls), 7)
        self.assertEqual(len(result.days), 7)

    def test_one_failing_day_does_not_discard_the_others(self):
        source = FakeSource(failures={"2026-09-05": TransientSourceError("502")})
        result = Collector(source).run_recent(days_back=7, now=NOW)
        self.assertEqual(len(result.days), 6)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("2026-09-05", result.warnings[0])


class BackfillJobTest(unittest.TestCase):
    def test_skips_days_already_stored(self):
        source = FakeSource()
        known = {"2026-09-06", "2026-09-07"}
        result = Collector(source).run_backfill(
            date(2026, 9, 5), date(2026, 9, 8), known_days=known
        )
        collected = {day.date_key for day in result.days}
        self.assertEqual(collected, {"2026-09-05"})
        self.assertEqual(len(source.dashboard_calls), 1, "no se relee lo ya guardado")

    def test_walks_every_calendar_date_in_the_span(self):
        source = FakeSource()
        result = Collector(source).run_backfill(date(2026, 9, 1), date(2026, 9, 8))
        self.assertEqual(len(result.days), 7)


class ClampToTomorrowTest(unittest.TestCase):
    """El recorte que impide que la fuente descarte el día en curso."""

    def test_a_range_beyond_tomorrow_is_trimmed(self):
        from mlaf.timeframe import clamp_to_tomorrow
        self.assertEqual(
            clamp_to_tomorrow(date(2026, 10, 1), date(2026, 9, 8)),
            date(2026, 9, 9),
        )

    def test_a_range_that_already_ends_earlier_is_left_alone(self):
        from mlaf.timeframe import clamp_to_tomorrow
        self.assertEqual(
            clamp_to_tomorrow(date(2026, 9, 5), date(2026, 9, 8)),
            date(2026, 9, 5),
        )

    def test_on_the_last_day_of_the_month_both_coincide(self):
        from mlaf.timeframe import clamp_to_tomorrow, month_range
        self.assertEqual(month_range(date(2026, 9, 30)), (date(2026, 9, 1), date(2026, 10, 1)))


class FilterFormatTest(unittest.TestCase):
    def test_reproduces_the_offset_the_source_expects(self):
        # El desfase -03:00 es el del servicio, no el del usuario. Cambiarlo por
        # el de Hermosillo desplazaría el día y contaminaría toda la serie.
        self.assertEqual(
            filter_time_range(date(2026, 9, 8), date(2026, 9, 9)),
            "2026-09-08T00:00:00.000-03:00--2026-09-09T00:00:00.000-03:00",
        )


if __name__ == "__main__":
    unittest.main()

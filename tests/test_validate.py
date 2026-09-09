"""Pruebas de las invariantes de integridad."""

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from mlaf.errors import ValidationError
from mlaf.normalize import DailyMetrics, normalize_daily_metrics, normalize_sales
from mlaf.validate import (
    reconcile_sales_against_daily,
    validate_daily_metrics,
    validate_sales,
    validate_source_freshness,
)

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 9, 1, 30, tzinfo=timezone.utc)


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def metrics(**overrides) -> DailyMetrics:
    base = dict(
        date_key="2026-09-08",
        clicks=2124,
        buyers=30,
        estimated_orders=46,
        estimated_products=57,
        gross_sales=Decimal("23671.83"),
        estimated_sales=Decimal("22339.15"),
        non_effective_sales_amount=Decimal("1332.68"),
        non_effective_sales_count=3,
        marketplace_commission=Decimal("2287.50"),
        store_commission=Decimal("77.50"),
        total_commission=Decimal("2365.00"),
        source_timestamp="2026-09-09T01:14:35.000Z",
    )
    base.update(overrides)
    return DailyMetrics(**base)


class RealZeroTest(unittest.TestCase):
    """Un cero informado por la fuente es un dato; un fallo nunca lo es."""

    def test_a_genuine_zero_day_is_accepted(self):
        quiet_day = metrics(
            clicks=0, buyers=0, estimated_orders=0, estimated_products=0,
            gross_sales=Decimal("0.00"), estimated_sales=Decimal("0.00"),
            non_effective_sales_amount=Decimal("0.00"), non_effective_sales_count=0,
            marketplace_commission=Decimal("0.00"), store_commission=Decimal("0.00"),
            total_commission=Decimal("0.00"),
        )
        validate_daily_metrics(quiet_day, now=NOW)  # no debe lanzar


class AccountingIdentityTest(unittest.TestCase):
    def test_estimated_sales_must_equal_gross_minus_non_effective(self):
        with self.assertRaises(ValidationError) as caught:
            validate_daily_metrics(metrics(estimated_sales=Decimal("19000.00")), now=NOW)
        self.assertIn("no cuadran", str(caught.exception))

    def test_total_commission_must_equal_its_two_components(self):
        with self.assertRaises(ValidationError) as caught:
            validate_daily_metrics(metrics(total_commission=Decimal("9999.00")), now=NOW)
        self.assertIn("no es la suma", str(caught.exception))

    def test_commission_cannot_exceed_gross_sales(self):
        with self.assertRaises(ValidationError):
            validate_daily_metrics(
                metrics(
                    gross_sales=Decimal("100.00"),
                    estimated_sales=Decimal("100.00"),
                    non_effective_sales_amount=Decimal("0.00"),
                    non_effective_sales_count=0,
                    marketplace_commission=Decimal("500.00"),
                    store_commission=Decimal("0.00"),
                    total_commission=Decimal("500.00"),
                ),
                now=NOW,
            )

    def test_rounding_within_a_cent_does_not_trip_the_identity(self):
        validate_daily_metrics(metrics(estimated_sales=Decimal("22339.17")), now=NOW)

    def test_negative_values_are_rejected(self):
        with self.assertRaises(ValidationError):
            validate_daily_metrics(metrics(clicks=-1), now=NOW)

    def test_non_effective_amount_without_count_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_daily_metrics(metrics(non_effective_sales_count=0), now=NOW)

    def test_a_future_source_timestamp_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_daily_metrics(
                metrics(source_timestamp="2026-09-10T00:00:00.000Z"), now=NOW
            )


class FreshnessTest(unittest.TestCase):
    def test_a_recent_cut_passes(self):
        validate_source_freshness(metrics(), now=NOW)

    def test_a_stale_cut_is_refused_so_the_last_valid_value_survives(self):
        with self.assertRaises(ValidationError) as caught:
            validate_source_freshness(metrics(), now=NOW + timedelta(hours=9))
        self.assertIn("último valor válido", str(caught.exception))


class SalesValidationTest(unittest.TestCase):
    def test_duplicate_sale_identifiers_are_rejected(self):
        rows = load("sales_page.json")["item_list"]
        duplicated = normalize_sales(rows + rows[:1])
        with self.assertRaises(ValidationError) as caught:
            validate_sales(duplicated)
        self.assertIn("duplicadas", str(caught.exception))

    def test_sales_outside_the_requested_range_are_rejected(self):
        sales = normalize_sales(load("sales_page.json")["item_list"])
        with self.assertRaises(ValidationError):
            validate_sales(sales, expected_range=(date(2026, 9, 1), date(2026, 9, 2)))

    def test_sales_inside_the_requested_range_pass(self):
        sales = normalize_sales(load("sales_page.json")["item_list"])
        validate_sales(sales, expected_range=(date(2026, 9, 8), date(2026, 9, 9)))


class ReconciliationTest(unittest.TestCase):
    """Las diferencias se informan; nunca se escalan los datos para forzar cuadre."""

    def test_reports_the_gap_between_detail_and_aggregate(self):
        sales = normalize_sales(load("sales_page.json")["item_list"])
        daily = normalize_daily_metrics(load("kpis_2026_09_08.json"), "2026-09-08")
        gaps = reconcile_sales_against_daily(sales, daily)
        # El fixture trae 2 de 49 ventas, así que la brecha debe ser evidente.
        self.assertEqual(gaps["units_difference"], Decimal(2 - 57))
        self.assertEqual(gaps["commission_difference"], Decimal("93.91") + Decimal("32.68") - Decimal("2365.00"))

    def test_a_complete_day_reconciles_to_zero(self):
        daily = normalize_daily_metrics(load("kpis_2026_09_08.json"), "2026-09-08")
        # Las ventas reales traen centavos exactos que suman el agregado sin
        # residuo; se reparte el remanente para reproducir esa condición en vez
        # de dividir en flotante y aceptar una tolerancia que oculte errores.
        total_cents = int(daily.total_commission * 100)
        per_row, remainder = divmod(total_cents, 57)
        sales = normalize_sales([
            {"id": str(index), "date": "08/09/2026", "productName": "x",
             "saleValue": 1.0, "saleUnits": 1,
             "commissionValue": (per_row + (1 if index < remainder else 0)) / 100}
            for index in range(57)
        ])
        gaps = reconcile_sales_against_daily(sales, daily)
        self.assertEqual(gaps["units_difference"], Decimal(0))
        self.assertEqual(gaps["commission_difference"], Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()

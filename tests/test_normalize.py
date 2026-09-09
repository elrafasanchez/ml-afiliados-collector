"""Pruebas de la traducción del vocabulario de Mercado Libre al canónico."""

import json
import unittest
from decimal import Decimal
from pathlib import Path

from mlaf.errors import SchemaError
from mlaf.normalize import (
    normalize_daily_metrics,
    normalize_sales,
    normalize_source_timestamp,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class InvertedSourceNamesTest(unittest.TestCase):
    """La trampa central de esta fuente.

    En la interfaz, "Órdenes estimadas" vale 46 y "Prod. estimados" vale 57. En
    la API esos números llegan bajo `requests` y `orders` respectivamente. Un
    mapeo por intuición intercambiaría ambas métricas sin que ninguna validación
    numérica lo detecte, porque las dos son enteros positivos plausibles.
    """

    def test_requests_are_orders_and_orders_are_products(self):
        metrics = normalize_daily_metrics(load("kpis_2026_09_08.json"), "2026-09-08")
        self.assertEqual(metrics.estimated_orders, 46, "`requests` es Órdenes estimadas")
        self.assertEqual(metrics.estimated_products, 57, "`orders` es Prod. estimados")


class RealDayTest(unittest.TestCase):
    """Contraste contra la exportación de 180 días verificada por el usuario."""

    def test_historical_day_matches_the_verified_export(self):
        metrics = normalize_daily_metrics(load("kpis_2026_03_15.json"), "2026-03-15")

        # Fila del 2026-03-15 en mercado_libre_180_dias.csv
        self.assertEqual(metrics.estimated_sales, Decimal("2233.15"))
        self.assertEqual(metrics.total_commission, Decimal("287.25"))
        self.assertEqual(metrics.marketplace_commission, Decimal("225.64"))
        self.assertEqual(metrics.store_commission, Decimal("61.61"))
        self.assertEqual(metrics.gross_sales, Decimal("2545.39"))
        self.assertEqual(metrics.non_effective_sales_amount, Decimal("312.24"))
        self.assertEqual(metrics.buyers, 3)
        self.assertEqual(metrics.clicks, 34)

    def test_the_export_column_ordenes_estimadas_actually_held_products(self):
        # El CSV guardó 9 en `ordenes_estimadas`, pero 9 es `orders`, es decir
        # Prod. estimados. Las órdenes reales de ese día fueron 8. Por eso el
        # relleno histórico se hace desde la fuente y no desde el CSV.
        metrics = normalize_daily_metrics(load("kpis_2026_03_15.json"), "2026-03-15")
        self.assertEqual(metrics.estimated_products, 9)
        self.assertEqual(metrics.estimated_orders, 8)

    def test_money_keeps_two_decimals_without_binary_drift(self):
        metrics = normalize_daily_metrics(load("kpis_2026_09_08.json"), "2026-09-08")
        self.assertEqual(
            metrics.marketplace_commission + metrics.store_commission,
            metrics.total_commission,
        )
        self.assertEqual(metrics.total_commission, Decimal("2365.00"))


class MissingMetricsTest(unittest.TestCase):
    def test_a_removed_metric_is_reported_instead_of_defaulted_to_zero(self):
        payload = load("kpis_2026_09_08.json")
        payload["data"] = [entry for entry in payload["data"] if entry["id"] != "buyers"]
        with self.assertRaises(SchemaError) as caught:
            normalize_daily_metrics(payload, "2026-09-08")
        self.assertIn("buyers", str(caught.exception))

    def test_missing_last_update_is_an_error_not_a_synthetic_timestamp(self):
        payload = load("kpis_2026_09_08.json")
        del payload["last_update"]
        with self.assertRaises(SchemaError):
            normalize_daily_metrics(payload, "2026-09-08")


class SourceTimestampTest(unittest.TestCase):
    def test_normalizes_to_utc_with_milliseconds(self):
        self.assertEqual(
            normalize_source_timestamp("2026-09-09T01:14:35Z"), "2026-09-09T01:14:35.000Z"
        )

    def test_converts_an_offset_to_utc(self):
        self.assertEqual(
            normalize_source_timestamp("2026-09-08T18:14:35-07:00"),
            "2026-09-09T01:14:35.000Z",
        )


class SalesTest(unittest.TestCase):
    def test_reads_the_transactional_detail(self):
        sales = normalize_sales(load("sales_page.json")["item_list"])
        self.assertEqual(len(sales), 2)
        first = sales[0]
        self.assertEqual(first.sale_id, "2000018355466108")
        self.assertEqual(first.date_key, "2026-09-08", "DD/MM/YYYY se convierte a ISO")
        self.assertEqual(first.item_id, "MLM3177998333", "el MLM sale del enlace")
        self.assertEqual(first.sale_value, Decimal("1050.39"))
        self.assertEqual(first.commission_value, Decimal("93.91"))
        self.assertEqual(first.sale_type, "DIRECT")

    def test_a_sale_without_identifier_is_rejected(self):
        with self.assertRaises(SchemaError):
            normalize_sales([{"date": "08/09/2026", "saleValue": 1, "saleUnits": 1,
                              "commissionValue": 0, "productName": "x"}])

    def test_a_link_without_mlm_yields_no_item_id_instead_of_a_wrong_one(self):
        sales = normalize_sales([{
            "id": "1", "date": "08/09/2026", "link": "https://example.com/x",
            "productName": "x", "saleValue": 1.0, "saleUnits": 1, "commissionValue": 0.0,
        }])
        self.assertIsNone(sales[0].item_id)

    def test_an_impossible_date_is_rejected(self):
        with self.assertRaises(SchemaError):
            normalize_sales([{"id": "1", "date": "31/02/2026", "productName": "x",
                              "saleValue": 1.0, "saleUnits": 1, "commissionValue": 0.0}])


if __name__ == "__main__":
    unittest.main()

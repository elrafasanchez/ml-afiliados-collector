"""Pruebas del parseo de la hidratación de Mercado Libre."""

import json
import unittest
from pathlib import Path

from mlaf.errors import AuthenticationError, SchemaError
from mlaf.normalize import normalize_product_rollup
from mlaf.parse import (
    extract_general_kpis,
    extract_product_rollup,
    extract_rendering_context,
    indexed_amounts,
)

FIXTURES = Path(__file__).parent / "fixtures"


def build_document(page_props: dict, *, trailing: bool = True) -> str:
    """Reproduce la forma real del documento servido por Mercado Libre.

    El script no es JSON puro: asigna un objeto y luego construye `Map` y `Set`,
    que no son parseables. La prueba conserva ese sufijo a propósito.
    """
    context = {
        "flags": {"isBot": False},
        "appProps": {"pageProps": page_props},
        "mainEntry": "attributions-dashboard-index",
    }
    suffix = (
        ';_n.ctx.r.assets.manifest=new Map([["framework.js","framework.1095236c.js"]]);'
        ';_n.ctx.r.assets.mainAssetsNames={scripts:new Set(["framework.js"])};'
        if trailing
        else ""
    )
    return (
        "<!DOCTYPE html><html><body>"
        f'<script id="__NORDIC_RENDERING_CTX__" nonce="">_n.ctx.r={json.dumps(context)}{suffix}</script>'
        "</body></html>"
    )


class ExtractRenderingContextTest(unittest.TestCase):
    def test_ignores_javascript_that_follows_the_object(self):
        html = build_document({"generalKpis": {"ok": True}})
        context = extract_rendering_context(html)
        self.assertEqual(context["mainEntry"], "attributions-dashboard-index")

    def test_braces_inside_translated_strings_do_not_unbalance_the_scan(self):
        # La fuente incluye un diccionario de traducciones con marcadores `{0}`.
        props = {"i18n": {"La URL debe tener mínimo {0} caracteres.": "{{anidado}}"},
                 "generalKpis": {"ok": True}}
        context = extract_rendering_context(build_document(props))
        self.assertIn("i18n", context["appProps"]["pageProps"])

    def test_escaped_quote_does_not_end_the_string(self):
        # Una comilla escapada seguida de una llave sin cerrar: si el recorte
        # tratara la comilla como fin de cadena, contaría esa llave y cortaría
        # el objeto en el sitio equivocado.
        original = 'comilla \\" escapada {'
        props = {"nota": original, "generalKpis": {"ok": True}}
        context = extract_rendering_context(build_document(props))
        self.assertEqual(context["appProps"]["pageProps"]["nota"], original)

    def test_login_wall_is_reported_as_authentication_failure(self):
        html = "<html><body>Ingresa a tu cuenta para continuar</body></html>"
        with self.assertRaises(AuthenticationError):
            extract_rendering_context(html)

    def test_unknown_document_is_reported_as_schema_change(self):
        with self.assertRaises(SchemaError):
            extract_rendering_context("<html><body>otra cosa</body></html>")

    def test_missing_general_kpis_is_a_schema_error_not_an_empty_result(self):
        with self.assertRaises(SchemaError):
            extract_general_kpis(build_document({"otro": 1}))


class ExtractBlocksTest(unittest.TestCase):
    def test_reads_kpis_and_product_rollup_from_the_same_document(self):
        kpis = json.loads((FIXTURES / "kpis_2026_09_08.json").read_text())
        rollup = json.loads((FIXTURES / "product_rollup.json").read_text())
        html = build_document({"generalKpis": kpis, "generalOrders": rollup})

        self.assertEqual(extract_general_kpis(html)["last_update"], "2026-09-09T01:14:35Z")
        self.assertEqual(extract_product_rollup(html)["total_results"], 42)


class ProductRollupTest(unittest.TestCase):
    """`generalOrders` no son órdenes.

    La clave se llama así, pero contiene la pestaña "Productos vendidos" ya
    agregada por producto. El detalle transacción por transacción vive en otro
    extremo y tiene otra forma. Confundirlos es fácil: ambos responden con
    `item_list` y `total_results`.
    """

    def setUp(self):
        self.raw = json.loads((FIXTURES / "product_rollup.json").read_text())

    def test_reads_a_real_product_row(self):
        products = normalize_product_rollup(self.raw["item_list"])
        first = products[0]
        self.assertEqual(first.item_id, "MLM5254341382")
        self.assertEqual(first.units, 22)
        self.assertEqual(first.direct_units, 22)
        self.assertEqual(first.indirect_units, 0)
        self.assertEqual(str(first.sales), "6112.70")
        self.assertEqual(str(first.commission), "718.93")

    def test_converts_the_fraction_rate_into_a_percentage(self):
        # La fuente entrega 0.14; la interfaz muestra 14 %.
        products = normalize_product_rollup(self.raw["item_list"])
        self.assertEqual(str(products[0].rate), "14.00")

    def test_units_must_split_into_direct_and_indirect(self):
        broken = [dict(self.raw["item_list"][0], quantity=99)]
        with self.assertRaises(SchemaError):
            normalize_product_rollup(broken)

    def test_a_product_without_entity_id_is_rejected(self):
        broken = [{key: value for key, value in self.raw["item_list"][0].items() if key != "entity_id"}]
        with self.assertRaises(SchemaError):
            normalize_product_rollup(broken)


class IndexedAmountsTest(unittest.TestCase):
    def test_indexes_by_id_instead_of_position(self):
        entries = [{"id": "b", "current_amount": 2}, {"id": "a", "current_amount": 1}]
        self.assertEqual(indexed_amounts(entries), {"b": 2.0, "a": 1.0})

    def test_rejects_a_boolean_masquerading_as_a_number(self):
        with self.assertRaises(SchemaError):
            indexed_amounts([{"id": "a", "current_amount": True}])

    def test_rejects_a_metric_without_numeric_value(self):
        with self.assertRaises(SchemaError):
            indexed_amounts([{"id": "a", "current_amount": None}])


if __name__ == "__main__":
    unittest.main()

"""A replacement range must be complete even without a declared total."""
import unittest
from datetime import date
from mlaf.errors import SchemaError
from mlaf.source import MercadoLibreSource


class SalesPaginationTest(unittest.TestCase):
    def source(self, pages):
        source = MercadoLibreSource('test-cookie', min_interval=0)
        source.fetch_sales_page = lambda *args, page=1, **kwargs: pages[page - 1]
        return source

    def test_page_limit_without_total_fails_closed(self):
        source = self.source([{'item_list': [{'id': 1}]}, {'item_list': [{'id': 2}]}])
        with self.assertRaises(SchemaError):
            source.fetch_all_sales(date(2026, 9, 1), date(2026, 9, 8), max_pages=2)

    def test_empty_terminal_page_confirms_completion_without_total(self):
        source = self.source([{'item_list': [{'id': 1}]}, {'item_list': []}])
        self.assertEqual(source.fetch_all_sales(date(2026, 9, 1), date(2026, 9, 8), max_pages=2), [{'id': 1}])

    def test_exact_declared_total_at_page_limit_is_valid(self):
        source = self.source([{'item_list': [{'id': 1}], 'total_results': 2}, {'item_list': [{'id': 2}]}])
        self.assertEqual(len(source.fetch_all_sales(date(2026, 9, 1), date(2026, 9, 8), max_pages=2)), 2)

    def test_empty_page_before_declared_total_fails_closed(self):
        source = self.source([{'item_list': [{'id': 1}], 'total_results': 2}, {'item_list': []}])
        with self.assertRaises(SchemaError):
            source.fetch_all_sales(date(2026, 9, 1), date(2026, 9, 8), max_pages=2)

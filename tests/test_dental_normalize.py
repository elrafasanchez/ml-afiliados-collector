"""Reglas de normalización de Dentalink que no dependen de la red."""

from __future__ import annotations

import unittest

from dental.normalize import confirmed_empty_days

class ConfirmedEmptyDaysTest(unittest.TestCase):
    """Un día sin cobros y un día sin leer no pueden verse igual."""

    def test_marca_los_dias_sin_caja(self):
        cortes = [{"dateKey": "2026-09-08", "registerId": "1"}]
        vacios = confirmed_empty_days(cortes, ["2026-09-06", "2026-09-07", "2026-09-08"], "2026-09-09T20:00:00Z")
        self.assertEqual([v["dateKey"] for v in vacios], ["2026-09-06", "2026-09-07"])
        for v in vacios:
            self.assertEqual(v["totalCollectedCents"], 0)
            self.assertEqual(v["paymentCount"], 0)
            self.assertIsNone(v["registerId"])
            self.assertEqual(v["state"], "CLOSED")

    def test_no_pisa_un_dia_con_caja(self):
        cortes = [{"dateKey": "2026-09-09", "registerId": "1"}]
        self.assertEqual(confirmed_empty_days(cortes, ["2026-09-09"], "x"), [])

    def test_identificador_estable(self):
        una = confirmed_empty_days([], ["2026-09-06"], "2026-09-09T20:00:00Z")
        otra = confirmed_empty_days([], ["2026-09-06"], "2026-09-09T21:00:00Z")
        self.assertEqual(una[0]["id"], otra[0]["id"], "dos corridas no deben crear dos renglones")

"""Reglas de normalización de Dentalink que no dependen de la red."""

from __future__ import annotations

import unittest

from dental.normalize import confirmed_empty_days

class ConfirmedEmptyDaysTest(unittest.TestCase):
    """Un día sin cobros y un día sin leer no pueden verse igual."""

    def test_devuelve_fechas_no_cortes(self):
        cortes = [{"dateKey": "2026-09-08", "registerId": "1"}]
        vacios = confirmed_empty_days(cortes, ["2026-09-06", "2026-09-07", "2026-09-08"])
        self.assertEqual(vacios, ["2026-09-06", "2026-09-07"])
        for v in vacios:
            self.assertIsInstance(v, str, "debe ser una fecha, no un corte con caja nula")

    def test_no_pisa_un_dia_con_caja(self):
        cortes = [{"dateKey": "2026-09-09", "registerId": "1"}]
        self.assertEqual(confirmed_empty_days(cortes, ["2026-09-09"]), [])

    def test_repetible(self):
        una = confirmed_empty_days([], ["2026-09-06"])
        otra = confirmed_empty_days([], ["2026-09-06"])
        self.assertEqual(una, otra, "dos corridas deben producir lo mismo")

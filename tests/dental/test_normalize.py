"""Pruebas de la traducción de Dentalink al modelo canónico."""

from __future__ import annotations

import unittest

from dental.errors import SchemaError
from dental.normalize import (
    appointment_status,
    build_collection_snapshots,
    normalize_appointments,
    normalize_payments,
    normalize_registers,
    patient_reference,
    payment_method,
)


class PatientMinimizationTest(unittest.TestCase):
    """El tablero necesita distinguir personas, no identificarlas."""

    def test_keeps_first_name_and_one_initial(self):
        self.assertEqual(patient_reference("María Gómez Ruiz", 44), "María R.")
        self.assertEqual(patient_reference("Juan Pérez", 12), "Juan P.")

    def test_a_single_name_survives_whole(self):
        self.assertEqual(patient_reference("Madonna", 7), "Madonna")

    def test_without_a_name_it_falls_back_to_the_identifier(self):
        self.assertEqual(patient_reference(None, 91), "Paciente 91")
        self.assertEqual(patient_reference("   ", 91), "Paciente 91")


class PaymentMethodTest(unittest.TestCase):
    def test_maps_the_spanish_labels(self):
        self.assertEqual(payment_method("Efectivo"), "CASH")
        self.assertEqual(payment_method("Transferencia bancaria"), "TRANSFER")
        self.assertEqual(payment_method("Tarjeta de crédito"), "CARD")
        self.assertEqual(payment_method("Pago mixto"), "MIXED")

    def test_an_unknown_method_is_not_forced_into_cash(self):
        # Clasificar lo desconocido como efectivo desplazaría dinero entre
        # cuentas en el tablero sin que nada lo señalara.
        self.assertEqual(payment_method("Vale de despensa"), "OTHER")
        self.assertEqual(payment_method(None), "OTHER")


class AppointmentStatusTest(unittest.TestCase):
    def test_reads_the_spanish_states_with_their_inflections(self):
        self.assertEqual(appointment_status("No asiste"), "NO_SHOW")
        self.assertEqual(appointment_status("Cancelada por el paciente"), "CANCELLED")
        self.assertEqual(appointment_status("Atendiéndose"), "IN_TREATMENT")
        self.assertEqual(appointment_status("En sala de espera"), "WAITING")
        self.assertEqual(appointment_status("Atendido"), "ATTENDED")
        self.assertEqual(appointment_status("Confirmada"), "CONFIRMED")

    def test_accents_do_not_change_the_result(self):
        self.assertEqual(appointment_status("En atención"), "IN_TREATMENT")
        self.assertEqual(appointment_status("En atencion"), "IN_TREATMENT")

    def test_an_unknown_state_is_reported_as_other(self):
        self.assertEqual(appointment_status("Estado nuevo de Dentalink"), "OTHER")

    def test_the_raw_text_travels_untouched(self):
        # El sitio resuelve el estado real con este texto; interpretarlo aquí y
        # descartarlo crearía dos verdades.
        rows = [{"id": 1, "fecha": "2026-09-09", "hora_inicio": "10:00",
                 "id_dentista": 5, "estado": "Atendiéndose", "id_paciente": 3}]
        cita = normalize_appointments(rows)[0]
        self.assertEqual(cita["sourceStatus"], "Atendiéndose")
        self.assertEqual(cita["status"], "IN_TREATMENT")


class MoneyTest(unittest.TestCase):
    def test_amounts_become_whole_cents(self):
        rows = [{"id": 9, "fecha_recepcion": "2026-09-09", "monto_pago": 1500.55,
                 "medio_pago": "Efectivo", "id_caja": 7}]
        self.assertEqual(normalize_payments(rows)[0]["amountCents"], 150055)

    def test_a_missing_amount_is_an_error_not_a_zero(self):
        # Una API que cambia de forma no puede convertirse en cobros de cero.
        with self.assertRaises(SchemaError):
            normalize_payments([{"id": 9, "fecha_recepcion": "2026-09-09", "medio_pago": "Efectivo"}])

    def test_a_missing_date_is_an_error(self):
        with self.assertRaises(SchemaError):
            normalize_payments([{"id": 9, "monto_pago": 10, "medio_pago": "Efectivo"}])


class TimeZoneTest(unittest.TestCase):
    """Las horas de Dentalink son locales de la clínica, no UTC."""

    def test_a_payment_keeps_the_clinic_day(self):
        rows = [{"id": 9, "fecha_recepcion": "2026-09-09", "hora_recepcion": "19:30",
                 "monto_pago": 100, "medio_pago": "Efectivo", "id_caja": 7}]
        pago = normalize_payments(rows)[0]
        self.assertEqual(pago["paidAt"], "2026-09-09T19:30:00-07:00")
        self.assertEqual(
            pago["dateKey"], "2026-09-09",
            "Un cobro de las 19:30 pertenece al día de la clínica, no al siguiente en UTC.",
        )


class RegisterTest(unittest.TestCase):
    def _register(self, **overrides):
        base = {"id": 7, "fecha_apertura": "2026-09-09", "hora_apertura": "08:00",
                "acumulado": 5000.0, "gastos": 200.0, "saldo_total": 4800.0}
        base.update(overrides)
        return base

    def test_an_open_register_has_no_closing_time(self):
        caja = normalize_registers([self._register()])[0]
        self.assertIsNone(caja["closedAt"])
        self.assertEqual(caja["totalCollectedCents"], 500000)

    def test_a_closed_register_records_when_it_closed(self):
        caja = normalize_registers([self._register(fecha_cierre="2026-09-09", hora_cierre="19:45")])[0]
        self.assertEqual(caja["closedAt"], "2026-09-09T19:45:00-07:00")

    def test_a_register_without_opening_date_is_rejected(self):
        with self.assertRaises(SchemaError):
            normalize_registers([{"id": 7, "acumulado": 1}])


class CollectionSnapshotTest(unittest.TestCase):
    """El corte del día sale de las mismas cajas y cobros, no de otra consulta."""

    def setUp(self):
        self.payments = normalize_payments([
            {"id": 1, "fecha_recepcion": "2026-09-09", "monto_pago": 1000, "medio_pago": "Efectivo", "id_caja": 7},
            {"id": 2, "fecha_recepcion": "2026-09-09", "monto_pago": 500, "medio_pago": "Tarjeta", "id_caja": 7},
            {"id": 3, "fecha_recepcion": "2026-09-09", "monto_pago": 250, "medio_pago": "Efectivo", "id_caja": 8},
        ])

    def test_an_open_register_reports_the_sum_of_its_payments(self):
        registers = normalize_registers([
            {"id": 7, "fecha_apertura": "2026-09-09", "acumulado": 9999.0, "gastos": 0, "saldo_total": 0},
        ])
        corte = build_collection_snapshots(registers, self.payments, "2026-09-09T20:00:00.000Z")[0]
        self.assertEqual(corte["state"], "OPEN")
        self.assertEqual(
            corte["totalCollectedCents"], 150000,
            "Mientras la caja sigue abierta manda la suma de sus cobros, no un acumulado que aún se mueve.",
        )
        self.assertEqual(corte["paymentCount"], 2)

    def test_a_closed_register_is_authoritative(self):
        registers = normalize_registers([
            {"id": 7, "fecha_apertura": "2026-09-09", "fecha_cierre": "2026-09-09",
             "acumulado": 1500.0, "gastos": 0, "saldo_total": 1500.0},
        ])
        corte = build_collection_snapshots(registers, self.payments, "2026-09-09T20:00:00.000Z")[0]
        self.assertEqual(corte["state"], "CLOSED")
        self.assertEqual(corte["totalCollectedCents"], 150000)

    def test_several_registers_produce_separate_snapshots(self):
        registers = normalize_registers([
            {"id": 7, "fecha_apertura": "2026-09-09", "acumulado": 0, "gastos": 0, "saldo_total": 0},
            {"id": 8, "fecha_apertura": "2026-09-09", "acumulado": 0, "gastos": 0, "saldo_total": 0},
        ])
        cortes = build_collection_snapshots(registers, self.payments, "2026-09-09T20:00:00.000Z")
        self.assertEqual(len(cortes), 2)
        self.assertEqual({c["registerId"] for c in cortes}, {"7", "8"})
        # Cada caja cuenta solo lo suyo: sumarlas da el día, sin duplicar.
        self.assertEqual(sum(c["totalCollectedCents"] for c in cortes), 175000)

    def test_the_snapshot_identifier_is_stable(self):
        # Reejecutar la sincronización debe actualizar el mismo corte, no crear otro.
        registers = normalize_registers([
            {"id": 7, "fecha_apertura": "2026-09-09", "acumulado": 0, "gastos": 0, "saldo_total": 0},
        ])
        a = build_collection_snapshots(registers, self.payments, "2026-09-09T20:00:00.000Z")[0]
        b = build_collection_snapshots(registers, self.payments, "2026-09-09T23:00:00.000Z")[0]
        self.assertEqual(a["id"], b["id"])


if __name__ == "__main__":
    unittest.main()

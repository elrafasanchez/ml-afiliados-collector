"""Pruebas de los trabajos de Dental Amigo, sin red."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from dental.clinic_time import HERMOSILLO
from dental.errors import AuthError, TransientError, ValidationError
from dental.jobs import DentalCollector, validate_snapshot
from dental.normalize import normalize_payments

# Media tarde del 9 de septiembre en la clínica.
NOW = datetime(2026, 9, 9, 16, 30, tzinfo=HERMOSILLO)


class FakeAPI:
    """API de mentira que registra qué recursos y rangos se pidieron."""

    def __init__(self, *, resources=None, failures=None):
        self.resources = resources or {}
        self.failures = failures or {}
        self.calls: list[tuple[str, dict | None]] = []
        self.requests_made = 0

    def collect(self, path, *, params=None, **kwargs):
        self.calls.append((path, params))
        self.requests_made += 1
        if path in self.failures:
            raise self.failures[path]
        return list(self.resources.get(path, []))

    @staticmethod
    def date_filter(field, start, end):
        from dental.dentalink import DentalinkAPI
        return DentalinkAPI.date_filter(field, start, end)


def base_resources(**overrides):
    resources = {
        "/dentistas": [
            {"id": 5, "nombre": "Ana Torres", "especialidad": "Ortodoncia", "habilitado": True},
            {"id": 6, "nombre": "Luis Vega", "especialidad": "General", "habilitado": True},
        ],
        "/cajas": [
            {"id": 7, "fecha_apertura": "2026-09-09", "hora_apertura": "08:00",
             "acumulado": 1500.0, "gastos": 0.0, "saldo_total": 1500.0},
        ],
        "/pagos": [
            {"id": 1, "fecha_recepcion": "2026-09-09", "monto_pago": 1000.0,
             "medio_pago": "Efectivo", "id_caja": 7},
            {"id": 2, "fecha_recepcion": "2026-09-09", "monto_pago": 500.0,
             "medio_pago": "Tarjeta", "id_caja": 7},
        ],
        "/citas": [
            {"id": 30, "fecha": "2026-09-09", "hora_inicio": "10:00", "hora_fin": "11:00",
             "id_dentista": 5, "estado": "Atendido", "id_paciente": 100,
             "nombre_paciente": "Rosa Díaz López"},
            {"id": 31, "fecha": "2026-09-09", "hora_inicio": "17:00", "hora_fin": "18:00",
             "id_dentista": 6, "estado": "En sala de espera", "id_paciente": 101,
             "nombre_paciente": "Pedro Lara Sosa"},
        ],
    }
    resources.update(overrides)
    return resources


class OperationsJobTest(unittest.TestCase):
    def test_reads_the_clinic_day_and_not_the_utc_day(self):
        api = FakeAPI(resources=base_resources())
        DentalCollector(api).run_operations(now=NOW)
        rangos = [p for path, p in api.calls if path == "/citas"]
        self.assertIn("2026-09-09", str(rangos[0]))

    def test_collects_every_resource_the_page_needs(self):
        api = FakeAPI(resources=base_resources())
        result = DentalCollector(api).run_operations(now=NOW)
        self.assertEqual(result.counts(), {
            "doctors": 2, "appointments": 2, "registers": 1,
            "payments": 2, "snapshots": 1, "payrolls": 0,
        })

    def test_stays_within_a_modest_request_budget(self):
        api = FakeAPI(resources=base_resources())
        result = DentalCollector(api).run_operations(now=NOW)
        self.assertLessEqual(result.requests_made, 5, "una corrida de 20 min debe ser barata")

    def test_patient_names_never_leave_whole(self):
        api = FakeAPI(resources=base_resources())
        result = DentalCollector(api).run_operations(now=NOW)
        referencias = [a["patientReference"] for a in result.snapshot.appointments]
        self.assertEqual(sorted(referencias), ["Pedro S.", "Rosa L."])
        self.assertNotIn("Rosa Díaz López", str(result.snapshot.appointments))


class ReconciliationTest(unittest.TestCase):
    def test_an_open_register_does_not_warn_about_a_gap(self):
        # Con la caja abierta el acumulado todavía se mueve: una diferencia ahí
        # es esperable y advertirlo cada veinte minutos sería ruido.
        api = FakeAPI(resources=base_resources(**{
            "/cajas": [{"id": 7, "fecha_apertura": "2026-09-09", "acumulado": 9999.0,
                        "gastos": 0.0, "saldo_total": 0.0}],
        }))
        result = DentalCollector(api).run_operations(now=NOW)
        self.assertEqual(result.reconciliation["closedRegisters"], 0)
        self.assertEqual(result.warnings, [])

    def test_a_closed_register_that_disagrees_does_warn(self):
        api = FakeAPI(resources=base_resources(**{
            "/cajas": [{"id": 7, "fecha_apertura": "2026-09-09", "fecha_cierre": "2026-09-09",
                        "acumulado": 9999.0, "gastos": 0.0, "saldo_total": 9999.0}],
        }))
        result = DentalCollector(api).run_operations(now=NOW)
        self.assertTrue(any("corte del día" in w for w in result.warnings))
        self.assertEqual(result.reconciliation["differenceCents"], 999900 - 150000)

    def test_a_closed_register_that_agrees_stays_quiet(self):
        api = FakeAPI(resources=base_resources(**{
            "/cajas": [{"id": 7, "fecha_apertura": "2026-09-09", "fecha_cierre": "2026-09-09",
                        "acumulado": 1500.0, "gastos": 0.0, "saldo_total": 1500.0}],
        }))
        result = DentalCollector(api).run_operations(now=NOW)
        self.assertEqual(result.reconciliation["differenceCents"], 0)
        self.assertEqual(result.warnings, [])

    def test_a_day_without_registers_is_reported(self):
        api = FakeAPI(resources=base_resources(**{"/cajas": [], "/pagos": []}))
        result = DentalCollector(api).run_operations(now=NOW)
        self.assertTrue(any("ninguna caja" in w for w in result.warnings))


class FailureIsNeverZeroTest(unittest.TestCase):
    """Un fallo se propaga; jamás se convierte en cobros de cero."""

    def test_an_expired_token_propagates(self):
        api = FakeAPI(resources=base_resources(), failures={"/pagos": AuthError("token revocado")})
        with self.assertRaises(AuthError):
            DentalCollector(api).run_operations(now=NOW)

    def test_a_transient_failure_propagates(self):
        api = FakeAPI(resources=base_resources(), failures={"/cajas": TransientError("502")})
        with self.assertRaises(TransientError):
            DentalCollector(api).run_operations(now=NOW)

    def test_a_genuine_day_without_payments_is_data(self):
        # Cero cobros informados por la fuente sí es un dato válido.
        api = FakeAPI(resources=base_resources(**{"/pagos": []}))
        result = DentalCollector(api).run_operations(now=NOW)
        self.assertEqual(result.reconciliation["paymentsCents"], 0)
        validate_snapshot(result)


class ValidationTest(unittest.TestCase):
    def test_duplicate_payments_are_refused(self):
        api = FakeAPI(resources=base_resources())
        result = DentalCollector(api).run_operations(now=NOW)
        result.snapshot.cash_register_payments.append(result.snapshot.cash_register_payments[0])
        with self.assertRaises(ValidationError):
            validate_snapshot(result)

    def test_an_appointment_without_its_doctor_is_refused(self):
        api = FakeAPI(resources=base_resources(**{
            "/citas": [{"id": 30, "fecha": "2026-09-09", "hora_inicio": "10:00",
                        "id_dentista": 999, "estado": "Atendido", "id_paciente": 100}],
        }))
        result = DentalCollector(api).run_operations(now=NOW)
        with self.assertRaises(ValidationError) as caught:
            validate_snapshot(result)
        self.assertIn("doctor", str(caught.exception))

    def test_a_correct_snapshot_passes(self):
        api = FakeAPI(resources=base_resources())
        result = DentalCollector(api).run_operations(now=NOW)
        validate_snapshot(result)


class MonthJobTest(unittest.TestCase):
    def test_reads_from_the_first_of_the_month_to_today(self):
        api = FakeAPI(resources=base_resources())
        result = DentalCollector(api).run_month(now=NOW)
        self.assertEqual(result.reconciliation["start"], "2026-09-01")
        self.assertEqual(result.reconciliation["end"], "2026-09-09")

    def test_the_month_does_not_re_read_the_agenda(self):
        # La agenda de días pasados no cambia; pedirla otra vez sería gastar
        # peticiones contra un tercero para reescribir lo mismo.
        api = FakeAPI(resources=base_resources())
        DentalCollector(api).run_month(now=NOW)
        self.assertNotIn("/citas", [path for path, _ in api.calls])


if __name__ == "__main__":
    unittest.main()

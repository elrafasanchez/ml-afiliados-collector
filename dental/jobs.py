"""Trabajos de sincronización de Dental Amigo.

La cadencia responde a cómo cambia cada dato, no a un número redondo:

    operations  cada 20 min   agenda, cajas y cobros del día en curso
    month       cada 6 horas  relee el mes para recoger revisiones de Dentalink
    payrolls    cada 6 horas  liquidaciones activas y del periodo

La agenda cambia durante la jornada; el histórico del mes casi nunca. Releer el
mes entero cada veinte minutos gastaría peticiones contra un servicio de
terceros para volver a escribir lo mismo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .clinic_time import clinic_today, iso_utc, month_start
from .dentalink import DentalinkAPI
from .errors import DentalError, ValidationError
from .logging_setup import log
from .normalize import (
    ClinicSnapshot,
    build_collection_snapshots,
    confirmed_empty_days,
    normalize_appointments,
    normalize_doctors,
    normalize_payments,
    normalize_registers,
)


@dataclass
class SyncResult:
    job: str
    started_at: str
    snapshot: ClinicSnapshot = field(default_factory=ClinicSnapshot)
    warnings: list[str] = field(default_factory=list)
    reconciliation: dict[str, Any] = field(default_factory=dict)
    requests_made: int = 0
    #: Marca real de la fuente: el instante más reciente que Dentalink reporta,
    #: distinto del momento en que corrió el trabajo.
    source_timestamp: str | None = None

    def counts(self) -> dict[str, int]:
        return {
            "doctors": len(self.snapshot.doctors),
            "appointments": len(self.snapshot.appointments),
            "registers": len(self.snapshot.cash_registers),
            "payments": len(self.snapshot.cash_register_payments),
            "snapshots": len(self.snapshot.collection_snapshots),
            "payrolls": len(self.snapshot.payrolls),
        }


class DentalCollector:
    """Orquesta las lecturas de Dentalink sobre un cliente ya autenticado."""

    def __init__(self, api: DentalinkAPI) -> None:
        self._api = api

    # ------------------------------------------------------------- primitivas

    def read_doctors(self) -> list[dict[str, Any]]:
        return normalize_doctors(self._api.collect("/dentistas"))

    def read_range(self, start: date, end: date) -> tuple[list, list, list]:
        """Cajas, cobros y cortes de un rango de días de clínica."""
        registers = normalize_registers(
            self._api.collect("/cajas", params=DentalinkAPI.date_filter("fecha_apertura", start, end))
        )
        payments = normalize_payments(
            self._api.collect("/pagos", params=DentalinkAPI.date_filter("fecha_recepcion", start, end))
        )
        now = iso_utc()
        snapshots = build_collection_snapshots(registers, payments, now)
        # Los días del rango que la fuente respondió sin cajas se marcan como
        # ceros confirmados. Un día ausente significa "no leímos", nunca "$0".
        dias = []
        cursor = start
        while cursor <= end:
            dias.append(cursor.isoformat())
            cursor = cursor + timedelta(days=1)
        snapshots.extend(confirmed_empty_days(snapshots, dias, now))
        return registers, payments, snapshots

    def read_appointments(self, day: date) -> list[dict[str, Any]]:
        return normalize_appointments(
            self._api.collect("/citas", params=DentalinkAPI.date_filter("fecha", day, day))
        )

    # --------------------------------------------------------------- trabajos

    def run_operations(self, *, now: datetime | None = None) -> SyncResult:
        """Lo que cambia durante la jornada: agenda, cajas y cobros de hoy."""
        today = clinic_today(now)
        result = SyncResult(job="operations", started_at=iso_utc())

        result.snapshot.doctors = self.read_doctors()
        registers, payments, snapshots = self.read_range(today, today)
        result.snapshot.cash_registers = registers
        result.snapshot.cash_register_payments = payments
        result.snapshot.collection_snapshots = snapshots
        result.snapshot.appointments = self.read_appointments(today)

        self._reconcile_day(result, today, registers, payments, snapshots)
        result.requests_made = self._api.requests_made
        result.source_timestamp = iso_utc()
        return result

    def run_month(self, *, now: datetime | None = None) -> SyncResult:
        """Relee el mes en curso para recoger revisiones de la fuente."""
        today = clinic_today(now)
        result = SyncResult(job="month", started_at=iso_utc())

        registers, payments, snapshots = self.read_range(month_start(today), today)
        result.snapshot.cash_registers = registers
        result.snapshot.cash_register_payments = payments
        result.snapshot.collection_snapshots = snapshots

        result.reconciliation = {
            "scope": "month",
            "start": month_start(today).isoformat(),
            "end": today.isoformat(),
            "registers": len(registers),
            "payments": len(payments),
            "collectedCents": sum(s["totalCollectedCents"] for s in snapshots),
            "paymentsCents": sum(p["amountCents"] for p in payments),
        }
        result.requests_made = self._api.requests_made
        result.source_timestamp = iso_utc()
        return result

    # ---------------------------------------------------------- reconciliación

    @staticmethod
    def _reconcile_day(
        result: SyncResult,
        day: date,
        registers: list[dict[str, Any]],
        payments: list[dict[str, Any]],
        snapshots: list[dict[str, Any]],
    ) -> None:
        """Contrasta el corte contra el detalle del que sale.

        Una diferencia se informa; nunca se ajusta ninguno de los dos para
        forzar el cuadre.
        """
        detail = sum(p["amountCents"] for p in payments)
        closed = [s for s in snapshots if s["state"] == "CLOSED"]
        cut = sum(s["totalCollectedCents"] for s in snapshots)

        result.reconciliation = {
            "scope": "today",
            "dateKey": day.isoformat(),
            "registers": len(registers),
            "closedRegisters": len(closed),
            "payments": len(payments),
            "paymentsCents": detail,
            "snapshotCents": cut,
            "differenceCents": cut - detail,
        }

        # Con todas las cajas cerradas los dos números deben coincidir. Con
        # alguna abierta la diferencia es esperable y no merece advertencia.
        if closed and len(closed) == len(registers) and cut != detail:
            result.warnings.append(
                f"El corte del día suma {cut} centavos frente a {detail} del detalle de cobros."
            )
        if not registers:
            result.warnings.append(
                f"Dentalink no reporta ninguna caja para {day.isoformat()}."
            )


def validate_snapshot(result: SyncResult) -> None:
    """Invariantes previas a publicar. Un fallo detiene la publicación."""
    problems: list[str] = []
    snapshot = result.snapshot

    for payment in snapshot.cash_register_payments:
        if payment["amountCents"] < 0:
            problems.append(f"El cobro {payment['id']} es negativo.")
    for register in snapshot.cash_registers:
        if register["totalCollectedCents"] < 0 or register["totalExpensesCents"] < 0:
            problems.append(f"La caja {register['id']} tiene importes negativos.")

    ids = [p["id"] for p in snapshot.cash_register_payments]
    if len(ids) != len(set(ids)):
        problems.append("Hay cobros repetidos en el lote.")

    doctor_ids = {d["id"] for d in snapshot.doctors}
    if doctor_ids:
        huérfanas = [a["id"] for a in snapshot.appointments if a["doctorId"] not in doctor_ids]
        if huérfanas:
            problems.append(
                f"{len(huérfanas)} citas apuntan a un doctor que no vino en el lote."
            )

    if problems:
        raise ValidationError("La instantánea no superó la validación: " + " ".join(problems))

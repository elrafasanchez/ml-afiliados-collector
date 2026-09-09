"""Traducción de la API de Dentalink al modelo canónico de Dental Amigo.

El vocabulario canónico ya existe en el sitio (`types/dental-amigo.ts`) y no se
reinventa aquí: este módulo solo traduce.

Dos decisiones deliberadas:

**El estado de la cita se envía crudo.** El sitio ya resuelve el estado real con
prioridad sobre la inferencia horaria, e interpreta el español con sus flexiones.
Duplicar esa regla en Python crearía dos verdades que se separarían con el
tiempo, así que aquí se manda `sourceStatus` verbatim y un `status` canónico
conservador.

**Los datos de paciente se minimizan antes de salir de este proceso.** El
tablero solo necesita distinguir personas, no identificarlas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .clinic_time import clinic_date_key, to_iso_clinic
from .errors import SchemaError


def _require(row: dict[str, Any], field_name: str, path: str) -> Any:
    """Exige un campo. Un campo ausente es un cambio de contrato, no un vacío.

    Devolver ``None`` en silencio convertiría una API que cambió de forma en
    ceros contables, que es exactamente lo que no debe pasar.
    """
    if field_name not in row:
        raise SchemaError(f"{path}: la API dejó de exponer `{field_name}`.")
    return row[field_name]


def _cents(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SchemaError(f"{path}: importe no numérico ({value!r}).")
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError) as error:
        raise SchemaError(f"{path}: importe ilegible ({value!r}).") from error


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def patient_reference(name: Any, patient_id: Any) -> str:
    """Minimiza el nombre a *nombre de pila + inicial del apellido*.

    El tablero necesita distinguir a una persona de otra dentro de una agenda,
    no identificarla. Guardar el nombre completo sería recoger más de lo que el
    producto usa.
    """
    text = _text(name)
    if not text:
        return f"Paciente {patient_id}" if patient_id is not None else "Paciente"
    parts = [p for p in re.split(r"\s+", text) if p]
    if not parts:
        return f"Paciente {patient_id}"
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0].upper()}."


#: Medios de pago de Dentalink → vocabulario canónico. Lo desconocido es OTHER,
#: nunca se fuerza a efectivo.
_PAYMENT_METHODS = (
    (r"efectivo|cash", "CASH"),
    (r"transfer|deposito|spei", "TRANSFER"),
    (r"tarjeta|credito|debito|card|terminal", "CARD"),
    (r"mixto|combinad", "MIXED"),
)


def payment_method(value: Any) -> str:
    text = (_text(value) or "").lower()
    for pattern, canonical in _PAYMENT_METHODS:
        if re.search(pattern, text):
            return canonical
    return "OTHER"


#: Estados de Dentalink → estado canónico de la ingesta. El texto original viaja
#: aparte en `sourceStatus`, que es lo que el sitio usa para decidir de verdad.
_APPOINTMENT_STATUS = (
    (r"no asiste|inasistent", "NO_SHOW"),
    (r"cancelad|anulad|reprogram|deshabilitad", "CANCELLED"),
    (r"atendiendose|en atencion|tratamiento iniciado", "IN_TREATMENT"),
    (r"sala de espera|en espera", "WAITING"),
    (r"atendido|completad|finalizad|terminad|realizad", "ATTENDED"),
    (r"confirmad|notificad", "CONFIRMED"),
    (r"no confirmad|programad|agendad|citad", "UNCONFIRMED"),
)


def _strip_accents(text: str) -> str:
    import unicodedata

    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    )


def appointment_status(raw: Any) -> str:
    text = _strip_accents((_text(raw) or "").lower())
    for pattern, canonical in _APPOINTMENT_STATUS:
        if re.search(pattern, text):
            return canonical
    return "OTHER"


@dataclass
class ClinicSnapshot:
    """Instantánea normalizada, lista para la ruta de ingesta existente."""

    doctors: list[dict[str, Any]] = field(default_factory=list)
    appointments: list[dict[str, Any]] = field(default_factory=list)
    cash_registers: list[dict[str, Any]] = field(default_factory=list)
    cash_register_payments: list[dict[str, Any]] = field(default_factory=list)
    collection_snapshots: list[dict[str, Any]] = field(default_factory=list)
    payrolls: list[dict[str, Any]] = field(default_factory=list)

    def as_payload(self, synced_at: str, *, active_payrolls_complete: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": "CLINIC_SALES",
            "syncedAt": synced_at,
            "doctors": self.doctors,
            "appointments": self.appointments,
            "cashRegisters": self.cash_registers,
            "cashRegisterPayments": self.cash_register_payments,
            "collectionSnapshots": self.collection_snapshots,
        }
        if self.payrolls or active_payrolls_complete:
            payload["payrolls"] = self.payrolls
            payload["payrollsSourceUpdatedAt"] = synced_at
            if active_payrolls_complete:
                payload["activePayrollsComplete"] = True
        return payload


def normalize_doctors(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Profesionales activos de la clínica."""
    doctors = []
    for row in rows:
        identifier = _require(row, "id", "dentistas")
        nombre = _text(row.get("nombre")) or _text(row.get("nombre_completo")) or f"Doctor {identifier}"
        doctors.append({
            "id": str(identifier),
            "name": nombre,
            "specialty": _text(row.get("especialidad")),
            # `habilitado` es el campo que Dentalink usa para dar de baja sin
            # borrar. Si no viene, se asume activo antes que ocultar a alguien.
            "active": bool(row.get("habilitado", row.get("activo", True))),
            "userId": _text(row.get("id_usuario")),
            "commissionRateBps": None,
            "contractType": _text(row.get("tipo_contrato")),
        })
    return doctors


def normalize_payments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cobros individuales, tal como los registra cada caja."""
    payments = []
    for row in rows:
        identifier = _require(row, "id", "pagos")
        fecha = _text(_require(row, "fecha_recepcion", "pagos"))
        if not fecha:
            raise SchemaError("pagos: un cobro llegó sin `fecha_recepcion`.")
        paid_at = to_iso_clinic(fecha, time_part=_text(row.get("hora_recepcion")) or "12:00:00")
        payments.append({
            "id": str(identifier),
            "dateKey": clinic_date_key(paid_at),
            "paidAt": paid_at,
            "registerId": _text(row.get("id_caja")),
            "paymentMethod": payment_method(row.get("medio_pago")),
            "amountCents": _cents(_require(row, "monto_pago", "pagos"), "pagos.monto_pago"),
            "responsibleReference": _text(row.get("id_usuario")),
            "sourceReference": _text(row.get("folio_boleta")),
        })
    return payments


def normalize_registers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cajas del periodo.

    ``estado`` distingue abierta de cerrada. Solo una caja **cerrada** aporta un
    total definitivo; mientras siga abierta su saldo todavía puede moverse, y el
    sitio ya prefiere el cierre sobre la apertura cuando ambos existen.
    """
    registers = []
    for row in rows:
        identifier = _require(row, "id", "cajas")
        opened = _text(_require(row, "fecha_apertura", "cajas"))
        if not opened:
            raise SchemaError("cajas: una caja llegó sin `fecha_apertura`.")
        closed = _text(row.get("fecha_cierre"))
        registers.append({
            "id": str(identifier),
            "openedAt": to_iso_clinic(opened, time_part=_text(row.get("hora_apertura")) or "08:00:00"),
            "closedAt": to_iso_clinic(closed, time_part=_text(row.get("hora_cierre")) or "20:00:00") if closed else None,
            "totalCollectedCents": _cents(row.get("acumulado", row.get("saldo_total", 0)), "cajas.acumulado"),
            "totalExpensesCents": _cents(row.get("gastos", 0), "cajas.gastos"),
            "closingBalanceCents": _cents(row.get("saldo_total", 0), "cajas.saldo_total"),
            "sourceReference": str(identifier),
        })
    return registers


def build_collection_snapshots(
    registers: list[dict[str, Any]],
    payments: list[dict[str, Any]],
    updated_at: str,
) -> list[dict[str, Any]]:
    """Un corte por caja y día, con su estado y lo cobrado en ella.

    Se construye a partir de las cajas y los cobros ya normalizados, no de una
    tercera consulta: así el corte no puede contradecir al detalle del que sale.
    """
    snapshots = []
    for register in registers:
        date_key = clinic_date_key(register["openedAt"])
        own = [p for p in payments if p["registerId"] == register["id"]]
        snapshots.append({
            "id": f"register-{register['id']}-{date_key}",
            "dateKey": date_key,
            "registerId": register["id"],
            "state": "CLOSED" if register["closedAt"] else "OPEN",
            "paymentCount": len(own),
            # Una caja cerrada manda con su total; una abierta se describe con
            # la suma de sus cobros, que es lo único definitivo hasta el cierre.
            "totalCollectedCents": register["totalCollectedCents"] if register["closedAt"]
            else sum(p["amountCents"] for p in own),
            "totalExpensesCents": register["totalExpensesCents"],
            "responsibleReference": None,
            "sourceReference": register["sourceReference"],
            "updatedAt": updated_at,
        })
    return snapshots


def normalize_appointments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Citas del día, con su estado crudo intacto."""
    appointments = []
    for row in rows:
        identifier = _require(row, "id", "citas")
        fecha = _text(_require(row, "fecha", "citas"))
        if not fecha:
            raise SchemaError("citas: una cita llegó sin `fecha`.")
        inicio = _text(row.get("hora_inicio")) or "00:00:00"
        scheduled = to_iso_clinic(fecha, time_part=inicio)
        raw_status = _text(row.get("estado")) or _text(row.get("nombre_estado")) or ""
        appointments.append({
            "id": str(identifier),
            "scheduledAt": scheduled,
            "durationMinutes": _duration(inicio, _text(row.get("hora_fin"))),
            "patientReference": patient_reference(row.get("nombre_paciente"), row.get("id_paciente")),
            "doctorId": str(_require(row, "id_dentista", "citas")),
            "sourceReference": _text(row.get("id_sucursal")),
            # Verbatim: el sitio decide con esto, no con nuestra interpretación.
            "sourceStatus": raw_status,
            "status": appointment_status(raw_status),
            "box": _text(row.get("id_sillon")) or _text(row.get("sillon")),
            "arrivalAt": None,
            "treatmentStartedAt": None,
            "treatmentEndedAt": None,
            "statusUpdatedAt": None,
        })
    return appointments


def _duration(start: str, end: str | None) -> int:
    """Duración en minutos; 30 por omisión cuando la fuente no da el fin."""
    if not end:
        return 30
    try:
        sh, sm = (int(x) for x in start.split(":")[:2])
        eh, em = (int(x) for x in end.split(":")[:2])
    except (ValueError, IndexError):
        return 30
    minutes = (eh * 60 + em) - (sh * 60 + sm)
    return minutes if minutes > 0 else 30

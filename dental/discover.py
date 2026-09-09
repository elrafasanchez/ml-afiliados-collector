"""Reconocimiento de la forma real de la API, sin exponer datos de pacientes.

La documentación de Dentalink describe los recursos, pero no todos sus campos ni
cómo se atribuye un cobro a su doctor y tratamiento. Adivinar ese mapeo produce
cifras plausibles y equivocadas, que es el peor resultado posible en algo
contable.

Este módulo pide una página de cada recurso y reporta **qué campos existen y de
qué tipo son**, con los valores enmascarados. Con eso el mapeo se fija contra la
realidad en una sola pasada, y el informe puede pegarse en cualquier sitio sin
filtrar información clínica.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from .clinic_time import clinic_today
from .dentalink import DentalinkAPI
from .errors import DentalError

#: Campos cuyo valor no debe salir jamás del proceso, ni siquiera como muestra.
_SENSITIVE = (
    "nombre", "paciente", "cliente", "rut", "dni", "email", "correo",
    "telefono", "celular", "direccion", "observacion", "comentario", "nota",
)


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return "object"
    return "str"


def _sample(field: str, value: Any) -> str:
    """Muestra segura: los campos con posible dato personal se enmascaran."""
    lowered = field.lower()
    if any(marker in lowered for marker in _SENSITIVE):
        return "«oculto»"
    if isinstance(value, str):
        return value[:40]
    if isinstance(value, (int, float, bool)) or value is None:
        return str(value)
    return "…"


def describe(rows: list[dict[str, Any]], limit: int = 3) -> list[str]:
    """Describe los campos observados en un conjunto de filas."""
    if not rows:
        return ["  (sin filas en el rango consultado)"]

    campos: dict[str, set[str]] = {}
    for row in rows[:50]:
        for field, value in row.items():
            campos.setdefault(field, set()).add(_kind(value))

    lines = []
    for field in sorted(campos):
        tipos = "|".join(sorted(campos[field]))
        muestras = {_sample(field, row.get(field)) for row in rows[:limit] if field in row}
        lines.append(f"  {field:28} {tipos:12} ej: {', '.join(sorted(muestras))[:70]}")
    return lines


#: Recursos a inspeccionar y cómo acotarlos, para no descargar el histórico.
def _plan(days_back: int):
    today = clinic_today()
    since = today - timedelta(days=days_back)
    return [
        ("dentistas", "/dentistas", None),
        ("cajas", "/cajas", DentalinkAPI.date_filter("fecha_apertura", since, today)),
        ("pagos", "/pagos", DentalinkAPI.date_filter("fecha_recepcion", since, today)),
        ("citas", "/citas", DentalinkAPI.date_filter("fecha", today, today)),
        ("liquidaciones", "/liquidaciones", None),
        ("sucursales", "/sucursales", None),
    ]


def run(api: DentalinkAPI, *, days_back: int = 7, per_resource: int = 40) -> str:
    """Devuelve un informe legible de la forma real de cada recurso."""
    report: list[str] = [
        "RECONOCIMIENTO DE LA API DE DENTALINK",
        "Los valores con posible dato personal aparecen como «oculto».",
        "",
    ]

    for nombre, path, params in _plan(days_back):
        report.append(f"── {nombre}  ({path}) ".ljust(78, "─"))
        try:
            rows: list[dict[str, Any]] = []
            for row in api.paginate(path, params=params, max_pages=3):
                rows.append(row)
                if len(rows) >= per_resource:
                    break
            report.append(f"  filas leídas: {len(rows)}")
            report.extend(describe(rows))
        except DentalError as error:
            report.append(f"  ✗ {type(error).__name__}: {error}")
        except Exception as error:  # noqa: BLE001 - el reconocimiento no debe abortar
            report.append(f"  ✗ inesperado: {type(error).__name__}: {error}")
        report.append("")

    report.append(f"peticiones realizadas: {api.requests_made}")
    return "\n".join(report)

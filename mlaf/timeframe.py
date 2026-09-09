"""Calendario y rangos, sin fechas fijas en el código.

Todo el producto razona en el calendario local de **America/Hermosillo**. Sonora
no aplica horario de verano, así que el desfase es UTC-7 constante; usar un
desfase fijo evita depender de que la base de datos de zonas horarias esté
instalada en el contenedor donde corra el recolector.

El filtro que acepta Mercado Libre usa un desfase **-03:00** que no corresponde
a la zona del usuario: es el huso interno del servicio. Lo que delimita el día
es el par de marcas, no el huso, así que se reproduce literalmente.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

HERMOSILLO = timezone(timedelta(hours=-7), name="America/Hermosillo")
SOURCE_FILTER_OFFSET = "-03:00"


def today_in_hermosillo(now: datetime | None = None) -> date:
    """Fecha natural vigente para el usuario. Nunca se codifica un día fijo."""
    moment = now.astimezone(HERMOSILLO) if now else datetime.now(HERMOSILLO)
    return moment.date()


def month_start(day: date) -> date:
    return day.replace(day=1)


def next_month_start(day: date) -> date:
    first = month_start(day)
    if first.month == 12:
        return date(first.year + 1, 1, 1)
    return date(first.year, first.month + 1, 1)


def filter_time_range(start: date, end_exclusive: date) -> str:
    """Construye el valor de ``filter_time_range`` que espera la fuente."""
    return (
        f"{start.isoformat()}T00:00:00.000{SOURCE_FILTER_OFFSET}--"
        f"{end_exclusive.isoformat()}T00:00:00.000{SOURCE_FILTER_OFFSET}"
    )


def day_range(day: date) -> tuple[date, date]:
    return day, day + timedelta(days=1)


def month_range(day: date) -> tuple[date, date]:
    """Rango del mes vigente que **sí** incluye el día en curso.

    TRAMPA DE LA FUENTE
    -------------------
    Cuando el rango solicitado se extiende más allá del día actual, Mercado
    Libre lo trunca en *ayer* y descarta el día en curso. Verificado el
    2026-09-08 con el mes de septiembre:

        2026-09-01 .. 2026-10-01  (mes natural)  →  232,556.70   ← sin el día 8
        2026-09-01 .. 2026-09-09  (hasta mañana) →  264,756.96   ← con el día 8
        2026-09-08 .. 2026-09-09  (solo el día 8)→   32,200.26

    Pedir el mes natural devolvería el mes **sin hoy**, subestimándolo en un día
    entero sin ningún aviso. Por eso el rango termina siempre en mañana, que
    dentro del mes en curso nunca supera al primero del mes siguiente y coincide
    con él el último día del mes.
    """
    return month_start(day), clamp_to_tomorrow(next_month_start(day), day)


def clamp_to_tomorrow(end_exclusive: date, today: date) -> date:
    """Recorta el fin de un rango para que la fuente no descarte el día actual.

    Ver la explicación en :func:`month_range`. Cualquier consulta cuyo fin caiga
    después de mañana debe pasar por aquí.
    """
    tomorrow = today + timedelta(days=1)
    return min(end_exclusive, tomorrow)


def date_span(start: date, end_exclusive: date):
    """Itera un día natural a la vez, de más reciente a más antiguo."""
    current = end_exclusive - timedelta(days=1)
    while current >= start:
        yield current
        current -= timedelta(days=1)


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )

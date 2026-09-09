"""Calendario de la clínica.

Todo el negocio de Dental Amigo razona en **America/Hermosillo**. Sonora no
aplica horario de verano, así que el desfase es UTC-7 constante; usar un desfase
fijo evita depender de que la base de zonas horarias esté instalada en el
contenedor donde corra el recolector.

GitHub programa en UTC. Convertir mal aquí desplazaría el día de la clínica y
mandaría los cobros de la tarde al día siguiente.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

HERMOSILLO = timezone(timedelta(hours=-7), name="America/Hermosillo")


def clinic_today(now: datetime | None = None) -> date:
    """Día natural vigente para la clínica. Nunca se codifica una fecha fija."""
    moment = now.astimezone(HERMOSILLO) if now else datetime.now(HERMOSILLO)
    return moment.date()


def clinic_date_key(value: datetime | str) -> str:
    """Fecha natural de la clínica para una marca de tiempo cualquiera."""
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=HERMOSILLO)
    return parsed.astimezone(HERMOSILLO).date().isoformat()


def month_start(day: date) -> date:
    return day.replace(day=1)


def commission_window(now: datetime | None = None) -> tuple[date, date]:
    """Semana de comisiones vigente: **viernes a jueves**.

    A los doctores se les paga el viernes por transferencia, así que la semana
    que se está acumulando empieza el viernes anterior (o el mismo día, si hoy
    es viernes) y termina el jueves siguiente.
    """
    today = clinic_today(now)
    # weekday(): lunes=0 … viernes=4. Días transcurridos desde el último viernes.
    days_since_friday = (today.weekday() - 4) % 7
    start = today - timedelta(days=days_since_friday)
    return start, start + timedelta(days=6)


def iso_utc(moment: datetime | None = None) -> str:
    return (
        (moment or datetime.now(timezone.utc))
        .astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def to_iso_clinic(value: str, *, time_part: str = "00:00:00") -> str:
    """Combina una fecha (y hora opcional) de Dentalink en ISO con zona.

    Dentalink entrega fechas y horas por separado y sin zona horaria; son horas
    locales de la clínica. Adjuntar el desfase explícitamente evita que se
    interpreten como UTC más adelante.
    """
    day = value.strip()[:10]
    clock = (time_part or "00:00:00").strip()
    if len(clock) == 5:
        clock = f"{clock}:00"
    return f"{day}T{clock}-07:00"

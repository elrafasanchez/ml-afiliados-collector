"""Comprobaciones de integridad previas a publicar cualquier dato.

Regla rectora: **un cero solo es válido si Mercado Libre reporta cero.** Un
tiempo de espera agotado, un fallo de sesión o una respuesta vacía inesperada
jamás deben convertirse en cero; deben propagarse como error para que el
dashboard conserve el último valor bueno y marque la fuente como degradada.

Este módulo no decide qué hacer ante un fallo: solo detecta y describe.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .errors import ValidationError
from .normalize import DailyMetrics, SaleRecord

#: Tolerancia al comparar importes que la fuente redondea de forma distinta.
MONEY_TOLERANCE = Decimal("0.05")

#: Un corte de la fuente más viejo que esto no sirve para el día en curso.
MAX_SOURCE_AGE = timedelta(hours=6)


def validate_daily_metrics(metrics: DailyMetrics, *, now: datetime | None = None) -> None:
    """Verifica invariantes de un día. Lanza :class:`ValidationError` al fallar."""
    problems: list[str] = []

    for name in (
        "clicks",
        "buyers",
        "estimated_orders",
        "estimated_products",
        "non_effective_sales_count",
    ):
        value = getattr(metrics, name)
        if value < 0:
            problems.append(f"`{name}` es negativo ({value}).")

    for name in (
        "gross_sales",
        "estimated_sales",
        "non_effective_sales_amount",
        "marketplace_commission",
        "store_commission",
        "total_commission",
    ):
        value = getattr(metrics, name)
        if value < 0:
            problems.append(f"`{name}` es negativo ({value}).")

    # Identidad contable declarada por la propia interfaz de Mercado Libre:
    # las ventas estimadas son las brutas menos las no efectivas.
    expected_estimated = metrics.gross_sales - metrics.non_effective_sales_amount
    if abs(expected_estimated - metrics.estimated_sales) > MONEY_TOLERANCE:
        problems.append(
            f"Ventas estimadas ({metrics.estimated_sales}) no cuadran con "
            f"brutas menos no efectivas ({expected_estimated})."
        )

    # La comisión total debe ser la suma de sus dos componentes.
    expected_total = metrics.marketplace_commission + metrics.store_commission
    if abs(expected_total - metrics.total_commission) > MONEY_TOLERANCE:
        problems.append(
            f"La comisión total ({metrics.total_commission}) no es la suma de "
            f"Mercado Libre ({metrics.marketplace_commission}) y tienda "
            f"({metrics.store_commission})."
        )

    # Una comisión no puede superar la venta que la origina.
    if metrics.total_commission > metrics.gross_sales and metrics.gross_sales > 0:
        problems.append("La comisión total supera a las ventas brutas.")

    # Coherencia estructural: no puede haber importe no efectivo sin cuenta.
    if metrics.non_effective_sales_amount > 0 and metrics.non_effective_sales_count == 0:
        problems.append("Hay importe de ventas no efectivas pero la cuenta es cero.")

    try:
        date.fromisoformat(metrics.date_key)
    except ValueError:
        problems.append(f"`date_key` no es una fecha válida: {metrics.date_key!r}.")

    problems.extend(_timestamp_problems(metrics.source_timestamp, now=now))

    if problems:
        raise ValidationError(
            f"El día {metrics.date_key} no superó la validación: " + " ".join(problems)
        )


def _timestamp_problems(source_timestamp: str, *, now: datetime | None) -> list[str]:
    moment = now or datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(source_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return [f"`source_timestamp` no es una fecha ISO: {source_timestamp!r}."]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed > moment + timedelta(minutes=10):
        return ["El corte de la fuente está en el futuro."]
    return []


def validate_source_freshness(
    metrics: DailyMetrics,
    *,
    now: datetime | None = None,
    max_age: timedelta = MAX_SOURCE_AGE,
) -> None:
    """Exige que el corte del día en curso sea reciente.

    Solo aplica al día vigente: para días cerrados el corte es legítimamente
    antiguo y volver a exigir frescura invalidaría el histórico.
    """
    moment = now or datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(metrics.source_timestamp.replace("Z", "+00:00"))
    if moment - parsed > max_age:
        raise ValidationError(
            f"El corte de Mercado Libre para {metrics.date_key} tiene "
            f"{moment - parsed} de antigüedad; se conserva el último valor válido."
        )


def validate_sales(
    sales: list[SaleRecord],
    *,
    expected_range: tuple[date, date] | None = None,
) -> None:
    """Verifica el detalle transaccional: sin duplicados y dentro del rango."""
    problems: list[str] = []

    seen: set[str] = set()
    duplicates: set[str] = set()
    for sale in sales:
        if sale.sale_id in seen:
            duplicates.add(sale.sale_id)
        seen.add(sale.sale_id)
    if duplicates:
        sample = ", ".join(sorted(duplicates)[:5])
        problems.append(f"Ventas duplicadas ({len(duplicates)}): {sample}.")

    for sale in sales:
        if sale.sale_units <= 0:
            problems.append(f"La venta {sale.sale_id} tiene {sale.sale_units} unidades.")
        if sale.sale_value < 0 or sale.commission_value < 0:
            problems.append(f"La venta {sale.sale_id} tiene importes negativos.")

    if expected_range is not None:
        start, end_exclusive = expected_range
        for sale in sales:
            day = date.fromisoformat(sale.date_key)
            if not (start <= day < end_exclusive):
                problems.append(
                    f"La venta {sale.sale_id} cae en {sale.date_key}, "
                    f"fuera de {start}..{end_exclusive}."
                )
                break

    if problems:
        raise ValidationError("El detalle de ventas no superó la validación: " + " ".join(problems))


def reconcile_sales_against_daily(
    sales: list[SaleRecord],
    metrics: DailyMetrics,
) -> dict[str, Decimal]:
    """Compara la suma del detalle contra el agregado del mismo día.

    Devuelve las diferencias en lugar de lanzar: una discrepancia es información
    que hay que **registrar e investigar**, no una razón para descartar el día ni
    para escalar los valores hasta que cuadren.
    """
    detail_units = sum(sale.sale_units for sale in sales)
    detail_commission = sum(
        (sale.commission_value for sale in sales), start=Decimal("0")
    )
    return {
        "units_difference": Decimal(detail_units - metrics.estimated_products),
        "commission_difference": (detail_commission - metrics.total_commission).quantize(
            Decimal("0.01")
        ),
    }

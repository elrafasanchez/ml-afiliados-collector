"""Trabajos de sincronización, con su granularidad y su coste explícitos.

La sincronización es incremental a propósito. Volver a leer 180 días cada media
hora costaría 180 peticiones por ejecución sin aportar nada: los días cerrados
casi nunca cambian. El reparto es:

    today     cada 30 min   ~3 peticiones   día en curso + mes vigente + detalle
    recent    cada 6 horas   7 peticiones   relee la última semana por revisiones
    audit     semanal      ~180 peticiones  auditoría completa y reconciliación
    backfill  a demanda     1 por día vacío rellena huecos del histórico

Cada trabajo es idempotente: se identifica cada día por su fecha natural y cada
venta por su identificador de Mercado Libre, así que repetir una ejecución
actualiza en lugar de duplicar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .errors import CollectorError, ValidationError
from .logging_setup import log
from .normalize import (
    DailyMetrics,
    ProductRollup,
    SaleRecord,
    normalize_daily_metrics,
    normalize_product_rollup,
    normalize_sales,
)
from .parse import extract_general_kpis, extract_product_rollup
from .source import MercadoLibreSource
from .timeframe import (
    clamp_to_tomorrow,
    day_range,
    date_span,
    month_range,
    next_month_start,
    today_in_hermosillo,
    utc_now_iso,
)
from .validate import (
    reconcile_sales_against_daily,
    validate_daily_metrics,
    validate_sales,
    validate_source_freshness,
)


@dataclass
class ProductSample:
    """Corte por producto tal como viene incrustado en la página.

    El documento trae solo la primera página, así que `complete` distingue un
    corte completo de una muestra. Sin esa marca, comparar diez productos contra
    el total del día levantaría una alarma en cada ejecución.
    """

    products: list[ProductRollup]
    declared_total: int | None
    complete: bool


@dataclass
class CollectionResult:
    """Lo recolectado por un trabajo, más su rastro de ejecución."""

    job: str
    started_at: str
    days: list[DailyMetrics] = field(default_factory=list)
    sales: list[SaleRecord] = field(default_factory=list)
    sales_range: tuple[date, date] | None = None
    month_aggregate: DailyMetrics | None = None
    month_range: tuple[date, date] | None = None
    products: list[ProductRollup] = field(default_factory=list)
    reconciliation: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    requests_made: int = 0

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job": self.job,
            "collectedAt": utc_now_iso(),
            "days": [day.as_payload() for day in self.days],
            "run": {
                "startedAt": self.started_at,
                "finishedAt": utc_now_iso(),
                "requestsMade": self.requests_made,
                "recordsRead": len(self.days) + len(self.sales),
                "warnings": self.warnings,
            },
        }
        if self.sales_range is not None:
            payload["sales"] = [sale.as_payload() for sale in self.sales]
            payload["salesRange"] = {
                "start": self.sales_range[0].isoformat(),
                "endExclusive": self.sales_range[1].isoformat(),
            }
        if self.month_aggregate is not None and self.month_range is not None:
            payload["monthAggregate"] = self.month_aggregate.as_payload()
            payload["monthRange"] = {
                "start": self.month_range[0].isoformat(),
                "endExclusive": self.month_range[1].isoformat(),
            }
        if self.reconciliation:
            payload["reconciliation"] = self.reconciliation
        return payload


class Collector:
    """Orquesta los trabajos sobre una fuente ya autenticada."""

    def __init__(self, source: MercadoLibreSource) -> None:
        self._source = source
        self._requests = 0

    # ------------------------------------------------------------- primitivas

    def read_day(self, day: date, *, require_fresh: bool = False) -> DailyMetrics:
        """Lee las métricas agregadas de un día natural."""
        metrics, _ = self.read_day_with_products(day, require_fresh=require_fresh)
        return metrics

    def read_day_with_products(
        self, day: date, *, require_fresh: bool = False
    ) -> tuple[DailyMetrics, "ProductSample"]:
        """Lee el día y su corte por producto del **mismo** documento.

        Los dos bloques viajan en la misma respuesta, así que obtener el corte
        por producto no cuesta ninguna petición adicional.
        """
        start, end = day_range(day)
        html = self._source.fetch_dashboard_html(start, end)
        self._requests += 1
        metrics = normalize_daily_metrics(extract_general_kpis(html), day.isoformat())
        validate_daily_metrics(metrics)
        if require_fresh:
            validate_source_freshness(metrics)
        try:
            rollup = extract_product_rollup(html)
            products = normalize_product_rollup(rollup.get("item_list", []))
            declared = rollup.get("total_results")
            # El documento incrusta solo la primera página del corte por
            # producto. Saber si está completo evita comparar 10 productos
            # contra el total del día y levantar una alarma falsa.
            complete = isinstance(declared, int) and len(products) >= declared
        except CollectorError:
            # El corte por producto es un complemento: si cambia de forma, el día
            # sigue siendo válido y no se pierde la métrica principal.
            products, declared, complete = [], None, False
        return metrics, ProductSample(products=products, declared_total=declared, complete=complete)

    def read_range_aggregate(
        self, start: date, end_exclusive: date, *, today: date | None = None
    ) -> DailyMetrics:
        """Lee el agregado de un rango arbitrario (mes, rally, 180 días).

        El fin se recorta a mañana: si se pide más allá, la fuente descarta el
        día en curso en silencio. Ver :func:`mlaf.timeframe.month_range`.

        Se reutiliza la misma estructura canónica; ``date_key`` guarda el inicio
        del rango, que es lo que identifica al periodo.
        """
        end_exclusive = clamp_to_tomorrow(end_exclusive, today or today_in_hermosillo())
        html = self._source.fetch_dashboard_html(start, end_exclusive)
        self._requests += 1
        metrics = normalize_daily_metrics(extract_general_kpis(html), start.isoformat())
        validate_daily_metrics(metrics)
        return metrics

    def read_sales(self, start: date, end_exclusive: date) -> list[SaleRecord]:
        """Lee el detalle transaccional completo de un rango."""
        raw = self._source.fetch_all_sales(start, end_exclusive)
        self._requests += 1
        sales = normalize_sales(raw)
        validate_sales(sales, expected_range=(start, end_exclusive))
        return sales

    # --------------------------------------------------------------- trabajos

    def run_today(self, *, now: datetime | None = None) -> CollectionResult:
        """Día en curso, mes vigente y detalle transaccional del día.

        El agregado mensual se toma directamente de Mercado Libre para el rango
        del mes completo, que **ya incluye el día en curso**. Por eso no se suma
        el día por separado: hacerlo lo contaría dos veces.
        """
        today = today_in_hermosillo(now)
        result = CollectionResult(job="today", started_at=utc_now_iso())

        day_metrics, sample = self.read_day_with_products(today)
        result.products = sample.products
        try:
            validate_source_freshness(day_metrics)
        except ValidationError as error:
            # Se conserva el dato y se marca la advertencia: el destino decidirá
            # si lo publica como vigente o como rezagado.
            result.warnings.append(str(error))
        result.days.append(day_metrics)

        start, end = day_range(today)
        sales = self.read_sales(start, end)
        result.sales = sales
        result.sales_range = (start, end)

        gaps = reconcile_sales_against_daily(sales, day_metrics)
        # Tercera vía de contraste: Mercado Libre publica su propio corte por
        # producto. Si las unidades del detalle, del agregado y de ese corte no
        # coinciden, se informa; nunca se ajusta ninguno para forzar el cuadre.
        rollup_units = sum(product.units for product in sample.products)
        result.reconciliation = {
            "scope": "today",
            "dateKey": today.isoformat(),
            "unitsDifference": float(gaps["units_difference"]),
            "commissionDifference": float(gaps["commission_difference"]),
            "productRollupProducts": len(sample.products),
            "productRollupDeclared": sample.declared_total,
            "productRollupComplete": sample.complete,
        }
        # Comparar un corte parcial contra el total del día produciría siempre
        # una diferencia que no significa nada.
        if sample.complete:
            result.reconciliation["rollupVsAggregateUnits"] = (
                rollup_units - day_metrics.estimated_products
            )
            if rollup_units != day_metrics.estimated_products:
                result.warnings.append(
                    f"El corte por producto suma {rollup_units} unidades frente a "
                    f"{day_metrics.estimated_products} del agregado."
                )
        if gaps["units_difference"] != 0:
            result.warnings.append(
                f"El detalle del día suma {len(sales)} ventas con "
                f"{gaps['units_difference']} unidades de diferencia frente al agregado."
            )

        month_start, month_end = month_range(today)
        result.month_aggregate = self.read_range_aggregate(month_start, month_end, today=today)
        # El rango publicado es el mes natural completo, que es lo que identifica
        # al periodo; lo que se recortó es solo la consulta a la fuente.
        result.month_range = (month_start, next_month_start(today))

        result.requests_made = self._requests
        return result

    def run_recent(self, *, days_back: int = 7, now: datetime | None = None) -> CollectionResult:
        """Relee los últimos días para capturar revisiones de la fuente."""
        today = today_in_hermosillo(now)
        result = CollectionResult(job="recent", started_at=utc_now_iso())
        from datetime import timedelta

        for offset in range(days_back):
            day = today - timedelta(days=offset)
            try:
                result.days.append(self.read_day(day))
            except CollectorError as error:
                # Un día que falla no debe tumbar la reconciliación de los otros.
                result.warnings.append(f"{day.isoformat()}: {error}")
                log("day_failed", date=day.isoformat(), error=str(error))
        result.requests_made = self._requests
        return result

    def run_backfill(
        self, start: date, end_exclusive: date, *, known_days: set[str] | None = None
    ) -> CollectionResult:
        """Rellena días del histórico, saltando los que ya están almacenados."""
        result = CollectionResult(job="backfill", started_at=utc_now_iso())
        skip = known_days or set()

        for day in date_span(start, end_exclusive):
            if day.isoformat() in skip:
                continue
            try:
                result.days.append(self.read_day(day))
                log("day_collected", date=day.isoformat())
            except CollectorError as error:
                result.warnings.append(f"{day.isoformat()}: {error}")
                log("day_failed", date=day.isoformat(), error=str(error))
        result.requests_made = self._requests
        return result

    def run_audit(self, start: date, end_exclusive: date) -> CollectionResult:
        """Auditoría completa: relee cada día y contrasta contra el agregado.

        No ajusta los días para forzar el cuadre. Informa la diferencia para que
        se investigue, que es la única respuesta honesta ante una discrepancia.
        """
        result = self.run_backfill(start, end_exclusive)
        result.job = "audit"

        aggregate = self.read_range_aggregate(start, end_exclusive)
        daily_sales = sum((day.estimated_sales for day in result.days), start_value())
        daily_commission = sum((day.total_commission for day in result.days), start_value())

        result.reconciliation = {
            "scope": "audit",
            "start": start.isoformat(),
            "endExclusive": end_exclusive.isoformat(),
            "daysCollected": len(result.days),
            "daysExpected": (end_exclusive - start).days,
            "dailySalesSum": float(daily_sales),
            "aggregateSales": float(aggregate.estimated_sales),
            "salesDifference": float(daily_sales - aggregate.estimated_sales),
            "dailyCommissionSum": float(daily_commission),
            "aggregateCommission": float(aggregate.total_commission),
            "commissionDifference": float(daily_commission - aggregate.total_commission),
        }
        result.requests_made = self._requests
        return result


def start_value():
    from decimal import Decimal

    return Decimal("0")

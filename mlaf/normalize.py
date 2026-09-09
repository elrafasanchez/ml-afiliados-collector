"""Traducción del vocabulario de Mercado Libre al modelo canónico.

AVISO IMPORTANTE SOBRE LOS NOMBRES DE LA FUENTE
------------------------------------------------
Mercado Libre usa identificadores que **no** corresponden a lo que muestra su
propia interfaz. Verificado el 2026-09-08 comparando el DOM visible contra la
hidratación en el mismo rango:

    id de la API   etiqueta en pantalla    significado real
    ------------   --------------------    -------------------------------
    requests       "Órdenes estimadas"     pedidos hechos por compradores
    orders         "Prod. estimados"       unidades de producto vendidas

Es decir, ``orders`` **no** son órdenes y ``requests`` **sí** lo son. Confiar en
los nombres intercambia dos métricas sin que ninguna validación lo note, porque
ambas son enteros positivos plausibles. Este módulo es el único lugar donde se
resuelve esa correspondencia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import SchemaError
from .parse import indexed_amounts

# Correspondencia entre el `id` de la fuente y el campo canónico.
_COUNT_FIELDS = {
    "clicks": "clicks",
    "buyers": "buyers",
    "requests": "estimated_orders",
    "orders": "estimated_products",
}
_COMMISSION_FIELDS = {
    "marketplace": "marketplace_commission",
    "seller": "store_commission",
    "summary": "total_commission",
}
_SALES_FIELDS = {
    "total_gross_sales": "gross_sales",
    "total_estimated_sales": "estimated_sales",
    "total_not_effective_sales": "non_effective_sales_amount",
    "count_not_effective_sales": "non_effective_sales_count",
}


def _money(value: float) -> Decimal:
    """Convierte a Decimal con dos decimales, sin pasar por float dos veces."""
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as error:
        raise SchemaError(f"Importe no representable: {value!r}") from error


def _count(value: float, name: str) -> int:
    if value != int(value):
        raise SchemaError(f"`{name}` debería ser entero y llegó {value!r}.")
    return int(value)


@dataclass(frozen=True)
class DailyMetrics:
    """Métricas canónicas de un día natural.

    Los importes son ``Decimal`` para que la serialización a centavos sea exacta.
    """

    date_key: str
    clicks: int
    buyers: int
    estimated_orders: int
    estimated_products: int
    gross_sales: Decimal
    estimated_sales: Decimal
    non_effective_sales_amount: Decimal
    non_effective_sales_count: int
    marketplace_commission: Decimal
    store_commission: Decimal
    total_commission: Decimal
    source_timestamp: str

    def as_payload(self) -> dict[str, Any]:
        """Representación JSON para la API de ingesta."""
        return {
            "dateKey": self.date_key,
            "clicks": self.clicks,
            "buyers": self.buyers,
            "estimatedOrders": self.estimated_orders,
            "estimatedProducts": self.estimated_products,
            "grossSales": float(self.gross_sales),
            "estimatedSales": float(self.estimated_sales),
            "nonEffectiveSalesAmount": float(self.non_effective_sales_amount),
            "nonEffectiveSalesCount": self.non_effective_sales_count,
            "marketplaceCommission": float(self.marketplace_commission),
            "storeCommission": float(self.store_commission),
            "totalCommission": float(self.total_commission),
            "sourceTimestamp": self.source_timestamp,
        }


@dataclass(frozen=True)
class SaleRecord:
    """Una venta individual atribuida al afiliado."""

    sale_id: str
    date_key: str
    item_id: str | None
    product_name: str
    product_image: str | None
    link: str | None
    category_name: str | None
    store_name: str | None
    sale_value: Decimal
    sale_units: int
    commission_value: Decimal
    commission_percentage: Decimal | None
    sale_type: str | None
    status: str | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "saleId": self.sale_id,
            "dateKey": self.date_key,
            "itemId": self.item_id,
            "productName": self.product_name,
            "productImage": self.product_image,
            "link": self.link,
            "categoryName": self.category_name,
            "storeName": self.store_name,
            "saleValue": float(self.sale_value),
            "saleUnits": self.sale_units,
            "commissionValue": float(self.commission_value),
            "commissionPercentage": (
                float(self.commission_percentage)
                if self.commission_percentage is not None
                else None
            ),
            "saleType": self.sale_type,
            "status": self.status,
        }


def normalize_daily_metrics(kpis: dict[str, Any], date_key: str) -> DailyMetrics:
    """Convierte ``generalKpis`` en métricas canónicas de un día."""
    counts = indexed_amounts(kpis.get("data"))
    commissions = indexed_amounts(kpis.get("commissions"))
    sales = indexed_amounts(kpis.get("sales"))

    missing = (
        [key for key in _COUNT_FIELDS if key not in counts]
        + [key for key in _COMMISSION_FIELDS if key not in commissions]
        + [key for key in _SALES_FIELDS if key not in sales]
    )
    if missing:
        raise SchemaError(
            "La fuente dejó de exponer estas métricas: " + ", ".join(sorted(missing))
        )

    return DailyMetrics(
        date_key=date_key,
        clicks=_count(counts["clicks"], "clicks"),
        buyers=_count(counts["buyers"], "buyers"),
        estimated_orders=_count(counts["requests"], "requests"),
        estimated_products=_count(counts["orders"], "orders"),
        gross_sales=_money(sales["total_gross_sales"]),
        estimated_sales=_money(sales["total_estimated_sales"]),
        non_effective_sales_amount=_money(sales["total_not_effective_sales"]),
        non_effective_sales_count=_count(
            sales["count_not_effective_sales"], "count_not_effective_sales"
        ),
        marketplace_commission=_money(commissions["marketplace"]),
        store_commission=_money(commissions["seller"]),
        total_commission=_money(commissions["summary"]),
        source_timestamp=normalize_source_timestamp(kpis.get("last_update")),
    )


def normalize_source_timestamp(value: Any) -> str:
    """Normaliza el ``last_update`` de Mercado Libre a ISO-8601 en UTC.

    Este es el instante en que la fuente calculó los datos, y es distinto del
    momento en que nosotros los leímos. Guardarlos por separado es lo que permite
    distinguir "el trabajo corrió" de "los datos son recientes".
    """
    if not isinstance(value, str) or not value.strip():
        raise SchemaError("La fuente no expuso `last_update`.")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise SchemaError(f"`last_update` no es una fecha ISO válida: {value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def normalize_sales(item_list: Any, fallback_date_key: str | None = None) -> list[SaleRecord]:
    """Convierte ``item_list`` en registros de venta canónicos."""
    if not isinstance(item_list, list):
        raise SchemaError("`item_list` no es una lista.")

    records: list[SaleRecord] = []
    for raw in item_list:
        if not isinstance(raw, dict):
            raise SchemaError("Una venta no es un objeto.")
        sale_id = raw.get("id")
        if not isinstance(sale_id, (str, int)) or not str(sale_id).strip():
            raise SchemaError("Una venta llegó sin identificador estable.")
        records.append(
            SaleRecord(
                sale_id=str(sale_id).strip(),
                date_key=_sale_date_key(raw.get("date"), fallback_date_key),
                item_id=_item_id(raw.get("link")),
                product_name=str(raw.get("productName") or "").strip() or "(sin título)",
                product_image=_optional_text(raw.get("productImage")),
                link=_optional_text(raw.get("link")),
                category_name=_optional_text(raw.get("categoryName")),
                store_name=_optional_text(raw.get("storeName")),
                sale_value=_money(_numeric(raw.get("saleValue"), "saleValue")),
                sale_units=_count(_numeric(raw.get("saleUnits"), "saleUnits"), "saleUnits"),
                commission_value=_money(_numeric(raw.get("commissionValue"), "commissionValue")),
                commission_percentage=(
                    _money(_numeric(raw.get("commissionPercentage"), "commissionPercentage"))
                    if raw.get("commissionPercentage") is not None
                    else None
                ),
                sale_type=_optional_text(raw.get("saleType")),
                status=_optional_text(raw.get("status")),
            )
        )
    return records


def _numeric(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f"`{name}` no es numérico: {value!r}")
    return float(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _item_id(link: Any) -> str | None:
    """Extrae el identificador MLM del enlace del producto."""
    if not isinstance(link, str):
        return None
    import re

    match = re.search(r"/MLM-?(\d+)", link)
    return f"MLM{match.group(1)}" if match else None


def _sale_date_key(value: Any, fallback: str | None) -> str:
    """Convierte la fecha ``DD/MM/YYYY`` de la fuente en ``YYYY-MM-DD``."""
    if isinstance(value, str) and value.strip():
        text = value.strip()
        parts = text.split("/")
        if len(parts) == 3:
            day, month, year = parts
            try:
                return date(int(year), int(month), int(day)).isoformat()
            except ValueError as error:
                raise SchemaError(f"Fecha de venta inválida: {value!r}") from error
    if fallback:
        return fallback
    raise SchemaError(f"Una venta llegó sin fecha utilizable: {value!r}")


@dataclass(frozen=True)
class ProductRollup:
    """Un producto del corte "Productos vendidos" de un periodo.

    Mercado Libre ya separa las unidades directas de las indirectas y expone la
    tasa de comisión de cada tipo. Reconstruir esto sumando transacciones daría
    lo mismo en el caso normal, pero tenerlo de la fuente permite contrastar una
    cosa contra la otra.
    """

    item_id: str
    title: str
    category: str | None
    image: str | None
    link: str | None
    units: int
    direct_units: int
    indirect_units: int
    sales: Decimal
    commission: Decimal
    #: Tasa efectiva en porcentaje (la fuente la da como fracción: 0.14 = 14 %).
    rate: Decimal

    def as_payload(self) -> dict[str, Any]:
        return {
            "itemId": self.item_id,
            "title": self.title,
            "category": self.category,
            "image": self.image,
            "link": self.link,
            "units": self.units,
            "directUnits": self.direct_units,
            "indirectUnits": self.indirect_units,
            "sales": float(self.sales),
            "commission": float(self.commission),
            "rate": float(self.rate),
        }


def normalize_product_rollup(item_list: Any) -> list[ProductRollup]:
    """Convierte el corte por producto en registros canónicos."""
    if not isinstance(item_list, list):
        raise SchemaError("El corte por producto no es una lista.")

    products: list[ProductRollup] = []
    for raw in item_list:
        if not isinstance(raw, dict):
            raise SchemaError("Un producto no es un objeto.")
        entity_id = raw.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise SchemaError("Un producto llegó sin `entity_id`.")

        units = _count(_numeric(raw.get("quantity"), "quantity"), "quantity")
        direct = _count(
            _numeric(raw.get("quantity_direct", 0), "quantity_direct"), "quantity_direct"
        )
        indirect = _count(
            _numeric(raw.get("quantity_not_direct", 0), "quantity_not_direct"),
            "quantity_not_direct",
        )
        if direct + indirect != units:
            raise SchemaError(
                f"El producto {entity_id} declara {units} unidades pero "
                f"{direct} directas + {indirect} indirectas."
            )

        products.append(
            ProductRollup(
                item_id=entity_id.strip(),
                title=str(raw.get("product") or "").strip() or "(sin título)",
                category=_optional_text(raw.get("category")),
                image=_optional_text(raw.get("image_uri")),
                link=_optional_text(raw.get("link")),
                units=units,
                direct_units=direct,
                indirect_units=indirect,
                sales=_money(_numeric(raw.get("total_sales"), "total_sales")),
                commission=_money(_numeric(raw.get("earnings"), "earnings")),
                # La fuente entrega la tasa como fracción; se guarda en porcentaje
                # para que coincida con lo que muestra la interfaz.
                rate=_money(_numeric(raw.get("fee", 0), "fee") * 100),
            )
        )
    return products

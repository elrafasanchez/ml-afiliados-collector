"""Extracción de datos del documento hidratado de Mercado Libre.

El dashboard de Afiliados es una aplicación Nordic renderizada en servidor. Todo
el estado que pinta la pantalla viaja dentro de una etiqueta ``<script>``:

    <script id="__NORDIC_RENDERING_CTX__" nonce="">_n.ctx.r={...};_n.ctx.r.assets...</script>

El contenido **no** es JSON: es una secuencia de asignaciones de JavaScript. Solo
la primera es un objeto literal, y las siguientes construyen ``Map`` y ``Set``,
que no son parseables. Por eso se recorta el objeto con conteo de llaves en vez
de usar una expresión regular perezosa o ``json.loads`` sobre todo el bloque.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .errors import AuthenticationError, SchemaError

_SCRIPT_ID = "__NORDIC_RENDERING_CTX__"
_SCRIPT_RE = re.compile(
    r"<script[^>]*id=[\"']" + re.escape(_SCRIPT_ID) + r"[\"'][^>]*>(.*?)</script>",
    re.DOTALL,
)
_ASSIGNMENT_RE = re.compile(r"_n\.ctx\.r\s*=\s*")

# Señales de que Mercado Libre devolvió el muro de acceso en lugar del panel.
_LOGIN_MARKERS = (
    "/jms/mlm/lgz/login",
    "authentication_required",
    "Ingresa a tu cuenta",
)


def extract_rendering_context(html: str) -> dict[str, Any]:
    """Devuelve el objeto de hidratación de la página de Afiliados.

    Lanza :class:`AuthenticationError` si la respuesta es la pantalla de acceso,
    y :class:`SchemaError` si el documento ya no tiene la forma esperada. Nunca
    devuelve un diccionario vacío para disimular un fallo.
    """
    match = _SCRIPT_RE.search(html)
    if not match:
        if _looks_like_login(html):
            raise AuthenticationError(
                "Mercado Libre devolvió la pantalla de acceso: la sesión caducó."
            )
        raise SchemaError(
            f"El documento no contiene la etiqueta {_SCRIPT_ID}; "
            "la fuente cambió de estructura."
        )

    payload = _slice_first_object(match.group(1))
    try:
        context = json.loads(payload)
    except json.JSONDecodeError as error:
        raise SchemaError(
            f"El contexto de hidratación no es un objeto JSON válido: {error}"
        ) from error

    if not isinstance(context, dict):
        raise SchemaError("El contexto de hidratación no es un objeto.")
    return context


def _looks_like_login(html: str) -> bool:
    return any(marker in html for marker in _LOGIN_MARKERS)


def _slice_first_object(script: str) -> str:
    """Recorta el primer objeto literal asignado a ``_n.ctx.r``.

    Recorre el texto contando llaves y respetando cadenas y escapes, de modo que
    las llaves que aparecen dentro de los textos traducidos no desbalanceen el
    conteo.
    """
    assignment = _ASSIGNMENT_RE.search(script)
    if not assignment:
        raise SchemaError("No se encontró la asignación `_n.ctx.r=` en la hidratación.")

    start = script.find("{", assignment.end())
    if start < 0:
        raise SchemaError("La asignación `_n.ctx.r=` no va seguida de un objeto.")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(script)):
        char = script[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return script[start : index + 1]

    raise SchemaError("El objeto de hidratación quedó sin cerrar.")


def extract_page_props(html: str) -> dict[str, Any]:
    """Devuelve ``appProps.pageProps`` del documento hidratado."""
    context = extract_rendering_context(html)
    page_props = context.get("appProps", {}).get("pageProps")
    if not isinstance(page_props, dict):
        raise SchemaError("La hidratación no expone `appProps.pageProps`.")
    return page_props


def extract_general_kpis(html: str) -> dict[str, Any]:
    """Devuelve el bloque de métricas agregadas del periodo consultado."""
    kpis = extract_page_props(html).get("generalKpis")
    if not isinstance(kpis, dict):
        raise SchemaError("La hidratación no expone `generalKpis`.")
    if not kpis.get("last_update"):
        # Diagnose source schema failures without logging cookies or page data.
        from .logging_setup import log
        props = extract_page_props(html)
        log("kpi_schema_missing_timestamp", kpi_keys=sorted(kpis),
            page_keys=sorted(props),
            value_types={key: type(value).__name__ for key, value in kpis.items()},
            reference_now=props.get("referenceNow"), nrt=props.get("isNrtEnabled"),
            validation_failed=props.get("validationFailed"),
            sales=kpis.get("sales"), filter_time_range=kpis.get("filter_time_range"))
    return kpis


def extract_product_rollup(html: str) -> dict[str, Any]:
    """Devuelve el corte por producto que Mercado Libre incrusta en la página.

    ATENCIÓN AL NOMBRE DE LA FUENTE
    -------------------------------
    La clave se llama ``generalOrders``, pero **no contiene órdenes**: contiene
    la pestaña "Productos vendidos" ya agregada por producto, con campos como
    ``quantity``, ``quantity_direct``, ``quantity_not_direct``, ``fee``,
    ``earnings`` y ``total_sales``.

    El detalle transacción por transacción es otra cosa y vive en el extremo
    XHR ``/affiliate-program/api/dashboard/sales/general``, cuyas filas sí traen
    un ``id`` de venta y una ``date``. Confundirlos es fácil porque ambos
    responden con ``item_list`` y ``total_results``, pero sus ``total_results``
    ni siquiera coinciden: uno cuenta productos distintos y el otro, ventas.
    """
    rollup = extract_page_props(html).get("generalOrders")
    if not isinstance(rollup, dict):
        raise SchemaError("La hidratación no expone el corte por producto.")
    return rollup


def indexed_amounts(entries: Any, field: str = "current_amount") -> dict[str, float]:
    """Convierte ``[{"id": x, "<field>": n}, ...]`` en ``{x: n}``.

    Mercado Libre entrega las métricas como listas de objetos identificados en
    vez de como un diccionario. Indexarlas por ``id`` evita depender del orden,
    que no está garantizado.
    """
    if not isinstance(entries, list):
        raise SchemaError(f"Se esperaba una lista de métricas, llegó {type(entries).__name__}.")

    result: dict[str, float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise SchemaError("Una métrica no es un objeto.")
        identifier = entry.get("id")
        if not isinstance(identifier, str):
            raise SchemaError("Una métrica no tiene `id` de texto.")
        value = entry.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise SchemaError(f"La métrica `{identifier}` no tiene un `{field}` numérico.")
        result[identifier] = float(value)
    return result

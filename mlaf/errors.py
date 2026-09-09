"""Errores del recolector, clasificados por lo que el llamador debe hacer."""


class CollectorError(RuntimeError):
    """Base de todos los fallos del recolector."""

    #: Código estable para registrar en `sync_runs` sin depender del texto.
    code = "COLLECTOR_ERROR"
    #: Indica si reintentar tiene alguna probabilidad de éxito.
    retryable = False


class AuthenticationError(CollectorError):
    """La sesión de Mercado Libre no es válida o caducó.

    Nunca es reintentable: requiere que una persona renueve las cookies.
    """

    code = "AUTH_EXPIRED"
    retryable = False


class TransientSourceError(CollectorError):
    """Fallo temporal de la fuente (red, 5xx, límite de tasa)."""

    code = "SOURCE_TRANSIENT"
    retryable = True


class SchemaError(CollectorError):
    """Mercado Libre respondió, pero con una estructura desconocida.

    No es reintentable: reintentar produciría el mismo resultado y el síntoma
    real es que la fuente cambió de forma.
    """

    code = "SCHEMA_MISMATCH"
    retryable = False


class ValidationError(CollectorError):
    """Los datos extraídos no superaron las comprobaciones de integridad."""

    code = "VALIDATION_FAILED"
    retryable = False


class IngestError(CollectorError):
    """El destino rechazó o no pudo recibir los datos."""

    code = "INGEST_FAILED"
    retryable = True

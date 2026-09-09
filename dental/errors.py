"""Errores del recolector de Dental Amigo, clasificados por la acción que exigen."""


class DentalError(RuntimeError):
    code = "DENTAL_ERROR"
    retryable = False


class AuthError(DentalError):
    """El token de Dentalink falta, caducó o fue revocado.

    Nunca es reintentable: hace falta que una persona lo regenere en el panel.
    """

    code = "DENTALINK_AUTH"
    retryable = False


class TransientError(DentalError):
    """Fallo temporal: red, 5xx o límite de tasa."""

    code = "DENTALINK_TRANSIENT"
    retryable = True


class SchemaError(DentalError):
    """La API respondió con una forma que el adaptador no reconoce.

    No es reintentable: repetir da lo mismo, y el síntoma real es que la fuente
    cambió de contrato.
    """

    code = "DENTALINK_SCHEMA"
    retryable = False


class ValidationError(DentalError):
    """Los datos normalizados no superaron las comprobaciones de integridad."""

    code = "DENTAL_VALIDATION"
    retryable = False


class IngestError(DentalError):
    """El dashboard rechazó o no pudo recibir el lote."""

    code = "DENTAL_INGEST"
    retryable = True

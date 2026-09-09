"""Recolector autónomo de métricas del programa de Afiliados de Mercado Libre.

El paquete está dividido en capas con fronteras explícitas:

``source``     Obtiene los documentos crudos de Mercado Libre (HTTP).
``parse``      Extrae estructuras de datos del documento hidratado.
``normalize``  Traduce el vocabulario de Mercado Libre al canónico.
``validate``   Comprueba invariantes antes de publicar cualquier dato.
``ingest``     Publica los datos en la API del dashboard.

Ninguna capa por debajo de ``ingest`` conoce credenciales, y ninguna capa por
encima de ``source`` conoce HTTP. Eso permite probar el parseo y las reglas de
negocio sin red y sin sesión.
"""

__version__ = "2.0.0"

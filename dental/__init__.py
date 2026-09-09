"""Recolector autónomo de Dental Amigo.

Dos fuentes, con naturalezas distintas:

``dentalink``  Operación de la clínica —cobros, cajas, agenda, liquidaciones—
               a través de la API oficial de Dentalink, con token durable.
``flujos_da``  Finanzas, que ya llegan solas desde un Apps Script del propio
               libro de cálculo y no necesitan recolector.

Este paquete es independiente de ``mlaf``: comparten repositorio y planificador,
pero ningún módulo. Mercado Libre está congelado y no debe poder romperse desde
aquí.
"""

__version__ = "1.0.0"

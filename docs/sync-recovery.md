# Supervisión de la sincronización — 13 de octubre de 2026

## Evidencia observada (America/Hermosillo, UTC−7)

- Run 38486216198: evento `schedule`, expresión `30 20 * * *` (13:30 local). GitHub creó e inició el run a las 16:46:01; el recolector empezó a las 16:46:06 y confirmó la ingesta a las 16:46:17. El retraso estuvo antes de crear el run, no en una larga ejecución del recolector.
- Run 38481009647: inicio 15:55:50, trabajo recent, ingesta 15:56:04. `recent` relee días; no publica el agregado mensual.
- Run 38487369221: automático, expresión `17,27,47,57 * * * *`, inicio 16:57:45, recolector 16:57:51, ingesta 16:57:56; today, 1 día, 86 ventas, 3 peticiones. Estos runs usan ddf4f8fe, previo a los cambios de este documento.
- En el navegador, antes de la corrección, Hoy ya coincidía con Mercado Libre: ventas estimadas 39,525.48 MXN y comisión 3,066.87 MXN. Fuente mostraba 15:16 (hora de México); el sitio 14:16 Hermosillo. El sitio mostraba recepción 16:46 para Hoy, mientras el mes seguía recibido a las 14:47, corte 12:30.
- La consulta de los últimos 20 runs mostró resultados exitosos e inicio inmediato tras su creación, pero huecos de horas. No demuestra la causa interna del retraso de GitHub ni excluye incidencias fuera de esa muestra.

## Reparaciones

El recolector comunica fallos anteriores a la ingesta al endpoint protegido `/api/mercadolibre/sync-health`. Envía únicamente códigos y tiempos; nunca excepciones de la fuente ni HTML. El informe es best-effort, un intento, 15 segundos; no altera datos de ventas. Una sesión caducada interrumpe también recent/backfill: antes podía quedar ocultada como una advertencia por día.

El sitio calcula alertas por silencio tras 45 minutos sin confirmar Hoy o el mes; refresca la salud junto con los datos. En igualdad de corte fuente, el resolvedor conserva la confirmación más reciente. No cambia el cálculo de ventas, comisiones, rallys ni bonos. La gráfica ahora renueva su serie de 180 días con el refresco de cada minuto; antes solo utilizaba la serie del montaje inicial.

El sitio aloja herramientas privadas MCP `mercadolibre_sync_status` y `ensure_mercadolibre_sync`, restringidas al propietario. El supervisor requiere el secreto `GITHUB_SYNC_TOKEN`: token fino limitado a `elrafasanchez/ml-afiliados-collector`, permiso Actions de lectura y escritura (Metadata lectura es implícito). No necesita el cookie de Mercado Libre fuera de GitHub.

Una comprobación recupera si Hoy o el mes llevan más de 45 minutos sin confirmarse. Usa bloqueo persistente D1 de 120 segundos y revisa runs activos antes del dispatch. Persiste el intento antes de enviar; ante respuesta ambigua espera al menos 20 minutos. Máximo tres intentos por ciclo de fallo, circuito de seis horas y después una nueva oportunidad limitada. Una recepción nueva reinicia el presupuesto. Sesión caducada: intervención humana, sin reintento del supervisor. GitHub mantiene concurrencia serial, y la ingesta usa claves estables para no sumar de nuevo ventas o bonos.

## Activación pendiente: no confundir código publicado con un respaldo activo

1. Configurar `GITHUB_SYNC_TOKEN` en los secretos del sitio, nunca en este repositorio o en mensajes. Aplicar el entorno mediante despliegue.
2. Verificar acceso a las dos herramientas del plugin privado del sitio desde una tarea de nube nueva. Instalar/conectar el plugin generado por Sites si falta.
3. Verificar con `ensure_mercadolibre_sync` y lectura posterior de `mercadolibre_sync_status` que el permiso sirve y que la ingesta se confirma. Una llamada manual demuestra acceso, no cadencia.
4. Leer `get_site.automations` y reutilizar una existente si cubre esto. Solo con acceso verificado crear UNA tarea Sites cada 30 minutos, America/Hermosillo. Debe comprobar y recuperar; no republicar el sitio ni enviar correos. Avisar cuando hay sesión caducada, permiso rechazado o circuito abierto; no notificar en comprobaciones sanas.
5. Registrar al menos dos ocurrencias automáticas consecutivas y sus recepciones, más una prueba controlada de omisión/fallo fuera de producción. No declarar resuelto antes.

## Verificación de publicación y lectura

Recolector: fd0f030, 115 pruebas pasan. Sitio: versión 86, commit d52fe9b9f92e0dcff12df73a5338fddb971f7a2d; typecheck, 10 suites de validación y build pasan (series revalidada tras corregir el refresco).

La respuesta real del navegador para `/api/mercadolibre/latest?scope=today` devolvió HTTP 200, sin caché de disco, `ml_sync_runs.id=1644`, fuente `2026-10-13T21:16:43.000Z` (14:16:43), recepción `2026-10-13T23:57:55.037Z` (16:57:55), ventas 39,525.48 y comisión 3,066.87. Coinciden con la fuente visible. El run es PARTIAL por dos unidades de diferencia entre detalle y agregado; la conciliación de comisión da diferencia 0. No se alteraron cifras para forzar coincidencia.

## Validación y límites

Pruebas sin red cubren informes sin secretos, conservar salida original si el informe falla, no enviar durante dry-run y propagar sesión caducada. Las pruebas del sitio cubren silencio, corte versus recepción, cambio de fecha Hermosillo, ausencia de un ámbito, cooldown, presupuesto de intentos y recuperación.

GitHub sigue ejecutando la extracción: el supervisor independiente evita depender exclusivamente de su cron, pero no elimina una caída total de Actions. Mercado Libre puede retrasar su propio corte o caducar la sesión. No se deben escribir ceros ni presentar una recepción reciente como corte nuevo.

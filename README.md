# Recolectores de Central de Ingresos

Dos recolectores independientes que alimentan el tablero desde GitHub Actions:
**no necesitan que tu computadora esté encendida ni que haya un navegador
abierto.**

| Recolector | Negocio | Fuente | Sección |
|---|---|---|---|
| `mlaf` | Mercado Libre | Panel de Afiliados (HTTP + cookie de sesión) | abajo |
| `dental` | Dental Amigo | API oficial de Dentalink (token) | [Dental Amigo](#dental-amigo) |

Comparten repositorio y planificador, pero **ningún módulo**: un cambio en uno
no puede romper al otro, y sus flujos usan grupos de concurrencia distintos.

---

# Mercado Libre

Sincroniza las métricas del programa de Afiliados y Creadores hacia el tablero
de Central de Ingresos.

## Qué hace

```
Mercado Libre  ──HTTP+cookies──▶  recolector  ──HTTPS+token──▶  /api/mercadolibre/sync  ──▶  D1
```

Lee dos cosas del panel oficial, para cualquier rango de fechas:

| Origen | Qué entrega |
|---|---|
| `GET /afiliados/dashboard?filter_time_range=…` | Agregados del rango, dentro del script `__NORDIC_RENDERING_CTX__`, incluido el `last_update` real de la fuente |
| `GET /affiliate-program/api/dashboard/sales/general?…` | Detalle venta por venta, paginado |

No usa navegador, ni AppleScript, ni lectura del DOM.

## Trabajos y frecuencia

| Trabajo | Cuándo | Coste | Para qué |
|---|---|---|---|
| `today` | cada 30 min | ~4 peticiones | día en curso, mes vigente y detalle del día |
| `recent` | cada 6 horas | 7 peticiones | relee la última semana por si la fuente revisó cifras |
| `audit` | lunes | ~180 peticiones | auditoría completa y reconciliación |
| `backfill` | a mano | 1 por día vacío | rellena huecos del histórico |

La separación es deliberada: releer 180 días cada media hora costaría 180
peticiones por ejecución sin aportar nada, porque los días cerrados casi nunca
cambian.

## Instalación

### 1. Crea el repositorio

Sube esta carpeta a un repositorio **privado** de GitHub.

```bash
cd ~/ml-afiliados-collector
git init && git add . && git commit -m "Recolector autónomo de Mercado Libre"
gh repo create ml-afiliados-collector --private --source=. --push
```

Sin `gh`, crea el repositorio desde la web y luego:

```bash
git remote add origin https://github.com/<tu-usuario>/ml-afiliados-collector.git
git push -u origin main
```

### 2. Configura los tres secretos

En **Settings → Secrets and variables → Actions → New repository secret**:

| Secreto | Valor |
|---|---|
| `ML_INGEST_URL` | `https://radar-afiliados-rafsl.el-rafasanchez.chatgpt.site/api/mercadolibre/sync` |
| `ML_INGEST_TOKEN` | El mismo token de ingesta que ya vive en los Secrets del Site |
| `ML_SESSION_COOKIE` | Tu cookie de sesión de Mercado Libre — ver [docs/renovar-sesion.md](docs/renovar-sesion.md) |

Los secretos de GitHub van cifrados y no aparecen en los registros. El
recolector además depura cualquier `Bearer …` antes de escribir un mensaje.

### 3. Primera ejecución

En la pestaña **Actions → Sincronizar Mercado Libre → Run workflow**, elige
`today`. Si termina en verde, la sincronización automática ya está viva.

Para cargar el histórico:

```
Run workflow → job: backfill → start: 2026-03-13 → end: 2026-09-09
```

## Comprobar sin publicar nada

```bash
export ML_SESSION_COOKIE='...'
export ML_DRY_RUN=1
python3 run_sync.py today
```

Imprime el lote que se habría enviado y no toca la base.

## Pruebas

```bash
python3 -m unittest discover -s tests -t .
```

Cubren el parseo de la hidratación real, la correspondencia de nombres de la
fuente, las invariantes contables, la reconciliación y el principio de que un
fallo nunca se convierte en cero.

## Códigos de salida

| Código | Significado | Acción |
|---|---|---|
| 0 | Publicado | ninguna |
| 1 | Fallo temporal (red, 5xx) | se reintenta solo en la siguiente ejecución |
| 2 | **Sesión caducada** | renovar `ML_SESSION_COOKIE` |
| 3 | La fuente cambió o falló la validación | revisar el parseo |

Ante el código 2 el flujo abre un aviso en el repositorio.

## Límites que conviene conocer

- **GitHub retrasa los trabajos programados** en horas pico. Un `today` cada 30
  minutos puede ejecutarse con 5–15 minutos de retraso. No afecta a la
  corrección de los datos, solo a la frescura.
- **GitHub desactiva los cron de un repositorio sin actividad durante 60 días.**
  Llega un correo antes; basta con reactivarlo desde Actions.
- **La sesión de Mercado Libre caduca.** No existe un token de larga duración
  para estos datos: es la única intervención manual del sistema.


---

# Dental Amigo

Sincroniza la operación de la clínica desde la **API oficial de Dentalink**.

```
Dentalink API  ──HTTPS+token──▶  recolector  ──HTTPS+token──▶  /api/dental-amigo/ingest  ──▶  D1
```

A diferencia de Mercado Libre, aquí sí hay un mecanismo soportado: un **token
durable** que emite la cuenta ADMIN. No hay cookies, no hay sesión que caduque y
no hace falta navegador. El token solo deja de servir si alguien lo revoca.

## Lo que lee

| Recurso | Para qué |
|---|---|
| `GET /dentistas` | Profesionales y especialidad |
| `GET /cajas` | Cajas del día, abiertas y cerradas |
| `GET /pagos` | Cobros individuales de pacientes |
| `GET /citas` | Agenda del día y estado real de cada cita |

**Flujos DA no pasa por aquí.** Esa fuente ya llega sola desde un Apps Script del
propio libro de cálculo, de lunes a sábado alrededor de la 1:00 PM de
Hermosillo. Duplicarla crearía dos escritores para el mismo dato.

## Trabajos y frecuencia

| Trabajo | Cuándo | Para qué |
|---|---|---|
| `operations` | cada 20 min | agenda, cajas y cobros del día en curso |
| `month` | cada 6 horas | relee el mes para recoger revisiones de Dentalink |
| `discover` | a mano | describe la forma real de la API sin publicar nada |

La agenda cambia durante la jornada; el histórico del mes casi nunca. Releer el
mes entero cada veinte minutos gastaría peticiones contra un tercero para volver
a escribir lo mismo.

## Secretos

| Secreto | Valor |
|---|---|
| `DENTALINK_API_TOKEN` | Token de la API, emitido por la cuenta ADMIN en **Configuración → API** |
| `DENTAL_INGEST_URL` | `https://radar-afiliados-rafsl.el-rafasanchez.chatgpt.site/api/dental-amigo/ingest` |
| `DENTAL_INGEST_TOKEN` | El token de ingesta de Dental Amigo que ya vive en los Secrets del Site |

## Reconocer la API antes de confiar en ella

La documentación de Dentalink describe los recursos pero no todos sus campos.
Antes de dar por buena una cifra contable:

```bash
export DENTALINK_API_TOKEN='...'
export DENTAL_DRY_RUN=1
python3 run_dental.py discover
```

Imprime qué campos existen y de qué tipo son, **con los datos de paciente
enmascarados**, para que el informe pueda revisarse sin exponer información
clínica.

## Privacidad

Los nombres de paciente se reducen a *nombre de pila + inicial del apellido*
antes de salir del proceso. El tablero necesita distinguir personas dentro de
una agenda, no identificarlas.

## Códigos de salida

| Código | Significado | Acción |
|---|---|---|
| 0 | Publicado | ninguna |
| 1 | Fallo temporal (red, 5xx) | se reintenta solo en la siguiente ejecución |
| 2 | **Token de Dentalink revocado** | la cuenta ADMIN lo regenera y se actualiza `DENTALINK_API_TOKEN` |
| 3 | La API cambió de forma o falló la validación | correr `discover` y revisar el mapeo |

Ante el código 2 el flujo abre un aviso etiquetado `dentalink-token`.

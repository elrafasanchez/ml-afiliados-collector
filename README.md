# Recolector de Afiliados de Mercado Libre

Sincroniza las métricas del programa de Afiliados y Creadores hacia el tablero
de Central de Ingresos. Corre en GitHub Actions: **no necesita que tu
computadora esté encendida ni que Chrome esté abierto.**

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

# Renovar la sesión de Mercado Libre

## Por qué hace falta

Mercado Libre **no publica una API oficial para las métricas de Afiliados y
Creadores**, ni un mecanismo OAuth para estos datos. La API pública de
`developers.mercadolibre.com` cubre vendedores, publicaciones y órdenes; no hay
alcance de afiliado ni exportación oficial.

La única credencial disponible es la cookie de sesión del navegador. Por eso
esta es la única tarea manual de todo el sistema.

## Cuándo

Cuando el repositorio abra un aviso titulado **"La sesión de Mercado Libre
caducó"**, o cuando el tablero muestre la fuente como rezagada durante varias
horas.

En la práctica, del orden de una vez cada varias semanas.

## Cómo

1. En Chrome, con tu sesión de Mercado Libre iniciada, abre
   `https://www.mercadolibre.com.mx/afiliados/dashboard`.
2. Abre las herramientas de desarrollo (`⌥⌘I`) y ve a la pestaña **Network**.
3. Recarga la página y haz clic en la primera petición de la lista (`dashboard`).
4. En **Headers → Request Headers**, localiza la línea `Cookie:`.
5. Copia **todo** su valor (es largo: son varias cookies separadas por `; `).
6. Pégalo en GitHub, en
   **Settings → Secrets and variables → Actions → `ML_SESSION_COOKIE` → Update**.
7. Lanza el trabajo `today` desde **Actions** y confirma que termina en verde.
8. Cierra el aviso del repositorio.

## Precauciones

- Esa cadena **es** tu sesión: quien la tenga entra a tu cuenta. Pégala solo en
  el campo de secretos de GitHub, nunca en un archivo del repositorio, en un
  mensaje ni en un ticket.
- Cerrar la sesión de Mercado Libre en el navegador invalida también la cookie
  del recolector.
- Si cambias tu contraseña, la sesión se invalida y hay que repetir este
  procedimiento.

## Mientras la sesión está caducada

El sistema **no escribe ceros**. Conserva el último valor válido de cada día,
marca la fuente como rezagada y muestra la última hora real de corte. El tablero
sigue siendo correcto; simplemente deja de avanzar hasta que se renueve.

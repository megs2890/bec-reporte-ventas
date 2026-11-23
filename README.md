# Reporte de ventas BEC

Servicio que reemplaza al reporte diario que se generaba en una computadora. Corre en Railway:

- **Cada dia a las 7:00 a.m. (hora de Panama)** consulta las facturas y notas de credito de InterFuerza
  de los ultimos 5 dias, actualiza la base y envia un correo con el resumen a los directores.
- **Pagina web con clave** (`/`): el mismo reporte de ventas con filtros, KPIs y comparativo.
- **Vista de tickets** (`/tickets`): entradas por mes, caja vs. facturado, fin de semana vs. semana,
  adicionales, mezcla de publico.
- **Estado** (`/estado`): ultimas ejecuciones y botones para actualizar a mano.

## Como funciona

| Pieza | Archivo |
|---|---|
| Cliente del API v4 de InterFuerza (25 registros por pagina, pausa entre llamadas) | `app/interfuerza.py` |
| Sincronizacion y conversion de facturas a lineas | `app/sync.py` |
| Calculos (reporte, tickets, resumen del correo) | `app/report.py` |
| Correo (Resend o SMTP) | `app/mailer.py` |
| Web, clave de acceso y programacion diaria | `app/main.py` |
| Historico inicial hasta el 17 nov 2026 (53,704 lineas) | `data/seed.json.gz` |

La base es SQLite y vive en un volumen de Railway. La primera vez que arranca carga el historico de
`data/seed.json.gz`; desde ahi solo pide a InterFuerza los dias recientes.

En la primera sincronizacion el servicio trae tambien unos dias que ya estan en el historico y compara
los montos factura por factura para elegir la formula correcta del neto por linea (la documentacion del
API no dice que campo usar). El resultado queda visible en `/estado` como "Formula de monto".

## Despliegue en Railway

1. **New Project > Deploy from GitHub repo** y elegir este repositorio.
2. **Volumen:** en el servicio, *Volumes > New Volume*, montado en `/data`.
3. **Variables** (pestaña *Variables*; ver `.env.example`):
   - `IFX_TOKEN`: token de InterFuerza (Configuracion > Apps > InterFuerza API > Agregar Token), con
     permiso para las acciones GET `invoices` y `credit-notes`.
   - `REPORT_PASSWORD`: la clave que usaran los directores para entrar.
   - `SESSION_SECRET`: una cadena larga al azar.
   - `DATA_DIR=/data`
   - `PUBLIC_URL`: la direccion publica del servicio (se genera en *Settings > Networking > Generate Domain*).
   - Correo: `EMAIL_PROVIDER`, `EMAIL_FROM`, `EMAIL_TO` y `RESEND_API_KEY` (o las variables `SMTP_*`).
4. Abrir la direccion publica, entrar con la clave y revisar `/estado`. El boton **Actualizar ahora**
   lanza la primera sincronizacion sin esperar a las 7:00 a.m.

### Puntos a revisar antes de confiar en las cifras

- **IP del token.** InterFuerza permite limitar cada token a una lista de IPs. Railway no tiene IP de
  salida fija salvo que se active *Static Outbound IPs* (plan Pro). Si el token esta limitado por IP,
  hay que activar esa opcion y registrar la IP, o dejar el token sin restriccion de IP.
- **Limite del API.** 20 llamadas cada 10 segundos; si se excede, InterFuerza bloquea la IP una hora.
  El servicio espera 0.8 s entre llamadas (`IFX_PAUSE`).
- **Correo.** Railway bloquea SMTP saliente en algunos planes; por eso el valor por defecto es Resend,
  que requiere verificar el dominio del remitente.
- **Primera sincronizacion.** No se ha probado contra el API real (no habia token al construirlo).
  Despues de la primera corrida, comparar en `/` el total de noviembre con InterFuerza.

## Desarrollo local

```
pip install -r requirements.txt pytest
python -m pytest -q
DATA_DIR=./data_local REPORT_PASSWORD=prueba uvicorn app.main:app --reload
```

"""Servicio web del reporte de ventas de BEC: pagina con clave, sincronizacion y correo diarios."""
import gzip
import hmac
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import config, db, mailer, report, sync
from .interfuerza import Interfuerza

TPL = os.path.join(os.path.dirname(__file__), "templates")
COOKIE = "bec_sesion"
MAX_AGE = 60 * 60 * 24 * 30
_cache: dict = {}
_lock = threading.Lock()


def _tpl(name: str) -> str:
    with open(os.path.join(TPL, name), encoding="utf-8") as f:
        return f.read()


def _signer() -> URLSafeTimedSerializer:
    secret = config.env("SESSION_SECRET") or config.env("REPORT_PASSWORD")
    return URLSafeTimedSerializer(secret or "sin-configurar", salt="bec-reporte")


def _open_access() -> bool:
    """ACCESO_ABIERTO=1: el reporte y la vista de tickets se abren con solo el enlace, sin clave.
    La pagina Estado y el boton de actualizar siguen pidiendo la clave."""
    return config.env("ACCESO_ABIERTO") == "1"


def _authed(request: Request) -> bool:
    if not config.env("REPORT_PASSWORD"):
        return False  # sin clave configurada no se muestra nada
    try:
        return _signer().loads(request.cookies.get(COOKIE, ""), max_age=MAX_AGE) == "ok"
    except BadSignature:
        return False


def _state() -> dict:
    """Paginas ya armadas; se recalculan solo cuando cambia la base."""
    with db.connect() as con:
        stamp = db.get_meta(con, "actualizado", "") + "|" + str(con.execute("SELECT COUNT(*) FROM lines").fetchone()[0])
        if _cache.get("stamp") == stamp:
            return _cache
        lines = db.all_lines(con)
        actualizado = db.get_meta(con, "actualizado", "") or "carga inicial"
    data = report.build_data(lines)
    rango = f"{min(l['d'] for l in lines)} a {max(l['d'] for l in lines)}" if lines else "sin datos"
    head = (_tpl("report_head.html").replace("__RANGO__", rango).replace("__MESES__", str(len(data["months"])))
            .replace("__LINEAS__", str(len(lines))).replace("__GENERADO__", actualizado))
    # "</" se escapa para que ningun texto de producto pueda cerrar la etiqueta <script>
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = head + "const DATA = " + payload + ";\n" + _tpl("report_tail.html")
    tk = json.dumps(report.tickets(lines), ensure_ascii=False).replace("</", "<\\/")
    tickets = _tpl("tickets.html").replace("__TICKETS__", tk).replace("__GENERADO__", actualizado)
    _cache.update(stamp=stamp, page=gzip.compress(page.encode("utf-8"), 6),
                  tickets=gzip.compress(tickets.encode("utf-8"), 6))
    return _cache


def _send_daily_mail(sync_ok: bool, error: str = "", only_test: bool = False) -> dict:
    """Correo diario en el formato de siempre, con los datos de ayer.
    Si la actualizacion fallo no se envia el reporte: solo un aviso a EMAIL_AVISOS.
    only_test=True lo manda unicamente a EMAIL_AVISOS, para probar sin escribir a los directores."""
    now = datetime.now(config.TZ)
    ts, day = now.strftime("%Y-%m-%d %H:%M"), (now.date() - timedelta(days=1)).isoformat()
    out: dict = {}
    try:
        if only_test and not config.EMAIL_AVISOS:
            raise RuntimeError("Falta EMAIL_AVISOS (a quien mandar la prueba)")
        if not sync_ok:
            if config.EMAIL_AVISOS:
                mailer.send("Reporte ventas BEC: datos sin actualizar",
                            text=f"La actualizacion de hoy desde InterFuerza fallo y no se envio el reporte.\n\n"
                                 f"Detalle: {error}\n\nRevise la pagina Estado: {config.PUBLIC_URL}/estado",
                            to=config.EMAIL_AVISOS)
            out["correo"] = "no enviado: la actualizacion fallo" + (" (aviso enviado)" if config.EMAIL_AVISOS else "")
        else:
            with db.connect() as con:
                lines = db.all_lines(con)
                actualizado = db.get_meta(con, "actualizado", "")
            e = report.daily_email(lines, day, config.PUBLIC_URL, actualizado, con_clave=not _open_access())
            to = config.EMAIL_AVISOS if only_test else config.EMAIL_TO
            subject = ("[PRUEBA] " if only_test else "") + e["asunto"]
            out["correo"] = mailer.send(subject, html=e["html"], text=e["texto"], to=to) + (" (prueba)" if only_test else "")
        with db.connect() as con:
            db.log_run(con, ts, "correo", True, out["correo"])
    except Exception as ex:
        out["correo_error"] = f"{type(ex).__name__}: {ex}"
        with db.connect() as con:
            db.log_run(con, ts, "correo", False, out["correo_error"])
    return out


def daily_job(send_mail: bool = True) -> dict:
    """Sincroniza con InterFuerza y envia el correo. Nunca se ejecuta dos veces a la vez."""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "ya hay una actualizacion en curso"}
    try:
        try:
            result = sync.run_sync(Interfuerza(config.env("IFX_TOKEN")))
        except Exception as e:
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            with db.connect() as con:
                db.log_run(con, datetime.now(config.TZ).strftime("%Y-%m-%d %H:%M"), "sync", False, result["error"])
        if send_mail and config.EMAIL_TO:
            result.update(_send_daily_mail(bool(result.get("ok")), result.get("error", "")))
        return result
    finally:
        _lock.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    sched = None
    if config.env("DISABLE_SCHEDULER") != "1":
        sched = BackgroundScheduler(timezone=config.TZ)
        sched.add_job(daily_job, CronTrigger(hour=config.SYNC_HOUR, minute=config.SYNC_MINUTE, timezone=config.TZ),
                      id="diario", max_instances=1, coalesce=True, misfire_grace_time=3600)
        sched.start()
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")


def _gz(body: bytes) -> Response:
    return Response(body, media_type="text/html; charset=utf-8",
                    headers={"Content-Encoding": "gzip", "Cache-Control": "no-store",
                             "X-Robots-Tag": "noindex, nofollow"})


@app.get("/robots.txt")
def robots():
    # el reporte no debe aparecer en buscadores
    return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")


@app.get("/salud")
def salud():
    with db.connect() as con:
        return {"ok": True, "datos_hasta": db.get_meta(con, "datos_hasta", ""), "actualizado": db.get_meta(con, "actualizado", "")}


@app.get("/entrar", response_class=HTMLResponse)
def entrar_form(error: int = 0):
    msg = '<div class="err">Clave incorrecta.</div>' if error else ""
    if not config.env("REPORT_PASSWORD"):
        msg = '<div class="err">Falta configurar REPORT_PASSWORD en Railway.</div>'
    return _tpl("login.html").replace("__ERROR__", msg)


@app.post("/entrar")
def entrar(clave: str = Form("")):
    pw = config.env("REPORT_PASSWORD")
    if not pw or not hmac.compare_digest(clave.encode(), pw.encode()):
        time.sleep(1.5)  # frena los intentos repetidos de adivinar la clave
        return RedirectResponse("/entrar?error=1", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, _signer().dumps("ok"), max_age=MAX_AGE, httponly=True, samesite="lax",
                    secure=config.PUBLIC_URL.startswith("https"))
    return resp


@app.get("/salir")
def salir():
    resp = RedirectResponse("/entrar", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/")
def home(request: Request):
    if not _open_access() and not _authed(request):
        return RedirectResponse("/entrar", status_code=303)
    return _gz(_state()["page"])


@app.get("/tickets")
def tickets(request: Request):
    if not _open_access() and not _authed(request):
        return RedirectResponse("/entrar", status_code=303)
    return _gz(_state()["tickets"])


@app.get("/estado", response_class=HTMLResponse)
def estado(request: Request, iniciado: int = 0):
    """Ultimas ejecuciones (sincronizacion y correo) y botones para actualizar a mano."""
    if not _authed(request):
        return RedirectResponse("/entrar", status_code=303)
    import html as _h
    with db.connect() as con:
        meta = {"Datos hasta": db.get_meta(con, "datos_hasta", ""), "Ultima actualizacion": db.get_meta(con, "actualizado", "") or "solo carga inicial",
                "Formula de monto": db.get_meta(con, "modo_monto", "") or "sin calibrar",
                "Programado": f"{config.SYNC_HOUR:02d}:{config.SYNC_MINUTE:02d} hora de Panama",
                "Destinatarios del correo": str(len(config.EMAIL_TO)),
                "Avisos y pruebas van a": ", ".join(config.EMAIL_AVISOS) or "sin configurar",
                "Envio de correo": f"{config.EMAIL_PROVIDER}, clave " + ("configurada" if config.env("SMTP_PASSWORD" if config.EMAIL_PROVIDER == "smtp" else "RESEND_API_KEY") else "FALTA"),
                "Token de InterFuerza": "configurado" if config.env("IFX_TOKEN") else "FALTA",
                "Enlace privado del resumen (para la tarea del correo)": f"{config.PUBLIC_URL}/resumen/{_resumen_token()}"}
        runs = db.last_runs(con)
    rows = "".join(f"<tr><td>{_h.escape(r['ts'])}</td><td>{_h.escape(r['kind'])}</td><td>{'ok' if r['ok'] else 'FALLO'}</td><td>{_h.escape(r['detail'] or '')}</td></tr>" for r in runs)
    info = "".join(f"<tr><td>{k}</td><td>{_h.escape(v)}</td></tr>" for k, v in meta.items())
    aviso = {1: "<p><b>Actualizacion iniciada.</b> Recargue esta pagina en unos minutos.</p>",
             2: "<p><b>Prueba de correo ejecutada.</b> El resultado aparece abajo, en la primera fila.</p>"}.get(iniciado, "")
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Estado</title>
<style>body{{font-family:Arial,sans-serif;margin:24px;color:#222;background:#FAFAF8}}table{{border-collapse:collapse;margin:12px 0 22px}}td,th{{border-bottom:1px solid #ddd;padding:7px 10px;text-align:left;font-size:13px;vertical-align:top;max-width:720px;word-break:break-word}}button{{background:#FF5100;color:#fff;border:0;border-radius:6px;padding:10px 14px;font-weight:bold;cursor:pointer;margin-right:8px}}</style></head><body>
<h1>Estado del reporte</h1><p><a href="/">Reporte</a> &middot; <a href="/tickets">Tickets</a></p>{aviso}
<table>{info}</table>
<form method="post" action="/actualizar" style="display:inline"><button>Actualizar ahora</button></form>
<form method="post" action="/actualizar?correo=1" style="display:inline"><button>Actualizar y enviar correo a todos</button></form>
<form method="post" action="/probar-correo" style="display:inline"><button>Enviar correo de prueba (solo avisos)</button></form>
<h2>Ultimas ejecuciones</h2><table><tr><th>Fecha</th><th>Tipo</th><th>Resultado</th><th>Detalle</th></tr>{rows}</table></body></html>"""


def _resumen_token() -> str:
    """Clave del enlace privado del resumen, derivada del secreto de sesion (no requiere otra variable)."""
    secret = config.env("SESSION_SECRET") or config.env("REPORT_PASSWORD")
    return hmac.new(secret.encode(), b"resumen-diario", "sha256").hexdigest()[:40] if secret else ""


@app.get("/resumen/{token}")
def resumen(token: str, dia: str = "", formato: str = ""):
    """Resumen del dia anterior para la tarea que envia el correo. Solo responde con el enlace privado."""
    good = _resumen_token()
    if not good or not hmac.compare_digest(token.encode(), good.encode()):
        return JSONResponse({"error": "no encontrado"}, status_code=404)
    today = datetime.now(config.TZ).date()
    with db.connect() as con:
        lines = db.all_lines(con)
        actualizado = db.get_meta(con, "actualizado", "")
        datos_hasta = db.get_meta(con, "datos_hasta", "")
    day = dia or (today - timedelta(days=1)).isoformat()
    out = report.daily_email(lines, day, config.PUBLIC_URL, actualizado, con_clave=not _open_access())
    # "al_dia" indica si la base ya se actualizo hoy; si no, no conviene enviar el correo
    out["al_dia"] = actualizado[:10] == today.isoformat() and datos_hasta >= day
    out["datos_hasta"] = datos_hasta
    if formato == "lista":
        # version en lineas "clave: valor", facil de leer para herramientas que no devuelven JSON literal
        rows = [("asunto", out["asunto"]), ("al_dia", "si" if out["al_dia"] else "no"),
                ("datos_hasta", datos_hasta), ("actualizado", actualizado), ("control", out["control"])]
        rows += list(out["campos"].items())
        body = "<!DOCTYPE html><html lang=\"es\"><head><meta charset=\"UTF-8\"><title>Resumen</title></head><body><ul>" + \
               "".join(f"<li>{k}: {v}</li>" for k, v in rows) + "</ul></body></html>"
        return HTMLResponse(body, headers={"Cache-Control": "no-store"})
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@app.post("/probar-correo")
def probar_correo(request: Request):
    """Manda el correo de ayer solo a EMAIL_AVISOS, sin tocar a los directores."""
    if not _authed(request):
        return JSONResponse({"error": "no autorizado"}, status_code=401)
    _send_daily_mail(True, only_test=True)
    return RedirectResponse("/estado?iniciado=2", status_code=303)


@app.post("/actualizar")
def actualizar(request: Request, correo: int = 0):
    """Lanza la actualizacion a mano (en segundo plano). ?correo=1 tambien envia el correo."""
    if not _authed(request):
        return JSONResponse({"error": "no autorizado"}, status_code=401)
    threading.Thread(target=daily_job, kwargs={"send_mail": bool(correo)}, daemon=True).start()
    return RedirectResponse("/estado?iniciado=1", status_code=303)

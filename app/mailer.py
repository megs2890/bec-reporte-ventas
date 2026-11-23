"""Envio del correo diario. Resend usa HTTPS (funciona en Railway); SMTP queda como alternativa."""
import smtplib
from email.message import EmailMessage

import httpx

from . import config


def send(subject: str, html: str | None = None, to: list[str] | None = None, text: str | None = None) -> str:
    """Envia un correo. Con `text` va en texto plano (formato del reporte diario); con `html`, en HTML."""
    to = to or config.EMAIL_TO
    if not to or not config.EMAIL_FROM:
        raise RuntimeError("Faltan EMAIL_TO o EMAIL_FROM")
    if config.EMAIL_PROVIDER == "smtp":
        for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
            if not config.env(k):
                raise RuntimeError(f"Falta {k}")
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, config.EMAIL_FROM, ", ".join(to)
        msg.set_content(text or "Abra este correo en un cliente que muestre HTML.")
        if html:
            msg.add_alternative(html, subtype="html")
        with smtplib.SMTP(config.env("SMTP_HOST"), int(config.env("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            s.login(config.env("SMTP_USER"), config.env("SMTP_PASSWORD"))
            s.send_message(msg)
        return f"smtp: enviado a {len(to)}"
    key = config.env("RESEND_API_KEY")
    if not key:
        raise RuntimeError("Falta RESEND_API_KEY")
    r = httpx.post("https://api.resend.com/emails", timeout=30,
                   headers={"Authorization": f"Bearer {key}"},
                   json={"from": config.EMAIL_FROM, "to": to, "subject": subject,
                         **({"html": html} if html else {"text": text or ""})})
    if r.status_code >= 400:
        raise RuntimeError(f"Resend respondio HTTP {r.status_code}: {r.text[:300]}")
    return f"resend: enviado a {len(to)}"


def render(summary: dict, actualizado: str, sync_ok: bool, fmt_date) -> tuple[str, str]:
    usd = lambda v: ("-" if v < 0 else "") + "${:,.0f}".format(abs(v))
    def delta(cur, prev):
        if not prev:
            return '<span style="color:#5A5650">sin base</span>'
        p = (cur - prev) / abs(prev) * 100
        color = "#00B451" if p >= 0 else "#C8321E"
        return f'<span style="color:{color}">{"+" if p >= 0 else ""}{p:.0f}%</span>'
    td = 'style="padding:8px 10px;border-bottom:1px solid #E8E5DF;text-align:right;font-size:14px"'
    tdl = td.replace("text-align:right", "text-align:left")
    rows = "".join(
        f'<tr><td {tdl}>{r["cat"]}</td><td {td}>{usd(r["dia"])}</td><td {td}>{usd(r["mes"])}</td>'
        f'<td {td}>{usd(r["ant"])}</td><td {td}>{delta(r["mes"], r["ant"])}</td></tr>' for r in summary["cats"])
    t, e = summary["total"], summary["entradas"]
    link = f'<p style="margin:22px 0"><a href="{config.PUBLIC_URL}" style="background:#FF5100;color:#fff;padding:11px 18px;border-radius:6px;text-decoration:none;font-weight:bold">Abrir el reporte completo</a></p>' if config.PUBLIC_URL else ""
    warn = "" if sync_ok else '<p style="background:#FFF3CD;padding:10px 12px;border-radius:6px;font-size:13px">La actualizacion de hoy desde InterFuerza fallo. Las cifras corresponden a la ultima carga correcta.</p>'
    th = 'style="padding:8px 10px;text-align:right;font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:#5A5650;border-bottom:2px solid #222"'
    html = f"""<div style="font-family:Arial,Helvetica,sans-serif;color:#222;max-width:640px;margin:0 auto;background:#FAFAF8;padding:24px">
<p style="font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:#FF5100;margin:0 0 6px">BEC Experience Center</p>
<h1 style="font-size:22px;margin:0 0 4px">Ventas del {fmt_date(summary["dia"])}</h1>
<p style="color:#5A5650;font-size:13px;margin:0 0 18px">Venta neta en USD, sin ITBMS. Actualizado el {actualizado}.</p>
{warn}
<table style="width:100%;border-collapse:collapse;margin-bottom:18px"><tr>
<td style="padding:12px;background:#fff;border:1px solid #E8E5DF"><div style="font-size:11px;text-transform:uppercase;color:#5A5650">Venta del dia</div><div style="font-size:24px;font-weight:bold">{usd(t["dia"])}</div></td>
<td style="padding:12px;background:#fff;border:1px solid #E8E5DF"><div style="font-size:11px;text-transform:uppercase;color:#5A5650">Mes a la fecha</div><div style="font-size:24px;font-weight:bold">{usd(t["mes"])}</div><div style="font-size:12px">{delta(t["mes"], t["ant"])} vs mes anterior</div></td>
<td style="padding:12px;background:#fff;border:1px solid #E8E5DF"><div style="font-size:11px;text-transform:uppercase;color:#5A5650">Entradas del dia</div><div style="font-size:24px;font-weight:bold">{e["dia"]:,}</div><div style="font-size:12px">{e["mes"]:,} en el mes · {delta(e["mes"], e["ant"])}</div></td>
</tr></table>
<table style="width:100%;border-collapse:collapse;background:#fff">
<tr><th {th.replace("text-align:right", "text-align:left")}>Categoria</th><th {th}>Dia</th><th {th}>Mes a la fecha</th><th {th}>Mes anterior*</th><th {th}>Var.</th></tr>
{rows}
<tr><td {tdl}><b>Total</b></td><td {td}><b>{usd(t["dia"])}</b></td><td {td}><b>{usd(t["mes"])}</b></td><td {td}><b>{usd(t["ant"])}</b></td><td {td}><b>{delta(t["mes"], t["ant"])}</b></td></tr>
</table>
<p style="color:#5A5650;font-size:12px;margin-top:10px">* Mes anterior al mismo dia del mes (hasta el {fmt_date(summary["corte_ant"])}).</p>
{link}
</div>"""
    return f'Ventas BEC · {fmt_date(summary["dia"])} · {usd(t["dia"])}', html

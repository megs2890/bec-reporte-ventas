import gzip, json, os, sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DISABLE_SCHEDULER", "1")

import pytest

from app import config, db, report, sync
from app.interfuerza import Interfuerza

SEED = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "seed.json.gz")


def seed_lines():
    with gzip.open(SEED, "rt", encoding="utf-8") as f:
        return json.load(f)


def to_api_docs(lines, cn):
    """Convierte lineas del historico al formato documentado del API (para simularlo)."""
    docs = defaultdict(list)
    for l in lines:
        if l["cn"] == cn:
            docs[(l["id"], l["d"])].append(l)
    out = []
    for (i, d), ls in docs.items():
        out.append({"Invoice": {"id": i, "Date": d + " 10:00:00", "Status": "FACTURADA"},
                    "Lines": [{"Codigo": l["cod"], "Nombre": l["nom"], "Marca": l["mar"],
                               "Category_L1": "" if l["c1"] == "SIN CATEGORIA" else l["c1"],
                               "Category_L2": "" if l["c2"] == "(sin sub-categoria)" else l["c2"],
                               "Unidades": str(l["u"]), "Precio_Unitario": str((l["a"] + 1.0) / l["u"]) if l["u"] else "0",
                               "Discount": "1.0" if l["u"] else "0", "TaxValue": str(l["t"]),
                               "Total": str(l["a"] + l["t"])} for l in ls]})
    return out


class FakeHTTP:
    """Simula el API: pagina de 25 en 25 y filtra por fecha."""
    def __init__(self, lines):
        self.docs = {"invoices": to_api_docs(lines, 0), "credit-notes": to_api_docs(lines, 1)}
        self.calls = 0

    def post(self, url, json):
        self.calls += 1
        f, t = json["filters"][0]["value"], json["filters"][1]["value"]
        docs = [d for d in self.docs[json["action"]] if f <= d["Invoice"]["Date"][:10] <= t]
        p = int(json["page"])
        page = docs[(p - 1) * 25:p * 25]
        key = json["action"]
        body = {"class": "GET", "action": json["action"], key: page}
        return type("R", (), {"status_code": 200, "text": "", "json": lambda self: body})()


@pytest.fixture
def base(tmp_path):
    path = str(tmp_path / "t.db")
    assert db.init(path, SEED) > 50000
    return path


def test_seed_y_datos(base):
    with db.connect(base) as con:
        lines = db.all_lines(con)
    data = report.build_data(lines)
    assert data["months"][-1] == "2026-11" and "TICKETS" in data["catsL1"]
    nov = sum(report.signed(l) for l in lines if l["m"] == "2026-11" and l["c1"] == "TICKETS")
    assert round(nov) == 10569
    oct_total = sum(report.signed(l) for l in lines if l["m"] == "2026-10")
    assert round(oct_total) == 64977


def test_tickets_coincide_con_analisis(base):
    with db.connect(base) as con:
        t = report.tickets(db.all_lines(con))
    i = t["m"].index("2026-10")
    assert (t["caja"][i], t["fact"][i], t["res"][i]) == (646, 754, 396)
    j = t["m"].index("2026-11")
    assert t["caja"][j] + t["fact"][j] == 481 and t["parcial"] is True
    assert t["adic"][j] == 3340


def test_sync_calibra_y_no_cambia_totales(base):
    lines = seed_lines()
    http = FakeHTTP(lines)
    api = Interfuerza("x", pause=0, client=http)
    now = datetime(2026, 11, 18, 7, 0, tzinfo=config.TZ)
    with db.connect(base) as con:
        before = con.execute("SELECT COUNT(*), ROUND(SUM(a),2) FROM lines").fetchone()[:]
    r = sync.run_sync(api, path=base, now=now)
    assert r["ok"], r
    assert r["modo_monto"] == "precio_x_unid_menos_desc"  # la unica formula que cuadra en el simulador
    with db.connect(base) as con:
        after = con.execute("SELECT COUNT(*), ROUND(SUM(a),2) FROM lines").fetchone()[:]
    assert before[0] == after[0] and abs(before[1] - after[1]) < 0.5
    assert r["verificacion"] == "coincide con el historico"
    assert http.calls > 4


def test_sync_sin_datos_no_borra(base):
    api = Interfuerza("x", pause=0, client=FakeHTTP([]))
    now = datetime(2026, 11, 18, 7, 0, tzinfo=config.TZ)
    r = sync.run_sync(api, path=base, now=now)
    assert r["ok"] and "aviso" in r
    with db.connect(base) as con:
        assert con.execute("SELECT COUNT(*) FROM lines").fetchone()[0] > 50000


def test_sync_error_queda_registrado(base):
    class Boom:
        def post(self, *a, **k):
            return type("R", (), {"status_code": 401, "text": "token invalido", "json": lambda s: {}})()
    r = sync.run_sync(Interfuerza("x", pause=0, client=Boom()), path=base)
    assert not r["ok"] and "401" in r["error"]
    with db.connect(base) as con:
        assert db.last_runs(con)[0]["ok"] == 0


def test_facturas_anuladas_se_excluyen():
    doc = {"Invoice": {"id": "C1", "Date": "2026-11-20", "Status": "Anulada"}, "Lines": [{"Unidades": "1", "Precio_Unitario": "15"}]}
    assert sync.doc_to_lines(doc, 0) == []


def test_resumen_diario(base):
    with db.connect(base) as con:
        s = report.daily_summary(db.all_lines(con))
    assert s["dia"] == "2026-11-17" and s["corte_ant"] == "2026-10-17"
    assert round(s["total"]["mes"]) == 44248
    from app import mailer
    subject, html = mailer.render(s, "2026-11-18 07:00", True, report.fmt_date)
    assert "17 nov 2026" in subject and "TICKETS" in html


def test_web(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "w.db"))
    monkeypatch.setenv("REPORT_PASSWORD", "clave-de-prueba")
    monkeypatch.setenv("SESSION_SECRET", "secreto-de-prueba")
    from fastapi.testclient import TestClient
    from app import main
    main._cache.clear()
    with TestClient(main.app, follow_redirects=False) as c:
        assert c.get("/salud").json()["datos_hasta"] == "2026-11-17"
        assert c.get("/").status_code == 303 and c.get("/tickets").status_code == 303
        assert c.post("/actualizar").status_code == 401
        assert c.post("/entrar", data={"clave": "mala"}).headers["location"] == "/entrar?error=1"
        assert c.post("/entrar", data={"clave": "clave-de-prueba"}).headers["location"] == "/"
        home = c.get("/")
        assert home.status_code == 200 and "const DATA = " in home.text and "53704 lineas" in home.text
        tk = c.get("/tickets")
        assert tk.status_code == 200 and '"hasta": "2026-11-17"' in tk.text
        assert "Estado del reporte" in c.get("/estado").text


def test_correo_igual_al_reporte_anterior(base):
    """Mismas cifras que el correo real enviado el 18-nov-2026 (datos del 17)."""
    with db.connect(base) as con:
        e = report.daily_email(db.all_lines(con), "2026-11-17", "https://x")
    assert e["asunto"] == "Reporte ventas - Martes 17 de Noviembre, 2026"
    t = e["texto"]
    for frag in ("Venta neta: $627.75", "Tickets vendidos: 17", "Adicionales vendidos: 10",
                 "Tickets vendidos: 507", "Adicionales vendidos: 296",
                 "Tickets: 761 | Adicionales: 184", "Diferencia tickets: [-] -254 (-33.4%)",
                 "Diferencia adicionales: [+] +112 (+60.9%)", "Tickets: 4,767 | Adicionales: 0",
                 "Diferencia tickets: [-] -4,260 (-89.4%)", "Diferencia adicionales: [+] +296 (sin base)",
                 "Octubre 2026 (2026-10-01 a 2026-10-17)", "Noviembre 2025 (2025-11-01 a 2025-11-17)"):
        assert frag in t, frag


def test_resumen_requiere_enlace_privado(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setenv("REPORT_PASSWORD", "clave-de-prueba")
    monkeypatch.setenv("SESSION_SECRET", "secreto-de-prueba")
    from fastapi.testclient import TestClient
    from app import main
    main._cache.clear()
    with TestClient(main.app, follow_redirects=False) as c:
        assert c.get("/resumen/malo").status_code == 404
        r = c.get(f"/resumen/{main._resumen_token()}?dia=2026-11-17")
        assert r.status_code == 200 and r.json()["venta_neta"] == 627.75 and r.json()["al_dia"] is False
        e = r.json()
        c = e["campos"]
        assert e["control"] == 17 + 10 + 507 + 296 + 761 + 184 + 4767 + 0 + 62775
        assert c["dif_tk_ant"] == "[-] -254 (-33.4%)" and c["venta_neta_fmt"] == "$627.75"
        with TestClient(main.app) as c2:
            page = c2.get(f"/resumen/{main._resumen_token()}?dia=2026-11-17&formato=lista").text
        assert "<li>tk_mes: 507</li>" in page and "<li>dif_ad_anio: [+] +296 (sin base)</li>" in page


def test_acceso_abierto(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("REPORT_PASSWORD", "clave-de-prueba")
    monkeypatch.setenv("ACCESO_ABIERTO", "1")
    from fastapi.testclient import TestClient
    from app import main
    main._cache.clear()
    with TestClient(main.app, follow_redirects=False) as c:
        assert c.get("/").status_code == 200 and c.get("/tickets").status_code == 200
        assert c.get("/estado").status_code == 303 and c.post("/actualizar").status_code == 401
        assert "Disallow: /" in c.get("/robots.txt").text
        r = c.get(f"/resumen/{main._resumen_token()}?dia=2026-11-17")
        assert r.status_code == 200 and "Se entra con la clave" not in r.json()["texto"]
    monkeypatch.delenv("ACCESO_ABIERTO")
    with TestClient(main.app, follow_redirects=False) as c:
        assert c.get("/").status_code == 303


def test_correo_diario_por_smtp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "smtp")
    monkeypatch.setattr(config, "EMAIL_FROM", "Reporte <x@gmail.com>")
    monkeypatch.setattr(config, "EMAIL_TO", ["d1@ejemplo.com", "d2@ejemplo.com"])
    monkeypatch.setattr(config, "EMAIL_AVISOS", ["yo@ejemplo.com"])
    for k, v in {"SMTP_HOST": "smtp.gmail.com", "SMTP_USER": "x@gmail.com", "SMTP_PASSWORD": "p"}.items():
        monkeypatch.setenv(k, v)
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, msg): sent.append(msg)

    from app import main, mailer
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    db.init(config.DB_PATH)
    r = main._send_daily_mail(True, only_test=True)
    assert "prueba" in r["correo"] and sent[-1]["To"] == "yo@ejemplo.com" and sent[-1]["Subject"].startswith("[PRUEBA] Reporte ventas - ")
    assert "RESUMEN DEL DIA ANTERIOR" in sent[-1].get_content()
    r = main._send_daily_mail(True)
    assert sent[-1]["To"] == "d1@ejemplo.com, d2@ejemplo.com" and not sent[-1]["Subject"].startswith("[PRUEBA]")
    r = main._send_daily_mail(False, "HTTP 401")
    assert sent[-1]["To"] == "yo@ejemplo.com" and "sin actualizar" in sent[-1]["Subject"] and "no enviado" in r["correo"]
    assert len(sent) == 3

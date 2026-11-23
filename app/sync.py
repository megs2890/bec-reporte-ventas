"""Sincroniza facturas y notas de credito de InterFuerza hacia la tabla de lineas."""
from datetime import datetime, timedelta

from . import config, db
from .interfuerza import Interfuerza


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else 0.0
    except ValueError:
        return 0.0


# Formas posibles de obtener el neto sin ITBMS de una linea. El campo exacto no esta
# documentado, asi que `calibrar` las prueba contra el historico y guarda la que cuadra.
AMOUNT_MODES = {
    "precio_x_unid_menos_desc": lambda l: _num(l.get("Unidades")) * _num(l.get("Precio_Unitario")) - _num(l.get("Discount")),
    "precio_x_unid_factor_desc": lambda l: _num(l.get("Unidades")) * _num(l.get("Precio_Unitario")) * (1 - _num(l.get("DiscountFactor")) / (100 if _num(l.get("DiscountFactor")) > 1 else 1)),
    "total": lambda l: _num(l.get("Total")),
    "total_menos_itbms": lambda l: _num(l.get("Total")) - _num(l.get("TaxValue")),
    "precio_x_unid": lambda l: _num(l.get("Unidades")) * _num(l.get("Precio_Unitario")),
}
DEFAULT_MODE = "precio_x_unid_menos_desc"


def _header(doc: dict) -> dict:
    for k in ("Invoice", "invoice", "CreditNote", "Credit_Note", "credit-note", "Header"):
        if isinstance(doc.get(k), dict):
            return doc[k]
    return doc


def doc_to_lines(doc: dict, cn: int, mode: str = DEFAULT_MODE) -> list[dict]:
    """Convierte un documento del API en lineas con el formato del reporte."""
    h = _header(doc)
    if str(h.get("Status", "")).strip().upper() in config.IFX_EXCLUDE_STATUS:
        return []
    d = str(h.get("Date", ""))[:10]
    if len(d) != 10:
        return []
    amount = AMOUNT_MODES.get(mode, AMOUNT_MODES[DEFAULT_MODE])
    out = []
    for l in doc.get("Lines") or doc.get("lines") or []:
        l = l.get("Line", l) if isinstance(l, dict) else {}
        out.append({
            "d": d, "m": d[:7],
            "c1": (l.get("Category_L1") or "").strip() or "SIN CATEGORIA",
            "c2": (l.get("Category_L2") or "").strip() or "(sin sub-categoria)",
            "cod": (l.get("Codigo") or "").strip() or "(sin codigo)",
            "nom": (l.get("Nombre") or l.get("Descripcion") or "").strip(),
            "mar": (l.get("Marca") or "").strip(),
            # las facturas conservan el signo (hay lineas negativas por devoluciones o cambios);
            # las notas de credito se guardan en positivo y se restan al calcular el neto
            "u": abs(_num(l.get("Unidades"))) if cn else _num(l.get("Unidades")),
            "a": round(abs(amount(l)) if cn else amount(l), 2),
            "t": round(abs(_num(l.get("TaxValue"))) if cn else _num(l.get("TaxValue")), 2),
            "id": str(h.get("id", "")),
            "cn": cn,
        })
    return out


def fetch_range(api: Interfuerza, d_from: str, d_to: str, mode: str):
    """Devuelve (lineas de facturas, lineas de notas de credito, documentos crudos)."""
    raw_inv = list(api.documents("invoices", d_from, d_to))
    raw_cn = list(api.documents("credit-notes", d_from, d_to))
    inv = [x for doc in raw_inv for x in doc_to_lines(doc, 0, mode)]
    cns = [x for doc in raw_cn for x in doc_to_lines(doc, 1, mode)]
    # el filtro de fechas del API podria ser mas amplio que el pedido: recortar
    inv = [x for x in inv if d_from <= x["d"] <= d_to]
    cns = [x for x in cns if d_from <= x["d"] <= d_to]
    return inv, cns, raw_inv


def calibrate(con, raw_invoices: list[dict]) -> tuple[str, dict]:
    """Elige la formula de monto que mejor reproduce el historico cargado, por factura."""
    known = {}
    for r in con.execute("SELECT id, SUM(a) s FROM lines WHERE cn = 0 GROUP BY id"):
        known[r["id"]] = r["s"]
    errors = {}
    for mode in AMOUNT_MODES:
        err, n = 0.0, 0
        for doc in raw_invoices:
            lines = doc_to_lines(doc, 0, mode)
            if not lines or lines[0]["id"] not in known:
                continue
            err += abs(sum(x["a"] for x in lines) - known[lines[0]["id"]])
            n += 1
        errors[mode] = (round(err, 2), n)
    usable = {m: e for m, (e, n) in errors.items() if n > 0}
    best = min(usable, key=usable.get) if usable else DEFAULT_MODE
    return best, errors


def run_sync(api: Interfuerza, days_back: int | None = None, d_from: str | None = None,
             d_to: str | None = None, path: str | None = None, now: datetime | None = None) -> dict:
    """Trae el rango (por defecto los ultimos N dias hasta ayer) y lo sustituye en la base."""
    now = now or datetime.now(config.TZ)
    if not d_to:
        d_to = now.strftime("%Y-%m-%d")
    if not d_from:
        d_from = (now - timedelta(days=days_back if days_back is not None else config.SYNC_DAYS_BACK)).strftime("%Y-%m-%d")
    ts = now.strftime("%Y-%m-%d %H:%M")
    try:
        with db.connect(path) as con:
            mode = db.get_meta(con, "modo_monto", "")
            seed_hasta = db.get_meta(con, "seed_hasta", "")
        info = {"desde": d_from, "hasta": d_to}
        if not mode:
            # primera vez: traer tambien dias que ya estan en el historico para calibrar la formula
            cal_from = d_from
            if seed_hasta:
                cal_from = min(d_from, (datetime.strptime(seed_hasta, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d"))
            _, _, raw = fetch_range(api, cal_from, d_to, DEFAULT_MODE)
            with db.connect(path) as con:
                mode, errors = calibrate(con, raw)
                db.set_meta(con, "modo_monto", mode)
                db.set_meta(con, "calibracion", str(errors))
            info["calibracion"] = errors
        inv, cns, raw = fetch_range(api, d_from, d_to, mode)
        info.update({"modo_monto": mode, "facturas": len(raw), "lineas": len(inv), "lineas_nc": len(cns)})
        with db.connect(path) as con:
            if inv:
                db.replace_range(con, d_from, d_to, 0, inv)
                if cns:  # si el API no lista notas de credito, se conservan las que ya habia
                    db.replace_range(con, d_from, d_to, 1, cns)
                last = con.execute("SELECT MAX(d) FROM lines").fetchone()[0] or ""
                db.set_meta(con, "datos_hasta", last)
            else:
                info["aviso"] = "El API no devolvio facturas en el rango; no se modifico la base."
            db.set_meta(con, "actualizado", ts)
            db.log_run(con, ts, "sync", True, str(info))
        info["ok"] = True
        return info
    except Exception as e:  # se registra y se informa; el reporte sigue mostrando lo ultimo valido
        with db.connect(path) as con:
            db.log_run(con, ts, "sync", False, f"{type(e).__name__}: {e}")
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "desde": d_from, "hasta": d_to}

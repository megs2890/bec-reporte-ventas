"""Calculos del reporte: datos para la pagina principal, vista de tickets y resumen del correo."""
from collections import defaultdict
from datetime import date, datetime, timedelta

MES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
ADMISIONES = ("Residentes", "Extranjeros", "Jubilados", "Ninos 3-12", "Ninos 0-2", "Grupos")


def signed(l: dict, key: str = "a") -> float:
    v = l.get(key) or 0.0
    return -abs(v) if l.get("cn") else v


def month_range(m_from: str, m_to: str) -> list[str]:
    y, m = int(m_from[:4]), int(m_from[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= m_to:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def build_data(lines: list[dict]) -> dict:
    """Objeto DATA que consume la pagina principal (mismo formato del reporte original)."""
    months = month_range(min(l["m"] for l in lines), max(l["m"] for l in lines)) if lines else []
    l2 = defaultdict(set)
    for l in lines:
        l2[l["c1"]].add(l["c2"])
    return {"lines": lines, "months": months, "catsL1": sorted(l2),
            "catsL2ByL1": {k: sorted(v) for k, v in l2.items()}}


def segment(nom: str) -> str:
    n = (nom or "").upper()
    if "GRUPOS" in n:
        return "Grupos"
    if "BUS" in n or "TKT EXT" in n:
        return "Bus Rojo"
    if "TASTING" in n or "GUIDED" in n or "DEGUSTA" in n:
        return "Degustaciones"
    if "VIP" in n or "COFFEE EXPERIENCE" in n or "CHOCOLATE EXPERIENCE" in n:
        return "Experiencias"
    if "RESIDENTES" in n or "2X1" in n:
        return "Residentes"
    if "EXTRANJEROS" in n:
        return "Extranjeros"
    if "JUBILADOS" in n:
        return "Jubilados"
    if "0 A 2" in n:
        return "Ninos 0-2"
    if "3 A 12" in n:
        return "Ninos 3-12"
    return "Otros"


def is_invoiced(doc_id: str) -> bool:
    """Documentos INV/SNC = facturado (grupos, tour operadoras, corporativos); el resto es caja."""
    return (doc_id or "").upper().startswith(("INV", "SNC"))


def tickets(lines: list[dict], months_back: int = 14) -> dict:
    """Serie mensual de tickets para la vista /tickets."""
    # se toma toda la categoria TICKETS para que el neto coincida con el reporte principal
    t = [l for l in lines if l["c1"] == "TICKETS"]
    if not t:
        return {"m": []}
    last_m = max(l["m"] for l in t)
    months = month_range(min(l["m"] for l in t), last_m)[-months_back:]
    rows = {m: {"neto": 0.0, "adic": 0.0, "caja": 0.0, "fact": 0.0, "seg": defaultdict(float),
                "days": set(), "day_caja": defaultdict(float)} for m in months}
    for l in t:
        r = rows.get(l["m"])
        if r is None:
            continue
        s, u, seg = signed(l), signed(l, "u"), segment(l["nom"])
        r["neto"] += s
        r["days"].add(l["d"])
        r["seg"][seg] += u
        if seg == "Degustaciones":
            r["adic"] += s
        if seg in ADMISIONES:
            if is_invoiced(l["id"]):
                r["fact"] += u
            else:
                r["caja"] += u
                r["day_caja"][l["d"]] += u
    out = {k: [] for k in ("m", "lbl", "neto", "adic", "caja", "fact", "dias", "we", "wd", "ext", "ipv", "deg",
                           "res", "extr", "nin", "nin_pct")}
    for m in months:
        r, sg = rows[m], rows[m]["seg"]
        we = [v for d, v in r["day_caja"].items() if date.fromisoformat(d).weekday() >= 5]
        wd = [v for d, v in r["day_caja"].items() if date.fromisoformat(d).weekday() < 5]
        ent = r["caja"] + r["fact"]
        nin = sg["Ninos 3-12"] + sg["Ninos 0-2"]
        indiv = ent - sg["Grupos"]
        out["m"].append(m)
        out["lbl"].append(f"{MES[int(m[5:7]) - 1]} {m[2:4]}")
        out["neto"].append(round(r["neto"]))
        out["adic"].append(round(r["adic"]))
        out["caja"].append(round(r["caja"]))
        out["fact"].append(round(r["fact"]))
        out["dias"].append(len(r["days"]))
        out["we"].append(round(sum(we) / len(we), 1) if we else 0)
        out["wd"].append(round(sum(wd) / len(wd), 1) if wd else 0)
        re_ = sg["Residentes"] + sg["Extranjeros"]
        out["ext"].append(round(sg["Extranjeros"] / re_ * 100, 1) if re_ else 0)
        out["ipv"].append(round(r["neto"] / ent, 2) if ent else 0)
        pag = ent - sg["Ninos 0-2"]
        out["deg"].append(round(sg["Degustaciones"] / pag * 100, 1) if pag else 0)
        out["res"].append(round(sg["Residentes"]))
        out["extr"].append(round(sg["Extranjeros"]))
        out["nin"].append(round(nin))
        out["nin_pct"].append(round(nin / indiv * 100, 1) if indiv else 0)
    last_d = max(l["d"] for l in t)
    out["hasta"] = last_d
    out["parcial"] = _is_partial(last_d)
    return out


def _is_partial(last_d: str) -> bool:
    d = date.fromisoformat(last_d)
    return (d + timedelta(days=1)).month == d.month


def daily_summary(lines: list[dict], day: str | None = None) -> dict:
    """Resumen para el correo: ultimo dia con datos, mes a la fecha y mismo tramo del mes anterior."""
    if not lines:
        return {}
    day = day or max(l["d"] for l in lines)
    d = date.fromisoformat(day)
    m = day[:7]
    prev_end = d.replace(day=1) - timedelta(days=1)
    pm = prev_end.strftime("%Y-%m")
    prev_cut = f"{pm}-{min(d.day, prev_end.day):02d}"
    cats = defaultdict(lambda: {"dia": 0.0, "mes": 0.0, "ant": 0.0})
    tk = {"dia": 0.0, "mes": 0.0, "ant": 0.0}
    for l in lines:
        s = signed(l)
        adm = l["c1"] == "TICKETS" and segment(l["nom"]) in ADMISIONES
        u = signed(l, "u") if adm else 0.0
        if l["d"] == day:
            cats[l["c1"]]["dia"] += s
            tk["dia"] += u
        if l["m"] == m and l["d"] <= day:
            cats[l["c1"]]["mes"] += s
            tk["mes"] += u
        if l["m"] == pm and l["d"] <= prev_cut:
            cats[l["c1"]]["ant"] += s
            tk["ant"] += u
    rows = sorted(({"cat": c, **{k: round(v, 2) for k, v in x.items()}} for c, x in cats.items()
                   if any(abs(v) > 0.004 for v in x.values())), key=lambda r: -r["mes"])
    tot = {k: round(sum(r[k] for r in rows), 2) for k in ("dia", "mes", "ant")}
    return {"dia": day, "mes": m, "mes_ant": pm, "corte_ant": prev_cut, "cats": rows, "total": tot,
            "entradas": {k: round(v) for k, v in tk.items()}, "dias_mes": d.day}


def fmt_date(day: str) -> str:
    d = datetime.strptime(day, "%Y-%m-%d")
    return f"{d.day} {MES[d.month - 1].lower()} {d.year}"


# ---------- Correo diario (mismo formato del reporte que se enviaba antes) ----------
DIAS = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre",
         "Octubre", "Noviembre", "Diciembre"]
C2_TICKETS = {"TICKETS", "TICKET", "TOUR OPERADORAS / VENDEDORA"}
C2_ADICIONALES = {"ADICIONALES"}


def fecha_larga(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}, {d.year}"


def _units(lines, d_from: str, d_to: str) -> tuple[int, int]:
    tk = ad = 0.0
    for l in lines:
        if l["c1"] != "TICKETS" or not (d_from <= l["d"] <= d_to):
            continue
        if l["c2"] in C2_TICKETS:
            tk += signed(l, "u")
        elif l["c2"] in C2_ADICIONALES:
            ad += signed(l, "u")
    return round(tk), round(ad)


def _same_span(day: date, year: int, month: int) -> tuple[str, str]:
    """Del 1 al mismo dia del mes indicado (recortado si ese mes es mas corto)."""
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = (nxt - timedelta(days=1)).day
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{min(day.day, last):02d}"


def _dif(cur: int, prev: int) -> str:
    d = cur - prev
    sign = "[+]" if d >= 0 else "[-]"
    pct = f"({d / prev * 100:+.1f}%)" if prev else "(sin base)"
    return f"{sign} {d:+,} {pct}"


def daily_email(lines: list[dict], day: str, public_url: str = "", actualizado: str = "",
                con_clave: bool = True) -> dict:
    """Asunto y texto del correo diario para el dia indicado (normalmente ayer)."""
    d = date.fromisoformat(day)
    neta = sum(signed(l) for l in lines if l["d"] == day)
    tk_d, ad_d = _units(lines, day, day)
    m0 = f"{d.year:04d}-{d.month:02d}-01"
    tk_m, ad_m = _units(lines, m0, day)
    py, pm = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
    p0, p1 = _same_span(d, py, pm)
    tk_p, ad_p = _units(lines, p0, p1)
    y0, y1 = _same_span(d, d.year - 1, d.month)
    tk_y, ad_y = _units(lines, y0, y1)
    bar = "=" * 44
    mes, mes_p = f"{MESES[d.month - 1]} {d.year}", f"{MESES[pm - 1]} {py}"
    mes_y = f"{MESES[d.month - 1]} {d.year - 1}"
    link = (f"El reporte interactivo esta en linea (se actualiza cada manana):\n{public_url}\n"
            + ("Se entra con la clave del reporte.\n" if con_clave else "")) if public_url else ""
    text = f"""RESUMEN DEL DIA ANTERIOR
{bar}
  Fecha: {fecha_larga(day)}

  Venta neta: ${neta:,.2f}
  Tickets vendidos: {tk_d:,}
  Adicionales vendidos: {ad_d:,}

ACUMULADO DEL MES
{bar}
  Periodo: {m0} a {day}
  Tickets vendidos: {tk_m:,}
  Adicionales vendidos: {ad_m:,}

COMPARATIVO VS MES ANTERIOR
{bar}
  {mes} ({m0} a {day}):
     Tickets: {tk_m:,} | Adicionales: {ad_m:,}
  {mes_p} ({p0} a {p1}):
     Tickets: {tk_p:,} | Adicionales: {ad_p:,}
  Diferencia tickets: {_dif(tk_m, tk_p)}
  Diferencia adicionales: {_dif(ad_m, ad_p)}

COMPARATIVO VS MISMO MES DEL ANO ANTERIOR
{bar}
  {mes} ({m0} a {day}):
     Tickets: {tk_m:,} | Adicionales: {ad_m:,}
  {mes_y} ({y0} a {y1}):
     Tickets: {tk_y:,} | Adicionales: {ad_y:,}
  Diferencia tickets: {_dif(tk_m, tk_y)}
  Diferencia adicionales: {_dif(ad_m, ad_y)}

---
Filtro tickets: TICKETS / TICKETS,TICKET,TOUR OPERADORAS / VENDEDORA
Filtro adicionales: TICKETS / ADICIONALES

{link}
Este correo fue generado automaticamente."""
    # Campos cortos, ya con formato, para armar el correo sin copiar el texto largo.
    # "control" es la suma de las cantidades mas la venta neta en centavos: sirve para
    # comprobar que los valores se leyeron bien.
    campos = {
        "fecha_larga": fecha_larga(day), "venta_neta_fmt": f"${neta:,.2f}",
        "tk_dia": tk_d, "ad_dia": ad_d, "mes_desde": m0, "mes_hasta": day, "tk_mes": tk_m, "ad_mes": ad_m,
        "mes_nombre": mes, "ant_nombre": mes_p, "ant_desde": p0, "ant_hasta": p1, "tk_ant": tk_p, "ad_ant": ad_p,
        "dif_tk_ant": _dif(tk_m, tk_p), "dif_ad_ant": _dif(ad_m, ad_p),
        "anio_nombre": mes_y, "anio_desde": y0, "anio_hasta": y1, "tk_anio": tk_y, "ad_anio": ad_y,
        "dif_tk_anio": _dif(tk_m, tk_y), "dif_ad_anio": _dif(ad_m, ad_y),
    }
    control = tk_d + ad_d + tk_m + ad_m + tk_p + ad_p + tk_y + ad_y + round(neta * 100)
    return {"dia": day, "asunto": f"Reporte ventas - {fecha_larga(day)}", "texto": text,
            "actualizado": actualizado, "venta_neta": round(neta, 2), "campos": campos, "control": control}

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

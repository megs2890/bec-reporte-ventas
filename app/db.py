"""Almacen SQLite de lineas de venta."""
import gzip
import json
import os
import sqlite3
from contextlib import contextmanager

from . import config

COLS = ("d", "m", "c1", "c2", "cod", "nom", "mar", "u", "a", "t", "id", "cn")

SCHEMA = """
CREATE TABLE IF NOT EXISTS lines (
  d TEXT NOT NULL, m TEXT NOT NULL, c1 TEXT, c2 TEXT, cod TEXT, nom TEXT, mar TEXT,
  u REAL, a REAL, t REAL, id TEXT, cn INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_lines_d ON lines(d);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS runs (
  ts TEXT NOT NULL, kind TEXT NOT NULL, ok INTEGER NOT NULL, detail TEXT
);
"""


@contextmanager
def connect(path: str | None = None):
    path = path or config.DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init(path: str | None = None, seed_path: str | None = None) -> int:
    """Crea las tablas y, si la base esta vacia, carga el historico inicial. Devuelve lineas cargadas."""
    seed_path = config.SEED_PATH if seed_path is None else seed_path
    with connect(path) as con:
        con.executescript(SCHEMA)
        n = con.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
        if n or not seed_path or not os.path.exists(seed_path):
            return 0
        with gzip.open(seed_path, "rt", encoding="utf-8") as f:
            seed = json.load(f)
        insert_lines(con, seed)
        last = max(l["d"] for l in seed) if seed else ""
        set_meta(con, "seed_hasta", last)
        set_meta(con, "datos_hasta", last)
        return len(seed)


def insert_lines(con, lines) -> None:
    con.executemany(
        "INSERT INTO lines (%s) VALUES (%s)" % (",".join(COLS), ",".join("?" * len(COLS))),
        [tuple(l.get(c) for c in COLS) for l in lines],
    )


def replace_range(con, d_from: str, d_to: str, cn: int, lines) -> None:
    """Sustituye las lineas (facturas o notas de credito) de un rango de fechas."""
    con.execute("DELETE FROM lines WHERE d >= ? AND d <= ? AND cn = ?", (d_from, d_to, cn))
    insert_lines(con, lines)


def all_lines(con, d_from: str | None = None, d_to: str | None = None) -> list[dict]:
    q, args = "SELECT %s FROM lines" % ",".join(COLS), []
    if d_from and d_to:
        q += " WHERE d >= ? AND d <= ?"
        args = [d_from, d_to]
    q += " ORDER BY cn, d, id"
    return [dict(r) for r in con.execute(q, args)]


def get_meta(con, k: str, default: str = "") -> str:
    r = con.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
    return r[0] if r else default


def set_meta(con, k: str, v: str) -> None:
    con.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, v))


def log_run(con, ts: str, kind: str, ok: bool, detail: str) -> None:
    con.execute("INSERT INTO runs (ts, kind, ok, detail) VALUES (?, ?, ?, ?)", (ts, kind, int(ok), detail[:2000]))


def last_runs(con, n: int = 15) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT ts, kind, ok, detail FROM runs ORDER BY rowid DESC LIMIT ?", (n,))]

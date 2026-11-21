"""PostGIS access: schema setup, bulk inserts and the spatial SQL used by the pipeline."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import psycopg

from msp.config import db_url

SQL_DIR = Path(__file__).resolve().parent / "sql"


@contextmanager
def connect(url: str | None = None):
    conn = psycopg.connect(url or db_url(), autocommit=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(url: str | None = None) -> None:
    with connect(url) as conn:
        conn.execute((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))


def query_df(sql: str, params: dict | tuple | None = None, url: str | None = None) -> pd.DataFrame:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description] if cur.description else []
        return pd.DataFrame(cur.fetchall(), columns=cols)


def execute(sql: str, params: dict | tuple | None = None, url: str | None = None) -> int:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def copy_rows(table: str, columns: list[str], rows, url: str | None = None, geom_cols: dict | None = None) -> int:
    """Bulk insert via COPY. geom_cols maps a column to its SRID; values are given as WKT text and
    converted with ST_GeomFromText through a temporary staging table."""
    geom_cols = geom_cols or {}
    n = 0
    with connect(url) as conn, conn.cursor() as cur:
        stage = f"stage_{table}"
        col_defs = ", ".join(f"{c} text" for c in columns)
        cur.execute(f"CREATE TEMP TABLE {stage} ({col_defs}) ON COMMIT DROP")
        with cur.copy(f"COPY {stage} ({', '.join(columns)}) FROM STDIN") as cp:
            for row in rows:
                cp.write_row([None if (v is None or (isinstance(v, float) and v != v)) else str(v) for v in row])
                n += 1
        select = ", ".join(
            f"ST_SetSRID(ST_GeomFromText({c}), {geom_cols[c]})" if c in geom_cols else c for c in columns)
        cur.execute(f"""
            INSERT INTO {table} ({', '.join(columns)})
            SELECT {', '.join(_cast(table, c, cur, geom_cols) for c in columns)}
            FROM (SELECT {select.replace('ST_SetSRID', 'ST_SetSRID')} FROM {stage}) s({', '.join(columns)})
        """)
    return n


def _cast(table: str, column: str, cur, geom_cols: dict) -> str:
    if column in geom_cols:
        return column
    cur.execute("""SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
                   WHERE a.attrelid = %s::regclass AND a.attname = %s""", (table, column))
    row = cur.fetchone()
    return f"{column}::{row[0]}" if row else column

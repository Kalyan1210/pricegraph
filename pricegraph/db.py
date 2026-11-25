"""DuckDB storage: raw normalized rates, the Ember payer map, and a priced view."""
from __future__ import annotations

import threading

import duckdb

from .codes import SHOPPABLE
from .config import DB_PATH

_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS rates (
  source_id VARCHAR, system VARCHAR, hospital VARCHAR, code VARCHAR, code_type VARCHAR,
  description VARCHAR, setting VARCHAR, billing_class VARCHAR, modifiers VARCHAR,
  payer_raw VARCHAR, plan_raw VARCHAR, gross DOUBLE, cash DOUBLE, min DOUBLE, max DOUBLE,
  negotiated_dollar DOUBLE, negotiated_percentage DOUBLE, estimated_amount DOUBLE,
  algorithm VARCHAR, methodology VARCHAR
);
CREATE TABLE IF NOT EXISTS payer_map (
  payer_raw VARCHAR, plan_raw VARCHAR, payer VARCHAR, product VARCHAR, line_of_business VARCHAR,
  PRIMARY KEY (payer_raw, plan_raw)
);
CREATE TABLE IF NOT EXISTS services (code VARCHAR PRIMARY KEY, label VARCHAR, category VARCHAR);
CREATE TABLE IF NOT EXISTS loads (
  source_id VARCHAR PRIMARY KEY, hospital VARCHAR, system VARCHAR, parser VARCHAR,
  bytes BIGINT, rows_seen BIGINT, rows_kept BIGINT, truncated BOOLEAN, error VARCHAR, seconds DOUBLE
);
CREATE OR REPLACE VIEW priced AS
SELECT r.*, s.label AS service, s.category,
  coalesce(m.payer, r.payer_raw) AS payer, m.product, m.line_of_business,
  CASE
    WHEN r.negotiated_dollar > 0 THEN r.negotiated_dollar
    WHEN r.negotiated_percentage > 0 AND r.gross > 0 THEN round(r.negotiated_percentage / 100.0 * r.gross, 2)
    WHEN r.estimated_amount > 0 THEN r.estimated_amount
  END AS price,
  CASE
    WHEN r.negotiated_dollar > 0 THEN 'negotiated $'
    WHEN r.negotiated_percentage > 0 AND r.gross > 0 THEN '% of gross'
    WHEN r.estimated_amount > 0 THEN 'estimate'
  END AS price_basis
FROM rates r
LEFT JOIN services s ON s.code = r.code
LEFT JOIN payer_map m ON m.payer_raw = r.payer_raw AND m.plan_raw = coalesce(r.plan_raw, '');
"""


def con() -> duckdb.DuckDBPyConnection:
    global _con
    with _lock:
        if _con is None:
            _con = duckdb.connect(str(DB_PATH))
            _con.execute(SCHEMA)
            _con.execute("DELETE FROM services")
            _con.executemany("INSERT INTO services VALUES (?, ?, ?)",
                             [(c, l, k) for c, (l, k) in SHOPPABLE.items()])
        return _con


def query(sql: str, params: list | None = None) -> list[dict]:
    c = con()
    with _lock:
        cur = c.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def execute(sql: str, params: list | None = None) -> None:
    c = con()
    with _lock:
        c.execute(sql, params or [])


def insert_rates(rows: list[tuple]) -> None:
    if not rows:
        return
    c = con()
    with _lock:
        c.executemany("INSERT INTO rates VALUES (" + ",".join(["?"] * 20) + ")", rows)

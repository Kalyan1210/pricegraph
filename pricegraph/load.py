"""Stream each full price file through its Ember-written parser (sandboxed) into DuckDB."""
from __future__ import annotations

import time

from .codes import canonical, match_keys
from .config import PARSERS
from .db import execute, insert_rates
from .events import emit
from .sample import open_full
from .sandbox import stream_parser

BATCH = 5000


def hospital_label(src: dict) -> str:
    locs = src.get("locations") or [src["id"]]
    return locs[0] if len(locs) == 1 else f"{locs[0]} (+{len(locs) - 1} sites)"


def system_label(src: dict) -> str:
    d = src["domain"].replace("www.", "")
    return {"texashealth.org": "Texas Health Resources", "medicalcityhealthcare.com": "Medical City (HCA)",
            "methodisthealthsystem.org": "Methodist Health System", "utsouthwestern.edu": "UT Southwestern",
            "parklandhealth.org": "Parkland Health", "childrens.com": "Children's Health",
            "cookchildrens.org": "Cook Children's", "jpshealthnet.org": "JPS Health Network"}.get(d, d)


def _s(v):
    return None if v is None else str(v)[:500]


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def load_source(src: dict, parser: str, max_mb: float | None = None) -> dict:
    sid, hosp, system = src["id"], hospital_label(src), system_label(src)
    code = (PARSERS / f"{parser}.py").read_text()
    execute("DELETE FROM rates WHERE source_id = ?", [sid])
    emit("load_start", id=sid, hospital=hosp)
    buf: list[tuple] = []
    t0 = time.time()
    last = [0.0]

    def on_row(r: dict) -> None:
        buf.append((sid, system, hosp, canonical(r.get("match", "")), _s(r.get("match_type")),
                    _s(r.get("description")), _s(r.get("setting")), _s(r.get("billing_class")),
                    _s(r.get("modifiers")), _s(r.get("payer_name")), _s(r.get("plan_name")) or "",
                    _f(r.get("gross")), _f(r.get("cash")), _f(r.get("min")), _f(r.get("max")),
                    _f(r.get("negotiated_dollar")), _f(r.get("negotiated_percentage")),
                    _f(r.get("estimated_amount")), _s(r.get("negotiated_algorithm")), _s(r.get("methodology"))))
        if len(buf) >= BATCH:
            insert_rates(buf)
            buf.clear()

    def on_progress(st: dict) -> None:
        if time.time() - last[0] > 1.5:
            last[0] = time.time()
            emit("load_progress", id=sid, mb=round(st["bytes"] / 1e6, 1), rows_seen=st["rows_seen"],
                 rows_kept=st["rows_kept"])

    try:
        fp = open_full(src)
    except Exception as e:  # noqa: BLE001
        emit("load_done", id=sid, status="error", detail=str(e)[:200])
        return {"id": sid, "error": str(e)}
    try:
        st = stream_parser(code, fp, match_keys(), on_row, on_progress,
                           max_bytes=int(max_mb * 1e6) if max_mb else None)
    finally:
        fp.close()
    insert_rates(buf)
    secs = round(time.time() - t0, 1)
    execute("INSERT OR REPLACE INTO loads VALUES (?,?,?,?,?,?,?,?,?,?)",
            [sid, hosp, system, parser, st["bytes"], st["rows_seen"], st["rows_kept"], st["truncated"],
             st["error"], secs])
    emit("load_done", id=sid, status="error" if st["error"] else "ok", mb=round(st["bytes"] / 1e6, 1),
         rows_seen=st["rows_seen"], rows_kept=st["rows_kept"], truncated=st["truncated"], seconds=secs,
         detail=st["error"])
    return {"id": sid, **st, "seconds": secs}

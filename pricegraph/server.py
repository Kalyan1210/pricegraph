"""Dashboard server: live event stream, state, compare and ask endpoints, pipeline trigger."""
from __future__ import annotations

import asyncio
import json
import threading

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .config import BUDGET_CAP_USD, EVENTS, ROOT, SOURCES
from .ember import totals

app = FastAPI(title="PriceGraph")
_run_lock = threading.Lock()


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/events")
async def events(replay: int = 1):
    async def gen():
        EVENTS.touch()
        with open(EVENTS, encoding="utf-8") as f:
            if not replay:
                f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    yield f"data: {line.strip()}\n\n"
                else:
                    await asyncio.sleep(0.25)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/state")
def state():
    srcs = json.loads(SOURCES.read_text()) if SOURCES.exists() else []
    out = {"totals": totals(), "budget_cap": BUDGET_CAP_USD, "sources": srcs, "db": {}}
    try:
        from .db import query
        out["db"] = query("""SELECT count(*) rows, count(DISTINCT hospital) hospitals, count(DISTINCT code) services,
                                    count(DISTINCT payer_raw) payer_strings FROM rates""")[0]
        pm = query("SELECT count(*) s, count(DISTINCT payer) n FROM payer_map")[0]
        out["db"]["canonical_payers"], out["db"]["mapped_strings"] = pm["n"], pm["s"]
    except Exception as e:  # noqa: BLE001
        out["db_error"] = str(e)[:200]
    return out


@app.get("/api/services")
def services():
    from .db import query
    return query("""SELECT s.code, s.label, s.category, count(r.code) n, count(DISTINCT r.hospital) hospitals
                    FROM services s LEFT JOIN rates r ON r.code = s.code GROUP BY 1,2,3
                    HAVING count(r.code) > 0 ORDER BY hospitals DESC, n DESC""")


@app.get("/api/payers")
def payers():
    from .ask import list_payers
    return list_payers()


@app.get("/api/compare")
def compare(code: str, payer: str = "", lob: str = ""):
    from .ask import compare_prices
    return compare_prices(code, payer, lob)


class Ask(BaseModel):
    q: str


@app.post("/api/ask")
def ask_ep(body: Ask):
    from .ask import ask
    try:
        return ask(body.q)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:400]}, status_code=500)


class Run(BaseModel):
    limit: int | None = None
    max_mb: float | None = None
    load: bool = True


@app.post("/api/run")
def run_ep(body: Run):
    if not _run_lock.acquire(blocking=False):
        return JSONResponse({"error": "a run is already in progress"}, status_code=409)

    def go():
        from .pipeline import run
        try:
            run(limit=body.limit, max_mb=body.max_mb, load=body.load)
        finally:
            _run_lock.release()
    threading.Thread(target=go, daemon=True).start()
    return {"started": True}

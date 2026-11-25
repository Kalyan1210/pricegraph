"""End-to-end run: discover -> sample -> Ember parser agents -> sandboxed full loads -> payer map."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from . import agent
from .config import DATA, SOURCES
from .discover import discover
from .ember import spent, totals
from .events import emit, reset
from .load import load_source
from .payers import consolidate, normalize

BUILD = DATA / "build.json"
_kind_locks = {"json": threading.Lock(), "csv": threading.Lock()}


def _build_one(src: dict) -> dict:
    """At most one Ember agent per family (csv/json) at a time; everyone else re-checks the
    library after waiting, so a parser written for one hospital is reused by the rest for $0."""
    try:
        _, sample, info = agent.sample_path_for(src)
        fam = "json" if info["kind"] == "json" else "csv"
    except Exception:  # noqa: BLE001
        return agent.build_parser(src)
    with _kind_locks[fam]:
        return agent.build_parser(src)


def build(sources: list[dict], workers: int = 8) -> list[dict]:
    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(_build_one, sources))
    BUILD.write_text(json.dumps(results, indent=2, default=str))
    return results


def run(metro: str = "dfw", limit: int | None = None, load: bool = True, max_mb: float | None = None,
        load_workers: int = 4, fresh_events: bool = True) -> dict:
    if fresh_events:
        reset()
    start_spend = spent()
    emit("run_start", metro=metro, spent_before=round(start_spend, 5))
    sources = discover(metro)
    if limit:
        sources = sources[:limit]
    results = build(sources)
    ok = {r["id"]: r for r in results if r.get("status") in ("written", "reused")}
    by_id = {s["id"]: s for s in sources}
    loads = []
    if load and ok:
        with ThreadPoolExecutor(load_workers) as ex:
            loads = list(ex.map(lambda sid: load_source(by_id[sid], ok[sid]["parser"], max_mb), ok))
    payer = normalize() if load else {}
    if load:
        payer["consolidated"] = consolidate()["after"]
    t = totals()
    summary = {
        "files": len(sources),
        "parsers_written": sum(1 for r in results if r.get("status") == "written"),
        "reused": sum(1 for r in results if r.get("status") == "reused"),
        "failed": sum(1 for r in results if r.get("status") not in ("written", "reused")),
        "rows_kept": sum(l.get("rows_kept", 0) for l in loads),
        "gb_streamed": round(sum(l.get("bytes", 0) for l in loads) / 1e9, 2),
        "payers": payer, "run_cost_usd": round(t["cost_usd"] - start_spend, 5), "total_spent_usd": round(t["cost_usd"], 5),
    }
    emit("run_done", **summary)
    return summary

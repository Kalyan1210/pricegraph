"""Ember normalizes the payer/plan strings: thousands of spellings -> canonical payer + product.

"UNITED HEALTHCARE OF TX [1079]" / "UHC - UMR [107903]" / "United Healthcare Choice Plus"
all need to become payer=UnitedHealthcare with the right product, or nobody can compare prices.
Batches of strings go to Ember with a strict JSON schema; results are cached in DuckDB, so a
re-run only pays for strings it has never seen.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from .db import con, query, _lock
from .ember import BudgetExceeded, Ember
from .events import emit

BATCH = 120

PRODUCTS = ["Commercial PPO", "Commercial HMO", "Commercial EPO", "Commercial POS", "Commercial (unspecified)",
            "ACA Exchange", "Medicare Advantage", "Medicaid / CHIP managed care", "Medicare (traditional)",
            "Tricare / VA", "Workers comp / auto", "Self-funded employer / TPA", "Other / unclear"]

SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "i": {"type": "integer"},
            "payer": {"type": "string", "description": "Canonical parent insurer brand, e.g. UnitedHealthcare, Aetna, Cigna, Blue Cross Blue Shield of Texas, Humana, Superior HealthPlan, Baylor Scott & White Health Plan. Use the TPA/network name if no insurer."},
            "product": {"type": "string", "enum": PRODUCTS},
            "line_of_business": {"type": "string", "enum": ["commercial", "medicare", "medicaid", "exchange", "other"]},
        },
        "required": ["i", "payer", "product", "line_of_business"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}

SYSTEM = ("You normalize US health insurance payer and plan names taken from Texas hospital price files. "
          "Strings are noisy: internal IDs in brackets, abbreviations (BCBS, UHC, HMO, MA, MCD, EXCH, WC), "
          "hospital-specific contract names. For each item return the canonical parent insurer brand and the "
          "product type. Same insurer must always get exactly the same spelling. Use 'Other / unclear' "
          "rather than guessing a product.")


def pending() -> list[tuple[str, str]]:
    rows = query("""SELECT DISTINCT r.payer_raw, coalesce(r.plan_raw,'') AS plan_raw FROM rates r
                    LEFT JOIN payer_map m ON m.payer_raw = r.payer_raw AND m.plan_raw = coalesce(r.plan_raw,'')
                    WHERE r.payer_raw IS NOT NULL AND m.payer IS NULL ORDER BY 1, 2""")
    return [(r["payer_raw"], r["plan_raw"]) for r in rows]


def _do_batch(batch: list[tuple[str, str]], n: int) -> int:
    ember = Ember(purpose="payer_normalizer")
    lines = "\n".join(f'{i}\t{p}\t{pl}' for i, (p, pl) in enumerate(batch))
    out = ember.json([{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": f"index\tpayer_name\tplan_name\n{lines}"}],
                     SCHEMA, "payer_map", max_tokens=12000, effort="low", tag=f"batch{n}")
    rows = []
    for it in out.get("items", []):
        i = it.get("i")
        if isinstance(i, int) and 0 <= i < len(batch):
            p, pl = batch[i]
            rows.append((p, pl, it["payer"].strip(), it["product"], it["line_of_business"]))
    c = con()
    with _lock:
        c.executemany("INSERT OR REPLACE INTO payer_map VALUES (?,?,?,?,?)", rows)
    emit("payers_batch", n=n, mapped=len(rows), of=len(batch))
    return len(rows)


def normalize(workers: int = 6) -> dict:
    todo = pending()
    emit("payers_start", distinct=len(todo))
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    mapped = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(_do_batch, b, n) for n, b in enumerate(batches)]
        for f in futs:
            try:
                mapped += f.result()
            except BudgetExceeded as e:
                emit("payers_stop", detail=str(e))
            except Exception as e:  # noqa: BLE001
                emit("payers_error", detail=str(e)[:300])
    payers = query("SELECT count(DISTINCT payer) n FROM payer_map")[0]["n"]
    emit("payers_done", strings=len(todo), mapped=mapped, canonical_payers=payers)
    return {"strings": len(todo), "mapped": mapped, "canonical_payers": payers}


MERGE_SCHEMA = {
    "type": "object",
    "properties": {"merges": {"type": "array", "items": {
        "type": "object",
        "properties": {"from": {"type": "string"}, "to": {"type": "string"}},
        "required": ["from", "to"], "additionalProperties": False}}},
    "required": ["merges"], "additionalProperties": False,
}


def consolidate() -> dict:
    """Second pass: batches were normalized independently, so one insurer can end up with two
    spellings ("Oscar" / "Oscar Health"). Ember sees the whole canonical list once and merges."""
    names = query("SELECT payer, count(*) n FROM payer_map GROUP BY 1 ORDER BY 1")
    listing = "\n".join(f"{r['payer']}\t{r['n']}" for r in names)
    out = Ember(purpose="payer_consolidate").json(
        [{"role": "system", "content": (
            "Below are insurer names produced by separate normalization batches, with how many plan strings use each. "
            "Return merges only where two names are the SAME company and brand, spelled differently "
            "(e.g. 'Oscar' -> 'Oscar Health'). Keep distinct brands separate even under one parent "
            "(Aetna vs Aetna Better Health, Cigna vs Cigna Healthspring). Never merge into a vague name; "
            "'to' must be an existing name from the list, the clearest one.")},
         {"role": "user", "content": listing}],
        MERGE_SCHEMA, "payer_merges", max_tokens=6000, effort="low", tag="consolidate")
    valid = {r["payer"] for r in names}
    merges = [(m["to"], m["from"]) for m in out.get("merges", [])
              if m["from"] in valid and m["to"] in valid and m["from"] != m["to"]]
    c = con()
    if merges:
        with _lock:
            c.executemany("UPDATE payer_map SET payer = ? WHERE payer = ?", merges)
    after = query("SELECT count(DISTINCT payer) n FROM payer_map")[0]["n"]
    emit("payers_consolidated", before=len(names), after=after, merges=[f"{b} → {a}" for a, b in merges][:40])
    return {"before": len(names), "after": after, "merges": merges}

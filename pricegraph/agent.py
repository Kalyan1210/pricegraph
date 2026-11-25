"""The parser agent: Ember writes, tests and repairs a parser for one price-file format.

Flow per file:
  1. Try every parser already in the library on the sample (free). If one passes, reuse it.
  2. Otherwise Ember gets the sample and a `test_parser` tool. It writes code, the code
     runs in the sandbox, the validator reports back, Ember repairs until it passes.
  3. The passing parser joins the library under the file's format fingerprint.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import CACHE, PARSERS
from .ember import BudgetExceeded, Ember, cost_of
from .events import emit
from .sample import fetch_sample, sniff, trim_sample
from .sandbox import run_parser
from .validate import validate

MAX_TURNS = 7

SYSTEM = """You write Python parsers for US hospital price transparency files (CMS 45 CFR 180.50).
Files are huge (up to 2 GB), so the parser MUST stream: never read the whole file into memory.

Contract (exactly this):
    def parse(fp) -> Iterator[dict]
`fp` is a BINARY file object positioned at the start of the file. For CSV wrap it with
io.TextIOWrapper(fp, encoding="utf-8-sig", newline="", errors="replace") and use the csv module.
For JSON use the `ijson` library (installed) e.g. ijson.items(fp, "standard_charge_information.item").
Only the standard library plus ijson are available. No network, no file writes.

Yield one dict per (item/service, setting, payer, plan) with these keys (use None when absent):
  description: str
  codes: list of {"code": str, "type": str}  # every billing code on the row, CMS type names
                                             # (CPT, HCPCS, MS-DRG, APR-DRG, RC, CDM, NDC, ...); skip empty slots
  setting: str            # inpatient / outpatient / both
  billing_class: str
  modifiers: str
  gross: float            # standard_charge|gross
  cash: float             # standard_charge|discounted_cash
  min: float
  max: float
  payer_name: str
  plan_name: str
  negotiated_dollar: float
  negotiated_percentage: float
  negotiated_algorithm: str
  estimated_amount: float  # estimated_amount, or median_amount if that is what the file has
  methodology: str
Rules:
- Rows without a payer (only gross/cash) are still yielded once with payer_name None.
- Numbers: strip $ and commas, blank -> None, keep floats. Never yield strings for numeric keys.
- Header names vary in case, order, extra columns, BOM and line position (usually line 3 after
  two lines of hospital metadata). Locate the header row by content, map columns by lowercased name.
- "Wide" CSVs put payers in column names like standard_charge|<payer>|<plan>|negotiated_dollar:
  melt them into one dict per payer/plan that has any value.
- The sample you are tested on is cut off mid-file; that is expected.
Workflow: call test_parser with the full module source. Read the report, fix, call again.
When the report says ok, call finish. Keep code compact and robust."""

TOOLS = [
    {"type": "function", "function": {
        "name": "test_parser",
        "description": "Run the parser module on the file sample in a sandbox and validate its output.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "Full Python module source defining parse(fp)."}},
            "required": ["code"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "Declare the last tested parser final. Only valid after an ok report.",
        "parameters": {"type": "object", "properties": {
            "notes": {"type": "string", "description": "One line on what was tricky about this format."}},
            "required": ["notes"], "additionalProperties": False}}},
]


def library() -> list[Path]:
    return sorted(PARSERS.glob("*.py"))


def sample_path_for(src: dict) -> tuple[Path, bytes, dict]:
    raw = fetch_sample(src)
    info = sniff(raw)
    trimmed = trim_sample(raw, "json" if info["kind"] == "json" else "csv")
    p = CACHE / f"{src['id']}.trim"
    p.write_bytes(trimmed)
    return p, trimmed, info


def try_library(spath: Path, sample: bytes, kind: str) -> tuple[Path, dict] | None:
    for parser in library():
        meta = json.loads(parser.with_suffix(".json").read_text()) if parser.with_suffix(".json").exists() else {}
        if meta.get("kind") and (meta["kind"] == "json") != (kind == "json"):
            continue
        report = validate(run_parser(parser.read_text(), spath), sample, kind)
        if report["ok"]:
            return parser, report
    return None


def _render_sample(sample: bytes, kind: str) -> str:
    text = sample.decode("utf-8-sig", errors="replace")
    if kind == "json":
        return text[:14000]
    lines = text.splitlines()
    out, budget = [], 14000
    for line in lines[:40]:
        line = line if len(line) < 1500 else line[:1500] + " …[line truncated]"
        budget -= len(line)
        if budget < 0:
            break
        out.append(line)
    return "\n".join(out)


def build_parser(src: dict) -> dict:
    sid = src["id"]
    emit("file_start", id=sid, domain=src["domain"], locations=src["locations"][:3],
         n_locations=len(src["locations"]), flags=src.get("flags", []))
    try:
        spath, sample, info = sample_path_for(src)
    except Exception as e:  # noqa: BLE001
        emit("file_done", id=sid, status="fetch_failed", detail=str(e)[:200])
        return {"id": sid, "status": "fetch_failed", "detail": str(e)}
    kind = info["kind"]
    emit("file_profiled", id=sid, fmt=kind, fingerprint=info["fingerprint"], columns=len(info["header"]))

    hit = try_library(spath, sample, kind)
    if hit:
        parser, report = hit
        emit("file_done", id=sid, status="reused", parser=parser.stem, rows=report["stats"]["rows"], cost=0.0)
        return {"id": sid, "status": "reused", "parser": parser.stem, "fingerprint": info["fingerprint"], "kind": kind}

    ember = Ember(purpose="parser_agent")
    meta = {"hospital_file": src["url"].split("?")[0].rsplit("/", 1)[-1], "locations": src["locations"][:5],
            "detected_kind": kind, "header_columns": info["header"][:60]}
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"File info:\n{json.dumps(meta)}\n\nFirst part of the file:\n```\n{_render_sample(sample, kind)}\n```"},
    ]
    last_code, last_report, cost = None, None, 0.0
    for turn in range(MAX_TURNS):
        effort = "low" if turn < 2 else "medium"
        try:
            resp = ember.chat(messages, tools=TOOLS, max_tokens=8000, effort=effort, tag=f"{sid}#{turn}")
        except BudgetExceeded as e:
            emit("file_done", id=sid, status="budget_stop", detail=str(e))
            return {"id": sid, "status": "budget_stop"}
        cost += cost_of(resp.get("usage") or {})
        msg = resp["choices"][0]["message"]
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls") and v is not None})
        calls = msg.get("tool_calls") or []
        if not calls:
            messages.append({"role": "user", "content": "Use the test_parser tool with your full parser code."})
            continue
        for call in calls:
            fn = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if fn == "test_parser":
                last_code = args.get("code", "")
                result = run_parser(last_code, spath)
                last_report = validate(result, sample, kind)
                emit("parser_test", id=sid, turn=turn, ok=last_report["ok"], rows=last_report["stats"].get("rows"),
                     errors=last_report["errors"][:2])
                content = json.dumps(last_report, default=str)[:6000]
            elif fn == "finish":
                content = "accepted" if (last_report and last_report["ok"]) else \
                    "rejected: the last test_parser report was not ok. Fix and test again."
            else:
                content = f"unknown tool {fn}"
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
        # A passing test ends the loop right away: no extra paid turn just to say "finish".
        if last_report and last_report["ok"]:
            break
    if not (last_report and last_report["ok"]):
        emit("file_done", id=sid, status="failed", turns=turn + 1, cost=round(cost, 5),
             errors=(last_report or {}).get("errors", [])[:2])
        return {"id": sid, "status": "failed", "turns": turn + 1, "cost": cost}

    name = info["fingerprint"]
    (PARSERS / f"{name}.py").write_text(last_code, encoding="utf-8")
    (PARSERS / f"{name}.json").write_text(json.dumps({
        "fingerprint": name, "kind": kind, "written_for": sid, "turns": turn + 1,
        "cost_usd": round(cost, 6), "stats": last_report["stats"]}, indent=2, default=str))
    emit("file_done", id=sid, status="written", parser=name, turns=turn + 1, cost=round(cost, 5),
         rows=last_report["stats"]["rows"])
    return {"id": sid, "status": "written", "parser": name, "turns": turn + 1, "cost": cost,
            "fingerprint": name, "kind": kind}

"""Plain-English price questions, answered by Ember calling tools over DuckDB.

Ember never states a number it did not get from a tool result, and the UI renders the
last price table straight from SQL, so the chart is never model-generated.
"""
from __future__ import annotations

import json

from .codes import SHOPPABLE
from .db import query
from .ember import Ember, cost_of

TOOLS = [
    {"type": "function", "function": {
        "name": "find_service",
        "description": "Search the shoppable services list for a procedure, test or stay. Returns codes with labels.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "list_payers",
        "description": "Canonical insurers available in the data, with products.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "compare_prices",
        "description": "Per-hospital negotiated price range for one billing code, optionally for one insurer and/or line of business. Also returns cash prices.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"},
            "payer": {"type": "string", "description": "Canonical insurer name from list_payers, or empty for all"},
            "line_of_business": {"type": "string", "enum": ["", "commercial", "medicare", "medicaid", "exchange", "other"]},
        }, "required": ["code", "payer", "line_of_business"], "additionalProperties": False}}},
]

SYSTEM = ("You answer questions about hospital prices in Dallas-Fort Worth using only the tools. "
          "Steps: find_service to get the code, list_payers if an insurer is named, then compare_prices. "
          "Reply in 2-4 short sentences: cheapest and most expensive hospital with dollar amounts, "
          "the spread, and the cash price if relevant. Only use numbers from tool results. "
          "Say plainly that these are posted negotiated rates, not a quote.")


def find_service(q: str) -> list[dict]:
    words = [w for w in q.lower().replace(",", " ").split() if len(w) > 2]
    scored = []
    for code, (label, cat) in SHOPPABLE.items():
        hay = f"{label} {cat} {code}".lower()
        s = sum(1 for w in words if w in hay)
        if s:
            scored.append((s, code, label))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, code, label in scored[:8]:
        n = query("SELECT count(*) n, count(DISTINCT hospital) h FROM rates WHERE code = ?", [code])[0]
        out.append({"code": code, "label": label, "rows": n["n"], "hospitals": n["h"]})
    return out


def list_payers() -> list[dict]:
    return query("""SELECT payer, list(DISTINCT product) products, count(*) rows FROM priced
                     WHERE payer IS NOT NULL AND price IS NOT NULL GROUP BY 1 ORDER BY rows DESC LIMIT 40""")


def compare_prices(code: str, payer: str = "", line_of_business: str = "") -> dict:
    where, params = ["code = ?", "price IS NOT NULL"], [code]
    if payer:
        where.append("lower(payer) = lower(?)")
        params.append(payer)
    if line_of_business:
        where.append("line_of_business = ?")
        params.append(line_of_business)
    rows = query(f"""SELECT hospital, system, count(*) n_rates, count(DISTINCT payer) payers,
                       round(min(price)) low, round(median(price)) median, round(max(price)) high
                     FROM priced WHERE {' AND '.join(where)} GROUP BY 1, 2 ORDER BY median""", params)
    cash = query("""SELECT hospital, round(median(cash)) cash FROM rates WHERE code = ? AND cash > 0
                    GROUP BY 1""", [code])
    cmap = {c["hospital"]: c["cash"] for c in cash}
    for r in rows:
        r["cash"] = cmap.get(r["hospital"])
    label = SHOPPABLE.get(code, ("", ""))[0]
    return {"code": code, "service": label, "payer": payer or "all insurers",
            "line_of_business": line_of_business or "all", "hospitals": rows}


def ask(question: str) -> dict:
    ember = Ember(purpose="ask")
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    table, cost, steps = None, 0.0, []
    for turn in range(6):
        resp = ember.chat(msgs, tools=TOOLS, max_tokens=3000, effort="low", tag=f"ask#{turn}")
        cost += cost_of(resp.get("usage") or {})
        msg = resp["choices"][0]["message"]
        msgs.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls") and v is not None})
        calls = msg.get("tool_calls") or []
        if not calls:
            return {"answer": msg.get("content", ""), "table": table, "cost_usd": round(cost, 6), "steps": steps}
        for call in calls:
            fn = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if fn == "find_service":
                res = find_service(args.get("query", ""))
            elif fn == "list_payers":
                res = list_payers()
            elif fn == "compare_prices":
                res = compare_prices(args.get("code", ""), args.get("payer", ""), args.get("line_of_business", ""))
                table = res
            else:
                res = {"error": f"unknown tool {fn}"}
            steps.append({"tool": fn, "args": args})
            msgs.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(res, default=str)[:12000]})
    return {"answer": "I could not finish that question in 6 steps.", "table": table,
            "cost_usd": round(cost, 6), "steps": steps}

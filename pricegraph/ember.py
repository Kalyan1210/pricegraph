"""Muse Ember client with a hard budget cap and a full token trace.

Every call is appended to traces/calls.jsonl with the request, the response,
token counts and computed cost. That file is the hackathon's required
"prompt/token trace". The ledger refuses calls once BUDGET_CAP_USD is reached.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

import httpx

from .config import (API_BASE, API_KEY, BUDGET_CAP_USD, LONG_CONTEXT_MULT,
                     LONG_CONTEXT_THRESHOLD, MODEL, PRICE_CACHED_IN, PRICE_IN,
                     PRICE_OUT, TRACES)
from .events import emit

TRACE_FILE = TRACES / "calls.jsonl"
_lock = threading.Lock()


class BudgetExceeded(RuntimeError):
    pass


def cost_of(usage: dict) -> float:
    prompt = int(usage.get("prompt_tokens") or 0)
    out = int(usage.get("completion_tokens") or 0)
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    fresh = max(prompt - cached, 0)
    usd = (fresh * PRICE_IN + cached * PRICE_CACHED_IN + out * PRICE_OUT) / 1_000_000
    if prompt > LONG_CONTEXT_THRESHOLD:
        usd *= LONG_CONTEXT_MULT
    return usd


def spent() -> float:
    if not TRACE_FILE.exists():
        return 0.0
    total = 0.0
    with open(TRACE_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                total += json.loads(line).get("cost_usd", 0.0)
            except json.JSONDecodeError:
                continue
    return total


def totals() -> dict:
    t = {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0,
         "reasoning_tokens": 0, "cost_usd": 0.0}
    if not TRACE_FILE.exists():
        return t
    with open(TRACE_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            u = r.get("usage") or {}
            t["calls"] += 1
            t["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
            t["cached_tokens"] += int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
            t["completion_tokens"] += int(u.get("completion_tokens") or 0)
            t["reasoning_tokens"] += int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
            t["cost_usd"] += r.get("cost_usd", 0.0)
    return t


class Ember:
    def __init__(self, purpose: str, effort: str = "low", timeout: float = 180):
        if not API_KEY:
            raise RuntimeError("MODEL_API_KEY missing: put it in .env (see README)")
        self.purpose = purpose
        self.effort = effort
        self.http = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            timeout=timeout,
        )

    def chat(self, messages: list[dict], *, tools: list[dict] | None = None,
             response_format: dict | None = None, max_tokens: int = 4000,
             effort: str | None = None, tag: str = "") -> dict:
        with _lock:
            already = spent()
        if already >= BUDGET_CAP_USD:
            raise BudgetExceeded(f"budget cap ${BUDGET_CAP_USD:.2f} reached (spent ${already:.4f})")
        body: dict[str, Any] = {
            "model": MODEL,
            "messages": messages,
            "reasoning_effort": effort or self.effort,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
        if response_format:
            body["response_format"] = response_format
        call_id = uuid.uuid4().hex[:12]
        t0 = time.time()
        for attempt in range(4):
            r = self.http.post("/chat/completions", json=body)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt * 2)
                continue
            break
        latency = time.time() - t0
        if r.status_code >= 400:
            emit("ember_error", purpose=self.purpose, tag=tag, status=r.status_code, detail=r.text[:400])
            raise RuntimeError(f"Ember HTTP {r.status_code}: {r.text[:400]}")
        data = r.json()
        usage = data.get("usage") or {}
        cost = cost_of(usage)
        rec = {
            "id": call_id, "ts": t0, "purpose": self.purpose, "tag": tag, "model": MODEL,
            "effort": body["reasoning_effort"], "latency_s": round(latency, 2),
            "usage": usage, "cost_usd": round(cost, 7),
            "request": {k: v for k, v in body.items() if k != "model"},
            "response": data.get("choices"),
        }
        with _lock, open(TRACE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        emit("ember_call", purpose=self.purpose, tag=tag, cost=round(cost, 6),
             prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
             latency=round(latency, 2), total=round(already + cost, 5))
        return data

    def json(self, messages: list[dict], schema: dict, name: str, **kw) -> dict:
        data = self.chat(messages, response_format={
            "type": "json_schema", "json_schema": {"name": name, "schema": schema}}, **kw)
        content = data["choices"][0]["message"].get("content") or "{}"
        return json.loads(content)

"""Find every hospital price file in a metro through the CMS-required cms-hpt.txt index.

Since 2025 CMS requires each hospital site to publish /cms-hpt.txt listing its
machine-readable file (MRF) URLs. In practice the indexes are messy: wrong field
names, shared files across many locations, HTML error pages, bot walls. This module
is deterministic (no model calls); it records the mess as flags.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import unquote, urlparse

import httpx

from .config import ROOT, SOURCES
from .events import emit

UA = "PriceGraph/0.1 (hospital price transparency research)"
FILE_RE = re.compile(r"\.(csv|json|zip|txt|xlsx)(\?|$)", re.I)


def parse_index(text: str) -> list[dict]:
    blocks, cur = [], {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().lower()
        v = v.strip()
        if k in ("location-name", "source-page-url", "mrf-url", "contact-name", "contact-email"):
            if k == "location-name" and "location-name" in cur:
                blocks.append(cur)
                cur = {}
            cur[k] = v
    if cur:
        blocks.append(cur)
    return blocks


def source_id(url: str) -> str:
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    name = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:60]
    return f"{name}-{hashlib.sha1(url.encode()).hexdigest()[:6]}"


def discover(metro: str) -> list[dict]:
    metros = json.loads((ROOT / "metros.json").read_text())
    domains = metros[metro]["domains"]
    by_url: dict[str, dict] = {}
    client = httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=25)
    for domain in domains:
        url = f"https://{domain}/cms-hpt.txt"
        try:
            r = client.get(url)
        except httpx.HTTPError as e:
            emit("index", domain=domain, status="error", detail=str(e)[:200])
            continue
        body = r.text
        if r.status_code != 200 or body.lstrip().startswith("<"):
            # HTML instead of a text index: 404 page or a bot challenge. We skip, never bypass.
            reason = "bot-wall" if "challenge" in body.lower() or "just a moment" in body.lower() else f"http-{r.status_code}-or-html"
            emit("index", domain=domain, status="skipped", detail=reason)
            continue
        blocks = parse_index(body)
        found = 0
        for b in blocks:
            flags = []
            mrf = b.get("mrf-url", "")
            if not mrf and FILE_RE.search(b.get("source-page-url", "")):
                mrf = b["source-page-url"]
                flags.append("mrf-url-in-wrong-field")
            if not mrf:
                continue
            src = by_url.get(mrf)
            if src is None:
                src = by_url[mrf] = {
                    "id": source_id(mrf),
                    "domain": domain,
                    "url": mrf,
                    "locations": [],
                    "flags": flags,
                }
                found += 1
            loc = b.get("location-name")
            if loc and loc not in src["locations"]:
                src["locations"].append(loc)
        emit("index", domain=domain, status="ok", files=found, locations=len(blocks))
    sources = sorted(by_url.values(), key=lambda s: (s["domain"], s["id"]))
    for s in sources:
        if len(s["locations"]) > 1:
            s["flags"].append(f"shared-by-{len(s['locations'])}-locations")
    SOURCES.write_text(json.dumps(sources, indent=2))
    emit("discovered", metro=metro, files=len(sources), domains=len(domains))
    return sources

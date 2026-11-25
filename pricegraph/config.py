"""Paths, model settings and the hard budget cap."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
CACHE = DATA / "cache"          # raw downloads and samples (gitignored)
PARSERS = ROOT / "parsers"      # Ember-written parsers, one per format fingerprint
TRACES = ROOT / "traces"        # every Ember call, for the submission's token trace
DB_PATH = Path(os.environ.get("PRICEGRAPH_DB", DATA / "pricegraph.duckdb"))
EVENTS = DATA / "events.jsonl"  # pipeline event stream the dashboard tails
SOURCES = DATA / "sources.json"

for p in (DATA, CACHE, PARSERS, TRACES):
    p.mkdir(parents=True, exist_ok=True)

API_BASE = os.environ.get("MUSE_BASE_URL", "https://api.meta.ai/v1")
API_KEY = os.environ.get("MODEL_API_KEY", "")
MODEL = "muse-ember-1.0"

# Muse Ember Standard-mode prices, USD per 1M tokens (requests <= 200K input tokens).
PRICE_IN = 0.10
PRICE_CACHED_IN = 0.004
PRICE_OUT = 0.50
# Requests above 200K input tokens bill at 3x for the whole request.
LONG_CONTEXT_THRESHOLD = 200_000
LONG_CONTEXT_MULT = 3.0

# The hackathon gives $10 total. Keep a buffer: the ledger refuses calls past this.
BUDGET_CAP_USD = float(os.environ.get("PRICEGRAPH_BUDGET_CAP", "8.00"))

# How much of each file Ember gets to see when writing a parser.
SAMPLE_BYTES = 48_000
SAMPLE_MAX_LINES = 60

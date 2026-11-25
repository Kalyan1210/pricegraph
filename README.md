# PriceGraph

**Every hospital price file in Dallas-Fort Worth, parsed by Muse Ember agents for pennies.**

US hospitals must publish every negotiated price they have with every insurer (45 CFR 180.50).
They do, as 60 files of 0.3 to 2 GB each in Dallas-Fort Worth alone, in CSV and JSON, with
shifted columns, zip wrappers, URLs in the wrong index field, codes in the wrong slot and
thousands of spellings of the same insurer. The data is public and almost nobody can use it.

PriceGraph turns that mess into one table you can ask: *"How much is an MRI of the lower
back with Aetna?"* The answer: Aetna's posted rates run from $217 to $6,823 across 47 hospitals.

## Results (Dallas-Fort Worth, first 300 MB of each file)

| | |
|---|---|
| Price files found through `cms-hpt.txt` | 60 across 8 health systems |
| Loaded | 57 (Methodist Charlton's index link is a 404; JPS Health's host times out) |
| Data streamed through sandboxed parsers | 16.3 GB |
| Prices kept (78 shoppable services) | 240,058 |
| Raw payer/plan strings → insurers | 776 → 161 |
| Parsers Ember wrote | 2 (one CSV, one JSON); every other file reused them for $0 |
| **Total Ember spend, including all testing** | **$0.035 over 20 calls** |

## Why this only works with Muse Ember

| Job | What Ember does | Why the unit cost matters |
|---|---|---|
| **Parser agent** | Reads a 48 KB sample of each file, writes a streaming parser, runs it in a sandbox, reads the validator's report, repairs, repeats | Model cost scales with the number of *formats*, not file size. 2 GB file, ~$0.002 of tokens |
| **Parser library** | A passing parser is saved and tried first on every new file | Most files reuse an existing parser for **$0** |
| **Payer normalizer** | Batches thousands of raw payer/plan strings into canonical insurer + product with a strict JSON schema | Thousands of strings for cents; cached, so re-runs cost nothing |
| **Ask agent** | Tool calls (`find_service`, `list_payers`, `compare_prices`) over DuckDB | A cent or less per question; numbers come only from SQL |

Every Ember call is written to [`traces/calls.jsonl`](traces/calls.jsonl) with its prompt,
response, token counts and computed cost. That file is the submission's token trace.
`python -m pricegraph spend` prints the totals.

## How it works

```
cms-hpt.txt index (per hospital site)        deterministic, no model
        │ discover: 60 files / 8 systems / ~75 locations, mess recorded as flags
        ▼
sample: 48 KB head + a chunk from ~8 MB in   deterministic
        │ fingerprint the format
        ▼
parser library ── a parser passes? ─ yes ──► reuse ($0)
        │ no
        ▼
Ember parser agent  ⇄  sandbox (no network, no writes, CPU limit)  ⇄  validator
        │ passing parser joins the library
        ▼
full file streamed (HTTP or zip) ─► sandboxed parser via stdin ─► shoppable services only ─► DuckDB
        ▼
Ember payer normalizer ─► payer_map            Ember ask agent ─► tools over DuckDB
        ▼
dashboard: live tile wall, cost meter, agent log, price comparison chart
```

Safety: Ember-written code never runs in the main process. On macOS it runs under
`sandbox-exec` with network and file writes denied, plus CPU and wall-clock limits. During a
full load the parent process does the downloading and pipes bytes into the sandboxed parser.

Budget: `pricegraph/ember.py` reads the trace before every call and refuses once spend
reaches `PRICEGRAPH_BUDGET_CAP` (default $8.00 of the $10). Every call sets
`reasoning_effort` explicitly (Ember defaults to `max`).

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Put your Meta Model API key in `.env` (gitignored):

```bash
read -s "k?Paste Muse API key: " && printf 'MODEL_API_KEY=%s\n' "$k" > .env && chmod 600 .env && unset k
```

```bash
.venv/bin/python -m pricegraph serve          # dashboard at http://localhost:8765, press Run pipeline
.venv/bin/python -m pricegraph run --max-mb 300   # or headless; --max-mb caps bytes streamed per file
.venv/bin/python -m pricegraph ask "MRI lower back with Aetna"
.venv/bin/python -m pricegraph spend
```

## What the numbers mean

Prices are posted negotiated rates and hospital estimates from the files, not quotes.
`price` uses the negotiated dollar amount if present, else the negotiated percentage times the
gross charge, else the file's estimated amount; the dashboard shows which basis each came from.
Only the shoppable services in [`pricegraph/codes.py`](pricegraph/codes.py) are loaded.
MS-DRG matches require an MS-DRG code type, because `0470` is also the audiology revenue code.

## Layout

```
pricegraph/  discover, sample, agent, sandbox, validate, load, payers, ask, ember, db, server
parsers/     Ember-written parsers, one per format fingerprint, with metadata
traces/      calls.jsonl, the full Ember token trace
tests/       hand-written reference parser used to test the harness (never in the library)
web/         the dashboard (one HTML file)
```

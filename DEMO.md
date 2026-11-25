# 3-minute demo script

**0:00 to 0:20 · The problem.** Open on one raw price file in a text editor: 2 GB, metadata
rows, `code|1` through `code|5`, `UNITED HEALTHCARE OF TX [1079]`. "Every hospital in America
has to publish this. Here is Dallas: 60 files, 8 health systems, around 75 hospitals. Nobody
can use it."

**0:20 to 1:20 · Ember builds the pipeline live.** Dashboard, press **Run pipeline**.
- The tile wall lights up as samples are profiled.
- The agent log shows Ember's tries: "try 1: ✗ 12/57 rows have a numeric field that is not a
  number" then "try 2: ✓ passed". The sandbox and validator are doing the checking, not vibes.
- Everything else flips to **reused · $0**. Point at the cost meter.

**1:20 to 1:50 · Scale.** GB streamed and prices loaded counters climbing. Then the payer
normalizer: "N payer names → M insurers" for under a cent.

**1:50 to 2:35 · The answer.** Ask: *"How much is an MRI of the lower back with Aetna?"* Show
the tool calls under the answer, then the chart: the same scan, posted rates differing by
several times depending on the hospital, with cash prices. Hover one row.

**2:35 to 3:00 · The receipt.** `python -m pricegraph spend`: total calls, tokens, dollars.
"Every call is in traces/calls.jsonl. The whole metro cost $X. That is the point of Ember:
work nobody would pay a frontier model to do, done for the price of a stamp."

Record after a full run so the counters are real. Keep `--max-mb` off for the final run if
time allows; otherwise say on screen how many MB per file were streamed.

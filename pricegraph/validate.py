"""Check a parser's output on the sample. The report is what Ember sees to repair its code."""
from __future__ import annotations

import csv
import io
import random

CODE_TYPES = {"CPT", "HCPCS", "MS-DRG", "APR-DRG", "R-DRG", "S-DRG", "APS-DRG", "AP-DRG", "APR-DRG",
              "TRIS-DRG", "RC", "CDM", "NDC", "ICD", "LOCAL", "EAPG", "HIPPS", "CMG", "APC", "CDT", "CCS"}
NUM_FIELDS = ["gross", "cash", "negotiated_dollar", "negotiated_percentage", "estimated_amount", "min", "max"]
STR_FIELDS = ["description", "setting", "billing_class", "payer_name", "plan_name",
              "negotiated_algorithm", "methodology", "modifiers"]
REQUIRED = ["description", "codes"]


def _data_lines(sample: bytes, header_idx: int | None) -> list[list[str]]:
    text = sample.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    if header_idx is None:
        for i, r in enumerate(rows[:8]):
            if any(c.strip().lower() == "description" for c in r):
                header_idx = i
                break
    if header_idx is None:
        return []
    data = rows[header_idx + 1:]
    return data[:-1] if len(data) > 1 else data   # last line may be cut mid-record


def validate(result: dict, sample: bytes, kind: str) -> dict:
    errors: list[str] = []
    rows = result.get("rows") or []
    if result.get("error"):
        e = result["error"]
        errors.append(f"parser raised {e.get('type')}: {e.get('message')}\n{e.get('traceback', '')}")
    stats = {"rows": len(rows)}
    if not rows:
        errors.append("parser produced 0 rows from the sample")
        return {"ok": False, "errors": errors, "stats": stats, "examples": []}

    bad_type = missing = str_nums = no_price = bad_codes = neg = pct_range = payer_missing = 0
    code_types_seen: dict[str, int] = {}
    for r in rows:
        if "__not_a_dict__" in r:
            bad_type += 1
            continue
        modifier_row = bool(r.get("modifiers")) and not r.get("codes")   # CMS allows code-less modifier rows
        if not r.get("description") or (not r.get("codes") and not modifier_row):
            missing += 1
        codes = r.get("codes") or []
        if not isinstance(codes, list) or not all(isinstance(c, dict) and c.get("code") and c.get("type") for c in codes):
            bad_codes += 1
        else:
            for c in codes:
                t = str(c["type"]).upper()
                code_types_seen[t] = code_types_seen.get(t, 0) + 1
        for f in NUM_FIELDS:
            v = r.get(f)
            if v is not None and not isinstance(v, (int, float)):
                str_nums += 1
                break
            if isinstance(v, (int, float)) and v < 0:
                neg += 1
        p = r.get("negotiated_percentage")
        if isinstance(p, (int, float)) and not (0 <= p <= 1000):
            pct_range += 1
        if not any(isinstance(r.get(f), (int, float)) for f in NUM_FIELDS) and not r.get("negotiated_algorithm"):
            no_price += 1
        if any(isinstance(r.get(f), (int, float)) for f in ("negotiated_dollar", "negotiated_percentage")) and not r.get("payer_name"):
            payer_missing += 1

    n = len(rows)
    if bad_type: errors.append(f"{bad_type} rows are not dicts")
    if missing: errors.append(f"{missing}/{n} rows missing description, or codes on a non-modifier row")
    if bad_codes: errors.append(f"{bad_codes}/{n} rows have malformed codes (need list of {{'code','type'}})")
    if str_nums: errors.append(f"{str_nums}/{n} rows have a numeric field that is not a number (convert strings like '1,234.50' to float, blanks to None)")
    if neg: errors.append(f"{neg} negative price values")
    if pct_range: errors.append(f"{pct_range} negotiated_percentage values outside 0-1000")
    if no_price > n * 0.5: errors.append(f"{no_price}/{n} rows carry no price at all")
    if payer_missing: errors.append(f"{payer_missing}/{n} rows have a negotiated price but no payer_name")
    unknown = {t: c for t, c in code_types_seen.items() if t not in CODE_TYPES}
    if unknown: errors.append(f"unknown code types {unknown}; use the CMS names (CPT, HCPCS, MS-DRG, RC, CDM, NDC, ...)")

    # Coverage: every data line in a tall CSV sample should appear in the output.
    if kind in ("csv-tall", "csv-wide", "csv-other"):
        lines = _data_lines(sample, None)
        stats["sample_data_lines"] = len(lines)
        descs = {str(r.get("description", "")).strip().lower() for r in rows}
        if lines:
            probe = random.Random(7).sample(lines, min(8, len(lines)))
            hdr_desc_idx = None
            text_rows = list(csv.reader(io.StringIO(sample.decode("utf-8-sig", errors="replace"))))
            for r in text_rows[:8]:
                low = [c.strip().lower() for c in r]
                if "description" in low:
                    hdr_desc_idx = low.index("description")
                    break
            if hdr_desc_idx is not None:
                miss = [l[hdr_desc_idx] for l in probe if len(l) > hdr_desc_idx and l[hdr_desc_idx].strip().lower() not in descs]
                if miss:
                    errors.append(f"{len(miss)}/{len(probe)} probed sample rows are missing from the output, e.g. {miss[:2]}")
        if kind == "csv-tall" and lines and n < len(lines) * 0.9:
            errors.append(f"tall CSV: {len(lines)} data lines but only {n} rows emitted")

    stats["code_types"] = code_types_seen
    stats["with_negotiated"] = sum(1 for r in rows if isinstance(r.get("negotiated_dollar"), (int, float)))
    stats["payers"] = len({r.get("payer_name") for r in rows if r.get("payer_name")})
    if result.get("tail_error"):
        stats["tail_error"] = result["tail_error"].get("type")
    examples = rows[:2] + ([rows[n // 2]] if n > 4 else [])
    return {"ok": not errors, "errors": errors[:10], "stats": stats, "examples": examples}

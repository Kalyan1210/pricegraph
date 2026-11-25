"""Hand-written reference parser for CMS v3 tall CSV. Test fixture only: never placed in parsers/."""
import csv, io

NUM = {"standard_charge|gross": "gross", "standard_charge|discounted_cash": "cash",
       "standard_charge|min": "min", "standard_charge|max": "max",
       "standard_charge|negotiated_dollar": "negotiated_dollar",
       "standard_charge|negotiated_percentage": "negotiated_percentage", "estimated_amount": "estimated_amount",
       "median_amount": "estimated_amount"}
STR = {"description": "description", "setting": "setting", "billing_class": "billing_class", "modifiers": "modifiers",
       "payer_name": "payer_name", "plan_name": "plan_name",
       "standard_charge|negotiated_algorithm": "negotiated_algorithm", "standard_charge|methodology": "methodology"}

def num(v):
    v = (v or "").replace("$", "").replace(",", "").strip()
    try: return float(v) if v else None
    except ValueError: return None

def parse(fp):
    rd = csv.reader(io.TextIOWrapper(fp, encoding="utf-8-sig", newline="", errors="replace"))
    hdr = None
    for row in rd:
        low = [c.strip().lower() for c in row]
        if hdr is None:
            if "description" in low: hdr = low
            continue
        if not any(row): continue
        rec = dict(zip(hdr, row))
        codes = []
        i = 1
        while f"code|{i}" in rec:
            c, t = rec.get(f"code|{i}", "").strip(), rec.get(f"code|{i}|type", "").strip().upper()
            if c and t: codes.append({"code": c, "type": t})
            i += 1
        out = {v: None for v in list(NUM.values()) + list(STR.values())}
        for k, v in NUM.items():
            if k in rec and out[v] is None: out[v] = num(rec[k])
        for k, v in STR.items():
            if k in rec: out[v] = rec[k].strip() or None
        out["codes"] = codes
        yield out

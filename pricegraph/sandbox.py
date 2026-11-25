"""Run Ember-written parser code in a locked-down subprocess.

macOS: sandbox-exec denies all network access and file writes outside a temp dir.
Every platform: CPU-time limit, wall-clock timeout, row cap, separate process.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HARNESS = r'''
import importlib.util, json, sys, traceback, resource, math
resource.setrlimit(resource.RLIMIT_CPU, (40, 45))
parser_path, sample_path, limit, truncated = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4] == "1"
out = {"rows": [], "error": None, "tail_error": None}
try:
    spec = importlib.util.spec_from_file_location("ember_parser", parser_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with open(sample_path, "rb") as fp:
        for row in mod.parse(fp):
            out["rows"].append(row)
            if len(out["rows"]) >= limit:
                break
except Exception as e:
    tb = traceback.format_exc().splitlines()
    err = {"type": type(e).__name__, "message": str(e)[:500], "traceback": "\n".join(tb[-12:])}
    if truncated and out["rows"]:
        out["tail_error"] = err   # the sample is cut mid-file; ending with an error there is fine
    else:
        out["error"] = err
def clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v
safe = []
for r in out["rows"]:
    if isinstance(r, dict):
        safe.append({str(k): (clean(v) if not isinstance(v, (list, dict)) else v) for k, v in r.items()})
    else:
        safe.append({"__not_a_dict__": repr(r)[:200]})
out["rows"] = safe
sys.stdout.write(json.dumps(out, default=str))
'''

PROFILE = """(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* (subpath "{tmp}") (literal "/dev/null"))
"""


def run_parser(parser_code: str, sample_path: Path, limit: int = 5000,
               truncated: bool = True, timeout: int = 60) -> dict:
    with tempfile.TemporaryDirectory(prefix="pg-sbx-") as tmp:
        tmp = os.path.realpath(tmp)
        ppath = Path(tmp) / "parser.py"
        ppath.write_text(parser_code, encoding="utf-8")
        hpath = Path(tmp) / "harness.py"
        hpath.write_text(HARNESS, encoding="utf-8")
        spath = Path(tmp) / "sample.bin"
        shutil.copyfile(sample_path, spath)
        cmd = [sys.executable, "-I", str(hpath), str(ppath), str(spath), str(limit), "1" if truncated else "0"]
        if platform.system() == "Darwin" and shutil.which("sandbox-exec"):
            prof = Path(tmp) / "sb.sb"
            prof.write_text(PROFILE.format(tmp=tmp))
            cmd = ["sandbox-exec", "-f", str(prof)] + cmd
        env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "HOME": tmp, "TMPDIR": tmp}
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=tmp, env=env)
        except subprocess.TimeoutExpired:
            return {"rows": [], "error": {"type": "Timeout", "message": f"parser exceeded {timeout}s on the sample"}}
        if p.returncode != 0 or not p.stdout.strip():
            return {"rows": [], "error": {"type": "Crash", "message": (p.stderr or "no output")[-1500:]}}
        try:
            return json.loads(p.stdout)
        except json.JSONDecodeError:
            return {"rows": [], "error": {"type": "BadOutput", "message": p.stdout[:500]}}


STREAM_HARNESS = r'''
import importlib.util, json, sys, math
parser_path, codes_path = sys.argv[1], sys.argv[2]
keep = json.load(open(codes_path))   # code -> "DRG" or "CPT" family
spec = importlib.util.spec_from_file_location("ember_parser", parser_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
out = sys.stdout
seen = kept = 0
def clean(v):
    return None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v
for row in mod.parse(sys.stdin.buffer):
    seen += 1
    codes = row.get("codes") or []
    hit = []
    for c in codes:
        if not isinstance(c, dict):
            continue
        k = str(c.get("code", "")).strip().upper()
        fam = keep.get(k)
        t = str(c.get("type", "")).upper()
        if (fam == "DRG" and t in ("MS-DRG", "DRG")) or (fam == "CPT" and t in ("CPT", "HCPCS")):  # APR-DRG numbers mean different things
            hit.append(c)
    if hit:
        row["match"] = str(hit[0]["code"]).strip().upper()
        row["match_type"] = str(hit[0].get("type", "")).upper()
        out.write(json.dumps({k: clean(v) for k, v in row.items()}, default=str) + "\n")
        kept += 1
    if seen % 200000 == 0:
        sys.stderr.write(json.dumps({"progress": seen, "kept": kept}) + "\n"); sys.stderr.flush()
sys.stderr.write(json.dumps({"done": seen, "kept": kept}) + "\n")
'''


def stream_parser(parser_code: str, fp, codes: dict[str, str], on_row, on_progress=None,
                  max_bytes: int | None = None) -> dict:
    """Pipe a (possibly multi-GB) byte stream into the sandboxed parser; get back only rows
    whose billing codes are in `codes`. Network stays blocked for the parser."""
    import threading
    with tempfile.TemporaryDirectory(prefix="pg-load-") as tmp:
        tmp = os.path.realpath(tmp)
        ppath = Path(tmp) / "parser.py"; ppath.write_text(parser_code, encoding="utf-8")
        hpath = Path(tmp) / "harness.py"; hpath.write_text(STREAM_HARNESS, encoding="utf-8")
        cpath = Path(tmp) / "codes.json"; cpath.write_text(json.dumps(codes))
        cmd = [sys.executable, "-I", str(hpath), str(ppath), str(cpath)]
        if platform.system() == "Darwin" and shutil.which("sandbox-exec"):
            prof = Path(tmp) / "sb.sb"; prof.write_text(PROFILE.format(tmp=tmp))
            cmd = ["sandbox-exec", "-f", str(prof)] + cmd
        env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "HOME": tmp, "TMPDIR": tmp}
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             cwd=tmp, env=env, bufsize=1 << 20)
        stats = {"bytes": 0, "rows_seen": 0, "rows_kept": 0, "truncated": False, "error": None}

        def feed():
            try:
                while True:
                    chunk = fp.read(1 << 20)
                    if not chunk:
                        break
                    stats["bytes"] += len(chunk)
                    p.stdin.write(chunk)
                    if max_bytes and stats["bytes"] >= max_bytes:
                        stats["truncated"] = True
                        break
            except (BrokenPipeError, ValueError):
                pass
            except Exception as e:  # noqa: BLE001  network errors mid-download
                stats["error"] = f"download: {e}"[:300]
            finally:
                try:
                    p.stdin.close()
                except Exception:  # noqa: BLE001
                    pass

        def errs():
            for line in p.stderr:
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    stats.setdefault("stderr", []).append(line.decode(errors="replace")[:300])
                    continue
                stats["rows_seen"] = msg.get("progress") or msg.get("done") or stats["rows_seen"]
                if on_progress:
                    on_progress(stats)

        tf, te = threading.Thread(target=feed, daemon=True), threading.Thread(target=errs, daemon=True)
        tf.start(); te.start()
        for line in p.stdout:
            try:
                on_row(json.loads(line))
                stats["rows_kept"] += 1
            except json.JSONDecodeError:
                continue
        p.wait(); tf.join(5); te.join(5)
        if p.returncode not in (0, None) and not stats["truncated"]:
            stats["error"] = stats["error"] or "parser crashed: " + " | ".join(stats.get("stderr", [])[-3:])
        return stats

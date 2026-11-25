"""CLI: python -m pricegraph <command>"""
from __future__ import annotations

import argparse
import json


def main() -> None:
    ap = argparse.ArgumentParser(prog="pricegraph")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover", help="find every price file in the metro (no model calls)")
    r = sub.add_parser("run", help="full pipeline: parsers -> loads -> payer map")
    r.add_argument("--limit", type=int)
    r.add_argument("--max-mb", type=float, help="cap MB streamed per file")
    r.add_argument("--no-load", action="store_true")
    sub.add_parser("payers", help="normalize payer names for rows already loaded")
    a = sub.add_parser("ask", help="ask a price question")
    a.add_argument("question")
    sub.add_parser("spend", help="Ember token and dollar totals from the trace")
    s = sub.add_parser("serve", help="dashboard on http://localhost:8765")
    s.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    if args.cmd == "discover":
        from .discover import discover
        print(f"{len(discover('dfw'))} files")
    elif args.cmd == "run":
        from .pipeline import run
        print(json.dumps(run(limit=args.limit, max_mb=args.max_mb, load=not args.no_load), indent=2))
    elif args.cmd == "payers":
        from .payers import consolidate, normalize
        print(normalize()); print(consolidate())
    elif args.cmd == "ask":
        from .ask import ask
        out = ask(args.question)
        print(out["answer"]); print(f"\n(cost ${out['cost_usd']:.5f})")
    elif args.cmd == "spend":
        from .ember import totals
        print(json.dumps(totals(), indent=2))
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("pricegraph.server:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

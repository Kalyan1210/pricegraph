"""Append-only event stream. The pipeline writes, the dashboard tails."""
from __future__ import annotations

import json
import threading
import time

from .config import EVENTS

_lock = threading.Lock()


def emit(kind: str, **data) -> None:
    rec = {"ts": round(time.time(), 3), "kind": kind, **data}
    line = json.dumps(rec, default=str)
    with _lock, open(EVENTS, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def reset() -> None:
    with _lock:
        EVENTS.write_text("", encoding="utf-8")

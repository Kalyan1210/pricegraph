"""Get a small sample of each price file and fingerprint its format.

Files run 0.3-2 GB. Ember only ever sees the first ~48 KB. The parser it writes
then streams the full file locally, so model cost scales with the number of
formats, not with file size.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from pathlib import Path
from typing import BinaryIO, Iterator

import httpx

from .config import CACHE, SAMPLE_BYTES, SAMPLE_MAX_LINES
from .discover import UA

MAX_ZIP_BYTES = 400 * 1024 * 1024


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=60)


def is_zip(url: str) -> bool:
    return ".zip" in url.lower().split("?")[0]


def download_zip(src: dict) -> Path:
    path = CACHE / f"{src['id']}.zip"
    if path.exists() and zipfile.is_zipfile(path):
        return path
    tmp = path.with_suffix(".part")
    with _client() as c, c.stream("GET", src["url"]) as r:
        r.raise_for_status()
        size = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                size += len(chunk)
                if size > MAX_ZIP_BYTES:
                    raise RuntimeError("zip larger than cap")
                f.write(chunk)
    tmp.rename(path)
    return path


def _zip_member(zpath: Path) -> tuple[zipfile.ZipFile, str]:
    zf = zipfile.ZipFile(zpath)
    names = [n for n in zf.namelist() if not n.endswith("/") and not n.startswith("__MACOSX")]
    names.sort(key=lambda n: zf.getinfo(n).file_size, reverse=True)
    return zf, names[0]


MID_OFFSET = 8 * 1024 * 1024     # second probe ~8 MB in, so samples aren't all one row type
MID_BYTES = 24_000


def _range(url: str, start: int, n: int) -> bytes:
    with _client() as c:
        data = b""
        with c.stream("GET", url, headers={"Range": f"bytes={start}-{start + n - 1}"}) as r:
            if start and r.status_code == 416:
                return b""          # file is smaller than the mid-probe offset: head sample is enough
            r.raise_for_status()
            if start and r.status_code != 206:
                return b""          # server ignores Range: skip the mid probe rather than download GBs
            for chunk in r.iter_bytes(1 << 15):
                data += chunk
                if len(data) >= n:
                    break
    return data[:n]


def fetch_sample(src: dict) -> bytes:
    """Head of the file, plus (for CSV) whole lines from ~8 MB in, spliced after the head."""
    path = CACHE / f"{src['id']}.sample"
    if path.exists():
        return path.read_bytes()
    if is_zip(src["url"]):
        zf, member = _zip_member(download_zip(src))
        with zf.open(member) as fp:
            head = fp.read(SAMPLE_BYTES)
            fp.read(MID_OFFSET)
            mid = fp.read(MID_BYTES)
        src.setdefault("flags", []).append(f"zipped:{member}")
    else:
        head = _range(src["url"], 0, SAMPLE_BYTES)
        mid = _range(src["url"], MID_OFFSET, MID_BYTES)
    data = head
    if mid and not head.lstrip(b"\xef\xbb\xbf \r\n\t").startswith((b"{", b"[")):
        lines = mid.split(b"\n")[1:-1]                    # drop partial first/last lines
        head_lines = head[: head.rfind(b"\n") + 1].split(b"\n")
        keep = head_lines[: SAMPLE_MAX_LINES // 2]        # metadata + header + first rows
        data = b"\n".join(keep).rstrip(b"\n") + b"\n" + b"\n".join(lines[: SAMPLE_MAX_LINES // 2]) + b"\n"
    path.write_bytes(data)
    return data


def trim_sample(data: bytes, kind: str) -> bytes:
    """CSV: cut to whole lines and at most SAMPLE_MAX_LINES. JSON: keep the prefix."""
    if kind == "json":
        return data
    cut = data.rfind(b"\n")
    if cut > 0:
        data = data[: cut + 1]
    lines = data.splitlines(keepends=True)
    return b"".join(lines[:SAMPLE_MAX_LINES])


def sniff(data: bytes) -> dict:
    text = data.decode("utf-8-sig", errors="replace").lstrip("﻿ \r\n\t")
    if text.startswith("{") or text.startswith("["):
        keys = sorted(set(re.findall(r'"([A-Za-z_|0-9]+)"\s*:', text)))
        fp = hashlib.sha1("|".join(keys).encode()).hexdigest()[:10]
        return {"kind": "json", "fingerprint": f"json-{fp}", "header": keys[:80]}
    rows = list(csv.reader(io.StringIO(text[:20000])))
    header = []
    for row in rows[:6]:
        low = [c.strip().lower() for c in row]
        if "description" in low:
            header = low
            break
    if not header and rows:
        header = [c.strip().lower() for c in rows[0]]
    wide = sum(1 for c in header if c.startswith("standard_charge|") and c.count("|") >= 3)
    sub = "csv-wide" if wide else ("csv-tall" if "payer_name" in header else "csv-other")
    fp = hashlib.sha1(",".join(header).encode()).hexdigest()[:10]
    return {"kind": sub, "fingerprint": f"{sub}-{fp}", "header": header, "wide_payer_columns": wide}


class _HttpStream(io.RawIOBase):
    """File-like wrapper over a streaming HTTP body so parsers can stream 2 GB files.

    Hospital file hosts drop long downloads (read timeouts, early closes). On any network error
    it reconnects with a Range request from the exact byte it reached, so the sandboxed parser
    downstream never notices. Asks for identity encoding so byte offsets are real file offsets."""

    MAX_RESUMES = 8

    def __init__(self, url: str):
        self._url = url
        self._pos = 0
        self._resumes = 0
        self._client = _client()
        self._cm = None
        self._buf = b""
        self._total = None
        self._open()

    def _open(self) -> None:
        headers = {"Accept-Encoding": "identity"}
        if self._pos:
            # Closed range: Azure Blob ignores open-ended "bytes=N-" but honors "bytes=N-M".
            end = (self._total - 1) if self._total else self._pos + 50_000_000_000
            headers["Range"] = f"bytes={self._pos}-{end}"
        self._cm = self._client.stream("GET", self._url, headers=headers)
        resp = self._cm.__enter__()
        resp.raise_for_status()
        if not self._pos and resp.headers.get("content-length", "").isdigit():
            self._total = int(resp.headers["content-length"])
        if self._pos and resp.status_code != 206:
            raise RuntimeError("server ignored Range on resume; cannot continue mid-file")
        self._it = resp.iter_raw(1 << 20)

    def _reopen(self) -> None:
        try:
            self._cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        import time as _t
        _t.sleep(min(2 ** self._resumes, 30))
        self._open()

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        while not self._buf:
            try:
                self._buf = next(self._it)
                self._pos += len(self._buf)
            except StopIteration:
                return 0
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                self._resumes += 1
                if self._resumes > self.MAX_RESUMES:
                    raise
                try:
                    from .events import emit
                    emit("download_resume", url=self._url.split("?")[0][-60:], at_mb=round(self._pos / 1e6, 1),
                         attempt=self._resumes, reason=type(e).__name__)
                except Exception:  # noqa: BLE001
                    pass
                self._reopen()
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n

    def close(self) -> None:
        try:
            if self._cm is not None:
                self._cm.__exit__(None, None, None)
        finally:
            self._client.close()
            super().close()


def open_full(src: dict) -> BinaryIO:
    """A binary stream over the whole file: zip member, or the HTTP body."""
    if is_zip(src["url"]):
        zf, member = _zip_member(download_zip(src))
        return zf.open(member)
    return io.BufferedReader(_HttpStream(src["url"]), buffer_size=1 << 20)

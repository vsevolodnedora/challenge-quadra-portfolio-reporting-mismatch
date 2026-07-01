"""Immutable raw store: every HTTP response body written verbatim, plus a provenance manifest.

Layout (gitignored):
    <raw_root>/<endpoint>/<partition>/page-NNNN.json   # exact response bytes
    <raw_root>/<endpoint>/<partition>/_done.json       # resume marker (excluded from load glob)
    <raw_root>/_manifest.jsonl                          # one line per response (no secrets)
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def partition_dir(raw_root: str, endpoint: str, partition: str) -> pathlib.Path:
    return pathlib.Path(raw_root) / endpoint / partition


def page_path(raw_root: str, endpoint: str, partition: str, page: int) -> pathlib.Path:
    return partition_dir(raw_root, endpoint, partition) / f"page-{page:04d}.json"


def write_page(path: pathlib.Path, text: str) -> pathlib.Path:
    """Write the exact response text. Write-once: refuse to clobber an existing page.

    Returns the path so callers can persist verbatim BEFORE parsing and then reference the
    saved file (e.g. in an error message) if the body turns out to be malformed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing raw page {path}")
    path.write_text(text)
    return path


def append_manifest(raw_root: str, record: dict) -> None:
    mf = pathlib.Path(raw_root) / "_manifest.jsonl"
    mf.parent.mkdir(parents=True, exist_ok=True)
    with mf.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def manifest_record(*, endpoint, partition, page, path, http_status, rows, total_records,
                    has_next_page, response_generated_at, ratelimit_remaining, params) -> dict:
    # params never contain the API key (it is a header) — safe to record verbatim.
    return {
        "endpoint": endpoint,
        "partition": partition,
        "page": page,
        "path": str(path),
        "http_status": http_status,
        "rows": rows,
        "total_records": total_records,
        "has_next_page": has_next_page,
        "response_generated_at": response_generated_at,
        "fetched_at": _now_iso(),
        "ratelimit_remaining": ratelimit_remaining,
        "params": params,
    }


def partition_complete(raw_root: str, endpoint: str, partition: str) -> bool:
    return (partition_dir(raw_root, endpoint, partition) / "_done.json").exists()


def mark_done(raw_root: str, endpoint: str, partition: str, summary: dict) -> None:
    d = partition_dir(raw_root, endpoint, partition)
    d.mkdir(parents=True, exist_ok=True)
    (d / "_done.json").write_text(json.dumps({**summary, "completed_at": _now_iso()}))


def read_data_rows(path: pathlib.Path) -> list:
    """Load the `data` array from a persisted page (used to enumerate asset_ids for Phase 1)."""
    return json.loads(path.read_text()).get("data", []) or []

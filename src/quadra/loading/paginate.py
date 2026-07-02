"""Pagination + the one HARD completeness gate — and persist-BEFORE-parse.

`iter_pages`/`fetch_single` write each 200 response body to disk verbatim the instant it
arrives, *before* attempting to parse it. So a corrupt or schema-deviating page is preserved
for later inspection even when it cannot be decoded — the raw store is the true system of
record, never a post-parse afterthought.

Two failure modes are then distinguished, and both are loud (never silent truncation):
  - RawParseError    : a persisted body is not valid JSON — we cannot count its rows.
  - CompletenessError: bodies parsed, but summed page rows != meta.total_records.
Neither is a *content* judgement: enum/null/row-count expectations remain recorded findings
in the storage layer. These two guard only against losing or miscounting data.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Callable, Iterator

import requests

from .client import QuadraClient

# Called with (page_number, response) the moment a 200 arrives; must write the verbatim
# body to disk and return the path it was written to.
WriteRaw = Callable[[int, requests.Response], pathlib.Path]


class CompletenessError(Exception):
    """Summed page rows != meta.total_records — extraction lost data. Fix, don't proceed."""


class RawParseError(Exception):
    """A persisted response body is not valid JSON. The bytes are safe on disk (path in the
    message) for schema/corruption inspection; we still stop because rows can't be counted."""


@dataclass
class Page:
    page: int
    resp: requests.Response
    body: dict
    meta: dict
    data: list
    params: dict
    path: pathlib.Path


def _parse_persisted(resp: requests.Response, path: pathlib.Path, where: str) -> dict:
    """Parse a body that has ALREADY been written to `path`; raise RawParseError on failure."""
    try:
        return resp.json()
    except ValueError as exc:
        raise RawParseError(
            f"{where}: response body is not valid JSON — saved verbatim to {path} "
            f"for inspection; {exc}"
        ) from exc


def iter_pages(client: QuadraClient, path: str, base_params: dict, page_size: int,
               write_raw: WriteRaw) -> Iterator[Page]:
    """Yield each page; raise CompletenessError after the final page if rows != total_records.

    Each body is persisted via `write_raw` BEFORE it is parsed, so the caller always has the
    verbatim bytes on disk even if parsing or the completeness gate later fails.
    """
    page = 1
    summed = 0
    total_records: object = None
    while True:
        params = {**base_params, "page": page, "page_size": page_size}
        resp = client.get(path, params)
        saved = write_raw(page, resp)  # verbatim bytes to disk FIRST
        body = _parse_persisted(resp, saved, f"{path} {base_params} page {page}")
        meta = body.get("meta") or {}
        data = body.get("data") or []
        if total_records is None:
            total_records = meta.get("total_records")
        summed += len(data)
        yield Page(page, resp, body, meta, data, params, saved)
        if not meta.get("has_next_page"):
            break
        page += 1
    if total_records is not None and summed != total_records:
        raise CompletenessError(
            f"{path} {base_params}: summed {summed} rows != meta.total_records {total_records}"
        )


def fetch_single(client: QuadraClient, path: str, params: dict, write_raw: WriteRaw) -> Page:
    """One-shot GET for non-paginated endpoints; persist-before-parse, then gate len==total."""
    resp = client.get(path, params)
    saved = write_raw(1, resp)  # verbatim bytes to disk FIRST
    body = _parse_persisted(resp, saved, f"{path} {params}")
    meta = body.get("meta") or {}
    data = body.get("data") or []
    total_records = meta.get("total_records")
    if total_records is not None and len(data) != total_records:
        raise CompletenessError(
            f"{path} {params}: single response has {len(data)} rows != total_records {total_records}"
        )
    return Page(1, resp, body, meta, data, params, saved)

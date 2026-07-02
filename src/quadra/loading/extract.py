"""Phase orchestration: pull each endpoint, persist verbatim, gate completeness, record findings.

Two phases (plans/DATA_LOADING.md §8):
  - trace: all light endpoints in full + feed-in/schedule for the 5 verified trace assets.
  - full:  all light endpoints in full + feed-in/schedule for every asset.

Resume unit is the partition (a `_done.json` marker). An interrupted (partial) partition is
reset and re-pulled — raw pages are otherwise write-once.
"""
from __future__ import annotations

import logging

from . import config as cfg
from . import endpoints as eps
from . import raw_store as rs
from .client import QuadraClient
from .endpoints import Endpoint
from .paginate import fetch_single, iter_pages

log = logging.getLogger("quadra.extract")


def _reset_partition(raw_root: str, ep: Endpoint) -> None:
    d = rs.partition_dir(raw_root, ep.name, ep.partition)
    if d.exists():
        for f in d.glob("page-*.json"):
            f.unlink()
        done = d / "_done.json"
        if done.exists():
            done.unlink()


def extract_endpoint(client: QuadraClient, raw_root: str, ep: Endpoint, findings: list,
                     *, resume: bool = True) -> int | None:
    """Extract one endpoint partition. Returns server total_records (or None).

    HARD gate: pagination completeness (inside iter_pages/fetch_single).
    SOFT check: expected_total mismatch is appended to `findings` and logged, never raised.
    """
    if resume and rs.partition_complete(raw_root, ep.name, ep.partition):
        log.info("skip %s/%s (already complete)", ep.name, ep.partition)
        return None
    _reset_partition(raw_root, ep)

    summed = 0
    total_records: object = None

    def write_raw(page, resp):
        # Persist the verbatim body the instant it arrives, BEFORE anyone parses it, so a
        # corrupt/schema-deviating page is preserved on disk for later validation.
        return rs.write_page(rs.page_path(raw_root, ep.name, ep.partition, page), resp.text)

    if ep.paginated:
        assert ep.page_size is not None
        for pg in iter_pages(client, ep.path, ep.params, ep.page_size, write_raw):
            _record_manifest(raw_root, ep, pg)
            if total_records is None:
                total_records = pg.meta.get("total_records")
            summed += len(pg.data)
    else:
        pg = fetch_single(client, ep.path, ep.params, write_raw)
        _record_manifest(raw_root, ep, pg)
        total_records = pg.meta.get("total_records")
        summed = len(pg.data)

    if ep.expected_total is not None and total_records != ep.expected_total:
        finding = {
            "check": "expected_total", "endpoint": ep.name, "partition": ep.partition,
            "expected": ep.expected_total, "actual": total_records,
        }
        findings.append(finding)
        log.warning("EXPECTATION %s/%s total_records=%s expected=%s (recorded, not fatal)",
                    ep.name, ep.partition, total_records, ep.expected_total)

    rs.mark_done(raw_root, ep.name, ep.partition,
                 {"rows": summed, "total_records": total_records})
    return total_records


def _record_manifest(raw_root: str, ep: Endpoint, pg) -> None:
    # The verbatim page was already written (pre-parse) by the write_raw closure; here we only
    # append the provenance line, which needs the parsed row/total/has_next_page counts.
    rs.append_manifest(raw_root, rs.manifest_record(
        endpoint=ep.path, partition=ep.partition, page=pg.page, path=pg.path,
        http_status=pg.resp.status_code, rows=len(pg.data),
        total_records=pg.meta.get("total_records"), has_next_page=pg.meta.get("has_next_page"),
        response_generated_at=pg.meta.get("generated_at"),
        ratelimit_remaining=pg.resp.headers.get("X-RateLimit-Remaining"), params=pg.params,
    ))


# -- phase drivers ----------------------------------------------------------
def extract_light(client, raw_root, findings, *, resume=True) -> None:
    for ep in eps.light_endpoints():
        extract_endpoint(client, raw_root, ep, findings, resume=resume)


def read_asset_ids(raw_root: str) -> list[str]:
    """Enumerate asset_ids from the persisted `assets` pages (drives Phase-1 per-asset pulls)."""
    base = rs.partition_dir(raw_root, "assets", "status=all")
    seen: set[str] = set()
    ordered: list[str] = []
    for page in sorted(base.glob("page-*.json")):
        for row in rs.read_data_rows(page):
            aid = row.get("asset_id")
            if aid is not None and aid not in seen:
                seen.add(aid)
                ordered.append(aid)
    return ordered


def extract_feedin_schedule(client, raw_root, asset_ids, findings, *, resume=True) -> None:
    n = len(asset_ids)
    for i, aid in enumerate(asset_ids, 1):
        extract_endpoint(client, raw_root, eps.feedin_asset(aid), findings, resume=resume)
        extract_endpoint(client, raw_root, eps.schedule_asset(aid), findings, resume=resume)
        if i % 25 == 0 or i == n:
            log.info("feed-in+schedule: %d/%d assets (requests so far: %d)",
                     i, n, client.request_count)


def run_trace(client, raw_root, findings, *, resume=True, assets=None) -> None:
    extract_light(client, raw_root, findings, resume=resume)
    extract_feedin_schedule(client, raw_root, assets or cfg.TRACE_ASSETS, findings, resume=resume)


def run_full(client, raw_root, findings, *, resume=True) -> None:
    extract_light(client, raw_root, findings, resume=resume)
    asset_ids = read_asset_ids(raw_root)
    if len(asset_ids) != 847:
        findings.append({"check": "asset_count", "expected": 847, "actual": len(asset_ids)})
        log.warning("asset_count=%d (expected 847) — recorded", len(asset_ids))
    extract_feedin_schedule(client, raw_root, asset_ids, findings, resume=resume)

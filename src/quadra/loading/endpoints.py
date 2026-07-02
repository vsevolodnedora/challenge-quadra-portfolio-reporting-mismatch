"""Endpoint catalog — every call and every parameter is an explicit, documented choice.

`light_endpoints()` are pulled in full in BOTH phases (they are small). Feed-in and schedule
are the two heavy per-asset surfaces and are built per `asset_id` (shallow page offsets,
natural cache key, resumable). Expected totals are the live-probed truth and are used only as
*recorded* expectations (soft checks), never as reasons to reject data.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config as cfg


@dataclass(frozen=True)
class Endpoint:
    name: str                       # raw-store table + directory name
    path: str                       # API path under BASE_URL
    params: dict = field(default_factory=dict)
    paginated: bool = False
    page_size: int | None = None
    partition: str = ""             # directory label under the endpoint (provenance)
    expected_total: int | None = None  # soft expectation only


def light_endpoints() -> list[Endpoint]:
    m = cfg.REPORT_MONTH
    d = {"date_from": cfg.DATE_FROM, "date_to": cfg.DATE_TO}
    return [
        Endpoint("assets", "/assets", {"status": "all"}, True, 200, "status=all", 847),
        Endpoint("contracts", "/data/contracts", {"include_amendments": "true"}, True, 200,
                 "include_amendments=true", 847),
        # prices rejects page/page_size -> single unpaginated call; product=all returns DA+ID.
        Endpoint("prices", "/data/prices", {**d, "product": "all"}, False, None, "product=all", 1488),
        Endpoint("eeg_premium", "/data/eeg_premium", {"month": m}, False, None, f"month={m}", 2),
        Endpoint("costs", "/data/costs", {"month": m}, True, 200, f"month={m}", 3388),
        Endpoint("costs_metadata", "/data/costs/metadata", {"month": m}, False, None, f"month={m}", 1),
        Endpoint("report_asset", "/report/monthly", {"month": m, "granularity": "asset"}, True, 100,
                 "granularity=asset", 847),
        Endpoint("report_region", "/report/monthly", {"month": m, "granularity": "region"}, False, None,
                 "granularity=region", 4),
        Endpoint("report_portfolio", "/report/monthly", {"month": m, "granularity": "portfolio"}, False,
                 None, "granularity=portfolio", 1),
    ]


def feedin_asset(asset_id: str) -> Endpoint:
    return Endpoint(
        "feedin", "/data/feedin",
        {"asset_id": asset_id, "date_from": cfg.DATE_FROM, "date_to": cfg.DATE_TO,
         "quality_filter": "all"},
        paginated=True, page_size=500, partition=f"asset_id={asset_id}", expected_total=2976,
    )


def schedule_asset(asset_id: str) -> Endpoint:
    # No fixed expected_total: schedule stops on an inactive asset's contract_end (verified);
    # per-asset count is validated later against contracts, and the portfolio total (626,472)
    # is checked in the storage layer.
    return Endpoint(
        "schedule", "/data/schedule",
        {"asset_id": asset_id, "date_from": cfg.DATE_FROM, "date_to": cfg.DATE_TO},
        paginated=True, page_size=500, partition=f"asset_id={asset_id}", expected_total=None,
    )

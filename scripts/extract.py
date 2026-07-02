#!/usr/bin/env python3
"""CLI: authenticated extraction of the QUADRA API into the immutable raw store.

Examples:
    python scripts/extract.py --phase trace          # Phase 0: light in full + 5 trace assets
    python scripts/extract.py --phase full           # Phase 1: complete pull, resumable
    python scripts/extract.py --phase trace --assets WND-0042,SOL-0032
    python scripts/extract.py --phase full --no-resume

Findings (recorded content expectations, never fatal) are written to
<raw_root>/_findings.json for the storage/reconciliation step to consume.
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

# Allow running from a source checkout without installation.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from quadra.loading import config as cfg          # noqa: E402
from quadra.loading import extract                # noqa: E402
from quadra.loading.client import QuadraClient    # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="QUADRA API extractor")
    ap.add_argument("--phase", choices=["trace", "full"], required=True)
    ap.add_argument("--raw-root", default=cfg.DEFAULT_RAW_ROOT)
    ap.add_argument("--assets", default=None,
                    help="comma-separated asset_ids to override the trace set (trace phase only)")
    ap.add_argument("--no-resume", action="store_true",
                    help="re-pull even partitions already marked complete")
    ap.add_argument("--env", default=".env")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("quadra.cli")

    config = cfg.load_config(args.env)
    client = QuadraClient(config.api_key, config.base_url)
    findings: list[dict] = []
    resume = not args.no_resume

    if args.phase == "trace":
        assets = [a.strip() for a in args.assets.split(",")] if args.assets else None
        extract.run_trace(client, args.raw_root, findings, resume=resume, assets=assets)
    else:
        if args.assets:
            log.warning("--assets is ignored in the full phase")
        extract.run_full(client, args.raw_root, findings, resume=resume)

    findings_path = pathlib.Path(args.raw_root) / "_findings.json"
    findings_path.parent.mkdir(parents=True, exist_ok=True)
    findings_path.write_text(json.dumps(findings, indent=2))

    log.info("done: %d HTTP requests, %d recorded finding(s) -> %s",
             client.request_count, len(findings), findings_path)
    for f in findings:
        log.info("finding: %s", json.dumps(f))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Print the capture plan resolved from a pathway map."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sunsponge.capture_service import build_capture_plan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve a SunSponge capture plan from a pathway map")
    parser.add_argument("--manifest", help="Path to pathway-manifest.md")
    parser.add_argument("--map", help="Path to verifier JSON output")
    parser.add_argument("--base-url", default="https://example.com", help="Base URL for SPA/page resolution")
    parser.add_argument("--viewports", default="desktop", help="Comma-separated viewports")
    parser.add_argument("--schemes", default="light", help="Comma-separated color schemes")
    args = parser.parse_args()

    if not args.manifest and not args.map:
        parser.error("provide --manifest or --map")

    payload = {
        "base_url": args.base_url,
        "viewports": [v.strip() for v in args.viewports.split(",") if v.strip()],
        "schemes": [s.strip() for s in args.schemes.split(",") if s.strip()],
    }
    if args.manifest:
        payload["manifest_path"] = args.manifest
    if args.map:
        payload["map_path"] = args.map

    urls, targets, settings = build_capture_plan(payload)
    discovery = settings.get("discovery") or {}
    pathway_count = discovery.get("pathway_count", "?")
    print(f"pathways: {pathway_count} -> targets: {len(targets)} ({len(urls)} unique URLs)")
    print(f"mode: {discovery.get('mode')} source: {discovery.get('source')} base: {discovery.get('base_url')}")
    print()
    for target in targets[:12]:
        print(
            f"  {target.pathway_id:28} {target.pathway_status:11} "
            f"{target.viewport_id:7} {target.scheme:5} {target.url}"
        )
    if len(targets) > 12:
        print(f"  ... and {len(targets) - 12} more targets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
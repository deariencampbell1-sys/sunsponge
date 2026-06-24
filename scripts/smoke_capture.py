#!/usr/bin/env python3
"""Capture 1-2 pages from a real URL into ./out and exit 0 on success."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sunsponge.capture_service import build_capture_plan, run_capture  # noqa: E402


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Capture pages to ./out")
    parser.add_argument("--manifest", help="Path to pathway-manifest.md")
    parser.add_argument("--map", help="Path to verifier JSON output")
    parser.add_argument("--base-url", default="", help="Base URL when using --manifest or --map")
    args, _unknown = parser.parse_known_args()
    out_dir = ROOT / "out"
    shots_dir = out_dir / "shots"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shots_dir.mkdir(parents=True, exist_ok=True)

    plan_payload: dict = {
        "viewports": ["desktop"],
        "schemes": ["light"],
        "format": "png",
        "full_page": True,
        "concurrency": 1,
        "wait_ms": 400,
        "timeout_ms": 30000,
    }
    if args.manifest or args.map:
        if args.manifest:
            plan_payload["manifest_path"] = args.manifest
        if args.map:
            plan_payload["map_path"] = args.map
        plan_payload["base_url"] = args.base_url or "https://example.com"
    else:
        plan_payload["urls"] = ["https://example.com/", "https://example.com/about"]

    _, targets, settings = build_capture_plan(plan_payload)

    results = run_capture(targets, settings, shots_dir, lambda _result: None)
    ok = [item for item in results if item.get("status") == "ok"]
    pngs = list(shots_dir.glob("*.png"))

    print(f"captured {len(ok)}/{len(results)} targets")
    for path in sorted(pngs):
        print(f"  {path} ({path.stat().st_size} bytes)")

    if not ok:
        print("smoke failed: no screenshots captured", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
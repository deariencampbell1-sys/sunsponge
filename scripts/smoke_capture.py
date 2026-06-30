#!/usr/bin/env python3
"""Map-driven smoke: capture a local built page into ./out and exit 0 on success.

Captur'd is a desktop, map-driven tool — so the smoke builds a tiny local HTML
page, points ``base_url`` at it via ``file://``, and drives the capture from a
pathway map (the bundled single-pathway fixture by default, or one you pass)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sunsponge.capture_service import build_capture_plan, run_capture  # noqa: E402

DEFAULT_MAP = ROOT / "tests" / "fixtures" / "single-pathway-manifest.md"
SAMPLE_HTML = "<!doctype html><html><head><title>Captur'd smoke</title></head>" \
    "<body style='font-family:sans-serif;padding:3rem'><h1>RHOBEAR Captur'd</h1>" \
    "<p>Local rested-state smoke page.</p></body></html>"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Capture a local page to ./out via a pathway map")
    parser.add_argument("--manifest", help="Path to pathway-manifest.md (defaults to the bundled fixture)")
    parser.add_argument("--map", help="Path to verifier JSON output")
    parser.add_argument("--base-url", default="", help="Built HTML location (defaults to a generated local page)")
    args, _unknown = parser.parse_known_args()

    out_dir = ROOT / "out"
    shots_dir = out_dir / "shots"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shots_dir.mkdir(parents=True, exist_ok=True)

    # Materialize a local built page to point at unless the caller supplied one.
    base_url = args.base_url
    if not base_url:
        site = out_dir / "site"
        site.mkdir(parents=True, exist_ok=True)
        index = site / "index.html"
        index.write_text(SAMPLE_HTML, encoding="utf-8")
        base_url = index.resolve().as_uri()

    plan_payload: dict = {
        "viewports": ["desktop"],
        "schemes": ["light"],
        "format": "png",
        "full_page": True,
        "concurrency": 1,
        "wait_ms": 400,
        "timeout_ms": 30000,
        "base_url": base_url,
    }
    if args.map:
        plan_payload["map_path"] = args.map
    else:
        plan_payload["manifest_path"] = args.manifest or str(DEFAULT_MAP)

    _, targets, settings = build_capture_plan(plan_payload)

    results = run_capture(targets, settings, shots_dir, lambda _result: None)
    ok = [item for item in results if item.get("status") == "ok"]
    pngs = list(shots_dir.glob("*.png"))

    print(f"captured {len(ok)}/{len(results)} targets from {base_url}")
    for path in sorted(pngs):
        print(f"  {path} ({path.stat().st_size} bytes)")

    if not ok:
        print("smoke failed: no screenshots captured", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

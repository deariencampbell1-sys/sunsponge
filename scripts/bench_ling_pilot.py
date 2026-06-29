#!/usr/bin/env python3
"""Proof case: time Ling 2.6 Flash piloting RHOBEAR Captur'd end to end.

Two timed legs, one cheap model:
  Leg 1 (MAP)     — Ling reads a product's HTML and returns a pathway manifest.
  Leg 2 (CAPTURE) — that map drives Captur'd to shoot every state x viewport x
                    scheme, deterministically, via Playwright.

Usage:
  OPENROUTER_API_KEY=... PYTHONPATH=src \
    python scripts/bench_ling_pilot.py --name "RHOBEAR Designs" \
      --subject-html /path/to/source.html --base-url http://localhost:4173/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sunsponge.capture_service import build_capture_plan, run_capture  # noqa: E402

MODEL = "inclusionai/ling-2.6-flash"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

MAP_SYSTEM = (
    "You are a UI cartographer for a screenshot tool. You are given the LIVE "
    "rendered DOM of a built product. Find the distinct RESTED STATES a user can "
    "reach by clicking ONE control (open a panel/modal, switch mode, open a tab) "
    "plus the default view.\n"
    "CRITICAL: the selector must point at the CLICKABLE CONTROL the user presses "
    "(a <button>/<a>/element with data-action, data-testid, id, or onclick) — "
    "NOT the panel/modal that appears afterward. Copy the attribute/value "
    "VERBATIM from the DOM; never invent attributes. Only include a row if you "
    "can see the real clickable element. The default/landing state has an EMPTY "
    "selector.\n\n"
    "Return ONLY a markdown pathway manifest, no prose, EXACTLY this shape:\n\n"
    "## Pathways Table\n\n"
    "| id | selector | trigger | status |\n|---|---|---|---|\n"
    "| default-view |  | initial load | WIRED |\n"
    "| ai-open | [data-action=\"ai-toggle\"] | click AI button | WIRED |\n\n"
    "6-14 rows, unique kebab-case ids, selectors copied exactly from the DOM."
)


def rendered_dom(url: str) -> str | None:
    """Load the served product and return its LIVE DOM, so Ling maps selectors
    that actually exist (not guesses from source)."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1440, "height": 1000})
            pg.goto(url, wait_until="networkidle", timeout=30000)
            pg.wait_for_timeout(2500)
            html = pg.content()
            b.close()
            return html
    except Exception as e:
        print(f"   (rendered-DOM fetch failed: {e}; falling back to source file)")
        return None


def call_ling(api_key: str, html: str) -> tuple[str, float, dict]:
    user = "Map this product's pathways:\n\n" + html[:24000]
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": MAP_SYSTEM},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 1500,
    }).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://rhobear.ai",
            "X-Title": "Capturd Ling Pilot",
        },
    )
    # Shared key — the swarm can be mid-burst; 429/5xx are transient. Back off
    # and retry, and only count the SUCCESSFUL call's wall time as the map time.
    last = None
    tries = 9
    for attempt in range(tries):
        try:
            t = time.time()
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
            dt = time.time() - t
            return data["choices"][0]["message"]["content"], dt, data.get("usage", {})
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 529) and attempt < tries - 1:
                wait = min(8 * (attempt + 1), 40)
                print(f"   (Ling {e.code}; shared key busy, backing off {wait}s, retry {attempt+1}/{tries-1})")
                time.sleep(wait)
                continue
            raise
    raise last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--subject-html", required=True, help="HTML file Ling maps")
    ap.add_argument("--base-url", required=True, help="where the built product is served")
    ap.add_argument("--out", default=str(ROOT / "out" / "bench"))
    args = ap.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2

    # Map the LIVE product DOM (selectors that exist), fall back to source file.
    html = rendered_dom(args.base_url) or Path(args.subject_html).read_text(encoding="utf-8", errors="ignore")
    shots_dir = Path(args.out) / "shots"
    if shots_dir.exists():
        import shutil
        shutil.rmtree(shots_dir.parent, ignore_errors=True)
    shots_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== {args.name} — Ling 2.6 Flash pilot ===")
    print(f"subject: {args.subject_html}  ({len(html):,} chars)")
    print(f"base-url: {args.base_url}")

    # ---- Leg 1: MAP ----
    print("\n[leg 1] Ling mapping the product...")
    manifest, t_map, usage = call_ling(key, html)
    Path(args.out, "map.md").write_text(manifest, encoding="utf-8")
    from sunsponge.pathway_map import load_pathway_map
    parsed = load_pathway_map(manifest_text=manifest)
    n_pathways = len(parsed.get("pathways") or [])
    print(f"   MAP TIME: {t_map:.1f}s  |  pathways found: {n_pathways}  |  "
          f"tokens in/out: {usage.get('prompt_tokens','?')}/{usage.get('completion_tokens','?')}")

    if n_pathways == 0:
        print("   Ling produced no parseable pathways — raw saved to map.md", file=sys.stderr)
        return 1

    # ---- Leg 2: CAPTURE ----
    print("\n[leg 2] Captur'd shooting every state x viewport x scheme...")
    payload = {
        "pathway_manifest": manifest,
        "base_url": args.base_url,
        "viewports": ["desktop", "tablet", "mobile"],
        "schemes": ["light", "dark"],
        "format": "png",
        "full_page": True,
        "concurrency": 4,
        "wait_ms": 500,
        "timeout_ms": 30000,
    }
    _urls, targets, settings = build_capture_plan(payload)
    t = time.time()
    results = run_capture(targets, settings, shots_dir, lambda _r: None)
    t_cap = time.time() - t
    ok = [r for r in results if r.get("status") == "ok"]
    fired = sum(1 for r in ok if r.get("trigger_fired"))
    pngs = list(shots_dir.glob("*.png"))
    total_bytes = sum(p.stat().st_size for p in pngs)
    # Distinct pictures (md5) — the honest count that does NOT include duplicates.
    import hashlib
    uniq = {hashlib.md5(p.read_bytes()).hexdigest() for p in pngs}
    print(f"   CAPTURE TIME: {t_cap:.1f}s  |  shots: {len(ok)}/{len(targets)} ok  |  "
          f"triggers fired: {fired}  |  {total_bytes/1024:.0f} KB of PNGs")

    # ---- Proof summary ----
    per_shot = t_cap / max(1, len(ok))
    print("\n=== PROOF CASE ===")
    print(f"product           : {args.name}")
    print(f"pilot model       : {MODEL}")
    print(f"map time          : {t_map:.1f}s  ({n_pathways} pathways mapped)")
    print(f"capture time      : {t_cap:.1f}s  ({len(ok)} shots, {per_shot:.2f}s/shot)")
    print(f"triggers fired    : {fired}/{len(ok)}  (states reached by a real click)")
    print(f"DISTINCT pictures : {len(uniq)}  (deduped by pixel hash — the real count)")
    print(f"end-to-end        : {t_map + t_cap:.1f}s, fully autonomous, ~$0 human")
    print(f"artifacts         : {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

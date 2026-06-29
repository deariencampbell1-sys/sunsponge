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
    "You are a UI cartographer for a screenshot tool. You are given a LIST of the "
    "live clickable controls of a built product (each as `selector=... text=...`). "
    "Pick the controls that lead to DISTINCT RESTED STATES worth a portfolio shot "
    "— primary views/tabs (Hub, Skills, Learn, Catalog, Settings...), mode/theme "
    "switches, and panels/modals — plus the default view. Skip pure external "
    "links (social icons), duplicates, and trivial buttons.\n"
    "Use each selector EXACTLY as given; never invent one. The default/landing "
    "state has an EMPTY selector.\n\n"
    "Return ONLY a markdown pathway manifest, no prose, EXACTLY this shape:\n\n"
    "## Pathways Table\n\n"
    "| id | selector | trigger | status |\n|---|---|---|---|\n"
    "| default-view |  | initial load | WIRED |\n"
    "| skills-view | [data-view=\"skills\"] | open Skills | WIRED |\n\n"
    "6-16 rows, unique kebab-case ids, selectors copied exactly from the list."
)


_EXTRACT_JS = r"""() => {
  const sel = (el) => {
    for (const a of ['data-action','data-view','data-room','data-tab','data-testid','data-rail-pane','data-mode','data-house','data-theme']) {
      const v = el.getAttribute(a); if (v) return `[${a}="${v}"]`;
    }
    if (el.id) return '#' + CSS.escape(el.id);
    const cls = (el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className);
    if (typeof cls === 'string' && cls.trim()) {
      const c = cls.trim().split(/\s+/).filter(x=>!/^(is-|has-)/.test(x))[0];
      if (c) return el.tagName.toLowerCase() + '.' + CSS.escape(c);
    }
    return el.tagName.toLowerCase();
  };
  const out = [], seen = new Set();
  const els = document.querySelectorAll('button,[role="button"],a[href],[data-action],[data-view],[data-room],[data-tab],[data-house],[data-theme],[data-testid],[onclick],nav li,[role="tab"]');
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) continue;
    const s = sel(el);
    const txt = (el.textContent||'').trim().replace(/\s+/g,' ').slice(0,40);
    const key = s + '|' + txt;
    if (seen.has(key)) continue; seen.add(key);
    out.push(`selector=${s}  text="${txt}"`);
    if (out.length >= 120) break;
  }
  return out.join('\n');
}"""


def extract_controls(url: str) -> str | None:
    """Return a compact list of the LIVE clickable controls (selector + label),
    so Ling maps real, existing selectors regardless of how big the app is."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1440, "height": 1000})
            pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(3500)
            controls = pg.evaluate(_EXTRACT_JS)
            b.close()
            return controls
    except Exception as e:
        print(f"   (controls extract failed: {e})")
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
    ap.add_argument("--subject-html", default="", help="fallback HTML file if live control extraction fails")
    ap.add_argument("--base-url", required=True, help="where the built product is served")
    ap.add_argument("--out", default=str(ROOT / "out" / "bench"))
    ap.add_argument("--viewports", default="desktop,tablet,mobile")
    ap.add_argument("--schemes", default="light,dark")
    args = ap.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2

    # Map the LIVE product's clickable controls (selectors that exist), so Ling
    # never guesses. Fall back to the source markup only if extraction fails.
    html = extract_controls(args.base_url)
    if not html:
        html = Path(args.subject_html).read_text(encoding="utf-8", errors="ignore") if args.subject_html else ""
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
        "viewports": [v.strip() for v in args.viewports.split(",") if v.strip()],
        "schemes": [s.strip() for s in args.schemes.split(",") if s.strip()],
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

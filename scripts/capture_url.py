#!/usr/bin/env python3
"""Headless screenshot of a single URL."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


async def capture(url: str, out: Path, width: int, height: int, wait_ms: int) -> None:
    from playwright.async_api import async_playwright

    out.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": width, "height": height})
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(wait_ms)
            await page.screenshot(path=str(out), type="png", full_page=False)
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("output")
    parser.add_argument("--width", type=int, default=1360)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--wait-ms", type=int, default=2500)
    args = parser.parse_args()
    asyncio.run(capture(args.url, Path(args.output), args.width, args.height, args.wait_ms))
    print(Path(args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
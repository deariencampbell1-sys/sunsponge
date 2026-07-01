#!/usr/bin/env python3
"""Capture rhobear-plans edgeless canvas with a populated doc."""

from __future__ import annotations

import asyncio
from pathlib import Path


async def to_view_coord(page, point: list[float], editor_index: int = 0) -> tuple[float, float]:
    container = page.locator("[data-affine-editor-container]").nth(editor_index)
    return await container.evaluate(
        """(container, point) => {
            const root = container.querySelector('affine-edgeless-root');
            if (!root) throw new Error('Edgeless root not found');
            return root.gfx.viewport.toViewCoord(point[0], point[1]);
        }""",
        point,
    )


async def drag_view(page, start: list[float], end: list[float], editor_index: int = 0) -> None:
    x1, y1 = await to_view_coord(page, start, editor_index)
    x2, y2 = await to_view_coord(page, end, editor_index)
    await page.mouse.move(x1, y1)
    await page.mouse.down()
    await page.mouse.move(x2, y2, steps=10)
    await page.mouse.up()


async def click_view(page, point: list[float], editor_index: int = 0) -> None:
    x, y = await to_view_coord(page, point, editor_index)
    await page.mouse.click(x, y)


async def set_edgeless_tool(page, tool: str, editor_index: int = 0) -> None:
    selectors = {
        "shape": "edgeless-toolbar-button.edgeless-shape-button",
        "note": "edgeless-toolbar-button.edgeless-note-button",
    }
    selector = selectors.get(tool)
    if not selector:
        return
    toolbar = page.locator("[data-affine-editor-container]").nth(editor_index).locator(
        "edgeless-toolbar-widget"
    )
    button = toolbar.locator(selector).first
    await button.wait_for(state="visible", timeout=15000)
    await button.locator(".icon-container").click()
    await page.wait_for_timeout(200)


async def main() -> int:
    from playwright.async_api import async_playwright

    url = "http://localhost:8080"
    out = Path(r"C:\Users\slang\rhobear-plans\site\canvas-shot.png")
    out.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1360, "height": 900})
        await context.add_init_script(
            "localStorage.setItem('open-link-mode', 'open-in-web');"
        )
        page = await context.new_page()
        try:
            page.set_default_timeout(60000)
            await page.goto(url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=25000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)

            await page.get_by_test_id("whiteboard-button").click(timeout=15000)
            await page.wait_for_timeout(8000)

            blank = page.get_by_role("button", name="Start with a blank canvas")
            if await blank.count():
                await blank.click(timeout=8000)
                await page.wait_for_timeout(1500)

            dark_toggle = page.get_by_role("button", name="Switch canvas surface to dark")
            if await dark_toggle.count():
                await dark_toggle.click(timeout=5000)
                await page.wait_for_timeout(800)

            await page.locator("affine-edgeless-root").wait_for(state="attached", timeout=30000)
            await page.wait_for_timeout(1000)

            await set_edgeless_tool(page, "note")
            await click_view(page, [120, 140])
            await page.wait_for_timeout(400)
            await page.keyboard.type("Product roadmap")
            await page.keyboard.press("Enter")
            await page.keyboard.type("Q3 milestones + launch checklist")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)

            await set_edgeless_tool(page, "note")
            await click_view(page, [120, 320])
            await page.wait_for_timeout(400)
            await page.keyboard.type("Design review → ship dark canvas")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)

            await set_edgeless_tool(page, "shape")
            await drag_view(page, [420, 100], [620, 280])
            await page.wait_for_timeout(400)

            await click_view(page, [40, 40])
            await page.wait_for_timeout(1200)

            await page.screenshot(path=str(out), type="png", full_page=False)
        finally:
            await context.close()
            await browser.close()

    print(out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
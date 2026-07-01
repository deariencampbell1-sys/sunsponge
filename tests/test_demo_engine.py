"""Tests for the DemoForge recorder (Phase 1).

Unit tests cover dataclass shape + DemoManager lifecycle.
Integration test (test_demo_recorder_integration) runs a real headful
Playwright session and programmatically clicks the page, then verifies
the resulting DemoSpec JSON.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

# Make src/ importable when running pytest from project root.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sunsponge.demo_engine import (  # noqa: E402
    BoundingRect,
    DemoManager,
    DemoRecorder,
    DemoRecorderError,
    DemoSpec,
    DemoStep,
    Hotspot,
    Interaction,
)


# ---------------------------------------------------------------------------
# Unit: dataclass shape
# ---------------------------------------------------------------------------


def test_dataclasses_roundtrip() -> None:
    spec = DemoSpec(
        id="abc123",
        name="Onboarding",
        goal="Sign up",
        viewport={"width": 1280, "height": 800},
        startUrl="http://localhost:8787",
    )
    step = DemoStep(
        index=0,
        timestamp=1200,
        pageUrl="http://localhost:8787/",
        pageTitle="Welcome",
        interaction=Interaction(
            type="click",
            target={"selector": "#cta", "tagName": "button"},
            hotspot={"xPct": 50.0, "yPct": 50.0},
            value=None,
        ),
        screenshotPath="demos/abc123/step_000.png",
    )
    spec.steps.append(step)
    d = spec.to_dict()
    assert d["version"] == 1
    assert d["id"] == "abc123"
    assert d["viewport"] == {"width": 1280, "height": 800}
    assert len(d["steps"]) == 1
    assert d["steps"][0]["interaction"]["target"]["selector"] == "#cta"
    assert d["steps"][0]["interaction"]["hotspot"]["xPct"] == 50.0
    # Round-trip via JSON to confirm serializability.
    json.dumps(d)


def test_dataclass_defaults() -> None:
    spec = DemoSpec()
    assert spec.version == 1
    assert spec.steps == []
    assert spec.viewport == {"width": 1440, "height": 900}
    assert spec.aiAnnotations is None


# ---------------------------------------------------------------------------
# Unit: DemoManager lifecycle
# ---------------------------------------------------------------------------


def test_demo_manager_rejects_bad_url(tmp_path: Path) -> None:
    mgr = DemoManager(output_root=tmp_path)
    with pytest.raises(DemoRecorderError, match="url is required"):
        mgr.start({"url": "", "name": "x"})
    with pytest.raises(DemoRecorderError, match="unsupported url scheme"):
        mgr.start({"url": "ftp://example.com", "name": "x"})


def test_demo_manager_start_get_discard(tmp_path: Path) -> None:
    mgr = DemoManager(output_root=tmp_path)
    recorder, sid = mgr.start(
        {"url": "https://example.com", "name": "Test", "goal": "verify"}
    )
    assert isinstance(recorder, DemoRecorder)
    assert sid
    assert mgr.get(sid) is recorder
    assert any(s["sessionId"] == sid for s in mgr.list_sessions())
    mgr.discard(sid)
    with pytest.raises(DemoRecorderError, match="unknown session"):
        mgr.get(sid)


def test_demo_recorder_initializes_output_dir(tmp_path: Path) -> None:
    out = tmp_path / "demos" / "session42"
    r = DemoRecorder(
        session_id="session42",
        url="https://example.com",
        name="t",
        goal="g",
        output_dir=out,
    )
    assert out.is_dir()
    assert r.spec.id == "session42"
    assert r.spec.startUrl == "https://example.com"
    assert r.spec.viewport == {"width": 1440, "height": 900}
    assert r.get_spec() is r.spec


# ---------------------------------------------------------------------------
# Integration: real headful recording with synthetic clicks
#
# Spins up a real Chromium (with Edge/Chrome fallback on Windows), injects the
# overlay, dispatches several page.mouse.click() events, stops the recorder,
# and validates the resulting demo.json.
#
# Skip on environments without a usable headful browser.
# ---------------------------------------------------------------------------


HTML_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Recorder test</title>
<style>
  body { margin: 0; font-family: system-ui; padding: 24px; }
  button { padding: 14px 24px; font-size: 16px; cursor: pointer; }
  #output { margin-top: 24px; padding: 16px; background: #f3f3f3; }
  #counter { font-weight: bold; font-size: 28px; }
</style></head>
<body>
  <h1 id="title">Test page</h1>
  <button id="btn-a">Button A</button>
  <button class="cta" id="btn-b">Button B</button>
  <div id="output">clicks: <span id="counter">0</span></div>
  <script>
    let n = 0;
    document.getElementById('btn-a').addEventListener('click', () => { n++; document.getElementById('counter').textContent = n; });
    document.getElementById('btn-b').addEventListener('click', () => { n++; document.getElementById('counter').textContent = n; });
  </script>
</body></html>
"""


def _can_launch_headful() -> bool:
    """Detect whether we have any usable headful browser."""
    import os as _os
    from playwright.sync_api import sync_playwright

    attempts = [{"headless": False}]
    if _os.name == "nt":
        attempts.extend([{"headless": False, "channel": "msedge"}, {"headless": False, "channel": "chrome"}])
    try:
        with sync_playwright() as p:
            for kw in attempts:
                try:
                    b = p.chromium.launch(**kw)
                    b.close()
                    return True
                except Exception:
                    continue
    except Exception:
        return False
    return False


@pytest.mark.skipif(
    not _can_launch_headful(),
    reason="no headful browser available (run `python -m playwright install chromium`)",
)
def test_demo_recorder_integration(tmp_path: Path) -> None:
    """End-to-end: start recorder, click buttons, stop, validate demo.json."""
    import asyncio as _asyncio
    import threading as _threading

    # Write the test page to disk and file-serve it.
    page_html = tmp_path / "page.html"
    page_html.write_text(HTML_PAGE, encoding="utf-8")
    target_url = page_html.resolve().as_uri()

    demos_root = tmp_path / "demos"
    mgr = DemoManager(output_root=demos_root)
    recorder, sid = mgr.start(
        {
            "url": target_url,
            "name": "Integration test",
            "goal": "verify 3+ click capture",
            "viewport": {"width": 1024, "height": 768},
        }
    )

    # Run the recorder's async start in a background thread. The recorder
    # owns its own event loop; we coordinate with it via run_coroutine_threadsafe.
    exc_holder: list[BaseException] = []

    def runner() -> None:
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)
        # Stash the loop on the recorder BEFORE start() so the test thread
        # can hand work to it the moment it sees recorder._loop.
        recorder._loop = loop
        try:
            loop.run_until_complete(recorder.start())
            # Park the loop so the test thread can dispatch work via
            # run_coroutine_threadsafe (clicks + stop teardown).
            loop.run_forever()
        except BaseException as e:  # pragma: no cover - surfaced via assert
            exc_holder.append(e)
        finally:
            try:
                loop.close()
            except Exception:
                pass

    t = _threading.Thread(target=runner, name=f"test-demo-{sid}", daemon=True)
    t.start()

    # Wait for the recorder's loop + page to come up.
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if recorder._loop is not None and recorder._page is not None:
            try:
                ready = _asyncio.run_coroutine_threadsafe(
                    recorder._page.evaluate(
                        "() => !!document.getElementById('__demo-recorder-indicator')"
                    ),
                    recorder._loop,
                ).result(timeout=2.0)
                if ready:
                    break
            except Exception:
                pass
        time.sleep(0.2)
    else:
        recorder._stopped.set()
        pytest.fail("recorder page never became ready")

    # Drive three synthetic clicks through the recorder's own page. We dispatch
    # real MouseEvents with clientX/clientY so the overlay's hotspot math runs
    # the same way it would for a human click. dx/dy are offsets from the
    # element's top-left — keep them inside the element so hotspot math is
    # meaningful (anything past the edge is clamped to 100).
    clicks = [
        ("#btn-a", 50, 10),   # ~middle-left of Button A
        ("#btn-b", 80, 10),   # ~middle of Button B (slightly wider text)
        ("#title", 40, 10),   # left side of the H1
    ]
    for sel, dx, dy in clicks:
        async def _fire():
            return await recorder._page.evaluate(
                """({sel, dx, dy}) => {
                    const el = document.querySelector(sel);
                    if (!el) throw new Error('no element ' + sel);
                    const r = el.getBoundingClientRect();
                    const x = r.left + dx;
                    const y = r.top + dy;
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, clientX: x, clientY: y, button: 0}));
                    el.dispatchEvent(new MouseEvent('mouseup',   {bubbles: true, clientX: x, clientY: y, button: 0}));
                    el.dispatchEvent(new MouseEvent('click',     {bubbles: true, clientX: x, clientY: y, button: 0}));
                    return true;
                }""",
                {"sel": sel, "dx": dx, "dy": dy},
            )

        _asyncio.run_coroutine_threadsafe(_fire(), recorder._loop).result(timeout=5.0)
        time.sleep(0.3)

    # Give the capture loop time to drain.
    deadline = time.time() + 5.0
    while time.time() < deadline and len(recorder.spec.steps) < 3:
        time.sleep(0.1)

    # Stop and persist (sync — safe from this thread).
    spec = recorder.stop()
    # Break the recorder thread out of loop.run_forever().
    recorder._loop.call_soon_threadsafe(recorder._loop.stop)
    t.join(timeout=5.0)
    assert exc_holder == [], f"recorder thread crashed: {exc_holder!r}"

    # ----- Validations -----
    out_dir = tmp_path / "demos" / sid
    demo_json = out_dir / "demo.json"
    assert demo_json.is_file(), f"demo.json not written at {demo_json}"
    saved = json.loads(demo_json.read_text(encoding="utf-8"))

    assert saved["id"] == sid
    assert saved["name"] == "Integration test"
    assert saved["viewport"] == {"width": 1024, "height": 768}
    assert saved["startUrl"] == target_url
    assert len(saved["steps"]) >= 3, f"expected 3+ steps, got {len(saved['steps'])}"

    # Each step should have a screenshot file on disk.
    for i, step in enumerate(saved["steps"]):
        shot_rel = step["screenshotPath"]
        assert shot_rel, f"step {i} missing screenshotPath"
        shot_abs = (out_dir.parent / shot_rel).resolve()
        assert shot_abs.is_file(), f"screenshot missing: {shot_abs}"
        assert shot_abs.stat().st_size > 0

        assert step["index"] == i
        assert step["interaction"]["type"] == "click"
        assert step["interaction"]["target"]["selector"], f"step {i} missing selector"
        assert "xPct" in step["interaction"]["hotspot"]
        assert "yPct" in step["interaction"]["hotspot"]
        assert 0.0 <= step["interaction"]["hotspot"]["xPct"] <= 100.0
        assert 0.0 <= step["interaction"]["hotspot"]["yPct"] <= 100.0
        assert "boundingRect" in step["interaction"]["target"]

    # Verify selectors point at real elements on the page.
    selectors_used = [s["interaction"]["target"]["selector"] for s in saved["steps"]]
    assert any(sel == "#btn-a" for sel in selectors_used), selectors_used
    assert any(sel == "#btn-b" for sel in selectors_used), selectors_used

    # Verify hotspot math against a known element (#btn-a). With dx=50 against
    # the button's width, xPct should be roughly 50/width*100.
    btn_a_step = next(s for s in saved["steps"] if s["interaction"]["target"]["selector"] == "#btn-a")
    rect = btn_a_step["interaction"]["target"]["boundingRect"]
    expected_xpct = (50.0 / rect["width"]) * 100.0
    assert abs(btn_a_step["interaction"]["hotspot"]["xPct"] - round(expected_xpct, 2)) < 0.5, (
        f"xPct mismatch: got {btn_a_step['interaction']['hotspot']['xPct']}, "
        f"expected ~{round(expected_xpct, 2)} (rect.width={rect['width']})"
    )

    # Make sure no step points at the badge itself.
    assert all("__demo-recorder-indicator" not in s["interaction"]["target"]["selector"] for s in saved["steps"])

    # Return a small summary for the human-readable test log.
    print(
        f"\n[demo-recorder test] session={sid} steps={len(saved['steps'])} "
        f"selectors={selectors_used} output={demo_json}"
    )
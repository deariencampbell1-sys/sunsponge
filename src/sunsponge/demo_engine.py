"""DemoForge — Phase 1 recorder.

Headful Playwright session that captures user clicks + screenshots and produces
a DemoSpec JSON ready for the AI annotation pipeline.

Reuses SunSponge's Playwright infrastructure (browser channel fallback for
Windows) but does not modify ``capture_service.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DemoSpec dataclasses — match ARCHITECTURE.md §1
# ---------------------------------------------------------------------------


@dataclass
class Hotspot:
    xPct: float
    yPct: float


@dataclass
class BoundingRect:
    x: float
    y: float
    width: float
    height: float


@dataclass
class Interaction:
    type: str  # 'click' | 'input' | 'submit' | 'navigate'
    target: dict
    hotspot: dict
    value: str | None = None


@dataclass
class DemoStep:
    index: int
    timestamp: int
    pageUrl: str
    pageTitle: str
    interaction: Interaction
    screenshotBase64: str | None = None  # deprecated — use screenshotPath
    screenshotPath: str | None = None  # local path to step_*.png
    screenshotError: str | None = None  # set when capture fails


@dataclass
class DemoSpec:
    version: int = 1
    id: str = ""
    name: str = ""
    goal: str = ""
    createdAt: str = ""
    viewport: dict = field(default_factory=lambda: {"width": 1440, "height": 900})
    startUrl: str = ""
    steps: list[DemoStep] = field(default_factory=list)
    # AI-annotation slot is empty in Phase 1; the AI pipeline fills it later.
    aiAnnotations: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# JS overlay — injected on every page load via page.add_init_script()
# ---------------------------------------------------------------------------

# The script is plain JavaScript; we ship it as a string constant so it lands
# in the page's main world on every navigation. It:
#   * draws a fixed ● RECORDING badge bottom-right (not click-captured)
#   * listens for clicks in capture phase
#   * builds a CSS selector, bounding rect, and percentage hotspot
#   * pushes the payload to Python via window.recordClick (expose_function)
#   * mirrors step count back into the badge via window.__demoRecorderStepCount
#
# Notes on the selector builder: it prefers IDs, then classes (max 2),
# then :nth-of-type for sibling disambiguation — same heuristic as
# journey-trace's getCssSelector() (see research-findings.md).
OVERLAY_JS = r"""
(() => {
  if (window.__demoRecorderInstalled) return;
  window.__demoRecorderInstalled = true;
  window.__demoRecorderStepCount = 0;

  const BADGE_ID = '__demo-recorder-indicator';

  function ensureBadge() {
    let badge = document.getElementById(BADGE_ID);
    if (badge) return badge;
    badge = document.createElement('div');
    badge.id = BADGE_ID;
    badge.style.cssText = [
      'position: fixed',
      'right: 16px',
      'bottom: 16px',
      'z-index: 2147483647',
      'padding: 8px 14px',
      'border-radius: 999px',
      'background: rgba(20, 20, 24, 0.92)',
      'color: #fff',
      'font: 600 13px/1.2 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
      'box-shadow: 0 4px 18px rgba(0,0,0,0.35)',
      'display: flex',
      'align-items: center',
      'gap: 8px',
      'pointer-events: auto',
      'user-select: none',
      'letter-spacing: 0.04em',
    ].join(';');
    const dot = document.createElement('span');
    dot.style.cssText = [
      'width: 10px',
      'height: 10px',
      'border-radius: 50%',
      'background: #ff3b30',
      'box-shadow: 0 0 0 0 rgba(255,59,48,0.6)',
      'animation: __demoRecPulse 1.4s ease-out infinite',
    ].join(';');
    const label = document.createElement('span');
    label.id = BADGE_ID + '__label';
    label.textContent = 'RECORDING · 0 steps';
    badge.appendChild(dot);
    badge.appendChild(label);
    const style = document.createElement('style');
    style.textContent = '@keyframes __demoRecPulse { 0% { box-shadow: 0 0 0 0 rgba(255,59,48,0.6); } 70% { box-shadow: 0 0 0 12px rgba(255,59,48,0); } 100% { box-shadow: 0 0 0 0 rgba(255,59,48,0); } }';
    (document.head || document.documentElement).appendChild(style);
    (document.body || document.documentElement).appendChild(badge);
    return badge;
  }

  function setStepCount(n) {
    window.__demoRecorderStepCount = n;
    const label = document.getElementById(BADGE_ID + '__label');
    if (label) label.textContent = 'RECORDING · ' + n + ' step' + (n === 1 ? '' : 's');
  }

  function buildSelector(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + (window.CSS && CSS.escape ? CSS.escape(el.id) : el.id);
    if (el === document.body) return 'body';
    const parts = [];
    let current = el;
    while (current && current !== document.body) {
      if (!current.tagName) break;
      let sel = current.tagName.toLowerCase();
      if (current.id) {
        parts.unshift('#' + (window.CSS && CSS.escape ? CSS.escape(current.id) : current.id));
        break;
      }
      if (current.className && typeof current.className === 'string') {
        const classes = current.className.trim().split(/\s+/).slice(0, 2)
          .filter(Boolean)
          .map(c => '.' + (window.CSS && CSS.escape ? CSS.escape(c) : c))
          .join('');
        if (classes) sel += classes;
      }
      const parent = current.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
        if (siblings.length > 1) {
          const idx = siblings.indexOf(current) + 1;
          sel += ':nth-of-type(' + idx + ')';
        }
      }
      parts.unshift(sel);
      current = current.parentElement;
    }
    return parts.join(' > ');
  }

  function isBadge(target) {
    if (!target || !target.closest) return false;
    return !!target.closest('#' + BADGE_ID);
  }

  function describeClick(event) {
    const el = event.target;
    if (!el || el.nodeType !== 1) return null;
    const rect = el.getBoundingClientRect();
    const clientX = event.clientX;
    const clientY = event.clientY;
    let xPct = 0, yPct = 0;
    if (rect.width > 0) xPct = ((clientX - rect.left) / rect.width) * 100;
    if (rect.height > 0) yPct = ((clientY - rect.top) / rect.height) * 100;
    xPct = Math.max(0, Math.min(100, +xPct.toFixed(2)));
    yPct = Math.max(0, Math.min(100, +yPct.toFixed(2)));

    let text = '';
    if (el.innerText) text = el.innerText.trim().replace(/\s+/g, ' ');
    else if (el.textContent) text = el.textContent.trim().replace(/\s+/g, ' ');
    if (text.length > 80) text = text.slice(0, 77) + '...';

    return {
      type: 'click',
      target: {
        selector: buildSelector(el),
        tagName: el.tagName.toLowerCase(),
        text: text || undefined,
        boundingRect: {
          x: +rect.x.toFixed(2),
          y: +rect.y.toFixed(2),
          width: +rect.width.toFixed(2),
          height: +rect.height.toFixed(2),
        },
      },
      hotspot: { xPct, yPct },
      value: undefined,
    };
  }

  function handleClick(event) {
    if (isBadge(event.target)) return;
    if (event.button !== undefined && event.button !== 0) return; // left button only
    const payload = describeClick(event);
    if (!payload) return;
    payload.pageUrl = location.href;
    payload.pageTitle = document.title;
    payload.timestamp = Date.now();
    if (typeof window.recordClick === 'function') {
      try { window.recordClick(payload); } catch (_) { /* bridge gone */ }
    }
    // Always also stash for any late-attaching consumer.
    window.__demoRecorderLastClick = payload;
  }

  // Defer until DOM exists; otherwise retry on first mutation.
  function attach() {
    if (!document.body) {
      // body not ready yet — try again on next tick
      return false;
    }
    ensureBadge();
    document.addEventListener('click', handleClick, true);
    return true;
  }

  if (!attach()) {
    const mo = new MutationObserver(() => {
      if (attach()) mo.disconnect();
    });
    if (document.documentElement) {
      mo.observe(document.documentElement, { childList: true, subtree: true });
    }
    // Also try once DOMContentLoaded fires
    document.addEventListener('DOMContentLoaded', () => attach(), { once: true });
  }

  // Expose step-count updater for the Python side.
  window.__demoRecorderSetStepCount = setStepCount;
})();
"""


# ---------------------------------------------------------------------------
# Browser launch — headful, with the same Windows channel fallback the capture
# service uses. We deliberately don't import _launch_browser from
# capture_service because that one is hardcoded to headless=True.
# ---------------------------------------------------------------------------

async def _launch_demo_browser(playwright: Any) -> Any:
    """Launch a headful Chromium, falling back to Edge/Chrome on Windows."""
    launch_args: list[str] = []
    attempts: list[dict[str, Any]] = [{"headless": False, "args": launch_args}]
    if os.name == "nt":
        attempts.extend([
            {"headless": False, "channel": "msedge", "args": launch_args},
            {"headless": False, "channel": "chrome", "args": launch_args},
        ])
    last_error: Exception | None = None
    for kwargs in attempts:
        try:
            return await playwright.chromium.launch(**kwargs)
        except Exception as exc:  # pragma: no cover - environment-specific
            last_error = exc
            logger.warning("demo launch attempt failed (%s): %s", kwargs, exc)
    raise RuntimeError(
        f"unable to launch a headful browser for demo recording: {last_error}"
    )


# ---------------------------------------------------------------------------
# DemoRecorder — one instance per recording session
# ---------------------------------------------------------------------------


class DemoRecorderError(RuntimeError):
    """Raised when a recording session fails or is in the wrong state."""


class DemoRecorder:
    """Headful Playwright recorder. Produces a DemoSpec + PNGs per click."""

    POLL_INTERVAL_S = 0.1

    def __init__(
        self,
        *,
        session_id: str,
        url: str,
        name: str,
        goal: str,
        viewport: dict[str, int] | None = None,
        output_dir: Path | None = None,
    ) -> None:
        self.session_id = session_id
        self.url = url
        self.name = name
        self.goal = goal
        self.viewport = viewport or {"width": 1440, "height": 900}
        self.output_dir = output_dir or (Path.cwd() / "demos" / session_id)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.spec = DemoSpec(
            id=session_id,
            name=name,
            goal=goal,
            createdAt=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            viewport=dict(self.viewport),
            startUrl=url,
        )

        self._click_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._capture_task: asyncio.Task | None = None
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._started_at_ms: int | None = None
        # threading.Event so we can signal stop from any thread (the API path
        # calls stop() from uvicorn's worker thread, not the recorder's loop).
        self._stopped = threading.Event()
        self._last_url: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ----- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Launch the browser, navigate, and start capturing."""
        from playwright.async_api import async_playwright

        if self._capture_task is not None:
            raise DemoRecorderError("recorder already started")

        self._loop = asyncio.get_running_loop()
        self._playwright = await async_playwright().start()
        self._browser = await _launch_demo_browser(self._playwright)
        self._context = await self._browser.new_context(
            viewport={"width": self.viewport["width"], "height": self.viewport["height"]},
            device_scale_factor=1,
            locale="en-US",
        )
        self._page = await self._context.new_page()

        # Bridge: JS calls window.recordClick(payload) → Python callback.
        await self._page.expose_function("recordClick", self._on_click)
        # Init script re-installs overlay on every navigation.
        await self._page.add_init_script(OVERLAY_JS)

        await self._page.goto(self.url, wait_until="domcontentloaded")
        self._last_url = self._page.url
        self._started_at_ms = int(time.time() * 1000)

        self._capture_task = asyncio.create_task(self._capture_loop(), name=f"demo-cap-{self.session_id}")
        logger.info("demo recorder started: session=%s url=%s", self.session_id, self.url)

    def stop(self) -> DemoSpec:
        """Stop the recorder from any thread/loop and return the persisted spec.

        Safe to call from the FastAPI worker thread: we hand the actual
        teardown off to the recorder's own loop via run_coroutine_threadsafe.
        """
        if self._loop is None or self._capture_task is None:
            raise DemoRecorderError("recorder was never started")
        self._stopped.set()
        future = asyncio.run_coroutine_threadsafe(self._stop_async(), self._loop)
        return future.result(timeout=30.0)

    async def _stop_async(self) -> DemoSpec:
        """Actual teardown — runs inside the recorder's event loop."""
        assert self._capture_task is not None
        try:
            await asyncio.wait_for(self._capture_task, timeout=5.0)
        except asyncio.TimeoutError:
            self._capture_task.cancel()
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._write_outputs()
        logger.info(
            "demo recorder stopped: session=%s steps=%d", self.session_id, len(self.spec.steps)
        )
        return self.spec

    # ----- click bridge ------------------------------------------------------

    async def _on_click(self, payload: dict) -> None:
        """Called by the page when a click is captured. Non-blocking enqueue."""
        # Bridge runs in the Playwright async loop — same loop as our task.
        await self._click_queue.put(payload)

    # ----- capture loop ------------------------------------------------------

    async def _capture_loop(self) -> None:
        assert self._page is not None
        while not self._stopped.is_set():
            try:
                payload = await asyncio.wait_for(
                    self._click_queue.get(), timeout=self.POLL_INTERVAL_S
                )
            except asyncio.TimeoutError:
                continue

            step_index = len(self.spec.steps)
            timestamp_ms = int(time.time() * 1000) - (self._started_at_ms or 0)

            # Screenshot AFTER the click so we capture the post-click state.
            try:
                png_bytes = await self._page.screenshot(full_page=False, type="png")
            except Exception as exc:
                logger.warning("screenshot failed on step %d: %s", step_index, exc)
                png_bytes = b""

            shot_filename = f"step_{step_index:03d}.png"
            shot_path = self.output_dir / shot_filename
            if png_bytes:
                shot_path.write_bytes(png_bytes)

            step = DemoStep(
                index=step_index,
                timestamp=timestamp_ms,
                pageUrl=payload.get("pageUrl") or self._safe_url(),
                pageTitle=payload.get("pageTitle") or (await self._safe_title()),
                interaction=Interaction(
                    type=payload.get("type", "click"),
                    target=payload.get("target", {}),
                    hotspot=payload.get("hotspot", {"xPct": 0, "yPct": 0}),
                    value=payload.get("value"),
                ),
                screenshotBase64=None,  # stored on disk via screenshotPath only (avoids JSON bloat)
                screenshotPath=str(shot_path.relative_to(self.output_dir.parent))
                if png_bytes
                else None,
                screenshotError=None if png_bytes else "screenshot capture failed",
            )
            self.spec.steps.append(step)
            self._last_url = step.pageUrl

            # Update the on-page badge step count.
            try:
                await self._page.evaluate(
                    "(n) => { if (window.__demoRecorderSetStepCount) window.__demoRecorderSetStepCount(n); }",
                    len(self.spec.steps),
                )
            except Exception:
                # Page might have navigated away mid-call; safe to ignore.
                pass

    async def _safe_url(self) -> str:
        try:
            return self._page.url if self._page else ""
        except Exception:
            return ""

    async def _safe_title(self) -> str:
        try:
            return await self._page.title() if self._page else ""
        except Exception:
            return ""

    # ----- output ------------------------------------------------------------

    def _write_outputs(self) -> None:
        """Persist demo.json next to the screenshots."""
        out_path = self.output_dir / "demo.json"
        out_path.write_text(
            json.dumps(self.spec.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        logger.info("wrote %s (%d steps)", out_path, len(self.spec.steps))

    # ----- public read-only handle -------------------------------------------

    def get_spec(self) -> DemoSpec:
        """Return the current spec snapshot. Safe to call while recording."""
        return self.spec


# ---------------------------------------------------------------------------
# DemoManager — in-process registry of active recordings (used by app.py)
# ---------------------------------------------------------------------------


class DemoManager:
    """Tracks active DemoRecorder sessions so the API can find them by id."""

    def __init__(self, output_root: Path | None = None) -> None:
        self.output_root = output_root or (Path.cwd() / "demos")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, DemoRecorder] = {}
        self._lock = threading.Lock()

    def new_session_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def start(self, payload: dict[str, Any]) -> tuple[DemoRecorder, str]:
        url = (payload.get("url") or "").strip()
        name = (payload.get("name") or "Untitled demo").strip()
        goal = (payload.get("goal") or "").strip()
        if not url:
            raise DemoRecorderError("url is required")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise DemoRecorderError(f"unsupported url scheme: {url}")

        viewport = payload.get("viewport") or {"width": 1440, "height": 900}
        session_id = payload.get("sessionId") or self.new_session_id()
        out_dir = self.output_root / session_id

        recorder = DemoRecorder(
            session_id=session_id,
            url=url,
            name=name,
            goal=goal,
            viewport=viewport,
            output_dir=out_dir,
        )
        with self._lock:
            self._sessions[session_id] = recorder
        return recorder, session_id

    def get(self, session_id: str) -> DemoRecorder:
        with self._lock:
            rec = self._sessions.get(session_id)
        if rec is None:
            raise DemoRecorderError(f"unknown session: {session_id}")
        return rec

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "sessionId": sid,
                    "url": r.url,
                    "name": r.name,
                    "stepCount": len(r.spec.steps),
                }
                for sid, r in self._sessions.items()
            ]


# ---------------------------------------------------------------------------
# Helper for synchronous callers (FastAPI threadpool)
# ---------------------------------------------------------------------------


def run_async(coro: Any) -> Any:
    """Run an async coroutine to completion from sync code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async context; create a new loop in a thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


__all__ = [
    "BoundingRect",
    "DemoManager",
    "DemoRecorder",
    "DemoRecorderError",
    "DemoSpec",
    "DemoStep",
    "Hotspot",
    "Interaction",
    "OVERLAY_JS",
    "run_async",
]
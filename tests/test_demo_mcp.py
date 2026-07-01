"""Tests for the DemoForge MCP server (Phase 4).

Covers all 7 tools via an in-memory FastMCP client (no stdio required).
Recording tools (``demo.record`` / ``demo.stop``) need a real browser so
they are exercised in a separate, opt-in integration test.

Run::

    pytest tests/test_demo_mcp.py -v
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------------------
# Helpers — synthesize on-disk demos for the disk-backed tools
# ---------------------------------------------------------------------------


SAMPLE_DEMO_ID = "test-demo-001"


def _write_sample_demo(demos_dir: Path, demo_id: str = SAMPLE_DEMO_ID) -> Path:
    """Write a tiny but valid demo (2 steps) and matching PNGs."""
    d = demos_dir / demo_id
    d.mkdir(parents=True, exist_ok=True)
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000040000000408060000"
        "00a9f1c81c0000001049444154789c63606060f80f00040001000500"
        "01c5d6f4a70000000049454e44ae426082"
    )  # 4x4 transparent PNG, valid enough for the viewer to fetch
    step_pngs = []
    for i in range(2):
        path = d / f"step_{i:03d}.png"
        path.write_bytes(png_bytes)
        step_pngs.append(str(path.relative_to(demos_dir)))
    spec = {
        "version": 1,
        "id": demo_id,
        "name": "Sample demo",
        "goal": "show 2 steps",
        "createdAt": "2026-07-01T12:00:00+00:00",
        "viewport": {"width": 1024, "height": 768},
        "startUrl": "https://example.com",
        "aiAnnotations": None,
        "steps": [
            {
                "index": 0,
                "timestamp": 0,
                "pageUrl": "https://example.com/",
                "pageTitle": "Home",
                "interaction": {
                    "type": "click",
                    "target": {
                        "selector": "#start",
                        "tagName": "button",
                        "text": "Start",
                        "boundingRect": {"x": 100, "y": 200, "width": 120, "height": 40},
                    },
                    "hotspot": {"xPct": 50, "yPct": 50},
                },
                "screenshotPath": step_pngs[0],
            },
            {
                "index": 1,
                "timestamp": 1200,
                "pageUrl": "https://example.com/done",
                "pageTitle": "Done",
                "interaction": {
                    "type": "click",
                    "target": {
                        "selector": "#finish",
                        "tagName": "button",
                        "text": "Finish",
                        "boundingRect": {"x": 300, "y": 400, "width": 120, "height": 40},
                    },
                    "hotspot": {"xPct": 60, "yPct": 40},
                },
                "screenshotPath": step_pngs[1],
            },
        ],
    }
    demo_json = d / "demo.json"
    demo_json.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return demo_json


def _build_server(tmp_path: Path):
    from sunsponge.demo_forge import DemoForge
    from sunsponge.demo_mcp import _build_server

    demos_dir = tmp_path / "demos"
    forge = DemoForge(demos_dir=demos_dir, viewer_template=ROOT / "viewer" / "demo-viewer.html")
    return _build_server(forge), forge


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_seven_tools_registered(tmp_path: Path) -> None:
    server, _forge = _build_server(tmp_path)
    tools = await server.list_tools()
    names = sorted(t.name for t in tools)
    assert names == sorted(
        [
            "demo.delete",
            "demo.edit",
            "demo.export",
            "demo.list",
            "demo.record",
            "demo.status",
            "demo.stop",
        ]
    )


# ---------------------------------------------------------------------------
# demo.list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_empty(tmp_path: Path) -> None:
    server, _forge = _build_server(tmp_path)
    result = await server.call_tool("demo.list", {})
    payload = _payload(result)
    assert payload["count"] == 0
    assert payload["demos"] == []


@pytest.mark.asyncio
async def test_list_returns_disk_demos(tmp_path: Path) -> None:
    _write_sample_demo(tmp_path / "demos")
    server, _forge = _build_server(tmp_path)
    result = await server.call_tool("demo.list", {})
    payload = _payload(result)
    assert payload["count"] == 1
    item = payload["demos"][0]
    assert item["demoId"] == SAMPLE_DEMO_ID
    assert item["name"] == "Sample demo"
    assert item["stepCount"] == 2
    assert item["status"] == "recorded"
    assert item["createdAt"] == "2026-07-01T12:00:00+00:00"
    assert item["hasVoiceover"] is False


# ---------------------------------------------------------------------------
# demo.status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_for_recorded_demo(tmp_path: Path) -> None:
    _write_sample_demo(tmp_path / "demos")
    server, _forge = _build_server(tmp_path)
    result = await server.call_tool("demo.status", {"demo_id": SAMPLE_DEMO_ID})
    payload = _payload(result)
    assert payload["demoId"] == SAMPLE_DEMO_ID
    assert payload["status"] == "recorded"
    assert payload["totalSteps"] == 2
    assert payload["stepsCompleted"] == 0


@pytest.mark.asyncio
async def test_status_for_enriched_demo(tmp_path: Path) -> None:
    demo_json = _write_sample_demo(tmp_path / "demos")
    data = json.loads(demo_json.read_text(encoding="utf-8"))
    data["aiAnnotations"] = {
        "summary": "Two clicks, demo done.",
        "style": "smooth",
        "generatedAt": "2026-07-01T12:05:00+00:00",
        "animationTimeline": [
            {"stepIndex": 0, "action": "zoomTo", "target": "#start", "duration": 500}
        ],
    }
    demo_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    server, _forge = _build_server(tmp_path)
    result = await server.call_tool("demo.status", {"demo_id": SAMPLE_DEMO_ID})
    payload = _payload(result)
    assert payload["status"] == "enriched"
    assert payload["stepsCompleted"] == 2


@pytest.mark.asyncio
async def test_status_missing_demo_returns_error(tmp_path: Path) -> None:
    server, _forge = _build_server(tmp_path)
    with pytest.raises(Exception, match="not found"):
        await server.call_tool("demo.status", {"demo_id": "does-not-exist"})


# ---------------------------------------------------------------------------
# demo.edit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_changes_annotation(tmp_path: Path) -> None:
    _write_sample_demo(tmp_path / "demos")
    server, _forge = _build_server(tmp_path)
    result = await server.call_tool(
        "demo.edit",
        {
            "demo_id": SAMPLE_DEMO_ID,
            "step_index": 0,
            "annotation": "Updated narration for the start click.",
        },
    )
    payload = _payload(result)
    assert payload["ok"] is True
    assert payload["step"]["annotation"] == "Updated narration for the start click."
    # Persisted on disk.
    on_disk = json.loads((tmp_path / "demos" / SAMPLE_DEMO_ID / "demo.json").read_text())
    assert on_disk["steps"][0]["annotation"] == "Updated narration for the start click."


@pytest.mark.asyncio
async def test_edit_rejects_bad_step_index(tmp_path: Path) -> None:
    _write_sample_demo(tmp_path / "demos")
    server, _forge = _build_server(tmp_path)
    with pytest.raises(Exception, match="out of range"):
        await server.call_tool(
            "demo.edit",
            {"demo_id": SAMPLE_DEMO_ID, "step_index": 99, "annotation": "x"},
        )


@pytest.mark.asyncio
async def test_edit_requires_a_change(tmp_path: Path) -> None:
    _write_sample_demo(tmp_path / "demos")
    server, _forge = _build_server(tmp_path)
    with pytest.raises(Exception, match="nothing to edit"):
        await server.call_tool(
            "demo.edit", {"demo_id": SAMPLE_DEMO_ID, "step_index": 0}
        )


# ---------------------------------------------------------------------------
# demo.delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_demo(tmp_path: Path) -> None:
    _write_sample_demo(tmp_path / "demos")
    server, _forge = _build_server(tmp_path)
    result = await server.call_tool("demo.delete", {"demo_id": SAMPLE_DEMO_ID})
    payload = _payload(result)
    assert payload == {"ok": True, "demoId": SAMPLE_DEMO_ID}
    assert not (tmp_path / "demos" / SAMPLE_DEMO_ID).exists()


@pytest.mark.asyncio
async def test_delete_rejects_path_traversal(tmp_path: Path) -> None:
    server, _forge = _build_server(tmp_path)
    for bad in ("../etc", "..", "a/b", "a\\b"):
        with pytest.raises(Exception, match="invalid demo id"):
            await server.call_tool("demo.delete", {"demo_id": bad})


# ---------------------------------------------------------------------------
# demo.export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_produces_standalone_html(tmp_path: Path) -> None:
    _write_sample_demo(tmp_path / "demos")
    server, _forge = _build_server(tmp_path)
    result = await server.call_tool("demo.export", {"demo_id": SAMPLE_DEMO_ID})
    payload = _payload(result)
    out_path = Path(payload["path"])
    assert out_path.is_file()
    assert out_path.name == "export.html"
    html = out_path.read_text(encoding="utf-8")
    # The sample <script id="demo-data"> block must be replaced with the
    # inlined spec — and screenshots must be base64-embedded.
    assert '"id": "sample-onboarding"' not in html  # sample spec replaced
    assert 'data:image/png;base64,' in html
    # Spot-check the new spec is present.
    assert '"name": "Sample demo"' in html
    assert f'"id": "{SAMPLE_DEMO_ID}"' in html
    # Footer JS still wired up.
    assert 'window.__demoViewer' in html
    # CRITICAL: the embedded spec must be valid JSON. A previous version of
    # export_demo() used re.sub with a string replacement, which ate
    # JSON backslash escapes (e.g. screenshotPath "foo\\bar" became "foo\bar"
    # and failed to parse). The test catches that regression.
    embedded = _extract_embedded_spec(html)
    assert embedded is not None, "embedded spec not found in export.html"
    assert embedded["id"] == SAMPLE_DEMO_ID
    assert embedded["name"] == "Sample demo"
    assert len(embedded["steps"]) == 2
    for i, step in enumerate(embedded["steps"]):
        assert step.get("screenshotBase64"), f"step {i} missing inlined screenshot"
        # Decoded bytes must be a real PNG (magic header 0x89504E47).
        decoded = base64.b64decode(step["screenshotBase64"])
        assert decoded[:4] == b"\x89PNG", f"step {i} screenshot not a valid PNG"


@pytest.mark.asyncio
async def test_export_rejects_unknown_format(tmp_path: Path) -> None:
    _write_sample_demo(tmp_path / "demos")
    server, _forge = _build_server(tmp_path)
    with pytest.raises(Exception, match="unsupported export format"):
        await server.call_tool(
            "demo.export", {"demo_id": SAMPLE_DEMO_ID, "format": "mp4"}
        )


@pytest.mark.asyncio
async def test_export_missing_demo_returns_error(tmp_path: Path) -> None:
    server, _forge = _build_server(tmp_path)
    with pytest.raises(Exception, match="not found"):
        await server.call_tool("demo.export", {"demo_id": "nope"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _payload(call_result: Any) -> Any:
    """Unwrap a fastmcp CallToolResult to the first structured-content payload.

    FastMCP returns ``CallToolResult`` with a ``structured_content`` mapping
    that matches the function's return dict. We accept either shape so the
    tests survive upstream API tweaks.
    """
    structured = getattr(call_result, "structured_content", None)
    if structured is not None:
        if isinstance(structured, dict) and "result" in structured and len(structured) == 1:
            return structured["result"]
        return structured
    # Fallback — read the first text block.
    content = getattr(call_result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except Exception:
                return text
    raise AssertionError(f"could not unwrap MCP result: {call_result!r}")


_SCRIPT_BLOCK_FOR_TEST = re.compile(
    r'<script id="demo-data"[^>]*>\n(.*?)\n  </script>', re.DOTALL
)


def _extract_embedded_spec(html: str) -> dict[str, Any] | None:
    """Pull the JSON DemoSpec embedded in the export.html template."""
    m = _SCRIPT_BLOCK_FOR_TEST.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# smoke: server starts without crashing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_list_tools_under_heavy_load(tmp_path: Path) -> None:
    """Smoke test — register + call list repeatedly."""
    server, _forge = _build_server(tmp_path)
    for _ in range(5):
        tools = await server.list_tools()
        assert any(t.name == "demo.list" for t in tools)
        await server.call_tool("demo.list", {})
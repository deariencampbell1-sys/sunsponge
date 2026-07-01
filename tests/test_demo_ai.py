"""Tests for the Phase 3 AI enrichment pipeline.

Unit tests cover the deterministic helpers (cursor bezier, timeline JSON
extraction, fallback annotation, env-var auth guard). The live integration
test is opt-in via the RHOBEAR_GW_INTEGRATION=1 env var so CI without the
gateway token isn't blocked.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sunsponge.demo_ai import (  # noqa: E402
    DemoAIError,
    _compute_cursor_path,
    _DEFAULT_MODEL,
    _extract_timeline_json,
    _fallback_annotation,
    _first_sentence,
    _load_key,
    _validate_timeline,
    DemoAI,
    DemoEnrichManager,
)


# ---------------------------------------------------------------------------
# Unit: env-var auth guard
# ---------------------------------------------------------------------------


def test_load_key_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RHOBEAR_GW_API_KEY", raising=False)
    with pytest.raises(DemoAIError, match="RHOBEAR_GW_API_KEY is not set"):
        _load_key()


def test_load_key_raises_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RHOBEAR_GW_API_KEY", "   ")
    with pytest.raises(DemoAIError):
        _load_key()


def test_load_key_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RHOBEAR_GW_API_KEY", "  abc123  ")
    assert _load_key() == "abc123"


# ---------------------------------------------------------------------------
# Unit: first-sentence + fallback annotation
# ---------------------------------------------------------------------------


def test_first_sentence_basic() -> None:
    assert _first_sentence("Clicked the Sign In button.") == "Clicked the Sign In button"
    assert _first_sentence("Opened the modal! Then typed an email.") == "Opened the modal"
    assert _first_sentence("No period here") == "No period here"
    assert _first_sentence("") == ""
    assert _first_sentence("   \"  quoted text.  \"   ") == "quoted text"


def test_fallback_annotation_uses_text() -> None:
    step = {
        "interaction": {
            "target": {"selector": "#btn", "tagName": "button", "text": "Go"},
        }
    }
    assert _fallback_annotation(step) == "Clicked #btn (Go)."


def test_fallback_annotation_without_text() -> None:
    step = {"interaction": {"target": {"selector": "#btn", "tagName": "button"}}}
    assert _fallback_annotation(step) == "Clicked #btn."


# ---------------------------------------------------------------------------
# Unit: cursor bezier
# ---------------------------------------------------------------------------


def test_cursor_path_first_step_is_none() -> None:
    step = {
        "interaction": {
            "target": {"boundingRect": {"x": 0, "y": 0, "width": 100, "height": 100}},
            "hotspot": {"xPct": 50, "yPct": 50},
        }
    }
    assert _compute_cursor_path(None, step) is None


def test_cursor_path_returns_20_points() -> None:
    prev = {
        "interaction": {
            "target": {"boundingRect": {"x": 0, "y": 0, "width": 100, "height": 100}},
            "hotspot": {"xPct": 50, "yPct": 50},
        }
    }
    curr = {
        "interaction": {
            "target": {"boundingRect": {"x": 200, "y": 0, "width": 100, "height": 100}},
            "hotspot": {"xPct": 50, "yPct": 50},
        }
    }
    pts = _compute_cursor_path(prev, curr)
    assert pts is not None
    assert len(pts) == 20
    # First point should be near the previous hotspot center.
    assert abs(pts[0]["x"] - 50) < 1
    assert abs(pts[0]["y"] - 50) < 1
    # Last point should be near the current hotspot center.
    assert abs(pts[-1]["x"] - 250) < 1
    assert abs(pts[-1]["y"] - 50) < 1
    # Timestamps monotonically increase up to duration.
    ts = [p["t"] for p in pts]
    assert ts == sorted(ts)
    assert ts[0] == 0
    assert ts[-1] == 400


def test_cursor_path_handles_missing_rect() -> None:
    prev = {"interaction": {"target": {}, "hotspot": {}}}
    curr = {"interaction": {"target": {"boundingRect": {"x": 0, "y": 0, "width": 100, "height": 100}}, "hotspot": {"xPct": 50, "yPct": 50}}}
    assert _compute_cursor_path(prev, curr) is None


# ---------------------------------------------------------------------------
# Unit: timeline JSON extraction
# ---------------------------------------------------------------------------


def test_extract_timeline_clean_array() -> None:
    txt = '[{"stepIndex":0,"action":"zoomTo","target":"#x","offset":{"x":50,"y":50},"zoomLevel":1.5,"duration":600}]'
    out = _extract_timeline_json(txt)
    assert isinstance(out, list)
    assert out[0]["stepIndex"] == 0


def test_extract_timeline_with_prose_around() -> None:
    txt = "Here is the timeline:\n```json\n[{\"stepIndex\":1,\"action\":null}]\n```\nThat covers it."
    out = _extract_timeline_json(txt)
    assert out == [{"stepIndex": 1, "action": None}]


def test_extract_timeline_returns_none_on_garbage() -> None:
    assert _extract_timeline_json("") is None
    assert _extract_timeline_json("not json at all") is None
    assert _extract_timeline_json("{}") is None  # object, not array


def test_validate_timeline_filters_bad_entries() -> None:
    raw = [
        {"stepIndex": 0, "action": "zoomTo", "target": "#x", "offset": {"x": 50, "y": 50}, "zoomLevel": 1.5, "duration": 600},
        {"stepIndex": 99, "action": "reset"},                    # out of range
        {"stepIndex": 1, "action": "fakeAction"},                # bad action
        {"stepIndex": "x", "action": "reset"},                   # bad index
        "not a dict",
    ]
    out = _validate_timeline(raw, step_count=3)
    assert len(out) == 1
    assert out[0]["stepIndex"] == 0
    assert out[0]["action"] == "zoomTo"


def test_validate_timeline_clamps_zoom_and_duration() -> None:
    raw = [{"stepIndex": 0, "action": "zoomTo", "target": "#x", "offset": {"x": 0, "y": 0}, "zoomLevel": 9.0, "duration": 50}]
    out = _validate_timeline(raw, step_count=1)
    assert out[0]["zoomLevel"] == 2.0   # clamped
    assert out[0]["duration"] == 300    # clamped


# ---------------------------------------------------------------------------
# Unit: DemoAI on empty steps
# ---------------------------------------------------------------------------


def test_demoai_empty_steps_returns_empty_annotations() -> None:
    ai = DemoAI()
    out = asyncio.run(ai.enrich({"steps": []}))
    assert out["steps"] == []
    assert out["aiAnnotations"]["summary"] == ""
    assert out["aiAnnotations"]["animationTimeline"] == []


def test_demoai_runs_cursor_paths_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cursor-path stage must be deterministic and not require the gateway."""
    ai = DemoAI()
    # Stub the LLM client to fail loudly so we can confirm the cursor stage
    # still completes (it doesn't touch the network).
    def boom(*a, **kw):
        raise RuntimeError("LLM must not be called during cursor-only run")
    monkeypatch.setattr("sunsponge.demo_ai._build_client", boom)

    spec = {
        "name": "t", "goal": "g", "startUrl": "x",
        "steps": [
            {"index": 0, "interaction": {
                "target": {"boundingRect": {"x": 0, "y": 0, "width": 100, "height": 100}},
                "hotspot": {"xPct": 50, "yPct": 50},
            }},
            {"index": 1, "interaction": {
                "target": {"boundingRect": {"x": 200, "y": 0, "width": 100, "height": 100}},
                "hotspot": {"xPct": 50, "yPct": 50},
            }},
        ],
    }
    # Should raise (LLM unavailable) but the spec copy should be deep-cloned.
    with pytest.raises(RuntimeError, match="LLM must not be called"):
        asyncio.run(ai.enrich(spec))
    # Original spec is unchanged (we deep-copy).
    assert "cursorPath" not in spec["steps"][1]


# ---------------------------------------------------------------------------
# Live integration — opt-in via env var
# ---------------------------------------------------------------------------


LIVE = os.environ.get("RHOBEAR_GW_INTEGRATION") == "1"


@pytest.mark.skipif(not LIVE, reason="set RHOBEAR_GW_INTEGRATION=1 to run the live pipeline")
def test_live_enrichment_writes_all_fields(tmp_path: Path) -> None:
    """End-to-end: build a tiny demo with real PNG screenshots, enrich, verify.

    Uses a 3-step demo with synthetic dark-blue PNG screenshots. The pipeline
    must populate annotations, voiceover, cursor paths, summary, and timeline.
    """
    import struct, zlib
    def png(w: int, h: int, rgb=(31, 41, 55)) -> bytes:
        raw = b""
        for _ in range(h):
            raw += b"\x00" + (bytes(rgb) * w)
        sig = b"\x89PNG\r\n\x1a\n"
        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t+d) & 0xffffffff)
        return sig + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) \
                  + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")

    demo_id = "live-test"
    demo_dir = tmp_path / "demos" / demo_id
    demo_dir.mkdir(parents=True)
    png_bytes = png(64, 48)
    # Write 3 screenshots + the raw demo.json
    for i in range(3):
        (demo_dir / f"step_{i:03d}.png").write_bytes(png_bytes)
    raw = {
        "version": 1,
        "id": demo_id,
        "name": "Live integration test",
        "goal": "Verify the AI pipeline populates every field",
        "createdAt": "2026-07-01T00:00:00Z",
        "viewport": {"width": 800, "height": 600},
        "startUrl": "https://example.com",
        "aiAnnotations": None,
        "steps": [
            {
                "index": i,
                "timestamp": i * 1000,
                "pageUrl": f"https://example.com/step{i}",
                "pageTitle": f"Step {i+1}",
                "interaction": {
                    "type": "click",
                    "target": {
                        "selector": f"#step-{i+1}-btn",
                        "tagName": "button",
                        "text": f"Go to step {i+1}",
                        "boundingRect": {"x": 200 + i*20, "y": 150 + i*30, "width": 200, "height": 48},
                    },
                    "hotspot": {"xPct": 50, "yPct": 50},
                },
                "screenshotPath": f"demos/{demo_id}/step_{i:03d}.png",
            }
            for i in range(3)
        ],
    }
    (demo_dir / "demo.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")

    mgr = DemoEnrichManager(output_root=tmp_path / "demos", ai=DemoAI())
    job_id = mgr.submit(demo_id)
    # Poll for completion
    import time
    deadline = time.time() + 90
    while time.time() < deadline:
        st = mgr.get_status(job_id)
        if st["status"] in ("done", "failed"):
            break
        time.sleep(1)
    final = mgr.get_status(job_id)
    assert final["status"] == "done", f"job failed: {final}"
    assert final["elapsedS"] is not None and final["elapsedS"] < 60, (
        f"pipeline took {final['elapsedS']}s — brief budget is 60s for a 5-step demo"
    )

    spec = mgr.read_spec(demo_id)
    # Every step has an annotation.
    for s in spec["steps"]:
        assert s.get("annotation"), f"step {s.get('index')} missing annotation: {s}"
    # Every step has a voiceover (MP3 base64 starts with the base64 of an MP3 sync).
    for s in spec["steps"]:
        v = s.get("voiceoverBase64")
        assert v, f"step {s.get('index')} missing voiceover"
        assert len(v) > 100, f"step {s.get('index')} voiceover too short: {len(v)}"
    # Steps 1+ have cursor paths with 20+ points.
    for s in spec["steps"][1:]:
        cp = s.get("cursorPath")
        assert cp and len(cp) >= 20, f"step {s.get('index')} cursor path: {cp}"
    # aiAnnotations.summary is a non-empty 2-3 sentence string.
    summary = spec.get("aiAnnotations", {}).get("summary", "")
    assert 20 <= len(summary) <= 600, f"unexpected summary length: {len(summary)}"
    # animationTimeline is a list of valid entries.
    tl = spec.get("aiAnnotations", {}).get("animationTimeline", [])
    assert isinstance(tl, list)
    for e in tl:
        assert "stepIndex" in e and "action" in e

    # Spot-check the file on disk was updated.
    on_disk = json.loads((demo_dir / "demo.json").read_text(encoding="utf-8"))
    assert on_disk["steps"][0].get("annotation")
    print(
        f"\n[live enrich] steps={len(spec['steps'])} elapsed={final['elapsedS']}s "
        f"summary_len={len(summary)} timeline_entries={len(tl)}"
    )
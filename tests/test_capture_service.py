from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sunsponge import app as sunsponge_app
from sunsponge.capture_service import (
    RestedCaptureError,
    build_capture_plan,
    normalize_url,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE_MANIFEST = FIXTURES / "pathway-manifest-sample.md"


def test_normalize_url_adds_https():
    assert normalize_url("example.com/page") == "https://example.com/page"


def test_normalize_url_rejects_unsupported_scheme():
    try:
        normalize_url("ftp://example.com/file.txt")
    except RestedCaptureError as exc:
        assert "unsupported URL" in str(exc)
    else:
        raise AssertionError("expected unsupported URL")


def test_normalize_url_accepts_local_html(tmp_path):
    # The built HTML the user points at lives locally — file:// must work.
    page = tmp_path / "index.html"
    page.write_text("<h1>Local</h1>", encoding="utf-8")

    assert normalize_url(str(page)).startswith("file://")


def test_build_capture_plan_requires_a_map():
    # Captur'd is map-driven only — no map, no plan (and definitely no URLs).
    try:
        build_capture_plan({"viewports": ["desktop"], "schemes": ["light"]})
    except RestedCaptureError as exc:
        assert "pathway map" in str(exc)
    else:
        raise AssertionError("expected a pathway-map-required error")


def test_build_capture_plan_expands_state_matrix():
    # 3 pathways in the fixture × 2 viewports × 2 schemes = 12 capture targets.
    urls, targets, settings = build_capture_plan({
        "manifest_path": str(SAMPLE_MANIFEST),
        "base_url": "https://example.com",
        "viewports": ["desktop", "mobile"],
        "schemes": ["light", "dark"],
        "format": "png",
    })

    assert settings["map"] is True
    assert settings["discovery"]["mode"] == "map"
    assert settings["discovery"]["pathway_count"] == 3
    assert len(targets) == 12
    assert settings["capture_count"] == 12
    assert {t.viewport_id for t in targets} == {"desktop", "mobile"}
    assert {t.scheme for t in targets} == {"light", "dark"}
    # Every target carries its pathway identity in the state id.
    assert all(t.pathway_id for t in targets)


def test_build_capture_plan_accepts_pasted_manifest():
    # The primary desktop path: the map arrives as pasted text, not a file.
    raw = SAMPLE_MANIFEST.read_text(encoding="utf-8")
    urls, targets, settings = build_capture_plan({
        "pathway_manifest": raw,
        "base_url": "https://example.com",
        "viewports": ["desktop"],
        "schemes": ["light"],
    })

    assert settings["discovery"]["mode"] == "map"
    assert len(targets) == 3


def test_selector_column_flows_to_capture_target():
    # A `selector` column in the map must reach the CaptureTarget so the engine
    # can click it to the rested state (the difference between distinct shots and
    # N copies of the same page).
    manifest = (
        "## Pathways Table\n\n"
        "| id | selector | trigger | status |\n|---|---|---|---|\n"
        "| default-view |  | initial load | WIRED |\n"
        "| ai-open | [data-action=\"ai-toggle\"] | click AI | WIRED |\n"
    )
    _urls, targets, _settings = build_capture_plan({
        "pathway_manifest": manifest,
        "base_url": "https://example.com",
        "viewports": ["desktop"],
        "schemes": ["light"],
    })
    by_id = {t.pathway_id: t for t in targets}
    assert by_id["ai-open"].trigger_selector == '[data-action="ai-toggle"]'
    assert by_id["default-view"].trigger_selector == ""


def test_api_registers_capture_routes():
    paths = {route.path for route in sunsponge_app.app.routes if hasattr(route, "path")}
    assert "/api/rested-captures/jobs" in paths
    assert "/api/rested-captures/jobs/{job_id}" in paths
    assert "/api/rested-captures/jobs/{job_id}/download" in paths


def test_api_can_start_capture_job(monkeypatch):
    class FakeCaptureManager:
        def start(self, payload):
            return {"ok": True, "job_id": "fake-job", "payload": payload}

    monkeypatch.setattr(sunsponge_app, "_CAPTURE_MANAGER", FakeCaptureManager())
    raw = SAMPLE_MANIFEST.read_text(encoding="utf-8")
    with TestClient(sunsponge_app.app) as client:
        response = client.post(
            "/api/rested-captures/jobs",
            json={"pathway_manifest": raw, "base_url": "https://example.com"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == "fake-job"

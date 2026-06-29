"""Regression tests for the release blockers called out in
``reports/pressure-sunsponge.md``, updated for the map-only desktop model:

1. Bad ``manifest_path`` / ``map_path`` must return HTTP 400 (not 500 + traceback)
   and must never leak the offending server path.
2. A request with no pathway map must be rejected with HTTP 400 before queueing
   (Captur'd is map-driven only — there is no URL/sitemap/crawl input).
3. Each ``results[]`` row must carry ``pathway_id`` + ``pathway_status`` from the
   map that drove the run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sunsponge import app as sunsponge_app
from sunsponge.capture_service import (
    RestedCaptureError,
    RestedCaptureManager,
    build_capture_plan,
)
from sunsponge.pathway_map import load_pathway_map

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# Fix 1 — bad manifest_path / map_path
# ---------------------------------------------------------------------------


def test_load_pathway_map_rejects_missing_manifest():
    """Missing manifest_path must raise a typed input error naming the input
    (not a raw FileNotFoundError) and must NOT include the offending path."""
    with pytest.raises(RestedCaptureError) as exc_info:
        load_pathway_map(manifest_path="/nope/does-not-exist.md")

    message = str(exc_info.value)
    assert "manifest_path" in message
    # The error must not leak the absolute server path.
    assert "/nope/does-not-exist.md" not in message


def test_load_pathway_map_rejects_missing_map():
    with pytest.raises(RestedCaptureError) as exc_info:
        load_pathway_map(map_path="/nope/does-not-exist.json")

    message = str(exc_info.value)
    assert "map_path" in message
    assert "/nope/does-not-exist.json" not in message


def test_build_capture_plan_rejects_missing_manifest():
    with pytest.raises(RestedCaptureError) as exc_info:
        build_capture_plan({
            "manifest_path": "/nope/manifest.md",
            "base_url": "https://example.com",
        })

    message = str(exc_info.value)
    assert "manifest_path" in message
    assert "/nope/manifest.md" not in message


def test_build_capture_plan_rejects_missing_map():
    with pytest.raises(RestedCaptureError) as exc_info:
        build_capture_plan({
            "map_path": "/nope/map.json",
            "base_url": "https://example.com",
        })

    message = str(exc_info.value)
    assert "map_path" in message
    assert "/nope/map.json" not in message


def test_api_returns_400_for_bad_manifest_path(caplog):
    """End-to-end: POST a bad manifest_path, get 400, no traceback in the log.

    Captur'd is a local desktop tool — pointing it at a local map file is a
    supported (CLI-convenience) input; a *missing* one is a clean 400, not a 500.
    """
    import logging

    caplog.set_level(logging.ERROR, logger="sunsponge.app")
    caplog.set_level(logging.ERROR, logger="uvicorn.error")

    with TestClient(sunsponge_app.app) as client:
        response = client.post(
            "/api/rested-captures/jobs",
            json={"manifest_path": "/nope/manifest.md", "base_url": "https://example.com"},
        )

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["ok"] is False
    assert "manifest_path" in body["error"]
    # No absolute server path leaked in the body.
    assert "/nope/manifest.md" not in body["error"]

    # And no raw Python traceback for FileNotFoundError or anything related.
    leaked = [
        record for record in caplog.records
        if "FileNotFoundError" in record.getMessage()
        or "Traceback (most recent call last)" in record.getMessage()
        or "capture job failed" in record.getMessage()
    ]
    assert leaked == [], f"unexpected error log records: {[r.getMessage() for r in leaked]}"


def test_api_returns_400_for_bad_map_path(caplog):
    import logging

    caplog.set_level(logging.ERROR, logger="sunsponge.app")
    caplog.set_level(logging.ERROR, logger="uvicorn.error")

    with TestClient(sunsponge_app.app) as client:
        response = client.post(
            "/api/rested-captures/jobs",
            json={"map_path": "/nope/map.json", "base_url": "https://example.com"},
        )

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["ok"] is False
    assert "map_path" in body["error"]
    assert "/nope/map.json" not in body["error"]

    leaked = [
        record for record in caplog.records
        if "FileNotFoundError" in record.getMessage()
        or "Traceback (most recent call last)" in record.getMessage()
        or "capture job failed" in record.getMessage()
    ]
    assert leaked == [], f"unexpected error log records: {[r.getMessage() for r in leaked]}"


def test_api_returns_400_for_directory_as_manifest_path(caplog):
    """A path that exists but is a directory must also be rejected (not 500)."""
    import logging

    caplog.set_level(logging.ERROR, logger="sunsponge.app")

    with TestClient(sunsponge_app.app) as client:
        response = client.post(
            "/api/rested-captures/jobs",
            json={"manifest_path": str(FIXTURES), "base_url": "https://example.com"},
        )

    assert response.status_code == 400, response.text
    body = response.json()
    assert "manifest_path" in body["error"]


# ---------------------------------------------------------------------------
# Fix 2 — a request with no map is rejected (no URL/sitemap/crawl fallback)
# ---------------------------------------------------------------------------


def test_build_capture_plan_rejects_no_map():
    with pytest.raises(RestedCaptureError) as exc_info:
        build_capture_plan({"viewports": ["desktop"], "schemes": ["light"]})

    assert "pathway map" in str(exc_info.value)


def test_api_returns_400_when_no_map():
    with TestClient(sunsponge_app.app) as client:
        response = client.post("/api/rested-captures/jobs", json={"base_url": "https://example.com"})

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["ok"] is False
    assert "pathway map" in body["error"]


# ---------------------------------------------------------------------------
# Fix 3 — pathway_id / pathway_status in results[]
# ---------------------------------------------------------------------------


def test_capture_plan_targets_have_pathway_metadata_for_map_run():
    """Map-mode targets carry pathway_id + pathway_status from the manifest."""
    _urls, targets, settings = build_capture_plan({
        "manifest_path": str(FIXTURES / "pathway-manifest-sample.md"),
        "base_url": "https://example.com",
        "viewports": ["desktop"],
        "schemes": ["light"],
    })

    assert settings["map"] is True
    assert len(targets) == 3
    by_pid = {t.pathway_id: t for t in targets}
    assert by_pid["capture-start"].pathway_status == "WIRED"
    assert by_pid["catalog-search"].pathway_status == "UNWIRED"


def test_api_results_row_has_pathway_fields_for_map_run(monkeypatch, tmp_path):
    """End-to-end: a map-mode job's results[] row carries pathway_id +
    pathway_status (non-null) alongside the existing capture fields."""
    from sunsponge import capture_service

    fake_map_results = [
        {
            "url": "https://example.com/",
            "state_id": "capture-start-wired-desktop-light",
            "viewport": "desktop",
            "scheme": "light",
            "width": 1440,
            "height": 1000,
            "pathway_id": "capture-start",
            "pathway_status": "WIRED",
            "status": "ok",
            "file": "001-capture-start-wired-desktop-light.png",
            "bytes": 1234,
            "attempts": 1,
            "elapsed_ms": 100,
        },
    ]

    def fake_run_capture(targets, settings, shots_dir_arg, progress):
        for result in fake_map_results:
            progress(result)
        return fake_map_results

    monkeypatch.setattr(capture_service, "run_capture", fake_run_capture)

    manager = RestedCaptureManager(root_dir=tmp_path / "jobs")
    monkeypatch.setattr(sunsponge_app, "_CAPTURE_MANAGER", manager)

    with TestClient(sunsponge_app.app) as client:
        map_response = client.post(
            "/api/rested-captures/jobs",
            json={
                "manifest_path": str(FIXTURES / "pathway-manifest-sample.md"),
                "base_url": "https://example.com",
            },
        )

    assert map_response.status_code == 200, map_response.text
    map_job = map_response.json()

    # Wait for the background thread to finish, then re-fetch.
    import time

    map_final = map_job
    for _ in range(100):
        time.sleep(0.05)
        with TestClient(sunsponge_app.app) as client:
            map_final = client.get(f"/api/rested-captures/jobs/{map_job['job_id']}").json()
        if map_final["status"] != "running":
            break

    assert map_final["status"] in {"done", "done_with_errors"}
    assert map_final["results"], "map job should have at least one result row"

    map_row = map_final["results"][0]
    assert map_row["pathway_id"] == "capture-start"
    assert map_row["pathway_status"] == "WIRED"
    # Existing fields must still be there.
    for field in ("url", "viewport", "scheme", "state_id", "file", "status", "bytes", "attempts", "elapsed_ms"):
        assert field in map_row, f"missing existing field {field!r} in map row"


# ---------------------------------------------------------------------------
# Smoke: still works with a real (small) fixture-driven plan
# ---------------------------------------------------------------------------


def test_parse_manifest_sample_round_trip_works():
    """Sanity: the happy-path parse still returns the expected pathways."""
    parsed = load_pathway_map(manifest_path=str(FIXTURES / "pathway-manifest-sample.md"))
    ids = [p["id"] for p in parsed["pathways"]]
    assert ids == ["capture-start", "capture-job-poll", "catalog-search"]


def test_sample_manifest_is_readable():
    parsed = load_pathway_map(manifest_path=str(FIXTURES / "pathway-manifest-sample.md"))
    assert parsed["pathways"], "sample manifest should still produce pathways"


def test_sample_verifier_map_is_readable():
    parsed = load_pathway_map(map_path=str(FIXTURES / "sample-verifier-map.json"))
    assert parsed["pathways"], "sample verifier map should still produce pathways"

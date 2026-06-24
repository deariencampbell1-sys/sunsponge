"""Regression tests for the three release blockers called out in
``reports/pressure-sunsponge.md``:

1. Bad ``manifest_path`` / ``map_path`` must return HTTP 400 (not 500 + traceback).
2. ``{"urls": ["not-a-url"]}`` must be rejected with HTTP 400 before queueing.
3. Each ``results[]`` row must carry ``pathway_id`` + ``pathway_status``
   (both ``null`` for plain-URL captures).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    """End-to-end: POST a bad manifest_path, get 400, no traceback in the log."""
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
# Fix 2 — URL input validation
# ---------------------------------------------------------------------------


def test_build_capture_plan_rejects_not_a_url():
    with pytest.raises(RestedCaptureError) as exc_info:
        build_capture_plan({"urls": ["not-a-url"]})

    message = str(exc_info.value)
    assert "not-a-url" in message
    assert "invalid URL" in message


def test_build_capture_plan_still_accepts_bare_host():
    """Existing behavior: bare hosts (e.g. 'example.com') are coerced to https."""
    urls, targets, _settings = build_capture_plan({
        "urls": ["example.com"],
        "viewports": ["desktop"],
        "schemes": ["light"],
    })
    assert urls == ["https://example.com/"]
    assert len(targets) == 1


def test_build_capture_plan_still_accepts_full_url():
    urls, targets, _settings = build_capture_plan({
        "urls": ["https://example.com/"],
        "viewports": ["desktop"],
        "schemes": ["light"],
    })
    assert urls == ["https://example.com/"]


def test_build_capture_plan_empty_list_still_uses_existing_error():
    """The empty-list behavior must not change."""
    with pytest.raises(RestedCaptureError) as exc_info:
        build_capture_plan({"urls": [""]})

    assert str(exc_info.value) == "add at least one URL"


def test_build_capture_plan_rejects_only_empty_string():
    with pytest.raises(RestedCaptureError) as exc_info:
        build_capture_plan({"urls": ["", "  "]})

    assert str(exc_info.value) == "add at least one URL"


def test_api_returns_400_for_not_a_url():
    with TestClient(sunsponge_app.app) as client:
        response = client.post(
            "/api/rested-captures/jobs",
            json={"urls": ["not-a-url"]},
        )

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["ok"] is False
    assert "not-a-url" in body["error"]


def test_api_still_queues_valid_url(monkeypatch):
    """A valid URL must still produce a queued job (the validation must not
    become a blanket reject)."""

    class FakeManager:
        def __init__(self) -> None:
            self.last_payload: dict[str, Any] | None = None

        def start(self, payload):
            self.last_payload = payload
            return {"ok": True, "job_id": "fake-job", "status": "queued", "payload": payload}

    fake = FakeManager()
    monkeypatch.setattr(sunsponge_app, "_CAPTURE_MANAGER", fake)

    with TestClient(sunsponge_app.app) as client:
        response = client.post(
            "/api/rested-captures/jobs",
            json={"urls": ["https://example.com/"]},
        )

    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == "fake-job"
    # The exact payload has many default fields populated; we only care that
    # the URL survived normalization and the validation step let it through.
    assert fake.last_payload is not None
    assert fake.last_payload.get("urls") == ["https://example.com/"]


def test_api_still_rejects_empty_list():
    with TestClient(sunsponge_app.app) as client:
        response = client.post(
            "/api/rested-captures/jobs",
            json={"urls": [""]},
        )

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "add at least one URL"


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


def test_capture_plan_targets_have_empty_pathway_for_plain_url_run():
    _urls, targets, _settings = build_capture_plan({
        "urls": ["https://example.com/"],
        "viewports": ["desktop"],
        "schemes": ["light"],
    })

    assert len(targets) == 1
    assert targets[0].pathway_id == ""
    assert targets[0].pathway_status == ""


def test_api_results_row_has_pathway_fields_for_map_run(monkeypatch, tmp_path):
    """End-to-end: a map-mode job's results[] row carries pathway_id +
    pathway_status (non-null), and a plain-URL job's results[] row carries
    both as null."""
    from sunsponge import capture_service

    # Capture into a known dir so the job is fully self-contained.
    work_dir = tmp_path / "cap"
    shots_dir = work_dir / "shots"
    shots_dir.mkdir(parents=True)

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
    fake_plain_results = [
        {
            "url": "https://example.com/",
            "state_id": "desktop-light",
            "viewport": "desktop",
            "scheme": "light",
            "width": 1440,
            "height": 1000,
            "pathway_id": None,
            "pathway_status": None,
            "status": "ok",
            "file": "001-example-com-desktop-light.png",
            "bytes": 5678,
            "attempts": 1,
            "elapsed_ms": 200,
        },
    ]

    def fake_run_capture(targets, settings, shots_dir_arg, progress):
        for result in (fake_map_results if settings.get("map") else fake_plain_results):
            progress(result)
        return fake_map_results if settings.get("map") else fake_plain_results

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
        plain_response = client.post(
            "/api/rested-captures/jobs",
            json={"urls": ["https://example.com/"]},
        )

    assert map_response.status_code == 200, map_response.text
    assert plain_response.status_code == 200, plain_response.text

    map_job = map_response.json()
    plain_job = plain_response.json()
    # Wait for the background thread to finish, then re-fetch.
    import time

    for _ in range(100):
        time.sleep(0.05)
        with TestClient(sunsponge_app.app) as client:
            map_final = client.get(f"/api/rested-captures/jobs/{map_job['job_id']}").json()
            plain_final = client.get(f"/api/rested-captures/jobs/{plain_job['job_id']}").json()
        if map_final["status"] != "running" and plain_final["status"] != "running":
            break

    assert map_final["status"] in {"done", "done_with_errors"}
    assert plain_final["status"] in {"done", "done_with_errors"}
    assert map_final["results"], "map job should have at least one result row"
    assert plain_final["results"], "plain-URL job should have at least one result row"

    map_row = map_final["results"][0]
    assert map_row["pathway_id"] == "capture-start"
    assert map_row["pathway_status"] == "WIRED"
    # Existing fields must still be there.
    for field in ("url", "viewport", "scheme", "state_id", "file", "status", "bytes", "attempts", "elapsed_ms"):
        assert field in map_row, f"missing existing field {field!r} in map row"

    plain_row = plain_final["results"][0]
    assert plain_row["pathway_id"] is None
    assert plain_row["pathway_status"] is None
    for field in ("url", "viewport", "scheme", "state_id", "file", "status", "bytes", "attempts", "elapsed_ms"):
        assert field in plain_row, f"missing existing field {field!r} in plain-URL row"


# ---------------------------------------------------------------------------
# Smoke: still works with a real (small) fixture-driven plan
# ---------------------------------------------------------------------------


def test_parse_manifest_sample_round_trip_works():
    """Sanity: the happy-path parse still returns the expected pathways."""
    parsed = load_pathway_map(manifest_path=str(FIXTURES / "pathway-manifest-sample.md"))
    ids = [p["id"] for p in parsed["pathways"]]
    assert ids == ["capture-start", "capture-job-poll", "catalog-search"]


# Smoke test that the test JSON file is well-formed enough for the API test
# above; if this fails the API test is meaningless.
def test_sample_manifest_is_readable():
    parsed = load_pathway_map(manifest_path=str(FIXTURES / "pathway-manifest-sample.md"))
    assert parsed["pathways"], "sample manifest should still produce pathways"


def test_sample_verifier_map_is_readable():
    parsed = load_pathway_map(map_path=str(FIXTURES / "sample-verifier-map.json"))
    assert parsed["pathways"], "sample verifier map should still produce pathways"

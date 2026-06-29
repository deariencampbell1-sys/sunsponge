"""Tests for the agent-control (``/v1``) capture API — the Captur'd slice of
the Family API Contract:

* ``POST /v1/capture {pathway_map, base_url?, viewports?, color_schemes?,
  workspace_id}`` → ``{ok, data:{job_id}}``. Captur'd is map-driven only — a
  pasted pathway map is required and there is no URL/sitemap input.
* ``GET  /v1/capture/{job_id}`` → ``{ok, data:{status, shots:[{url, viewport,
  scheme, image_ref}]}}`` — ``image_ref`` is a storage HANDLE (a served URL),
  never inline bytes.
* ``GET  /v1/capture/{job_id}/shots/{file}`` — fetch one shot by handle.

Envelope is ``{ok, data}`` on success and ``{ok, error}`` on failure; the
``/v1`` routes are gated by ``Authorization: Bearer $RHOBEAR_SERVICE_TOKEN``.
These wrap the SAME engine as the ``/api`` routes; no Playwright is needed
because ``run_capture`` is stubbed.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sunsponge import app as sunsponge_app
from sunsponge import capture_service
from sunsponge.capture_service import RestedCaptureManager

TOKEN = "test-service-token"

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# A one-pathway map that resolves to the built-HTML root, so the shot matrix is
# exactly viewports × schemes for a single page.
MAP_TEXT = (FIXTURES / "single-pathway-manifest.md").read_text(encoding="utf-8")
BASE_URL = "https://example.com"


def _capture_body(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"pathway_map": MAP_TEXT, "base_url": BASE_URL}
    body.update(extra)
    return body


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeManager:
    """Records the payload and returns a queued job without touching Playwright."""

    def __init__(self) -> None:
        self.last_payload: dict[str, Any] | None = None

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.last_payload = payload
        return {"ok": True, "job_id": "fake-job", "status": "queued"}


def _ok_capture_run(targets: list[Any], settings: dict[str, Any], shots_dir: Path, progress) -> list[dict[str, Any]]:
    """Mirror the engine: one ok result (with a real file on disk) per target."""
    results: list[dict[str, Any]] = []
    for index, target in enumerate(targets, start=1):
        file_name = f"{index:03d}-shot-{target.viewport_id}-{target.scheme}.png"
        path = shots_dir / file_name
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes" * 8)
        result = {
            "url": target.url,
            "state_id": target.state_id,
            "viewport": target.viewport_id,
            "scheme": target.scheme,
            "width": target.width,
            "height": target.height,
            "pathway_id": target.pathway_id or None,
            "pathway_status": target.pathway_status or None,
            "status": "ok",
            "file": file_name,
            "bytes": path.stat().st_size,
            "attempts": 1,
            "elapsed_ms": 5,
        }
        progress(result)
        results.append(result)
    return results


@pytest.fixture
def configured_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("RHOBEAR_SERVICE_TOKEN", TOKEN)
    return TOKEN


@pytest.fixture
def unset_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RHOBEAR_SERVICE_TOKEN", raising=False)


@pytest.fixture
def fake_manager(monkeypatch: pytest.MonkeyPatch) -> FakeManager:
    fake = FakeManager()
    monkeypatch.setattr(sunsponge_app, "_CAPTURE_MANAGER", fake)
    return fake


@pytest.fixture
def real_manager(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> RestedCaptureManager:
    monkeypatch.setattr(capture_service, "run_capture", _ok_capture_run)
    manager = RestedCaptureManager(root_dir=tmp_path / "jobs")
    monkeypatch.setattr(sunsponge_app, "_CAPTURE_MANAGER", manager)
    return manager


def _wait_done(client: TestClient, job_id: str, timeout: float = 5.0) -> dict[str, Any]:
    """Poll the contract endpoint until the job leaves running/queued."""
    deadline = time.perf_counter() + timeout
    last: dict[str, Any] = {}
    while time.perf_counter() < deadline:
        resp = client.get(f"/v1/capture/{job_id}", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200, resp.text
        last = resp.json()["data"]
        if last["status"] not in {"queued", "running"}:
            return last
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never finished: {last}")


# ---------------------------------------------------------------------------
# Auth + envelope
# ---------------------------------------------------------------------------


def test_routes_are_registered():
    paths = {route.path for route in sunsponge_app.app.routes if hasattr(route, "path")}
    assert "/v1/capture" in paths
    assert "/v1/capture/{job_id}" in paths
    assert "/v1/capture/{job_id}/shots/{file}" in paths


def test_post_returns_503_when_token_not_configured(unset_token, fake_manager):
    with TestClient(sunsponge_app.app) as client:
        resp = client.post(
            "/v1/capture",
            json=_capture_body(workspace_id="ws"),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "RHOBEAR_SERVICE_TOKEN" in body["error"]
    assert fake_manager.last_payload is None  # never reached the engine


def test_post_rejects_missing_bearer_token(configured_token, fake_manager):
    with TestClient(sunsponge_app.app) as client:
        resp = client.post("/v1/capture", json=_capture_body(workspace_id="ws"))
    assert resp.status_code == 401
    assert resp.json() == {"ok": False, "error": "invalid or missing service token"}


def test_post_rejects_wrong_token(configured_token, fake_manager):
    with TestClient(sunsponge_app.app) as client:
        resp = client.post(
            "/v1/capture",
            json=_capture_body(workspace_id="ws"),
            headers={"Authorization": "Bearer nope"},
        )
    assert resp.status_code == 401
    assert resp.json()["ok"] is False


def test_get_rejects_missing_token(configured_token, real_manager):
    with TestClient(sunsponge_app.app) as client:
        resp = client.get("/v1/capture/anything")
    assert resp.status_code == 401


def test_post_accepts_correct_token_and_envelopes(configured_token, fake_manager):
    with TestClient(sunsponge_app.app) as client:
        resp = client.post(
            "/v1/capture",
            json=_capture_body(
                workspace_id="ws-1",
                viewports=["desktop", "mobile"],
                color_schemes=["light", "dark"],
            ),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "data": {"job_id": "fake-job", "status": "queued"}}
    # The contract fields were translated into the engine payload.
    payload = fake_manager.last_payload
    assert payload is not None
    assert payload["workspace_id"] == "ws-1"
    # pathway_map (contract) -> pathway_manifest (engine); base_url carried through.
    # The envelope trims surrounding whitespace before handing off to the engine.
    assert payload["pathway_manifest"] == MAP_TEXT.strip()
    assert payload["base_url"] == BASE_URL
    assert "urls" not in payload and "sitemap_url" not in payload
    assert payload["viewports"] == ["desktop", "mobile"]
    # color_schemes (contract) -> schemes (engine)
    assert payload["schemes"] == ["light", "dark"]


# ---------------------------------------------------------------------------
# Input validation -> 400 {ok, error}
# ---------------------------------------------------------------------------


def test_post_400_when_workspace_id_missing(configured_token, fake_manager):
    with TestClient(sunsponge_app.app) as client:
        resp = client.post(
            "/v1/capture",
            json=_capture_body(),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert resp.status_code == 400
    assert resp.json() == {"ok": False, "error": "workspace_id is required"}


def test_post_400_when_no_map(configured_token, fake_manager):
    # No pathway map → rejected. Captur'd has no URL/sitemap fallback.
    with TestClient(sunsponge_app.app) as client:
        resp = client.post(
            "/v1/capture",
            json={"workspace_id": "ws"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert resp.status_code == 400
    assert "pathway map" in resp.json()["error"]


# ---------------------------------------------------------------------------
# Full flow: poll → shots with image_ref handles → fetch bytes
# ---------------------------------------------------------------------------


def test_poll_returns_shots_matrix_with_image_ref_handles(configured_token, real_manager):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(sunsponge_app.app) as client:
        resp = client.post(
            "/v1/capture",
            json=_capture_body(
                workspace_id="ws-matrix",
                viewports=["desktop", "mobile"],
                color_schemes=["light", "dark"],
            ),
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["data"]["job_id"]

        data = _wait_done(client, job_id)

    assert data["status"] == "done"
    assert data["total"] == 4
    assert data["completed"] == 4
    assert data["failed"] == 0
    # viewports x color_schemes honored: every combination present, exactly once.
    combos = {(shot["viewport"], shot["scheme"]) for shot in data["shots"]}
    assert combos == {("desktop", "light"), ("desktop", "dark"), ("mobile", "light"), ("mobile", "dark")}
    assert len(data["shots"]) == 4
    assert data["errors"] == []
    for shot in data["shots"]:
        assert set(shot) == {"url", "viewport", "scheme", "image_ref"}
        assert shot["url"] == "https://example.com/"
        assert f"/v1/capture/{job_id}/shots/" in shot["image_ref"]
        # image_ref is a HANDLE, never inline bytes.
        assert shot["image_ref"].split("/")[-1].endswith(".png")


def test_workspace_id_namespaces_storage_and_shot_is_served(configured_token, real_manager, tmp_path):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(sunsponge_app.app) as client:
        resp = client.post(
            "/v1/capture",
            json=_capture_body(workspace_id="acme-corp", viewports=["desktop"]),
            headers=headers,
        )
        job_id = resp.json()["data"]["job_id"]
        data = _wait_done(client, job_id)

        # The job is recorded under the workspace and the shot lives in the
        # workspace-scoped work dir.
        job = real_manager.get(job_id)
        assert job["workspace_id"] == "acme-corp"
        assert "/acme-corp/" in Path(job["work_dir"]).as_posix()

        # The image_ref handle resolves to actual image bytes.
        image_ref = data["shots"][0]["image_ref"]
        route = "/v1/capture" + image_ref.split("/v1/capture", 1)[1]  # strip scheme+host
        shot_resp = client.get(route, headers=headers)

    assert shot_resp.status_code == 200, shot_resp.text
    assert shot_resp.headers["content-type"] == "image/png"
    assert shot_resp.content.startswith(b"\x89PNG")
    assert len(shot_resp.content) > 0


def test_failed_shots_go_to_errors_without_image_ref(configured_token, monkeypatch, tmp_path):
    """A shot that fails capture must not get an image_ref; it appears in errors."""

    def flaky_run(targets, settings, shots_dir, progress):
        results = []
        for index, target in enumerate(targets, start=1):
            ok = index == 1
            file_name = f"{index:03d}-shot-{target.viewport_id}-{target.scheme}.png"
            result = {
                "url": target.url,
                "state_id": target.state_id,
                "viewport": target.viewport_id,
                "scheme": target.scheme,
                "width": target.width,
                "height": target.height,
                "pathway_id": target.pathway_id or None,
                "pathway_status": target.pathway_status or None,
                "status": "ok" if ok else "failed",
                "file": file_name if ok else "",
                "bytes": (shots_dir / file_name).write_bytes(b"\x89PNG\r\n\x1a\nok") if ok else 0,
                "error": None if ok else "boom",
                "attempts": 1,
                "elapsed_ms": 3,
            }
            progress(result)
            results.append(result)
        return results

    monkeypatch.setattr(capture_service, "run_capture", flaky_run)
    manager = RestedCaptureManager(root_dir=tmp_path / "jobs")
    monkeypatch.setattr(sunsponge_app, "_CAPTURE_MANAGER", manager)

    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(sunsponge_app.app) as client:
        resp = client.post(
            "/v1/capture",
            json=_capture_body(
                workspace_id="ws",
                viewports=["desktop", "mobile"],
                color_schemes=["light"],
            ),
            headers=headers,
        )
        job_id = resp.json()["data"]["job_id"]
        data = _wait_done(client, job_id)

    assert data["status"] == "done_with_errors"
    assert len(data["shots"]) == 1
    assert data["shots"][0]["image_ref"]
    assert len(data["errors"]) == 1
    assert "image_ref" not in data["errors"][0]
    assert data["errors"][0]["error"] == "boom"


# ---------------------------------------------------------------------------
# 404s
# ---------------------------------------------------------------------------


def test_get_unknown_job_is_404(configured_token, real_manager):
    with TestClient(sunsponge_app.app) as client:
        resp = client.get("/v1/capture/does-not-exist", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 404
    assert resp.json() == {"ok": False, "error": "capture job not found"}


def test_shot_unknown_job_is_404(configured_token, real_manager):
    with TestClient(sunsponge_app.app) as client:
        resp = client.get(
            "/v1/capture/does-not-exist/shots/whatever.png",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert resp.status_code == 404


def test_shot_missing_file_is_404(configured_token, real_manager):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(sunsponge_app.app) as client:
        resp = client.post(
            "/v1/capture",
            json=_capture_body(workspace_id="ws", viewports=["desktop"]),
            headers=headers,
        )
        job_id = resp.json()["data"]["job_id"]
        _wait_done(client, job_id)
        resp = client.get(
            f"/v1/capture/{job_id}/shots/999-no-such-shot.png",
            headers=headers,
        )
    assert resp.status_code == 404
    assert resp.json()["error"] == "shot not found"


def test_shot_rejects_path_traversal(configured_token, real_manager):
    """A crafted file name must not escape the job's shots dir."""
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(sunsponge_app.app) as client:
        resp = client.post(
            "/v1/capture",
            json=_capture_body(workspace_id="ws", viewports=["desktop"]),
            headers=headers,
        )
        job_id = resp.json()["data"]["job_id"]
        _wait_done(client, job_id)
        resp = client.get(
            f"/v1/capture/{job_id}/shots/..%2f..%2fapp.py",
            headers=headers,
        )
    assert resp.status_code == 404

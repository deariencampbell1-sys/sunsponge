from __future__ import annotations

from fastapi.testclient import TestClient

from sunsponge import app as sunsponge_app
from sunsponge.capture_service import (
    RestedCaptureError,
    build_capture_plan,
    discover_local_html,
    normalize_url,
    same_site_page_urls,
)


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
    page = tmp_path / "index.html"
    page.write_text("<h1>Local</h1>", encoding="utf-8")

    assert normalize_url(str(page)).startswith("file://")


def test_discover_local_html_folder_prioritizes_hub_sequence(tmp_path):
    (tmp_path / "index.html").write_text("<a href='nested.html'>Nested</a>", encoding="utf-8")
    (tmp_path / "activation.html").write_text("<h1>Activate</h1>", encoding="utf-8")
    (tmp_path / "intro-to-pops.html").write_text("<h1>Intro</h1>", encoding="utf-8")
    (tmp_path / "nested.html").write_text("<h1>Nested</h1>", encoding="utf-8")
    (tmp_path / "_head.html").write_text("<meta name='fragment'>", encoding="utf-8")

    urls, meta = discover_local_html(str(tmp_path), max_urls=10)

    assert len(urls) == 4
    assert urls[0].endswith("/activation.html")
    assert urls[1].endswith("/intro-to-pops.html")
    assert urls[2].endswith("/index.html")
    assert meta["mode"] == "local"


def test_build_capture_plan_expands_state_matrix():
    urls, targets, settings = build_capture_plan({
        "urls": ["example.com", "https://example.com/"],
        "viewports": ["desktop", "mobile"],
        "schemes": ["light", "dark"],
        "format": "png",
    })

    assert urls == ["https://example.com/"]
    assert len(targets) == 4
    assert {target.state_id for target in targets} == {
        "desktop-light",
        "desktop-dark",
        "mobile-light",
        "mobile-dark",
    }
    assert settings["capture_count"] == 4


def test_same_site_page_urls_filters_assets_external_and_fragments():
    links = [
        "/crew/#top",
        "https://www.example.com/hub/",
        "https://github.com/example/project",
        "/assets/logo.png",
        "mailto:hello@example.com",
        "/crew/",
    ]

    urls = same_site_page_urls("https://example.com/", links, {"example.com"})

    assert urls == [
        "https://example.com/crew/",
        "https://example.com/hub/",
    ]


def test_build_capture_plan_crawl_uses_discovered_pages(monkeypatch):
    def fake_discover_site_urls(seed_values, **_kwargs):
        assert seed_values == ["example.com"]
        return ["https://example.com/", "https://example.com/about/"], {"mode": "site", "page_count": 2}

    monkeypatch.setattr(
        "sunsponge.capture_service.discover_site_urls",
        fake_discover_site_urls,
    )
    urls, targets, settings = build_capture_plan({
        "crawl": True,
        "crawl_url": "example.com",
        "viewports": ["desktop"],
        "schemes": ["light"],
    })

    assert urls == ["https://example.com/", "https://example.com/about/"]
    assert len(targets) == 2
    assert settings["crawl"] is True
    assert settings["page_count"] == 2


def test_build_capture_plan_local_folder_expands_files(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Index</h1>", encoding="utf-8")
    (tmp_path / "activation.html").write_text("<h1>Activation</h1>", encoding="utf-8")

    urls, targets, settings = build_capture_plan({
        "local": True,
        "local_path": str(tmp_path),
        "viewports": ["desktop"],
        "schemes": ["light"],
    })

    assert len(urls) == 2
    assert len(targets) == 2
    assert settings["local"] is True
    assert settings["discovery"]["mode"] == "local"


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
    with TestClient(sunsponge_app.app) as client:
        response = client.post(
            "/api/rested-captures/jobs",
            json={"urls": ["https://example.com"]},
        )

    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == "fake-job"
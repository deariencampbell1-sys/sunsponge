from __future__ import annotations

from pathlib import Path

import pytest

from sunsponge.capture_service import build_capture_plan
from sunsponge.pathway_map import load_pathway_map, parse_manifest_md, parse_verifier_json

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RHOBEAR_MANIFEST = Path(r"C:\Users\slang\rhobear-app\docs\pathway-manifest.md")
VERIFIER_SAMPLE = Path(r"D:\rhobear-verifier\examples\sample-output-rhobear-app.json")


def test_parse_manifest_sample_extracts_pathways():
    parsed = parse_manifest_md(FIXTURES / "pathway-manifest-sample.md")
    ids = [p["id"] for p in parsed["pathways"]]
    assert ids == ["capture-start", "capture-job-poll", "catalog-search"]
    assert any(r["path"] == "/setup" for r in parsed["routes"])


def test_parse_verifier_json_extracts_findings_as_pathways():
    parsed = parse_verifier_json(FIXTURES / "sample-verifier-map.json")
    assert len(parsed["pathways"]) == 2
    assert parsed["pathways"][0]["status"] == "UNWIRED"
    assert parsed["pathways"][0]["downstreamCall"] == "/api/state"


def test_build_capture_plan_from_manifest_fixture():
    urls, targets, settings = build_capture_plan({
        "manifest_path": str(FIXTURES / "pathway-manifest-sample.md"),
        "base_url": "https://example.com",
        "viewports": ["desktop"],
        "schemes": ["light"],
    })

    assert settings["map"] is True
    assert settings["discovery"]["mode"] == "map"
    assert settings["discovery"]["pathway_count"] == 3
    assert len(targets) == 3
    assert len(urls) >= 1
    assert targets[0].pathway_id == "capture-start"
    assert targets[0].pathway_status == "WIRED"
    assert targets[0].state_id.startswith("capture-start-wired-desktop-light")
    assert targets[2].pathway_id == "catalog-search"
    assert targets[2].pathway_status == "UNWIRED"


def test_build_capture_plan_from_verifier_json_fixture():
    urls, targets, settings = build_capture_plan({
        "map_path": str(FIXTURES / "sample-verifier-map.json"),
        "base_url": "https://example.com",
        "viewports": ["desktop", "mobile"],
        "schemes": ["light", "dark"],
    })

    assert settings["discovery"]["source"] == "verifier-json"
    assert settings["discovery"]["pathway_count"] == 2
    assert len(targets) == 8
    assert {t.pathway_id for t in targets} == {
        "route-not-found-_api_state",
        "route-not-found-_api_control",
    }
    assert all(t.url.startswith("https://example.com") for t in targets)
    assert any("view=board" in t.url for t in targets)


@pytest.mark.skipif(not RHOBEAR_MANIFEST.is_file(), reason="rhobear-app manifest not available")
def test_build_capture_plan_from_rhobear_manifest():
    parsed = load_pathway_map(manifest_path=RHOBEAR_MANIFEST)
    assert len(parsed["pathways"]) == 71

    _urls, targets, settings = build_capture_plan({
        "manifest_path": str(RHOBEAR_MANIFEST),
        "base_url": "https://example.com",
        "viewports": ["desktop"],
        "schemes": ["light"],
    })

    assert settings["discovery"]["pathway_count"] == 71
    assert len(targets) == 71
    capture_targets = [t for t in targets if t.pathway_id == "capture-start"]
    assert len(capture_targets) == 1
    assert capture_targets[0].pathway_status == "WIRED"


@pytest.mark.skipif(not VERIFIER_SAMPLE.is_file(), reason="verifier sample output not available")
def test_build_capture_plan_from_verifier_sample_output():
    parsed = parse_verifier_json(VERIFIER_SAMPLE)
    assert len(parsed["pathways"]) > 100

    _urls, targets, settings = build_capture_plan({
        "map_path": str(VERIFIER_SAMPLE),
        "base_url": "https://example.com",
        "viewports": ["desktop"],
        "schemes": ["light"],
    })

    assert settings["discovery"]["source"] == "verifier-json"
    assert len(targets) == len(parsed["pathways"])
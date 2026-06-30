"""Captur'd is a desktop tool: the pathway map comes in as a PASTE/upload, not a
server path. These tests prove pasted map TEXT parses identically to a file, and
that the agent /v1 request accepts a pasted manifest.
"""

from pathlib import Path

import pytest

from sunsponge.app import CaptureRequestV1, _build_v1_payload
from sunsponge.capture_service import RestedCaptureError
from sunsponge.pathway_map import load_pathway_map

FIX = Path(__file__).parent / "fixtures"


def test_pasted_markdown_manifest_matches_file():
    p = FIX / "pathway-manifest-sample.md"
    from_file = load_pathway_map(manifest_path=p)
    from_text = load_pathway_map(manifest_text=p.read_text(encoding="utf-8"))
    assert from_text["pathways"] == from_file["pathways"]
    assert from_text["routes"] == from_file["routes"]
    assert from_text["file"] == "<pasted manifest>"


def test_pasted_verifier_map_matches_file():
    p = FIX / "sample-verifier-map.json"
    from_file = load_pathway_map(map_path=p)
    from_text = load_pathway_map(map_text=p.read_text(encoding="utf-8"))
    assert from_text["pathways"] == from_file["pathways"]
    assert from_text["file"] == "<pasted map>"


def test_empty_paste_is_rejected():
    with pytest.raises(RestedCaptureError):
        load_pathway_map(manifest_text="   ")
    with pytest.raises(RestedCaptureError):
        load_pathway_map()  # nothing at all


def test_v1_request_accepts_pasted_map():
    md = (FIX / "pathway-manifest-sample.md").read_text(encoding="utf-8")
    payload = _build_v1_payload(CaptureRequestV1(workspace_id="w", pathway_map=md))
    assert payload.get("pathway_manifest")
    assert "urls" not in payload and "sitemap_url" not in payload


def test_v1_request_requires_some_input():
    with pytest.raises(RestedCaptureError):
        _build_v1_payload(CaptureRequestV1(workspace_id="w"))

"""SSRF / local-access hardening for the network-facing capture manager.

The HTTP entry (RestedCaptureManager.start) must refuse internal/metadata/private
targets and local-filesystem/arbitrary-write options. The CLI (run_capture) is
intentionally unrestricted and is NOT covered here.
"""

import pytest

from sunsponge.capture_service import (
    RestedCaptureError,
    _assert_public_url,
    _assert_service_safe,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata — token theft
        "http://localhost/",
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://192.168.1.10/",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/",
    ],
)
def test_blocks_internal_and_nonhttp(url):
    with pytest.raises(RestedCaptureError):
        _assert_public_url(url)


def test_allows_public_literal_ip():
    # 8.8.8.8 is a public address; literal IP needs no DNS, so this is offline-safe.
    _assert_public_url("http://8.8.8.8/")


def test_service_safe_refuses_local_export_and_ssrf():
    bad = [
        {"local": True, "urls": ["https://example.com"], "workspace_id": "w"},
        {"local_path": "/etc", "urls": ["https://example.com"], "workspace_id": "w"},
        {"export_dir": "/tmp/x", "urls": ["https://example.com"], "workspace_id": "w"},
        {"urls": ["http://169.254.169.254/"], "workspace_id": "w"},
        {"sitemap_url": "http://127.0.0.1/sitemap.xml", "workspace_id": "w"},
    ]
    for payload in bad:
        with pytest.raises(RestedCaptureError):
            _assert_service_safe(payload)


def test_service_safe_allows_clean_public_payload():
    _assert_service_safe({"urls": ["http://8.8.8.8/"], "workspace_id": "w"})

"""Rested-state capture jobs for RHOBEAR Captur'd.

Captur'd is a DESKTOP tool. The input is always:
  1. your own built HTML/UI (a local file or folder you point at), and
  2. a pathway map (the interaction tree your agent produced), pasted or
     uploaded as markdown / verifier JSON.

There is no URL fetching, crawling, or sitemap discovery — the map says exactly
what to capture, so the run is deterministic and fast. This module is usable
without Playwright installed: imports stay clean, and dependency failures are
reported on the job instead of crashing the process.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import url2pathname
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse


DEFAULT_VIEWPORTS: dict[str, dict[str, int]] = {
    "desktop": {"width": 1440, "height": 1000},
    "tablet": {"width": 834, "height": 1112},
    "mobile": {"width": 390, "height": 844},
}
DEFAULT_SCHEMES = ("light", "dark")
MAX_TARGETS = 5000
DEFAULT_TIMEOUT_MS = 30000
DEFAULT_WAIT_MS = 600
DEFAULT_CONCURRENCY = 3
DEFAULT_RETRIES = 1
DEFAULT_RETRY_TIMEOUT_MS = 60000

USER_AGENT = "RHOBEAR Captur'd/1.0 (+https://github.com/deariencampbell1-sys/sunsponge)"

LOCAL_PAGE_EXTENSIONS = {".html", ".htm"}

TRACKING_QUERY_KEYS = {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "msclkid"}

REST_CSS = """
*, *::before, *::after {
  animation-delay: -1ms !important;
  animation-duration: 1ms !important;
  animation-iteration-count: 1 !important;
  scroll-behavior: auto !important;
  transition-delay: 0s !important;
  transition-duration: 0s !important;
  caret-color: transparent !important;
}
html { scroll-behavior: auto !important; }
""".strip()


class RestedCaptureError(ValueError):
    """User-correctable capture request error."""


@dataclass(frozen=True)
class CaptureTarget:
    index: int
    url: str
    viewport_id: str
    width: int
    height: int
    scheme: str
    pathway_id: str = ""
    pathway_status: str = ""
    pathway_trigger: str = ""

    @property
    def state_id(self) -> str:
        if self.pathway_id:
            status = _slug(self.pathway_status or "UNKNOWN", "unknown")
            pid = _slug(self.pathway_id, "pathway")
            return f"{pid}-{status}-{self.viewport_id}-{self.scheme}"
        return f"{self.viewport_id}-{self.scheme}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _app_data_dir() -> Path:
    configured = os.environ.get("SUNSPONGE_APP_DATA")
    if configured:
        return Path(configured).expanduser()
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if local:
        return Path(local) / "Capturd"
    return Path.home() / ".capturd"


def _slug(value: str, fallback: str = "site") -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:80] or fallback


def _job_name() -> str:
    return "rested-captures-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _is_local_input(value: str) -> bool:
    raw = (value or "").strip().strip('"')
    if not raw:
        return False
    if raw.lower().startswith("file://"):
        return True
    if re.match(r"^[a-zA-Z]:[\\/]", raw) or raw.startswith("\\\\"):
        return True
    return Path(raw).expanduser().exists()


def _file_url_from_path(path: Path) -> str:
    return path.expanduser().resolve().as_uri()


def _path_from_file_url(value: str) -> Path:
    parsed = urlparse(value)
    if parsed.scheme != "file":
        raise RestedCaptureError(f"unsupported local URL: {value}")
    raw_path = url2pathname(parsed.path)
    if os.name == "nt" and re.match(r"^/[a-zA-Z]:/", raw_path):
        raw_path = raw_path[1:]
    return Path(raw_path)


def _local_path_from_value(value: str) -> Path:
    raw = (value or "").strip().strip('"')
    if not raw:
        raise RestedCaptureError("empty local path")
    path = _path_from_file_url(raw) if raw.lower().startswith("file://") else Path(raw).expanduser()
    if not path.exists():
        raise RestedCaptureError(f"local path does not exist: {value}")
    return path.resolve()


def _normalize_local_url(value: str) -> str:
    path = _local_path_from_value(value)
    if path.is_dir():
        index = path / "index.html"
        if index.is_file():
            path = index
        else:
            raise RestedCaptureError(f"local folder has no index.html: {value}")
    if path.suffix.lower() not in LOCAL_PAGE_EXTENSIONS:
        raise RestedCaptureError(f"local file is not HTML: {value}")
    return _file_url_from_path(path)


def _local_key(value: str) -> str:
    try:
        path = _path_from_file_url(value)
    except RestedCaptureError:
        path = Path(value)
    key = str(path.expanduser().resolve())
    return key.lower() if os.name == "nt" else key


def _clean_query(query: str) -> str:
    pairs = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        lower = key.lower()
        if lower.startswith("utm_") or lower in TRACKING_QUERY_KEYS:
            continue
        pairs.append((key, value))
    return urlencode(pairs, doseq=True)


def _site_host_key(host: str | None) -> str:
    value = (host or "").strip().lower().rstrip(".")
    if value.startswith("www."):
        value = value[4:]
    return value


def _canonical_page_url(value: str, base: str | None = None, preferred_netloc: str | None = None) -> str:
    raw = (value or "").strip()
    if not raw:
        raise RestedCaptureError("empty URL")
    if raw.startswith(("#", "mailto:", "tel:", "sms:", "javascript:", "data:", "blob:")):
        raise RestedCaptureError(f"unsupported URL: {value}")
    if _is_local_input(raw):
        return _normalize_local_url(raw)
    if base:
        raw = urljoin(base, raw)
    if _is_local_input(raw):
        return _normalize_local_url(raw)
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RestedCaptureError(f"unsupported URL: {value}")

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if preferred_netloc:
        preferred = urlparse(f"https://{preferred_netloc}").netloc.lower()
        if _site_host_key(parsed.hostname) == _site_host_key(urlparse(f"https://{preferred}").hostname):
            netloc = preferred

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    path = re.sub(r"/(?:index|default)\.html?$", "/", path, flags=re.IGNORECASE)
    query = _clean_query(parsed.query)
    return urlunparse((scheme, netloc, path, "", query, ""))


def normalize_url(value: str) -> str:
    """Canonicalize a page reference. Handles local file paths / file:// URLs
    (the built HTML the user points at) as well as http(s) targets a map's
    pathway may resolve to."""
    return _canonical_page_url(value)


def build_capture_plan(payload: dict[str, Any]) -> tuple[list[str], list[CaptureTarget], dict[str, Any]]:
    """Build the capture plan from a pathway map + the built-HTML location.

    Captur'd is map-driven only: the map (pasted/uploaded markdown or verifier
    JSON, or a file path for CLI convenience) lists every state to capture, and
    ``base_url`` is where the user's built HTML lives (a local file/folder or a
    file:// URL). No crawling, no sitemaps, no URL discovery.
    """
    manifest_path = str(payload.get("manifest_path") or payload.get("manifest") or "").strip()
    map_path = str(payload.get("map_path") or payload.get("map") or "").strip()
    # Pasted/uploaded map content — the primary desktop path (no file on disk needed).
    manifest_text = str(
        payload.get("pathway_manifest")
        or payload.get("manifest_text")
        or payload.get("pathway_map")
        or ""
    ).strip()
    map_text = str(payload.get("map_text") or payload.get("pathway_map_json") or "").strip()

    if not (manifest_path or map_path or manifest_text or map_text):
        raise RestedCaptureError(
            "a pathway map is required — paste the manifest markdown (or upload it)"
        )

    viewport_ids = payload.get("viewports") or list(DEFAULT_VIEWPORTS)
    if not isinstance(viewport_ids, list):
        raise RestedCaptureError("viewports must be a list")
    viewport_ids = [str(v).strip().lower() for v in viewport_ids if str(v).strip()]
    if not viewport_ids:
        raise RestedCaptureError("choose at least one viewport")

    schemes = payload.get("schemes") or list(DEFAULT_SCHEMES)
    if isinstance(schemes, str):
        schemes = [schemes]
    if not isinstance(schemes, list):
        raise RestedCaptureError("schemes must be a list")
    schemes = [str(s).strip().lower() for s in schemes if str(s).strip()]
    if not schemes:
        raise RestedCaptureError("choose at least one color scheme")

    from sunsponge.pathway_map import load_pathway_map, plan_targets_from_map

    pathway_map = load_pathway_map(
        manifest_path=manifest_path or None,
        map_path=map_path or None,
        manifest_text=manifest_text or None,
        map_text=map_text or None,
    )
    base_url = str(payload.get("base_url") or "").strip()
    urls, descriptors = plan_targets_from_map(
        pathway_map,
        base_url=base_url,
        viewports=viewport_ids,
        schemes=schemes,
    )
    if len(descriptors) > MAX_TARGETS:
        raise RestedCaptureError(f"too many capture targets; maximum is {MAX_TARGETS}")

    discovery = {
        "mode": "map",
        "source": "manifest" if (manifest_path or manifest_text) else "verifier-json",
        "pathway_count": len(pathway_map.get("pathways") or []),
        "route_count": len(pathway_map.get("routes") or []),
        "page_count": len(urls),
        "target_count": len(descriptors),
        "base_url": base_url,
        "manifest_hash": pathway_map.get("hash"),
    }
    targets: list[CaptureTarget] = []
    for desc in descriptors:
        viewport = DEFAULT_VIEWPORTS.get(desc["viewport_id"])
        if not viewport:
            raise RestedCaptureError(f"unknown viewport: {desc['viewport_id']}")
        if desc["scheme"] not in {"light", "dark", "no-preference"}:
            raise RestedCaptureError(f"unknown color scheme: {desc['scheme']}")
        targets.append(
            CaptureTarget(
                index=int(desc["index"]),
                url=desc["url"],
                viewport_id=desc["viewport_id"],
                width=int(viewport["width"]),
                height=int(viewport["height"]),
                scheme=desc["scheme"],
                pathway_id=desc["pathway_id"],
                pathway_status=desc["pathway_status"],
                pathway_trigger=desc["pathway_trigger"],
            )
        )
    settings = {
        "format": str(payload.get("format") or "png").lower(),
        "full_page": bool(payload.get("full_page", True)),
        "timeout_ms": int(payload.get("timeout_ms") or DEFAULT_TIMEOUT_MS),
        "wait_ms": int(payload.get("wait_ms") or DEFAULT_WAIT_MS),
        "concurrency": max(1, min(8, int(payload.get("concurrency") or DEFAULT_CONCURRENCY))),
        "retries": max(0, min(3, int(payload.get("retries", DEFAULT_RETRIES)))),
        "retry_timeout_ms": max(
            int(payload.get("timeout_ms") or DEFAULT_TIMEOUT_MS),
            int(payload.get("retry_timeout_ms") or DEFAULT_RETRY_TIMEOUT_MS),
        ),
        "jpeg_quality": max(40, min(100, int(payload.get("jpeg_quality") or 88))),
        "capture_count": len(targets),
        "map": True,
        "manifest_path": manifest_path or None,
        "map_path": map_path or None,
        "page_count": len(urls),
        "discovery": discovery,
    }
    if settings["format"] not in {"png", "jpeg"}:
        raise RestedCaptureError("format must be png or jpeg")
    return urls, targets, settings


def _capture_file_name(target: CaptureTarget, fmt: str) -> str:
    ext = "jpg" if fmt == "jpeg" else "png"
    if target.pathway_id:
        pid = _slug(target.pathway_id, f"pathway-{target.index:03d}")
        status = _slug(target.pathway_status or "unknown", "unknown")
        return f"{target.index:03d}-{pid}-{status}-{target.viewport_id}-{target.scheme}.{ext}"
    parsed = urlparse(target.url)
    site = _slug(parsed.netloc + parsed.path, f"site-{target.index:03d}")
    return f"{target.index:03d}-{site}-{target.viewport_id}-{target.scheme}.{ext}"


async def _settle_page(page: Any, wait_ms: int) -> None:
    try:
        await page.add_style_tag(content=REST_CSS)
    except Exception:
        pass
    try:
        await page.evaluate(
            """async () => {
              if (document.fonts && document.fonts.ready) await document.fonts.ready;
              for (const video of Array.from(document.querySelectorAll('video'))) {
                try { video.pause(); } catch (_) {}
              }
              window.scrollTo(0, 0);
            }"""
        )
    except Exception:
        pass
    await page.wait_for_timeout(wait_ms)


def _target_context_key(target: CaptureTarget) -> tuple[str, str, int, int, str]:
    parsed = urlparse(target.url)
    if parsed.scheme == "file":
        return ("local-file", target.viewport_id, target.width, target.height, target.scheme)
    return (_site_host_key(parsed.hostname), target.viewport_id, target.width, target.height, target.scheme)


async def _capture_one(context: Any, target: CaptureTarget, settings: dict[str, Any], path: Path) -> dict[str, Any]:
    started = time.perf_counter()
    attempts = max(1, int(settings.get("retries", DEFAULT_RETRIES)) + 1)
    last_error = ""
    for attempt in range(1, attempts + 1):
        page = None
        try:
            timeout_ms = int(settings["timeout_ms"])
            if attempt > 1:
                timeout_ms = max(timeout_ms, int(settings.get("retry_timeout_ms") or DEFAULT_RETRY_TIMEOUT_MS))
            page = await context.new_page()
            page.set_default_timeout(timeout_ms)
            await page.goto(target.url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
            except Exception:
                pass
            await _settle_page(page, settings["wait_ms"])
            screenshot_kwargs: dict[str, Any] = {
                "path": str(path),
                "type": settings["format"],
                "full_page": settings["full_page"],
                "animations": "disabled",
            }
            if settings["format"] == "jpeg":
                screenshot_kwargs["quality"] = settings["jpeg_quality"]
            await page.screenshot(**screenshot_kwargs)
            stat = path.stat()
            return {
                "url": target.url,
                "state_id": target.state_id,
                "viewport": target.viewport_id,
                "scheme": target.scheme,
                "width": target.width,
                "height": target.height,
                "pathway_id": target.pathway_id or None,
                "pathway_status": target.pathway_status or None,
                "status": "ok",
                "file": path.name,
                "bytes": stat.st_size,
                "attempts": attempt,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
        except Exception as exc:
            last_error = str(exc)
            if attempt < attempts:
                await asyncio.sleep(0.35)
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    return {
        "url": target.url,
        "state_id": target.state_id,
        "viewport": target.viewport_id,
        "scheme": target.scheme,
        "width": target.width,
        "height": target.height,
        "pathway_id": target.pathway_id or None,
        "pathway_status": target.pathway_status or None,
        "status": "failed",
        "error": last_error,
        "attempts": attempts,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


async def _launch_browser(playwright: Any, *, allow_file_access: bool = False) -> Any:
    launch_args = ["--allow-file-access-from-files"] if allow_file_access else []
    attempts: list[dict[str, Any]] = [{"headless": True, "args": launch_args}]
    if os.name == "nt":
        attempts.extend([
            {"headless": True, "channel": "msedge", "args": launch_args},
            {"headless": True, "channel": "chrome", "args": launch_args},
        ])

    last_error: Exception | None = None
    for kwargs in attempts:
        try:
            return await playwright.chromium.launch(**kwargs)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error) if last_error else "unable to launch Chromium")


async def _run_capture_async(
    targets: list[CaptureTarget],
    settings: dict[str, Any],
    shots_dir: Path,
    progress: Any,
) -> list[dict[str, Any]]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install engine requirements, then run "
            "`python -m playwright install chromium` if system Edge is unavailable."
        ) from exc

    sem = asyncio.Semaphore(settings["concurrency"])
    results: list[dict[str, Any]] = []

    async with async_playwright() as p:
        allow_file_access = any(urlparse(target.url).scheme == "file" for target in targets)
        browser = await _launch_browser(p, allow_file_access=allow_file_access)
        contexts: dict[tuple[str, str, int, int, str], Any] = {}
        context_lock = asyncio.Lock()

        async def context_for(target: CaptureTarget) -> Any:
            key = _target_context_key(target)
            async with context_lock:
                context = contexts.get(key)
                if context is None:
                    context = await browser.new_context(
                        viewport={"width": target.width, "height": target.height},
                        color_scheme=None if target.scheme == "no-preference" else target.scheme,
                        reduced_motion="reduce",
                        device_scale_factor=1,
                        locale="en-US",
                        user_agent=USER_AGENT,
                    )
                    contexts[key] = context
                return context

        try:
            async def run_one(target: CaptureTarget) -> dict[str, Any]:
                async with sem:
                    file_name = _capture_file_name(target, settings["format"])
                    context = await context_for(target)
                    result = await _capture_one(context, target, settings, shots_dir / file_name)
                    progress(result)
                    return result

            results = await asyncio.gather(*(run_one(target) for target in targets))
        finally:
            for context in contexts.values():
                try:
                    await context.close()
                except Exception:
                    pass
            await browser.close()

    return results


def run_capture(
    targets: list[CaptureTarget],
    settings: dict[str, Any],
    shots_dir: Path,
    progress: Any,
) -> list[dict[str, Any]]:
    return asyncio.run(_run_capture_async(targets, settings, shots_dir, progress))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _zip_dir(source_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file() and path.resolve() != zip_path.resolve():
                zf.write(path, path.relative_to(source_dir))


class RestedCaptureManager:
    """In-process job store for rested website capture runs."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or (_app_data_dir() / "captures")
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        urls, targets, settings = build_capture_plan(payload)
        job_id = uuid.uuid4().hex[:12]
        job_name = _slug(str(payload.get("name") or "") or _job_name(), _job_name())
        workspace_id = str(payload.get("workspace_id") or "").strip()
        ws_slug = _slug(workspace_id, "workspace") if workspace_id else ""
        work_dir = self.root_dir / ws_slug / job_id if ws_slug else self.root_dir / job_id
        shots_dir = work_dir / "shots"
        export_dir_raw = str(payload.get("export_dir") or "").strip()
        export_mode = str(payload.get("export_mode") or "zip").lower()
        export_dir = Path(export_dir_raw).expanduser() if export_dir_raw else None

        job = {
            "ok": True,
            "job_id": job_id,
            "name": job_name,
            "status": "queued",
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "message": "Queued",
            "urls": urls,
            "total": len(targets),
            "completed": 0,
            "failed": 0,
            "results": [],
            "settings": settings,
            "discovery": settings.get("discovery") or {},
            "workspace_id": workspace_id or None,
            "work_dir": str(work_dir),
            "output_dir": "",
            "zip_path": "",
            "zip_url": "",
        }
        with self._lock:
            self._jobs[job_id] = job

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, targets, settings, work_dir, shots_dir, export_dir, export_mode),
            daemon=True,
            name=f"rested-capture-{job_id}",
        )
        thread.start()
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            return dict(job)

    def zip_path(self, job_id: str) -> Path:
        job = self.get(job_id)
        zip_path = Path(str(job.get("zip_path") or ""))
        if not zip_path.is_file():
            raise FileNotFoundError(job_id)
        return zip_path

    def shot_path(self, job_id: str, file: str) -> Path:
        """Resolve an individual shot file for token-gated retrieval.

        ``file`` is reduced to a bare basename and the resolved path must stay
        inside the job's ``shots/`` dir, so this cannot be used to read
        arbitrary files off the server.
        """
        job = self.get(job_id)
        work_dir = Path(str(job.get("work_dir") or ""))
        shots_dir = (work_dir / "shots").resolve()
        candidate = (shots_dir / Path(file).name).resolve()
        try:
            candidate.relative_to(shots_dir)
        except ValueError as exc:  # traversal attempt — treat as not found
            raise FileNotFoundError(job_id) from exc
        if not candidate.is_file():
            raise FileNotFoundError(job_id)
        return candidate

    def _patch(self, job_id: str, patch: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(patch)
            job["updated_at"] = utc_now_iso()

    def _record_result(self, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["results"].append(result)
            job["completed"] = int(job.get("completed", 0)) + 1
            if result.get("status") != "ok":
                job["failed"] = int(job.get("failed", 0)) + 1
            job["message"] = f"{job['completed']} of {job['total']} captured"
            job["updated_at"] = utc_now_iso()

    def _run_job(
        self,
        job_id: str,
        targets: list[CaptureTarget],
        settings: dict[str, Any],
        work_dir: Path,
        shots_dir: Path,
        export_dir: Path | None,
        export_mode: str,
    ) -> None:
        try:
            shots_dir.mkdir(parents=True, exist_ok=True)
            self._patch(job_id, {"status": "running", "message": "Capturing"})
            results = run_capture(
                targets,
                settings,
                shots_dir,
                lambda result: self._record_result(job_id, result),
            )
            manifest = {
                "job_id": job_id,
                "captured_at": utc_now_iso(),
                "settings": settings,
                "urls": self.get(job_id).get("urls") or [],
                "discovery": settings.get("discovery") or {},
                "results": results,
            }
            _write_json(work_dir / "manifest.json", manifest)

            output_dir = work_dir
            if export_dir is not None and export_mode == "folder":
                output_dir = export_dir / Path(str(self.get(job_id)["name"])).name
                if output_dir.exists():
                    output_dir = export_dir / f"{output_dir.name}-{job_id}"
                shutil.copytree(work_dir, output_dir)

            zip_parent = export_dir if export_dir is not None else work_dir
            zip_parent.mkdir(parents=True, exist_ok=True)
            zip_path = zip_parent / f"{Path(str(self.get(job_id)['name'])).name}.zip"
            if zip_path.exists():
                zip_path = zip_parent / f"{zip_path.stem}-{job_id}.zip"
            _zip_dir(output_dir, zip_path)

            status = "done" if int(self.get(job_id)["failed"]) == 0 else "done_with_errors"
            self._patch(
                job_id,
                {
                    "status": status,
                    "message": "Done" if status == "done" else "Done with errors",
                    "output_dir": str(output_dir),
                    "zip_path": str(zip_path),
                    "zip_url": f"/api/rested-captures/jobs/{job_id}/download",
                },
            )
        except Exception as exc:
            self._patch(job_id, {"ok": False, "status": "failed", "message": str(exc)})

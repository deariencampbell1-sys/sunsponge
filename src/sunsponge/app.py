"""Standalone FastAPI service for SunSponge website capture jobs."""

from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from sunsponge.capture_service import RestedCaptureError, RestedCaptureManager

logger = logging.getLogger(__name__)

SERVICE_TOKEN_ENV = "RHOBEAR_SERVICE_TOKEN"
EXTERNAL_BASE_ENV = "SUNSPONGE_EXTERNAL_BASE_URL"

UI_DIR = Path(__file__).resolve().parents[2] / "ui"
_CAPTURE_MANAGER = RestedCaptureManager()


class ServiceAuthError(Exception):
    """Raised by the agent-control auth dependency; rendered as the {ok,error} envelope."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _configured_service_token() -> str | None:
    return os.environ.get(SERVICE_TOKEN_ENV) or None


def require_service_token(authorization: str = Header(default="")) -> None:
    """Bearer-token gate for the agent-control (``/v1``) API.

    Fail-closed: if no service token is configured the endpoints report 503
    rather than silently opening up. A present-but-mismatched token is a 401.
    """
    expected = _configured_service_token()
    if not expected:
        raise ServiceAuthError(503, f"{SERVICE_TOKEN_ENV} is not configured on the server")
    token = ""
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1].strip()
    if not token or not hmac.compare_digest(token, expected):
        raise ServiceAuthError(401, "invalid or missing service token")


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _err(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _external_base(request: Request) -> str:
    configured = (os.environ.get(EXTERNAL_BASE_ENV) or "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def _shot_image_ref(base: str, job_id: str, file: str) -> str:
    return f"{base}/v1/capture/{job_id}/shots/{file}"


class RestedCaptureRequest(BaseModel):
    urls: list[str] | str = Field(default_factory=list)
    sitemap_url: str | None = None
    crawl: bool = False
    crawl_url: str | None = None
    local: bool = False
    local_path: str | None = None
    crawl_depth: int = 8
    crawl_concurrency: int = 6
    max_pages: int = 1000
    include_sitemaps: bool = True
    discovery_timeout_ms: int = 15000
    discovery_wait_ms: int = 150
    viewports: list[str] = Field(default_factory=lambda: ["desktop", "tablet", "mobile"])
    schemes: list[str] = Field(default_factory=lambda: ["light", "dark"])
    format: Literal["png", "jpeg"] = "png"
    full_page: bool = True
    timeout_ms: int = 30000
    wait_ms: int = 600
    concurrency: int = 3
    retries: int = 1
    retry_timeout_ms: int = 60000
    jpeg_quality: int = 88
    export_dir: str | None = None
    export_mode: Literal["zip", "folder"] = "zip"
    name: str | None = None
    manifest_path: str | None = None
    map_path: str | None = None
    base_url: str | None = None


def _request_payload(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=True)
    return model.dict(exclude_none=True)


class CaptureRequestV1(BaseModel):
    """Agent-control capture request — the SunSponge slice of the Family API Contract."""

    model_config = ConfigDict(extra="ignore")

    url: str | list[str] | None = None
    sitemap: str | None = None
    viewports: list[str] | None = None
    color_schemes: list[str] | None = None
    workspace_id: str | None = None
    full_page: bool = True
    format: Literal["png", "jpeg"] = "png"


def _build_v1_payload(body: CaptureRequestV1) -> dict[str, Any]:
    """Translate the contract request into the engine's existing payload shape.

    Reuses build_capture_plan() unchanged — the API is a thin envelope over the
    same capture logic the CLI and UI already use.
    """
    workspace_id = (body.workspace_id or "").strip()
    if not workspace_id:
        raise RestedCaptureError("workspace_id is required")

    urls_field = body.url
    urls_list: list[str] = []
    if isinstance(urls_field, list):
        urls_list = [u for u in (str(u).strip() for u in urls_field) if u]
    elif isinstance(urls_field, str) and urls_field.strip():
        urls_list = [urls_field.strip()]
    sitemap = (body.sitemap or "").strip()
    has_url = bool(urls_list)
    has_sitemap = bool(sitemap)
    if not has_url and not has_sitemap:
        raise RestedCaptureError("provide 'url' or 'sitemap'")
    if has_url and has_sitemap:
        raise RestedCaptureError("provide 'url' or 'sitemap', not both")

    payload: dict[str, Any] = {"workspace_id": workspace_id}
    if has_sitemap:
        payload["sitemap_url"] = sitemap
    else:
        payload["urls"] = urls_list
    if body.viewports is not None:
        payload["viewports"] = body.viewports
    if body.color_schemes is not None:
        payload["schemes"] = body.color_schemes
    payload["full_page"] = body.full_page
    payload["format"] = body.format
    return payload


def create_app() -> FastAPI:
    app = FastAPI(title="SunSponge", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ServiceAuthError)
    def _handle_service_auth(_request: Request, exc: ServiceAuthError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_err(exc.message))

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "sunsponge"}

    @app.post("/api/rested-captures/jobs", response_model=None)
    def post_rested_capture_job(body: RestedCaptureRequest) -> dict[str, Any] | JSONResponse:
        try:
            return _CAPTURE_MANAGER.start(_request_payload(body))
        except RestedCaptureError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
        except Exception:
            logger.exception("POST /api/rested-captures/jobs failed")
            return JSONResponse(status_code=500, content={"ok": False, "error": "capture job failed"})

    @app.get("/api/rested-captures/jobs/{job_id}", response_model=None)
    def get_rested_capture_job(job_id: str) -> dict[str, Any] | JSONResponse:
        try:
            return _CAPTURE_MANAGER.get(job_id)
        except KeyError:
            return JSONResponse(status_code=404, content={"ok": False, "error": "capture job not found"})

    @app.get("/api/rested-captures/jobs/{job_id}/download", response_model=None)
    def download_rested_capture_job(job_id: str) -> FileResponse | JSONResponse:
        try:
            zip_path = _CAPTURE_MANAGER.zip_path(job_id)
        except KeyError:
            return JSONResponse(status_code=404, content={"ok": False, "error": "capture job not found"})
        except FileNotFoundError:
            return JSONResponse(status_code=409, content={"ok": False, "error": "capture ZIP is not ready"})
        return FileResponse(str(zip_path), media_type="application/zip", filename=zip_path.name)

    # ------------------------------------------------------------------
    # Agent-control API — the SunSponge slice of the Family API Contract.
    # Async, bearer-token gated, {ok,data}|{ok,error} envelope. These wrap the
    # SAME capture engine as the /api routes above; they are purely additive.
    # ------------------------------------------------------------------

    @app.post("/v1/capture", response_model=None, dependencies=[Depends(require_service_token)])
    def v1_create_capture(body: CaptureRequestV1) -> dict[str, Any] | JSONResponse:
        try:
            payload = _build_v1_payload(body)
            job = _CAPTURE_MANAGER.start(payload)
        except RestedCaptureError as exc:
            return JSONResponse(status_code=400, content=_err(str(exc)))
        except Exception:
            logger.exception("POST /v1/capture failed")
            return JSONResponse(status_code=500, content=_err("capture job failed to start"))
        return _ok({"job_id": job["job_id"], "status": job.get("status", "queued")})

    @app.get("/v1/capture/{job_id}", response_model=None, dependencies=[Depends(require_service_token)])
    def v1_get_capture(job_id: str, request: Request) -> dict[str, Any] | JSONResponse:
        try:
            job = _CAPTURE_MANAGER.get(job_id)
        except KeyError:
            return JSONResponse(status_code=404, content=_err("capture job not found"))

        base = _external_base(request)
        shots: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for result in job.get("results") or []:
            row = {
                "url": result.get("url"),
                "viewport": result.get("viewport"),
                "scheme": result.get("scheme"),
            }
            if result.get("file") and result.get("status") == "ok":
                row["image_ref"] = _shot_image_ref(base, job_id, result["file"])
                shots.append(row)
            else:
                row["status"] = result.get("status")
                row["error"] = result.get("error")
                errors.append(row)

        return _ok({
            "job_id": job_id,
            "status": job.get("status"),
            "message": job.get("message"),  # surface top-level failures (e.g. browser launch) — was dropped
            "total": job.get("total", 0),
            "completed": job.get("completed", 0),
            "failed": job.get("failed", 0),
            "shots": shots,
            "errors": errors,
        })

    @app.get(
        "/v1/capture/{job_id}/shots/{file}",
        response_model=None,
        dependencies=[Depends(require_service_token)],
    )
    def v1_get_capture_shot(job_id: str, file: str) -> FileResponse | JSONResponse:
        try:
            path = _CAPTURE_MANAGER.shot_path(job_id, file)
        except KeyError:
            return JSONResponse(status_code=404, content=_err("capture job not found"))
        except FileNotFoundError:
            return JSONResponse(status_code=404, content=_err("shot not found"))
        media = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        return FileResponse(str(path), media_type=media, filename=path.name)

    if UI_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("sunsponge.app:app", host="127.0.0.1", port=8787, reload=False)


if __name__ == "__main__":
    main()
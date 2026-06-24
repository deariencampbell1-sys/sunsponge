"""Standalone FastAPI service for SunSponge website capture jobs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sunsponge.capture_service import RestedCaptureError, RestedCaptureManager

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parents[2] / "ui"
_CAPTURE_MANAGER = RestedCaptureManager()


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


def _request_payload(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=True)
    return model.dict(exclude_none=True)


def create_app() -> FastAPI:
    app = FastAPI(title="SunSponge", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    if UI_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("sunsponge.app:app", host="127.0.0.1", port=8787, reload=False)


if __name__ == "__main__":
    main()
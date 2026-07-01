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
from sunsponge.demo_engine import DemoManager, DemoRecorderError, run_async
from sunsponge.demo_ai import DemoAI, DemoAIError, DemoEnrichManager

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parents[2] / "ui"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMOS_ROOT = PROJECT_ROOT / "demos"
_CAPTURE_MANAGER = RestedCaptureManager()
_DEMO_MANAGER = DemoManager(output_root=DEMOS_ROOT)
_DEMO_AI = DemoAI()
_DEMO_ENRICH = DemoEnrichManager(output_root=DEMOS_ROOT, ai=_DEMO_AI)


class DemoRecordRequest(BaseModel):
    url: str
    name: str | None = "Untitled demo"
    goal: str | None = ""
    viewport: dict[str, int] | None = None
    sessionId: str | None = None


class DemoStopRequest(BaseModel):
    sessionId: str


class DemoEnrichRequest(BaseModel):
    demoId: str


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

    # ---- DemoForge recorder (Phase 1) ------------------------------------

    @app.post("/api/demos/record", response_model=None)
    def post_demos_record(body: DemoRecordRequest) -> dict[str, Any] | JSONResponse:
        """Launch a headful Playwright window that records user clicks."""
        try:
            recorder, session_id = _DEMO_MANAGER.start(_request_payload(body))
        except DemoRecorderError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
        except Exception:
            logger.exception("POST /api/demos/record failed to start")
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "failed to start recorder"}
            )

        # Start the async recorder in a background thread. We deliberately
        # don't await it — the browser must stay open until /stop is called.
        try:
            import threading as _threading

            _t = _threading.Thread(
                target=lambda: run_async(recorder.start()),
                name=f"demo-start-{session_id}",
                daemon=True,
            )
            _t.start()
        except Exception:
            logger.exception("failed to spawn recorder thread for %s", session_id)
            _DEMO_MANAGER.discard(session_id)
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "failed to launch browser"}
            )

        return {
            "ok": True,
            "sessionId": session_id,
            "message": (
                "Recording started. Interact with the browser window, then call "
                "POST /api/demos/stop with this sessionId."
            ),
        }

    @app.post("/api/demos/stop", response_model=None)
    def post_demos_stop(body: DemoStopRequest) -> dict[str, Any] | JSONResponse:
        try:
            recorder = _DEMO_MANAGER.get(body.sessionId)
        except DemoRecorderError as exc:
            return JSONResponse(status_code=404, content={"ok": False, "error": str(exc)})

        try:
            spec = recorder.stop()
        except DemoRecorderError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
        except Exception:
            logger.exception("failed to stop recorder %s", body.sessionId)
            _DEMO_MANAGER.discard(body.sessionId)
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "failed to stop recorder"}
            )

        step_count = len(spec.steps)
        out_dir = recorder.output_dir
        rel_demo = str(out_dir / "demo.json")
        try:
            rel_demo = str(out_dir.relative_to(PROJECT_ROOT) / "demo.json")
        except ValueError:
            pass

        _DEMO_MANAGER.discard(body.sessionId)
        return {
            "ok": True,
            "demoId": body.sessionId,
            "stepCount": step_count,
            "path": rel_demo,
            "status": "recorded",
        }

    @app.get("/api/demos/sessions")
    def get_demos_sessions() -> dict[str, Any]:
        return {"ok": True, "sessions": _DEMO_MANAGER.list_sessions()}

    # ---- DemoForge AI enrichment (Phase 3) -------------------------------

    @app.post("/api/demos/enrich", response_model=None)
    def post_demos_enrich(body: DemoEnrichRequest) -> dict[str, Any] | JSONResponse:
        """Kick off async AI enrichment for a recorded demo."""
        try:
            job_id = _DEMO_ENRICH.submit(body.demoId)
        except DemoAIError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
        except FileNotFoundError as exc:
            return JSONResponse(status_code=404, content={"ok": False, "error": str(exc)})
        except Exception:
            logger.exception("POST /api/demos/enrich failed to start")
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "failed to start enrich job"}
            )
        return {
            "ok": True,
            "demoId": body.demoId,
            "jobId": job_id,
            "status": "enriching",
            "message": "AI enrichment started. Poll /api/demos/enrich/{jobId} for status.",
        }

    @app.get("/api/demos/enrich/{job_id}", response_model=None)
    def get_demos_enrich_job(job_id: str) -> dict[str, Any] | JSONResponse:
        try:
            return {"ok": True, "job": _DEMO_ENRICH.get_status(job_id)}
        except KeyError:
            return JSONResponse(status_code=404, content={"ok": False, "error": "job not found"})

    @app.get("/api/demos/enrich", response_model=None)
    def get_demos_enrich_jobs() -> dict[str, Any]:
        return {"ok": True, "jobs": _DEMO_ENRICH.list_jobs()}

    @app.get("/api/demos/{demo_id}", response_model=None)
    def get_demos_spec(demo_id: str) -> dict[str, Any] | JSONResponse:
        try:
            return {"ok": True, "demoId": demo_id, "spec": _DEMO_ENRICH.read_spec(demo_id)}
        except FileNotFoundError:
            return JSONResponse(
                status_code=404, content={"ok": False, "error": f"demo '{demo_id}' not found"}
            )

    if UI_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("sunsponge.app:app", host="127.0.0.1", port=8787, reload=False)


if __name__ == "__main__":
    main()
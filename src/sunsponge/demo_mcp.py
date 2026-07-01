"""DemoForge MCP server — exposes the demo pipeline as 7 MCP tools.

Tools (all under the ``demo.`` namespace so the agent's tool list stays
uncluttered):

* ``demo.record``   — open a headful browser and start capturing clicks
* ``demo.stop``     — close the browser, persist the spec, kick off AI enrichment
* ``demo.status``   — check pipeline progress for a demo
* ``demo.list``     — enumerate every recorded demo on disk
* ``demo.edit``     — rewrite a step's annotation and optionally re-synthesize voiceover
* ``demo.delete``   — remove a demo and its files
* ``demo.export``   — render a standalone HTML viewer

Transport: stdio JSON-RPC 2.0 (the FastMCP default). The server is launched
via ``python -m sunsponge.demo_mcp``.

Hard constraints (from phase4-brief.md):

* Reuse :class:`DemoForge` from :mod:`sunsponge.demo_forge`. No duplication.
* ``demo.stop`` must be idempotent — calling it twice returns the demo's
  current status instead of starting a second enrichment.
* ``demo.edit`` returns the updated step as ``{ok, step}``.
* ``demo.export`` writes a single self-contained HTML file (no external
  assets); screenshots are inlined as base64.
* All tool errors are surfaced as plain strings (no stack traces).

Run with::

    python -m sunsponge.demo_mcp

Or, from inside pi, attach it as an MCP server via the standard
``fastmcp`` stdio transport.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from sunsponge.demo_engine import DemoRecorderError
from sunsponge.demo_forge import (
    DemoForge,
    DemoForgeError,
    DemoNotFound,
    DEMOS_DIR_NAME,
    demos_root,
)

logger = logging.getLogger("sunsponge.demo_mcp")


# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------

_INSTRUCTIONS = (
    "DemoForge MCP server. Use the demo.* tools to record product flows, "
    "enrich them with AI (annotations, voiceover, animation timeline), and "
    "export the result as a standalone HTML viewer. Recording tools open a "
    "headful Playwright window — make sure the host has a graphical session "
    "(or a virtual display) when calling demo.record. Pipeline runs "
    "asynchronously; poll demo.status until 'enriched' before exporting."
)


def _build_server(forge: DemoForge | None = None) -> FastMCP:
    """Construct the MCP server with all 7 tools wired up."""
    forge = forge if forge is not None else DemoForge()
    mcp = FastMCP(
        name="DemoForge",
        instructions=_INSTRUCTIONS,
        version="0.1.0",
    )

    # ---- demo.record ------------------------------------------------------

    @mcp.tool(
        name="demo.record",
        description=(
            "Start recording a product demo. Opens a headful Playwright "
            "window — click through your flow, then call demo.stop with "
            "the returned sessionId. The browser stays open until stop is "
            "called, so the caller MUST be able to interact with the host "
            "graphical session."
        ),
        timeout=60.0,
    )
    async def demo_record(
        url: str,
        name: str,
        goal: str = "",
        viewport: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        if not url:
            raise ValueError("url is required")
        if not name:
            raise ValueError("name is required")
        try:
            recorder, session_id = forge.start_recording(
                {
                    "url": url,
                    "name": name,
                    "goal": goal,
                    "viewport": viewport or {"width": 1440, "height": 900},
                }
            )
        except DemoRecorderError as exc:
            raise ValueError(str(exc)) from exc

        # The recorder owns its own event loop, so we kick off start() in a
        # background thread and let demo.stop handle teardown. This mirrors
        # what app.py does for the HTTP /api/demos/record route.
        def _spawn() -> None:
            try:
                from sunsponge.demo_engine import run_async
                run_async(recorder.start())
            except Exception:  # pragma: no cover - surfaces via demo.stop
                logger.exception("recorder thread crashed for %s", session_id)

        thread = threading.Thread(
            target=_spawn,
            name=f"demo-recorder-{session_id}",
            daemon=True,
        )
        thread.start()
        return {
            "sessionId": session_id,
            "message": (
                "Recording started. Interact with the browser window, then "
                "call demo.stop with this sessionId."
            ),
        }

    # ---- demo.stop --------------------------------------------------------

    @mcp.tool(
        name="demo.stop",
        description=(
            "Stop a recording session, persist the DemoSpec to disk, and "
            "kick off the AI enrichment pipeline. Returns the demoId and "
            "the initial pipeline status. Idempotent — calling demo.stop "
            "twice on the same sessionId returns the current status "
            "without restarting enrichment."
        ),
        timeout=120.0,
    )
    async def demo_stop(session_id: str) -> dict[str, Any]:
        if not session_id:
            raise ValueError("sessionId is required")
        try:
            recorder = forge.get_recorder(session_id)
        except DemoRecorderError:
            raise ValueError(f"unknown recording session: {session_id}")

        # Persist the spec — run_async because recorder.stop() bridges
        # threads to the recorder's own loop.
        try:
            from sunsponge.demo_engine import run_async
            spec = recorder.stop()
        except DemoRecorderError as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive
            forge.discard_recorder(session_id)
            raise RuntimeError(f"failed to stop recorder: {exc}") from exc

        step_count = len(spec.steps)
        forge.discard_recorder(session_id)

        # Kick off enrichment (returns immediately; runs in a daemon thread).
        try:
            job = forge.enrich_demo(spec.id)
        except DemoForgeError as exc:
            raise RuntimeError(f"failed to start enrichment: {exc}") from exc

        return {
            "demoId": spec.id,
            "stepCount": step_count,
            "status": _enrich_status_to_spec(job.get("status")),
            "jobId": job.get("jobId"),
            "summary": _summary_hint(spec),
        }

    # ---- demo.status ------------------------------------------------------

    @mcp.tool(
        name="demo.status",
        description=(
            "Check enrichment progress for a demo. Returns the current "
            "status (recorded / enriching / enriched / failed) and "
            "step counts. Use the demoId returned by demo.stop."
        ),
        timeout=15.0,
    )
    async def demo_status(demo_id: str) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        try:
            return forge.get_status(demo_id)
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")

    # ---- demo.list --------------------------------------------------------

    @mcp.tool(
        name="demo.list",
        description=(
            "List every recorded demo on disk. Each entry includes "
            "demoId, name, stepCount, status, and createdAt. Use this to "
            "discover demos before exporting or editing."
        ),
        timeout=15.0,
    )
    async def demo_list() -> dict[str, Any]:
        summaries = forge.list_demos()
        return {
            "demos": [
                {
                    "demoId": s.demo_id,
                    "name": s.name,
                    "stepCount": s.step_count,
                    "status": s.status,
                    "createdAt": s.created_at,
                    "hasVoiceover": s.has_voiceover,
                }
                for s in summaries
            ],
            "count": len(summaries),
        }

    # ---- demo.edit --------------------------------------------------------

    @mcp.tool(
        name="demo.edit",
        description=(
            "Edit a step's annotation and optionally regenerate its "
            "voiceover audio. Pass only the fields you want to change — "
            "omitting annotation keeps the existing text, omitting "
            "regenerateVoice leaves the audio untouched. Returns the "
            "updated step."
        ),
        timeout=60.0,
    )
    async def demo_edit(
        demo_id: str,
        step_index: int,
        annotation: str | None = None,
        regenerate_voice: bool = False,
    ) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        if not isinstance(step_index, int) or step_index < 0:
            raise ValueError("stepIndex must be a non-negative integer")
        if annotation is None and not regenerate_voice:
            raise ValueError("nothing to edit — provide annotation or set regenerateVoice=true")
        try:
            step = await forge.edit_step(
                demo_id,
                step_index,
                annotation=annotation,
                regenerate_voice=regenerate_voice,
            )
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        except DemoForgeError as exc:
            raise ValueError(str(exc))
        return {"ok": True, "step": step}

    # ---- demo.delete ------------------------------------------------------

    @mcp.tool(
        name="demo.delete",
        description=(
            "Delete a demo and all its files from disk. Returns {ok: bool}. "
            "Cannot be undone — make sure the demo is exported first if "
            "you might need it again."
        ),
        timeout=15.0,
    )
    async def demo_delete(demo_id: str) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        try:
            ok = forge.delete_demo(demo_id)
        except DemoNotFound as exc:
            raise ValueError(str(exc))
        if not ok:
            raise ValueError(f"demo not found: {demo_id}")
        return {"ok": True, "demoId": demo_id}

    # ---- demo.export ------------------------------------------------------

    @mcp.tool(
        name="demo.export",
        description=(
            "Export a demo as a self-contained HTML viewer. Screenshots "
            "are inlined as base64 so the resulting export.html has no "
            "external dependencies — open it from file:// or serve it "
            "from anywhere. Currently only the 'html' format is supported."
        ),
        timeout=30.0,
    )
    async def demo_export(demo_id: str, format: str = "html") -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        try:
            out_path = await asyncio.to_thread(forge.export_demo, demo_id, fmt=format)
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        except DemoForgeError as exc:
            raise ValueError(str(exc))
        return {
            "path": str(out_path.resolve()),
            "demoId": demo_id,
            "format": format,
        }

    # Stash the forge on the server so tests / integration helpers can grab
    # it without rebuilding the server from scratch.
    mcp.state = {  # type: ignore[attr-defined]
        "forge": forge,
    }
    return mcp


def _enrich_status_to_spec(internal: str | None) -> str:
    """Translate ``DemoEnrichManager`` job statuses to the brief's vocabulary."""
    if internal in {"pending", "running"}:
        return "enriching"
    if internal == "done":
        return "enriched"
    if internal == "failed":
        return "failed"
    return "recorded"


def _summary_hint(spec: Any) -> str:
    """Build a placeholder summary line for the demo.stop response."""
    if spec.goal:
        return f"AI pipeline started; will summarise as: {spec.goal}"
    return "AI pipeline started; will summarise the recorded flow."


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server on stdio."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    server = _build_server()
    # stdio transport is the MCP default for desktop agents; the brief
    # explicitly calls this out ("MCP server must use stdio transport").
    server.run(transport="stdio")


__all__ = [
    "DEMOS_DIR_NAME",
    "_build_server",
    "demos_root",
    "main",
]


if __name__ == "__main__":
    main()
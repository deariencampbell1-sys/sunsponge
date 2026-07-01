# Phase 4 Brief: DemoForge MCP Tools

## Goal
Expose the demo pipeline as MCP tools so any pi agent can command it. "Hey, record a demo of the signup flow." → browser opens → user clicks through → AI enriches → export as standalone HTML viewer.

## Architecture

```
Agent (pi, swarm worker, Telegram bot)
  │
  │  MCP protocol (JSON-RPC over stdio or HTTP)
  ▼
DemoForge MCP Server (Python, FastMCP)
  │
  │  Internal calls
  ├──▶ SunSponge API (record / stop / enrich / export)
  │
  ▼
DemoSpecs on disk
```

## Tools

### `demo.record`

Start recording a product demo. Launches a headful Playwright browser window.

```yaml
name: demo.record
description: Start recording a product demo. Opens a browser window — interact with it, then call demo.stop.
parameters:
  url: string (required)       # URL to open
  name: string (required)       # demo name for the spec metadata
  goal: string (optional)       # what the flow demonstrates
  viewport: object (optional)   # { width: 1440, height: 900 }
returns:
  sessionId: string
  message: string               # "Recording started. Click through your flow, then run demo.stop."
```

### `demo.stop`

Stop recording and run the AI enrichment pipeline. Returns when enrichment is complete.

```yaml
name: demo.stop
description: Stop recording. Processes screenshots with AI — adds annotations, voiceover, cursor paths.
parameters:
  sessionId: string (required)
returns:
  demoId: string
  stepCount: number
  status: string                # "recorded" | "enriching" | "enriched" | "failed"
  summary: string               # AI-generated 2-3 sentence flow summary
```

### `demo.status`

Check pipeline progress.

```yaml
name: demo.status
description: Check enrichment progress for a demo.
parameters:
  demoId: string (required)
returns:
  demoId: string
  status: string                # "pending" | "enriching" | "enriched" | "failed"
  stepsCompleted: number
  totalSteps: number
  error: string (optional)
```

### `demo.list`

List all recorded demos.

```yaml
name: demo.list
description: List all recorded demos in the workspace.
parameters: none
returns:
  demos: [{ demoId, name, stepCount, status, createdAt }]
```

### `demo.edit`

Edit a step's annotation and optionally regenerate its voiceover.

```yaml
name: demo.edit
description: Edit a step's annotation text and optionally regenerate its voiceover.
parameters:
  demoId: string (required)
  stepIndex: number (required)
  annotation: string (optional)  # new annotation text
  regenerateVoice: boolean (optional, default false)  # re-run TTS for this step
returns:
  ok: boolean
  step: object                   # updated step data
```

### `demo.delete`

Delete a demo and its files.

```yaml
name: demo.delete
description: Delete a demo and all its screenshots from disk.
parameters:
  demoId: string (required)
returns:
  ok: boolean
```

### `demo.export`

Export a demo as a standalone viewer HTML file.

```yaml
name: demo.export
description: Export a demo as a self-contained HTML viewer file. Embeds DemoSpec + screenshots inline.
parameters:
  demoId: string (required)
  format: string (optional, default "html")  # "html" only for now
returns:
  path: string                   # absolute path to the exported file
  url: string (optional)         # if served, the URL
```

## Implementation

### Option A: FastMCP (recommended)

Use the `fastmcp` Python package — the simplest way to build an MCP server:

```python
from fastmcp import FastMCP
from sunsponge.demo_engine import DemoManager, DemoRecorderError
from sunsponge.demo_ai import DemoAI

mcp = FastMCP("DemoForge")

@mcp.tool()
async def demo_record(url: str, name: str, goal: str = "", viewport: dict = None) -> dict:
    ...

@mcp.tool()
async def demo_stop(session_id: str) -> dict:
    ...

@mcp.tool()
async def demo_status(demo_id: str) -> dict:
    ...

# ... etc
```

Install: `pip install fastmcp`

Run: `python -m sunsponge.demo_mcp` (starts stdio MCP server)

### Option B: Standalone script

If fastmcp adds too much weight, build a minimal MCP server using the JSON-RPC stdio protocol directly (~100 lines of Python). Spec: https://spec.modelcontextprotocol.io/

Either way, the MCP server:
1. Starts a demo manager (reuses `DemoManager` from Phase 1)
2. Loads existing demos from the `demos/` directory
3. Exposes 7 tools
4. Communicates via stdio JSON-RPC 2.0

### Export Logic

`demo.export` reads the DemoSpec JSON, embeds it into the viewer template HTML (from Phase 2, `viewer/demo-viewer.html`), and saves as a standalone file. Screenshots are inlined as base64 data URIs so the file works from anywhere.

```python
def export_demo(demo_id: str, demo_spec: DemoSpec, viewer_template: str) -> str:
    # Read viewer template
    template = (PROJECT_ROOT / "viewer" / "demo-viewer.html").read_text()

    # Find the <script id="demo-data"> block and replace its content
    spec_json = json.dumps(demo_spec.to_dict(), indent=2)
    exported = template.replace(
        '<script id="demo-data" type="application/json">\n...',
        f'<script id="demo-data" type="application/json">\n{spec_json}\n',
    )

    # Write to demos/{demo_id}/export.html
    out_path = PROJECT_ROOT / "demos" / demo_id / "export.html"
    out_path.write_text(exported)
    return str(out_path)
```

## File: `src/sunsponge/demo_mcp.py`

## Dependencies

Add to `requirements.txt`:
```
fastmcp>=2.0
```

## Agent Workflow Example

```
Agent: demo.record("http://localhost:8787", "SunSponge Onboarding", "User signs up and captures their first screenshot")
→ { "sessionId": "abc123", "message": "Recording started." }

[User clicks through the flow in the browser window]

Agent: demo.stop("abc123")
→ { "demoId": "abc123", "stepCount": 5, "status": "enriching" }

[AI pipeline runs: vision → script → TTS → cursor → animation timeline]

Agent: demo.status("abc123")
→ { "status": "enriched", "stepsCompleted": 5, "totalSteps": 5 }

Agent: demo.export("abc123")
→ { "path": "demos/abc123/export.html" }

Agent: "Demo exported to demos/abc123/export.html. Open it to view the walkthrough."
```

## Hard Constraints

- MCP server must use stdio transport (compatible with pi's MCP adapter)
- Must reuse existing `DemoManager` and `DemoAI` classes (no duplication)
- Export must produce a single standalone HTML file (no external assets)
- All tools must return clean error messages (no stack traces)
- `demo.stop` must handle the case where the AI pipeline is already running (return status, don't double-run)

## Acceptance Criteria

1. Start MCP server: `python -m sunsponge.demo_mcp`
2. pi connects to the MCP server (via `mcp-cli` or pi's MCP adapter)
3. `demo.record("https://example.com", "Test")` opens browser
4. Click 3 things in the browser
5. `demo.stop(sessionId)` runs AI pipeline and returns enriched spec
6. `demo.list()` shows the new demo
7. `demo.export(demoId)` produces a standalone HTML file
8. Open the exported HTML — screenshots visible, hotspots positioned, annotations showing

## Deliverable

`src/sunsponge/demo_mcp.py` + updated `requirements.txt`. Report: line count, tool list, a sample agent conversation log showing the full record→enrich→export flow.

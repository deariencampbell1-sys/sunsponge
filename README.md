# RHOBEAR Captur'd

Portfolio-quality screenshots of **your own built HTML**, in every rested state.

Captur'd is a **desktop** tool: you run it on your own machine, point it at the
HTML/UI your agent built for you, hand it a **pathway map** (the interaction tree
of buttons and states), and it takes a clean, animation-free shot of every state
across the viewports and color schemes you pick. The map removes all guesswork,
so the run is **deterministic and fast** — no sitting around while a crawler
"figures life out."

Not AI-powered, and it does **not** browse the internet. Just Playwright, your
map, and PNG/JPEG output.

- **No URLs, no crawling, no sitemaps.** The input is your built HTML + a map.
- **The map comes from your agent.** Ask your LLM to map out the pathways
  (buttons/states) of the thing it built; paste that markdown (or verifier JSON)
  into Captur'd, or upload it.
- **Runs locally.** Reading your local HTML and writing shots to a local folder
  are the whole point — nothing leaves your machine.

## Brand

Captur'd is a sibling to [RHOBEAR Designs](https://github.com/deariencampbell1-sys/rhobear-designs)
(the open-source website editor) in the RHOBEAR family. Same deep-navy canvas,
its own accent — sun-amber (Designs owns red; the Hub owns teal):

| Token | Value | Use |
|-------|-------|-----|
| Navy | `#1A1A2E` | Shared base with Designs |
| Canvas | `#11111d` | Page background |
| Accent | `#F5A524` | Sun-amber (Designs uses red) |
| Accent hover | `#D98A12` | Buttons, highlights |
| Ink | `#ECEAF2` | Primary text |
| Muted | `#A6A3B8` | Secondary text |

## Install

```bash
cd sunsponge
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
python -m playwright install chromium
```

## Run it

```bash
export PYTHONPATH=src            # Windows: set PYTHONPATH=src
python -m sunsponge.app          # uvicorn on http://127.0.0.1:8787
```

Open http://127.0.0.1:8787 for the capture studio. Give it two things:

1. **Built HTML** — a local path or folder (e.g. `C:\site\index.html` or `C:\site`).
2. **Pathway map** — paste the markdown your agent produced, or upload the `.md` /
   `.json`.

Pick your viewports/schemes and hit **Capture**.

UI endpoints (localhost, browser-facing):

- `POST /api/rested-captures/jobs` — start a capture job
- `GET /api/rested-captures/jobs/{job_id}` — poll job status
- `GET /api/rested-captures/jobs/{job_id}/download` — download ZIP

## The pathway map

Captur'd knows *what* to capture from a map — the interaction tree your own LLM
produces. It accepts the same shapes as
[rhobear-verifier](https://github.com/deariencampbell1-sys/rhobear-verifier):

**Markdown manifest** (`pathway-manifest.md`) — tables of pathways (id, location,
trigger, status) and routes.

**Verifier JSON** (`verifier-report.json`) — `{ version, repo, manifest_summary,
summary, checks{<name>:{findings[]}} }`.

Each pathway expands to one capture target per viewport/scheme. Output files are
named by pathway id and status (e.g. `001-capture-start-wired-desktop-light.png`).

You can also drive it from the CLI for a quick plan check:

```bash
set PYTHONPATH=src
python scripts/demo_map_plan.py --manifest C:\path\to\pathway-manifest.md --base-url file:///C:/site/index.html
python scripts/demo_map_plan.py --map      C:\path\to\verifier-report.json --base-url file:///C:/site/index.html
```

Payload / API fields (same semantics everywhere):

- `pathway_manifest` — the manifest **markdown, pasted** (primary path)
- `map_text` — verifier **JSON, pasted**
- `manifest_path` / `map_path` — a local file path (CLI convenience)
- `base_url` — where the built HTML lives (a local path or `file://` URL)

## Agent-control API (`/v1/capture`)

The Captur'd slice of the Family API Contract — the form a sibling RHOBEAR agent
calls. Async (Playwright takes time), bearer-token gated, and wrapped in a
`{ok, data}` / `{ok, error}` envelope. It is **map-driven only** — there is no
URL/sitemap input.

Set the service token before starting the server (the `/v1` routes are
**fail-closed** — they return `503` until it is configured):

```bash
export RHOBEAR_SERVICE_TOKEN=<secret>
# Optional: public base for the image_ref URLs when behind a proxy:
export SUNSPONGE_EXTERNAL_BASE_URL=https://captures.example.com
```

`POST /v1/capture` — enqueue a rested-state, animation-free full-page capture
across the requested viewports/schemes:

```bash
curl -X POST http://127.0.0.1:8787/v1/capture \
  -H "Authorization: Bearer $RHOBEAR_SERVICE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"pathway_map":"# Pathway Manifest\n\n## 1. Pathways Table\n| id | location | status |\n|---|---|---|\n| home | App.jsx:1 | WIRED |",
       "base_url":"file:///C:/site/index.html","workspace_id":"demo",
       "viewports":["desktop","tablet","mobile"],"color_schemes":["light","dark"]}'
# -> {"ok":true,"data":{"job_id":"d14b1d26a582","status":"queued"}}
```

Request body:

| Field | Required | Notes |
|-------|----------|-------|
| `pathway_map` | yes | the pathway-manifest markdown (or verifier JSON), pasted |
| `base_url` | recommended | where the built HTML lives (local path / `file://`) |
| `workspace_id` | yes | namespaces shot storage |
| `viewports` | no | subset of `desktop` / `tablet` / `mobile` (default: all three) |
| `color_schemes` | no | subset of `light` / `dark` (default: both) |
| `full_page` | no | default `true` |
| `format` | no | `png` (default) or `jpeg` |

`GET /v1/capture/{job_id}` — poll status. `shots[]` carries one row per captured
state/viewport/scheme; `image_ref` is a **storage handle** (a token-gated URL the
caller fetches separately — never inline bytes):

```bash
curl -H "Authorization: Bearer $RHOBEAR_SERVICE_TOKEN" \
  http://127.0.0.1:8787/v1/capture/d14b1d26a582
# -> {"ok":true,"data":{"status":"done","total":6,"completed":6,"failed":0,
#       "shots":[{"url":"file:///C:/site/index.html","viewport":"desktop","scheme":"light",
#                 "image_ref":"http://127.0.0.1:8787/v1/capture/.../shots/001-...png"}],
#       "errors":[]}}
```

`GET /v1/capture/{job_id}/shots/{file}` — fetch one shot by its `image_ref`
(also bearer-token gated). Status values: `queued` → `running` →
`done` / `done_with_errors` / `failed`.

The `/v1` routes are additive: they wrap the **same** capture engine as the CLI,
UI, and `/api` routes above.

## Tests

```bash
set PYTHONPATH=src
pytest
```

## Layout

```
src/sunsponge/
  capture_service.py   # engine: map-driven plan + Playwright capture (RestedCaptureManager)
  pathway_map.py       # pathway-manifest.md / verifier-JSON ingestion
  app.py               # FastAPI service: /api (UI) + /v1/capture (agent-control) + static UI
ui/
  index.html           # Captur'd shell
  RestedCaptureView.jsx
tests/
  test_capture_service.py
  test_pathway_map.py
  test_paste_map.py
  test_v1_capture_api.py   # agent-control /v1/capture contract
  test_release_blockers.py
scripts/
  demo_map_plan.py
  smoke_capture.py
```

## License

MIT

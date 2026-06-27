# SunSponge

Website screenshot capture. Load a site, discover its pages, and capture many viewports and color schemes in one job.

Not AI-powered. Just Playwright, a crawl plan, and PNG output.

## Brand

SunSponge is a sibling to RHOBEAR Designs (the open-source website editor). Same deep-navy canvas, different accent:

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

## Run the service

```bash
export PYTHONPATH=src            # Windows: set PYTHONPATH=src
python -m sunsponge.app          # uvicorn on http://127.0.0.1:8787
```

or directly with uvicorn (handy for setting environment variables):

```bash
PYTHONPATH=src python -m uvicorn sunsponge.app:app --host 127.0.0.1 --port 8787
```

Open http://127.0.0.1:8787 for the capture UI.

UI endpoints (open, browser-facing):

- `POST /api/rested-captures/jobs` — start a capture job
- `GET /api/rested-captures/jobs/{job_id}` — poll job status
- `GET /api/rested-captures/jobs/{job_id}/download` — download ZIP

## Agent-control API (`/v1/capture`)

The SunSponge slice of the Family API Contract — the form the Plans agent calls.
Async (Playwright takes time), bearer-token gated, and wrapped in a
`{ok, data}` / `{ok, error}` envelope.

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
  -d '{"url":"https://example.com/","workspace_id":"demo",
       "viewports":["desktop","tablet","mobile"],"color_schemes":["light","dark"]}'
# -> {"ok":true,"data":{"job_id":"d14b1d26a582","status":"queued"}}
```

Request body:

| Field | Required | Notes |
|-------|----------|-------|
| `url` | one of `url`/`sitemap` | single URL or a list of URLs |
| `sitemap` | one of `url`/`sitemap` | sitemap URL (expanded into page URLs) |
| `workspace_id` | yes | namespaces shot storage |
| `viewports` | no | subset of `desktop` / `tablet` / `mobile` (default: all three) |
| `color_schemes` | no | subset of `light` / `dark` (default: both) |
| `full_page` | no | default `true` |
| `format` | no | `png` (default) or `jpeg` |

`GET /v1/capture/{job_id}` — poll status. `shots[]` carries one row per captured
page/viewport/scheme; `image_ref` is a **storage handle** (a token-gated URL the
caller fetches separately — never inline bytes):

```bash
curl -H "Authorization: Bearer $RHOBEAR_SERVICE_TOKEN" \
  http://127.0.0.1:8787/v1/capture/d14b1d26a582
# -> {"ok":true,"data":{"status":"done","total":6,"completed":6,"failed":0,
#       "shots":[{"url":"https://example.com/","viewport":"desktop","scheme":"light",
#                 "image_ref":"http://127.0.0.1:8787/v1/capture/.../shots/001-...png"}],
#       "errors":[]}}
```

`GET /v1/capture/{job_id}/shots/{file}` — fetch one shot by its `image_ref`
(also bearer-token gated). Status values: `queued` → `running` →
`done` / `done_with_errors` / `failed`.

The `/v1` routes are additive: they wrap the **same** capture engine as the CLI,
UI, and `/api` routes above. See `evidence/capture-api/SUMMARY.md` for a live
end-to-end run.

## Feed it a map

SunSponge can skip blind crawling when you already have a pathway map. It accepts the same shapes as [rhobear-verifier](https://github.com/deariencampbell1-sys/rhobear-verifier):

**Markdown manifest** (`pathway-manifest.md`) — tables of pathways (id, location, trigger, status) and routes.

```bash
set PYTHONPATH=src
python scripts/demo_map_plan.py --manifest C:\path\to\docs\pathway-manifest.md --base-url https://example.com
```

**Verifier JSON** (`verifier-report.json`) — `{ version, repo, manifest_summary, summary, checks{<name>:{findings[]}} }`.

```bash
set PYTHONPATH=src
python scripts/demo_map_plan.py --map C:\path\to\verifier-report.json --base-url https://example.com
```

Each pathway expands to one capture target per viewport/scheme. Output files are named by pathway id and status (e.g. `001-capture-start-wired-desktop-light.png`).

API fields (same semantics):

- `manifest_path` — path to `.md` on the server
- `map_path` — path to verifier `.json` on the server
- `base_url` — page origin for resolving SPA views from pathway locations

Crawl and XML sitemap modes still work when no map is provided.

## Capture a site (CLI smoke)

Captures two pages from example.com into `./out`:

```bash
set PYTHONPATH=src
python scripts/smoke_capture.py
```

## Tests

```bash
set PYTHONPATH=src
pytest
```

## Layout

```
src/sunsponge/
  capture_service.py   # engine: crawl, sitemap, Playwright capture (RestedCaptureManager)
  pathway_map.py       # pathway-manifest.md / verifier-JSON ingestion
  app.py               # FastAPI service: /api (UI) + /v1/capture (agent-control) + static UI
ui/
  index.html           # SunSponge shell
  RestedCaptureView.jsx
tests/
  test_capture_service.py
  test_v1_capture_api.py   # agent-control /v1/capture contract
scripts/
  smoke_capture.py
```

## License

MIT
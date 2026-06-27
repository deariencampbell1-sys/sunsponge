# capture-api evidence — agent-control `/v1/capture` API

Verification that the SunSponge slice of the Family API Contract works end-to-end
against a live `uvicorn` server. Captured 2026-06-27 on branch `lane/capture-agent-api`.

## Service

```bash
RHOBEAR_SERVICE_TOKEN=dev-token \
SUNSPONGE_APP_DATA=/tmp/ss-ev \
PYTHONPATH=src python3 -m uvicorn sunsponge.app:app --host 127.0.0.1 --port 8787
```

Full server log: `server.log`. Chromium was available locally; no engine breakage
was found (baseline capture of example.com across desktop/mobile × light/dark
returned 4/4 ok before any code change).

## Contract checks (all live)

| Check | Result |
|-------|--------|
| `POST /v1/capture` missing/ wrong bearer token | `401` `{"ok":false,"error":"invalid or missing service token"}` |
| shot fetch without token | `401` |
| `POST` with no `url`/`sitemap` | `400` `{"ok":false,"error":"provide 'url' or 'sitemap'"}` |
| `POST` missing `workspace_id` | `400` `{"ok":false,"error":"workspace_id is required"}` |
| `POST` with `url:"not-a-url"` | `400` (rejected before Playwright) |
| `POST` happy path | `200` `{"ok":true,"data":{"job_id":"d14b1d26a582","status":"queued"}}` |
| `GET /v1/capture/{job_id}` after done | `200`, `status:"done"`, `total:6`, `failed:0`, 6 `shots[]` with `image_ref` |
| unknown job `GET` | `404` `{"ok":false,"error":"capture job not found"}` |
| path-traversal shot fetch (`..%2f..%2fREADME.md`) | `404` (basename-reduced, no escape) |
| `workspace_id` scoping on disk | `/tmp/ss-ev/captures/demo/<job_id>` ✓ |

Transcripts: `01-post-capture.json`, `02-get-status.json`, `job-manifest.json`.

## `image_ref` is a handle, not bytes

Each shot row carries `image_ref` = a token-gated URL, e.g.
`http://127.0.0.1:8787/v1/capture/d14b1d26a582/shots/001-example-com-desktop-light.png`.
The shot bytes are fetched separately via `GET /v1/capture/{job_id}/shots/{file}`.

## Viewports × color_schemes honored

Request: `viewports:["desktop","tablet","mobile"]`, `color_schemes:["light","dark"]`
→ 6 captures, one per combination. PNG widths (parsed from IHDR) match the
requested viewports exactly:

| Shot | PNG size | expected width |
|------|----------|----------------|
| desktop-light / dark | 1440×1000 | 1440 ✓ |
| tablet-light / dark  | 834×1112  | 834 ✓ |
| mobile-light / dark  | 390×844   | 390 ✓ |

Samples: `samples/{viewport}-{scheme}.png`.

## Rested-state / animation-free

The engine (unchanged) settles each page before the screenshot: injects `REST_CSS`
(zeroes animation/transition duration, disables scroll-behavior), sets
`reduced_motion="reduce"`, pauses `<video>`, waits `document.fonts.ready`, then
captures with Playwright `animations:"disabled"` and `full_page:true`. example.com
is static, so the captures are the settled full-page render.

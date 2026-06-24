# SunSponge pressure test — `pt-sunsponge`

**Lane:** `pt-sunsponge` (autonomous pressure test of `deariencampbell1-sys/sunsponge`)
**Branch under test:** `origin/feat/stand-up-sunsponge` (PR base)
**Branch of this report:** `test/pressure-sunsponge`
**Date:** 2026-06-24
**Verifier model:** MiniMax-M3
**Environment:** Ubuntu 26.04 x86_64, Python 3.14.4, Playwright 1.60.0 + Chromium
(via `ms-playwright/chromium-1228`, already cached on the VPS — `playwright
install chromium` itself fails on Ubuntu 26.04 because Playwright hasn't added
it to the support matrix, so we relied on the pre-installed `chromium-1228`).
The app itself, `python -m sunsponge.app`, has no such restriction.

---

## Verdict — RELEASE-READY: **no**

The core capture pipeline works end-to-end and the map-ingestion path holds for
both `--manifest` (pathway-manifest.md) and `--map` (verifier-report.json),
including a 160-target large map. The two REDs below are real but small.

### Blockers (must fix before release)

1. **Bad `manifest_path` / `map_path` return HTTP 500 + raw Python traceback
   in the server log** (file-not-found leaks path). Should be a 400 with a
   user-friendly error. See `evidence/pt-sunsponge/500-traceback.md` and
   `500-traceback.json`.
2. **URL input is not validated.** Posting `{"urls": ["not-a-url"]}` is
   silently coerced to `https://not-a-url/` and queued; it then fails inside
   Playwright with a DNS error. Users won't realize they typed the URL wrong
   until the run completes.

### Non-blockers (worth fixing soon, not release-blocking)

3. **Job state is in-memory only.** Restarting the server makes all in-flight
   and historical jobs invisible (`GET /api/rested-captures/jobs/{id}` returns
   404). Captures that completed but were not yet downloaded via `/download`
   still exist on disk, but cannot be re-downloaded without a job_id lookup
   service.
4. **Per-result `pathway_id` / `pathway_status` are not in the API response
   `results[]` objects.** They are embedded in the filename and in the
   internal `state_id`, but the JSON `results[]` row only has `url`,
   `viewport`, `scheme`, `state_id`, `file`, `status`, `bytes`, `attempts`,
   `elapsed_ms`. Tooling that wants to group results by pathway has to parse
   the filename.

### Everything else: green

- App boots cleanly on `:8787`, UI is served, `/api/health` is 200.
- Single URL capture produces correct viewport×scheme variants with the
  expected `NNN-{host}-{viewport}-{scheme}.png` naming.
- Map ingestion produces `NNN-{pathway-id}-{status}-{viewport}-{scheme}.png`
  exactly matching the lane's expected pattern (e.g.
  `001-home-light-wired-desktop-light.png`).
- 160-target large map finishes in ~31s with `concurrency=4` and 0 failures.
- Three concurrent jobs all complete successfully.
- A bad host (DNS NXDOMAIN) is reported per-result with a clear
  `done_with_errors` status, no crash, no traceback in the log.
- `pytest` → 14 passed, 2 skipped.
- The `/download` endpoint returns a valid ZIP with `manifest.json` + the
  expected `shots/*.png` (verified by unzipping — see
  `evidence/pt-sunsponge/single-download.zip`).

---

## Matrix

| # | Area                       | Result | Evidence |
|---|----------------------------|--------|----------|
| 1 | Boot                       | PASS   | `health.json`, `ui.html`, `server.log` (no tracebacks on boot) |
| 2 | Single capture             | PASS   | `single-final.json`, `single-download.zip`, `samples/001-example-com-*.png` |
| 3 | Map ingestion              | PASS   | `map-final.json`, `samples/00{1,2,3}-*.png`, `tests/fixtures/pt-sunspressure-manifest.md` |
| 4 | Robustness (multi + bad + large) | PARTIAL PASS | `multi-N-final.json`, `bad-final.json`, `large-final.json`, `500-traceback.*` |
| 5 | Error surface              | PARTIAL PASS | 2 FileNotFoundError tracebacks logged (see "Blockers" #1) but no crashes |

---

## 1. Boot — PASS

```
$ PYTHONPATH=src .venv/bin/python -m sunsponge.app
INFO:     Started server process [2208626]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8787 (Press CTRL+C to quit)
```

Probes:

| Probe | Result |
|-------|--------|
| `GET /api/health` | `200 {"status":"ok","service":"sunsponge"}` |
| `GET /` | `200`, 1311 B HTML (`ui.html`) |
| `GET /RestedCaptureView.jsx` | `200`, `application/octet-stream` (the JSX is served raw — fine, the file is referenced as `<script type="module">` from `ui/index.html`) |
| `GET /sunsponge.css` | `200`, `text/css` |
| `GET /api.js` | `200`, `text/javascript` |
| `pytest` | `14 passed, 2 skipped` |

Note: `playwright install chromium` exits with `Playwright does not support
chromium on ubuntu26.04-x64` on this host, but the pre-cached
`/home/slang/.cache/ms-playwright/chromium-1228/chrome-linux64/` is on disk
and works, so all captures ran cleanly.

---

## 2. Single capture — PASS

Request:

```bash
curl -X POST http://127.0.0.1:8787/api/rested-captures/jobs \
  -H 'content-type: application/json' \
  -d '{"urls":["https://example.com/"],
       "viewports":["desktop","mobile"],
       "schemes":["light","dark"],
       "format":"png","full_page":false,
       "concurrency":2,"wait_ms":300,"timeout_ms":20000,
       "export_dir":"./out/single","export_mode":"folder",
       "name":"single-example"}'
```

Final state: `status=done completed=4/4 failed=0`.

Result rows (one per viewport×scheme variant):

| url | viewport | scheme | state_id | file | bytes |
|-----|----------|--------|----------|------|-------|
| https://example.com/ | desktop | light | desktop-light | 001-example-com-desktop-light.png | 20260 |
| https://example.com/ | desktop | dark  | desktop-dark  | 001-example-com-desktop-dark.png  | 20260 |
| https://example.com/ | mobile  | light | mobile-light  | 001-example-com-mobile-light.png  | 17224 |
| https://example.com/ | mobile  | dark  | mobile-dark   | 001-example-com-mobile-dark.png   | 17224 |

Both `export_mode=folder` (4 PNGs + `manifest.json` under
`out/single/single-example/`) and `export_mode=zip`
(`out/single/single-example.zip`, 57 286 B) are produced. The
`GET /api/rested-captures/jobs/{id}/download` endpoint also returns the ZIP
(`evidence/pt-sunsponge/single-download.zip`, 57 286 B,
`Content-Type: application/zip`); unzipping it yields `manifest.json` and the
4 expected PNGs.

Naming matches the lane spec: `NNN-{host}-{viewport}-{scheme}.png`.

---

## 3. Map ingestion — PASS

**Fixture** (committed under `tests/fixtures/`):
- `pt-sunspressure-manifest.md` — 3 pathways (`home-light` WIRED,
  `about-page` WIRED, `missing-route` UNWIRED), each resolving to
  `https://example.com/?view={capture|catalog|workbench}` via the standard
  `ViewName.jsx` → `?view=` mapping.
- `pt-sunspressure-large.json` — 40 synthetic pathways, generated for the
  robustness run (see §4).

**Plan output (`demo_map_plan.py`):**

```
$ PYTHONPATH=src python scripts/demo_map_plan.py \
    --manifest tests/fixtures/pt-sunspressure-manifest.md \
    --base-url https://example.com/
pathways: 3 -> targets: 3 (3 unique URLs)
mode: map source: manifest base: https://example.com/
  home-light                WIRED     desktop light https://example.com/?view=capture
  about-page                WIRED     desktop light https://example.com/?view=catalog
  missing-route             UNWIRED   desktop light https://example.com/?view=workbench
```

**Capture:** 3 pathways × 2 viewports × 2 schemes = **12 PNGs, all OK**.

Naming matches the lane spec exactly: `NNN-{pathway-id}-{status}-{viewport}-{scheme}.png`,
e.g. `001-home-light-wired-desktop-light.png`,
`002-about-page-wired-mobile-dark.png`,
`003-missing-route-unwired-desktop-light.png`.

The 12 PNGs are in `evidence/pt-sunsponge/map-out/pt-map-fixture/shots/` and
representative samples in `evidence/pt-sunsponge/samples/`.

The map ingestion pipeline also works with verifier JSON:
`--map tests/fixtures/sample-verifier-map.json` (the upstream sample) parses
and the `--map tests/fixtures/pt-sunspressure-large.json` (40 pathways) runs
end-to-end in §4 below.

---

## 4. Robustness — PARTIAL PASS

### 4a. Bad host (DNS NXDOMAIN) — PASS (graceful)

```bash
curl -X POST .../jobs -d '{"urls":["https://this-host-definitely-does-not-exist-12345.invalid/"],...}'
```

Final: `status=done_with_errors completed=1/1 failed=1`. Per-result error:

```
Page.goto: net::ERR_NAME_NOT_RESOLVED at https://this-host-definitely-does-not-exist-12345.invalid/
Call log:
  - navigating to "https://this-host-definitely-does-not-exist-12345.invalid/", waiting un
```

No crash, no traceback in the log, server stays healthy.

### 4b. Three concurrent jobs — PASS

Three independent jobs queued in rapid succession (each: 1 URL, 1 viewport, 1
scheme). All three reported `status=done completed=1/1 failed=0`. Server
remained healthy (`/api/health` 200 throughout).

### 4c. Large map (40 pathways, 160 targets) — PASS

`tests/fixtures/pt-sunspressure-large.json` → 40 pathways × 2 viewports ×
2 schemes = **160 targets**, all resolved to `https://example.com/` because
the synthetic map uses the standard `ViewName.jsx` → `?view=` mapping.

```
status:    done
completed: 160 / 160
failed:    0
discovery: {mode: map, source: verifier-json, pathway_count: 40,
            route_count: 12, page_count: 1, target_count: 160, ...}
elapsed:   ~31s  (concurrency=4, wait_ms=100, timeout_ms=15000)
```

No hangs, no retries. Output dir (`out/large/pt-large/shots/`) contains all
160 PNGs; samples in `evidence/pt-sunsponge/samples/`.

### 4d. Bad map / manifest paths — RED → Blocker #1

```
POST /api/rested-captures/jobs
  body: {"map_path":"/nonexistent/path/foo.json", "base_url":"https://example.com/"}
→ HTTP 500  {"ok":false,"error":"capture job failed"}

POST /api/rested-captures/jobs
  body: {"manifest_path":"/nonexistent/path/foo.md", "base_url":"https://example.com/"}
→ HTTP 500  {"ok":false,"error":"capture job failed"}
```

The server log contains the raw `FileNotFoundError` traceback for both,
including the offending path. Verbatim from `server.log`:

```
Traceback (most recent call last):
  File "/opt/rhobear/projects/brand-spread/pt-sunsponge/src/sunsponge/app.py", line 77, in post_rested_capture_job
    return _CAPTURE_MANAGER.start(_request_payload(body))
  ...
  File "/opt/rhobear/projects/brand-spread/pt-sunsponge/src/sunsponge/pathway_map.py", line 53, in load_pathway_map
    return parse_verifier_json(Path(map_path))
  File "/opt/rhobear/projects/brand-spread/pt-sunsponge/src/sunsponge/pathway_map.py", line 76, in parse_verifier_json
    data = json.loads(map_path.read_text(encoding="utf-8"))
  ...
FileNotFoundError: [Errno 2] No such file or directory: '/nonexistent/path/foo.json'
INFO:     127.0.0.1:51112 - "POST /api/rested-captures/jobs HTTP/1.1" 500 Internal Server Error
```

Full tracebacks saved as `evidence/pt-sunsponge/500-traceback.json` and
`evidence/pt-sunsponge/500-traceback.md`. **Should be 400** (input
validation) and the body should carry the actual reason, not the generic
`capture job failed`.

### 4e. Loose URL normalization — RED → Blocker #2

```
POST /api/rested-captures/jobs
  body: {"urls":["not-a-url"]}
→ HTTP 200, job queued, total=6 (3 viewports × 2 schemes)
  results[*].url = "https://not-a-url/"
  results[*].status = "failed", err = "Page.goto: net::ERR_NAME_NOT_RESOLVED at https://not-a-url/"
```

By contrast, `{"urls":[""]}` correctly returns `400 {"ok":false,"error":"add
at least one URL"}` — the empty-URL case is handled, but the
no-scheme/no-host case is silently coerced. The 6 capture attempts then
consume real time before failing.

### 4f. Unknown / wrong-server job_id — INFO (non-blocker)

```
GET /api/rested-captures/jobs/nonexistent
→ HTTP 404  {"ok":false,"error":"capture job not found"}
```

Correct. But see Blocker/Non-blocker #3: job state is process-local, so any
job_id from a previous server instance returns 404.

### 4g. Download before completion — PASS (correct status, no crash)

```
GET /api/rested-captures/jobs/{running_job_id}/download
→ HTTP 409  {"ok":false,"error":"capture ZIP is not ready"}
```

The handler is correct: it returns 409 with a clear message rather than
serving a half-built ZIP.

---

## 5. Error surface — PARTIAL PASS

The server log has exactly **2 uncaught `FileNotFoundError` tracebacks** over
the full test run, both from the bad-`map_path` / bad-`manifest_path` cases
in §4d. Both are caught by the catch-all in `app.py` and turned into a 500
response, so the server itself does not crash and `/api/health` stays 200
throughout.

There are **no** tracebacks for: bad host DNS, empty URL, unknown job_id,
download-before-ready, or any normal-capture failure. All such failures are
reported per-result or via a clean 4xx response.

Saved artifacts:
- Full log (with tracebacks): `evidence/pt-sunsponge/server.log`
- Cleaned log (tracebacks elided for readability):
  `evidence/pt-sunsponge/server.log.clean`
- Each traceback isolated: `evidence/pt-sunsponge/500-traceback.json` and
  `evidence/pt-sunsponge/500-traceback.md`

---

## Repro

```bash
# 1. Boot
PYTHONPATH=src .venv/bin/python -m sunsponge.app > server.log 2>&1 &
curl -sS http://127.0.0.1:8787/api/health     # -> 200 ok

# 2. Single capture
curl -X POST http://127.0.0.1:8787/api/rested-captures/jobs \
  -H 'content-type: application/json' \
  -d '{"urls":["https://example.com/"],
       "viewports":["desktop","mobile"],"schemes":["light","dark"],
       "export_dir":"./out/single","export_mode":"folder","name":"single"}'

# 3. Map ingestion (uses committed fixture)
curl -X POST http://127.0.0.1:8787/api/rested-captures/jobs \
  -H 'content-type: application/json' \
  -d '{"manifest_path":"tests/fixtures/pt-sunspressure-manifest.md",
       "base_url":"https://example.com/",
       "viewports":["desktop","mobile"],"schemes":["light","dark"],
       "export_dir":"./out/map","export_mode":"folder","name":"pt-map"}'

# 3b. Map ingestion via verifier JSON
curl -X POST http://127.0.0.1:8787/api/rested-captures/jobs \
  -H 'content-type: application/json' \
  -d '{"map_path":"tests/fixtures/pt-sunspressure-large.json",
       "base_url":"https://example.com/",
       "viewports":["desktop","mobile"],"schemes":["light","dark"],
       "concurrency":4,"wait_ms":100,
       "export_dir":"./out/large","export_mode":"folder","name":"pt-large"}'

# 4. Edge cases
curl -X POST .../jobs -d '{"urls":[""]}'                                       # 400
curl -X POST .../jobs -d '{"urls":["not-a-url"]}'                              # 200 then fails
curl -X POST .../jobs -d '{"map_path":"/nope.json","base_url":"x"}'            # 500  (REGRESSION)
curl -X POST .../jobs -d '{"manifest_path":"/nope.md","base_url":"x"}'         # 500  (REGRESSION)
curl http://127.0.0.1:8787/api/rested-captures/jobs/nonexistent                # 404

# 5. Unit tests
PYTHONPATH=src .venv/bin/python -m pytest -q    # 14 passed, 2 skipped
```

---

## Touched paths

Only the lane-allowed paths were modified:

- `reports/pressure-sunsponge.md` (this file)
- `evidence/pt-sunsponge/` (PNGs, JSON responses, server log, traceback
  captures, sample outputs)
- `tests/fixtures/pt-sunspressure-manifest.md` (new, 3 pathways)
- `tests/fixtures/pt-sunspressure-large.json` (new, synthetic 40-pathway
  verifier JSON for the robustness run)

**Not touched** (per lane): any code under `src/`, `ui/`, `scripts/`, or the
two pre-existing fixtures under `tests/fixtures/`.

No secrets committed. No changes to product code.

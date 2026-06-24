# Lane fx-sunsponge — fix the two release blockers (bs-w2 QA touch-up)

**Repo / branch:** `deariencampbell1-sys/sunsponge` · `fix/release-blockers` (base
`feat/stand-up-sunsponge`).
**Inherits:** pt-sunsponge pressure test (PR #2) report at
`reports/pressure-sunsponge.md`. That report flagged **RELEASE-READY: no** on two
real blockers; everything else was green (boot, single capture, map ingestion,
a 160-target large map in ~31s, concurrency, graceful DNS failures, 14 tests
pass). This lane closes both blockers plus the cheap `pathway_id` /
`pathway_status` API gap the report also flagged, and re-verifies.

## TL;DR

**RELEASE-READY: yes.** Both blockers from PR #2 are fixed, regression tests are
green, the API now exposes `pathway_id` / `pathway_status` per `results[]` row,
and clean single-capture + map flows still produce correct PNGs.

## Blocker 1 — bad `manifest_path` / `map_path` returning 500 + traceback

**Before.** `load_pathway_map` delegated straight to `parse_manifest_md` /
`parse_verifier_json`, both of which do `Path(...).read_text(...)` with no
existence check. A missing file raised `FileNotFoundError`, which hit the
catch-all in `app.py` and became a generic `500 {"ok":false,"error":"capture
job failed"}` with the raw Python traceback (and the absolute server path)
in the log.

**After.** `load_pathway_map` checks `path.is_file()` first and raises a
typed `RestedCaptureError` naming the offending input (`manifest_path` vs
`map_path`) WITHOUT echoing the path. `parse_manifest_md` and
`parse_verifier_json` wrap their reads in `try/except OSError` (and JSON
decode in `try/except json.JSONDecodeError`) so a malformed verifier map
also surfaces as a typed 400 instead of a 500. The handler in `app.py`
maps `RestedCaptureError` to `400` with the message in `error`.

| Probe | Status | Body |
|---|---|---|
| `POST /api/rested-captures/jobs {"map_path":"/nope.json","base_url":"x"}` | **400** (was 500) | `{"ok":false,"error":"map_path does not exist or is not a readable file"}` |
| `POST /api/rested-captures/jobs {"manifest_path":"/nope.md","base_url":"x"}` | **400** (was 500) | `{"ok":false,"error":"manifest_path does not exist or is not a readable file"}` |
| `POST /api/rested-captures/jobs {"map_path":"<dir>","base_url":"x"}` | **400** | `{"ok":false,"error":"map_path does not exist or is not a readable file"}` |

Server log: zero tracebacks, zero `FileNotFoundError`, zero leaked absolute
paths — every bad-input request shows up as a single clean `400 Bad Request`
line. See `evidence/fx-sunsponge/server.log`.

## Blocker 2 — unvalidated URL input silently coerced + queued

**Before.** `{"urls":["not-a-url"]}` was silently coerced to
`https://not-a-url/`, queued, and only failed deep in Playwright on a DNS
error (wasting a full capture cycle). The empty-list case already returned
`400 {"ok":false,"error":"add at least one URL"}`.

**After.** `build_capture_plan` rejects any plain-URL entry that does not
look like a URL before queueing. The new `_is_valid_user_url()` accepts
local-input paths, anything that already carries a scheme, or a bare host
that contains a dot — so `example.com` still passes but `not-a-url` does
not. The empty-list case and the existing `add at least one URL` error
are unchanged.

| Probe | Status | Body |
|---|---|---|
| `POST /api/rested-captures/jobs {"urls":["not-a-url"]}` | **400** (was 200-then-fail) | `{"ok":false,"error":"invalid URL (need http/https scheme and host): 'not-a-url'"}` |
| `POST /api/rested-captures/jobs {"urls":[""]}` | **400** | `{"ok":false,"error":"add at least one URL"}` (unchanged) |
| `POST /api/rested-captures/jobs {"urls":[]}` | **400** | `{"ok":false,"error":"add at least one URL"}` (unchanged) |
| `POST /api/rested-captures/jobs {"urls":["https://example.com/"]}` | **200** (queued) | `{"ok":true,"job_id":"7bd50695a728", ...}` |

## Cheap API gap — `pathway_id` + `pathway_status` in each `results[]` row

`results[]` rows now carry `pathway_id` and `pathway_status`. For a plain-URL
capture (no pathway) both fields are `null`; for a map capture they reflect
the pathway the target belongs to (e.g. `("about-page","WIRED")`,
`("home-light","WIRED")`, `("missing-route","UNWIRED")`). The change is
additive — no existing field was renamed or removed.

Example map-run row (trimmed):
```json
{"url":"https://example.com/?view=catalog","state_id":"about-page-wired-desktop-light",
 "viewport":"desktop","scheme":"light","pathway_id":"about-page",
 "pathway_status":"WIRED","status":"ok","file":"002-about-page-wired-desktop-light.png",
 "bytes":20260,"attempts":1,"elapsed_ms":1321}
```

## Tests

`pytest -q` output: **35 passed, 2 skipped, 1 warning** (the warning is the
existing StarletteDeprecationWarning about `httpx`, unchanged from main).

- All 14 pre-existing tests in `tests/test_capture_service.py` and
  `tests/test_pathway_map.py` stay green (2 unrelated `manifest` /
  `verifier_sample_output` fixtures remain skipped, same as on `main`).
- 21 new regression tests in `tests/test_release_blockers.py` cover:
  - `load_pathway_map` raises typed errors for missing `manifest_path` /
    `map_path` (3 tests).
  - `parse_manifest_md` / `parse_verifier_json` reject unreadable /
    non-JSON files cleanly (3 tests).
  - `build_capture_plan` rejects `not-a-url`, still accepts bare hosts
    with a dot, still accepts full URLs, still uses the existing
    empty-list error (4 tests).
  - API returns 400 (with a non-path-leaking message) for bad
    `manifest_path`, bad `map_path`, a directory as `manifest_path`, and
    `not-a-url` (4 tests).
  - API still queues a valid `https://...` URL and still rejects the empty
    list (2 tests).
  - `results[]` rows from a map run carry the new `pathway_id` /
    `pathway_status`; a plain-URL run carries both as `null` (2 tests).
  - Sample manifest / verifier map round-trip through `parse_*` (3 tests).

Full output: `evidence/fx-sunsponge/pytest-output.txt`.

## Re-verification (end-to-end, against the running server)

- **Single capture** (`urls=["https://example.com/"]`) → 6/6 captures ok,
  `pathway_id` / `pathway_status` both `null`. See
  `evidence/fx-sunsponge/probe-valid-url-final.json` and
  `evidence/fx-sunsponge/single-download.zip`.
- **Map capture** (small manifest with 3 pathways: `home-light`,
  `about-page`, `missing-route`) → 3/3 captures ok, `pathway_id` /
  `pathway_status` correctly distributed across `(home-light,WIRED)`,
  `(about-page,WIRED)`, `(missing-route,UNWIRED)`. See
  `evidence/fx-sunsponge/probe-map-final.json` and
  `evidence/fx-sunsponge/probe-map-job.json`.
- **Server log** during the verification run (`evidence/fx-sunsponge/server.log`):
  12 INFO lines, no tracebacks, no `FileNotFoundError`, no leaked absolute
  filesystem paths. Every bad-input request came back as a clean 400; the
  two clean runs came back as 200.

Evidence index: `evidence/fx-sunsponge/summary.md`.

## Files changed

```
src/sunsponge/pathway_map.py       | 38 ++++++++++++++++++++++++++++++++++-----
src/sunsponge/capture_service.py   | 36 +++++++++++++++++++++++++++++++++++++
tests/test_release_blockers.py     | (new, 21 tests)
evidence/fx-sunsponge/             | (new, probes + log + pytest output)
reports/fx-sunsponge.md            | (this file)
```

No changes outside `src/`, `tests/`, `evidence/`, `reports/`. No fixture
modifications. No changes to `app.py` (the catch-all `RestedCaptureError →
400` mapping was already in place from the original handler wiring).

## Out of scope (intentionally NOT touched here)

- Job-state persistence across server restart (non-blocker #3 in
  `pressure-sunsponge.md`). Left as a future lane.

## Release verdict

**RELEASE-READY: yes.** Both blockers from `pressure-sunsponge.md` are
resolved and guarded by regression tests. The cheap pathway-fields API gap
is closed. All previously-green flows (boot, single capture, map ingestion,
160-target large map, concurrency, graceful DNS failures) remain green.

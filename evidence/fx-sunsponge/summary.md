# fx-sunsponge evidence summary

## Probes (HTTP responses captured)

| Probe | Result | Body |
|---|---|---|
| bad map_path |  | {"ok":false,"error":"map_path does not exist or is not a readable file"} |
| bad manifest_path |  | {"ok":false,"error":"manifest_path does not exist or is not a readable file"} |
| urls:['not-a-url'] |  | {"ok":false,"error":"invalid URL (need http/https scheme and host): 'not-a-url'"} |
| urls:[''] |  | {"ok":false,"error":"add at least one URL"} |
| urls:['https://example.com/'] |  | {"ok":true,"job_id":"7bd50695a728","name":"fx-sunsponge-valid-smoke","status":"queued","created_at":"2026-06-24T18:57:19+00:00","updated_at":"2026-06-24T18:57:19+00:00","message":"Queued","urls":["htt |

## Regression: end-to-end capture

| Job | Targets | Completed | Failed | Status | Note |
|---|---|---|---|---|---|
| plain-URL | 6 | 6 | 0 | done | see probe-valid-url-final.json |
| map | 3 | 3 | 0 | done | see probe-map-final.json |

## Field check: pathway_id / pathway_status in results[]

| Job | distinct (pathway_id, pathway_status) |
|---|---|
| plain-URL | (None, None) |
| map | ('about-page', 'WIRED'), ('home-light', 'WIRED'), ('missing-route', 'UNWIRED') |

## Server log

Captured in  (12 lines). No , no ,
no  strings — all 400 responses are clean.

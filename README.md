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
set PYTHONPATH=src
python -m sunsponge.app
```

Open http://127.0.0.1:8787 for the capture UI.

API endpoints:

- `POST /api/rested-captures/jobs` — start a capture job
- `GET /api/rested-captures/jobs/{job_id}` — poll job status
- `GET /api/rested-captures/jobs/{job_id}/download` — download ZIP

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
  capture_service.py   # engine: crawl, sitemap, Playwright capture
  app.py               # standalone FastAPI service + static UI
ui/
  index.html           # SunSponge shell
  RestedCaptureView.jsx
tests/
  test_capture_service.py
scripts/
  smoke_capture.py
```

## License

MIT
# Phase 1 Brief: DemoForge Playwright Recorder

## Goal
Extend SunSponge with an interactive demo recording mode. User opens a page in a headful Playwright window, clicks through a product flow, and the system captures screenshots + click hotspots at each step. Output is a DemoSpec JSON ready for the AI pipeline.

## What to Build

### New file: `src/sunsponge/demo_engine.py`
A `DemoRecorder` class that:
1. Launches a **headful** Playwright browser (user can see and interact with it)
2. Navigates to a URL
3. Injects a recording overlay script into the page
4. On every click, captures:
   - Full-viewport screenshot (PNG base64)
   - CSS selector of the clicked element
   - Click coordinates as percentages relative to the element's bounding box (xPct, yPct)
   - Page URL and title
   - Timestamp
5. Stores steps in memory as a `DemoSpec`
6. Exposes `start()`, `stop()`, and `get_spec()` methods

### New route: `POST /api/demos/record` in `app.py`
Accepts `{ url, name, goal }`. Launches the recorder. Returns `{ sessionId, message }`.

### New route: `POST /api/demos/stop`
Accepts `{ sessionId }`. Stops recording, writes DemoSpec JSON to disk. Returns `{ demoId, stepCount }`.

## DemoSpec Schema

Use the exact schema from `ARCHITECTURE.md` section 1. Implement as a Python dataclass in `demo_engine.py`:

```python
@dataclass
class Hotspot:
    xPct: float
    yPct: float

@dataclass
class BoundingRect:
    x: float; y: float; width: float; height: float

@dataclass
class Interaction:
    type: str              # 'click' | 'input' | 'submit' | 'navigate'
    target: dict           # { selector, tagName, text?, boundingRect? }
    hotspot: dict          # { xPct, yPct }
    value: str | None = None

@dataclass
class DemoStep:
    index: int
    timestamp: int
    pageUrl: str
    pageTitle: str
    interaction: Interaction
    screenshotBase64: str | None = None

@dataclass
class DemoSpec:
    version: int = 1
    id: str = ""
    name: str = ""
    goal: str = ""
    createdAt: str = ""
    viewport: dict = field(default_factory=lambda: {"width": 1440, "height": 900})
    startUrl: str = ""
    steps: list[DemoStep] = field(default_factory=list)
```

## Injected Recording Script

The overlay script injected via `page.evaluate()` or `page.add_init_script()` must:

```javascript
// 1. Create a floating record indicator (bottom-right corner, fixed position)
//    Shows: ● RECORDING with step count
// 2. Listen for clicks on the document (capturing phase)
//    On click:
//      a. Get target element
//      b. Compute CSS selector (walk up DOM, use nth-of-type for sibling disambiguation)
//      c. Compute xPct = ((clientX - rect.left) / rect.width) * 100
//      d. Compute yPct = ((clientY - rect.top) / rect.height) * 100
//      e. Get bounding rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
//      f. Get tagName, visible text (truncated 80 chars)
//      g. Send data to Python via Playwright's expose_function
// 3. Expose: window.__demoRecorder = { clickData: null }
//    Python reads window.__demoRecorder.clickData after each detected click
// 4. Do NOT capture clicks on the record indicator itself
```

Note on communication: Use `page.expose_function()` in Playwright to create a bridge. After each click, the JS sets `window.__demoRecorderClickData = { selector, tagName, text, hotspot: {xPct, yPct}, boundingRect, pageUrl, pageTitle }`. Python polls or uses a callback.

Alternative: use `page.evaluate()` in a loop to check for new click data every 100ms. Simpler, less fragile.

## Click Selector Construction

Adapt from journey-trace's `getCssSelector()` (see `ARCHITECTURE.md` references):

```javascript
function buildSelector(el) {
  if (el.id) return '#' + CSS.escape(el.id);
  if (el === document.body) return 'body';
  const parts = [];
  let current = el;
  while (current && current !== document.body) {
    let sel = current.tagName.toLowerCase();
    if (current.id) { parts.unshift('#' + CSS.escape(current.id)); break; }
    if (current.className && typeof current.className === 'string') {
      const classes = current.className.trim().split(/\s+/).slice(0, 2)
        .map(c => '.' + CSS.escape(c)).join('');
      sel += classes;
    }
    const parent = current.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
      if (siblings.length > 1) sel += ':nth-of-type(' + (siblings.indexOf(current) + 1) + ')';
    }
    parts.unshift(sel);
    current = current.parentElement;
  }
  return parts.join(' > ');
}
```

## API Endpoints

```
POST /api/demos/record
  Body: { "url": "http://localhost:8787", "name": "Onboarding Flow", "goal": "User signs up and captures first screenshot" }
  Returns: { "sessionId": "abc123", "message": "Recording started. Interact with the browser window, then call /api/demos/stop." }

POST /api/demos/stop
  Body: { "sessionId": "abc123" }
  Returns: { "demoId": "abc123", "stepCount": 8, "path": "demos/abc123/demo.json", "status": "recorded" }
```

## Output

Demo JSON saved to `demos/{demoId}/demo.json`. Screenshots saved alongside as `step_000.png`, `step_001.png`, etc. (or inline base64 in the JSON — your choice, but separate PNGs make the JSON smaller and easier to read).

## Hard Constraints

- Must reuse SunSponge's existing Playwright infrastructure (`_launch_browser`, browser lifecycle)
- Must work on Windows (where SunSponge currently runs)
- Click capture must be percentage-based (survives viewport resize)
- Password fields in `input` events must be redacted to `[REDACTED]`
- Do NOT modify `capture_service.py` — add new code in `demo_engine.py` only
- All paths relative to the sunsponge project root

## Acceptance Criteria

1. Start SunSponge, POST to `/api/demos/record` with a URL
2. Browser window opens showing the target page with a ● RECORDING badge
3. Click around the page — each click captured as a step
4. POST to `/api/demos/stop` — returns step count
5. Demo JSON saved with screenshots, correct selectors, and percentage-based hotspots
6. Hotspot xPct/yPct are correct when checked against a known element position

## References

- `src/sunsponge/capture_service.py` — existing Playwright usage pattern
- `src/sunsponge/app.py` — existing FastAPI routes pattern
- `ARCHITECTURE.md` — full schema and architecture
- `research-findings.md` — journey-trace click capture and rrweb patterns

## Deliverable

Working code in `src/sunsponge/demo_engine.py` + updated `app.py` with the two new routes. Report: file paths, line counts, and a manual test log showing a 3+ step recording.

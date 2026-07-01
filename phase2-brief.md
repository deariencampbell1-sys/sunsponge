# Phase 2 Brief: DemoForge Viewer

## Goal
Build a self-contained HTML viewer that replays a DemoSpec JSON as an interactive product walkthrough. Screenshots with pulsing hotspots, smooth cursor animation, voiceover, and keyboard nav. Zero dependencies — a single HTML file works from any filesystem or URL.

## Spec

### Input
A DemoSpec JSON file (as produced by Phase 1). May be loaded inline, from a `<script>` tag, or fetched from a URL.

### Viewer Behavior

```
┌──────────────────────────────────────────────┐
│  ┌──────────────────────────────────────┐    │
│  │                                      │    │
│  │        Screenshot (full viewport)    │    │
│  │                                      │    │
│  │              ◉ ← pulsing hotspot    │    │
│  │                                      │    │
│  │    🖱️ cursor animates to hotspot    │    │
│  │                                      │    │
│  └──────────────────────────────────────┘    │
│  ┌──────────────────────────────────────┐    │
│  │  ●●●●●●●○○○  Step 3 of 8            │    │
│  │  "Click the Capture button"          │    │
│  │           [◀] [▶] [⏸] [⏵]         │    │
│  └──────────────────────────────────────┘    │
└──────────────────────────────────────────────┘
```

### Features (required)

1. **Screenshot display** — each step shows its screenshot centered and scaled to fill the viewport (CSS `object-fit: contain` on a full-viewport `<img>`). Fade transition between steps (300ms opacity).

2. **Pulsing hotspot** — SVG circle positioned at the percentage coordinates from `step.interaction.hotspot`. Uses CSS `@keyframes` to pulse (scale 1 → 1.3, opacity 0.8 → 0.3, 1.2s loop). Positioned absolutely on the viewport using `step.interaction.target.boundingRect` and `step.interaction.hotspot.{xPct, yPct}`:
   ```
   hotspot_x_px = boundingRect.x + (boundingRect.width * xPct / 100)
   hotspot_y_px = boundingRect.y + (boundingRect.height * yPct / 100)
   ```
   Note: since boundingRect captures the element's position at record time, and the screenshot fills the viewport at the same dimensions, these pixel positions map directly to the screenshot.

3. **Cursor animation** — a small SVG cursor (🖱️-style) that animates from step N-1's hotspot to step N's hotspot when advancing. Uses `requestAnimationFrame` with a cubic bezier path (slight arc, not straight line). Duration: 400ms, easing: ease-in-out.

4. **Step navigation** — prev/next buttons, keyboard arrows (← →), click hotspot to advance. Shows "Step 3 of 8" counter.

5. **Progress bar** — thin bar at bottom showing current step / total. Clickable segments to jump to any step.

6. **Voiceover** — `<audio>` element that plays `step.voiceoverBase64` (base64-encoded audio) on step transition. Auto-advances to next step when audio ends (if auto-play enabled). Mute button.

7. **Auto-play** — play button starts auto-advancing through all steps. Configurable delay between steps (default 3s). Pauses on hover.

8. **Annotation display** — shows `step.annotation` text below the step counter (the AI-generated natural-language description of what the user did).

### Step State Machine

```
IDLE → (click next / → key / hotspot click) → TRANSITIONING
TRANSITIONING → (fade + cursor animation complete) → ACTIVE
ACTIVE → (click next / auto-play timer) → TRANSITIONING
ACTIVE → (click prev / ← key) → TRANSITIONING (reverse)
```

During TRANSITIONING, ignore input (prevent double-advance).

## File Structure

```
viewer/
  demo-viewer.html    # the complete viewer (inline CSS + JS)
```

HTML structure:
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DemoForge Viewer</title>
  <style>/* all CSS inline */</style>
</head>
<body>
  <div id="viewer">
    <!-- screenshot layer -->
    <div id="screenshot-container">
      <img id="screenshot" />
      <!-- hotspot overlay -->
      <svg id="hotspot-layer"><circle id="hotspot" /></svg>
      <!-- cursor -->
      <div id="cursor"></div>
    </div>
    <!-- controls -->
    <div id="controls">
      <div id="progress-bar"><!-- segments --></div>
      <div id="step-info">Step 1 of 8</div>
      <div id="annotation">Click the Sign In button</div>
      <div id="buttons">
        <button id="btn-prev">◀</button>
        <button id="btn-play">⏵</button>
        <button id="btn-next">▶</button>
        <button id="btn-mute">🔊</button>
      </div>
    </div>
  </div>
  <!-- DemoSpec loaded here -->
  <script type="application/json" id="demo-data">
    { /* DemoSpec JSON */ }
  </script>
  <script>/* all JS inline */</script>
</body>
</html>
```

## Loading DemoSpec

Support two modes:

1. **Embedded** — DemoSpec JSON in a `<script type="application/json" id="demo-data">` tag (for single-file export)
2. **URL param** — `?demo=demo.json` fetches the spec from a relative path

```javascript
function loadDemoSpec() {
  // Try embedded first
  const embedded = document.getElementById('demo-data');
  if (embedded?.textContent?.trim()) {
    return JSON.parse(embedded.textContent);
  }
  // Try URL param
  const params = new URLSearchParams(location.search);
  const url = params.get('demo');
  if (url) {
    return fetch(url).then(r => r.json());
  }
  throw new Error('No demo data found. Add ?demo=path/to/demo.json or embed in #demo-data.');
}
```

## Hotspot Positioning

```javascript
function positionHotspot(step) {
  const rect = step.interaction.target.boundingRect;
  const xPct = step.interaction.hotspot.xPct;
  const yPct = step.interaction.hotspot.yPct;

  // boundingRect is relative to viewport at record time
  // Screenshot fills the viewport → direct mapping
  const x = rect.x + (rect.width * xPct / 100);
  const y = rect.y + (rect.height * yPct / 100);

  hotspot.setAttribute('cx', x);
  hotspot.setAttribute('cy', y);
  hotspot.setAttribute('r', '18');
}
```

## Cursor Animation

```javascript
function animateCursor(fromX, fromY, toX, toY, duration = 400) {
  const start = performance.now();
  // Control point for slight arc
  const cpX = (fromX + toX) / 2;
  const cpY = Math.min(fromY, toY) - 40; // arc upward

  function tick(now) {
    const elapsed = now - start;
    const t = Math.min(elapsed / duration, 1);
    // Cubic bezier: B(t) = (1-t)³P0 + 3(1-t)²tP1 + 3(1-t)t²P2 + t³P3
    const u = 1 - t;
    const x = u*u*u*fromX + 3*u*u*t*cpX + 3*u*t*t*cpX + t*t*t*toX;
    const y = u*u*u*fromY + 3*u*u*t*cpY + 3*u*t*t*cpY + t*t*t*toY;
    cursor.style.left = x + 'px';
    cursor.style.top = y + 'px';
    if (t < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}
```

## CSS Requirements

- Dark overlay for controls (like a video player chrome)
- Controls auto-hide after 2s of inactivity, show on mouse move
- Hotspot pulse animation (SVG circle with CSS animation)
- Cursor styled as a small pointer (12×20px, with pointer icon or custom SVG)
- Smooth fade transitions between screenshots
- Responsive — viewer works at any viewport size
- Progress bar segments: filled for completed steps, empty for remaining

## Export Mode

The viewer HTML is used as a **template** — the Phase 1 recorder (or Phase 4 export tool) embeds a DemoSpec JSON into the `<script id="demo-data">` tag to produce a standalone file.

For Phase 2, just build the viewer. Hardcode a sample DemoSpec inline for testing.

## Sample Test Data

```json
{
  "version": 1,
  "id": "test-demo",
  "name": "Test Demo",
  "goal": "Verify viewer works",
  "createdAt": "2026-07-01T00:00:00Z",
  "viewport": { "width": 1024, "height": 768 },
  "startUrl": "http://localhost:8787",
  "steps": [
    {
      "index": 0,
      "timestamp": 500,
      "pageUrl": "http://localhost:8787",
      "pageTitle": "Test Page",
      "interaction": {
        "type": "click",
        "target": {
          "selector": "#btn-a",
          "tagName": "button",
          "text": "Button A",
          "boundingRect": { "x": 24, "y": 80, "width": 140, "height": 46 }
        },
        "hotspot": { "xPct": 35.71, "yPct": 21.74 }
      },
      "screenshotPath": "",
      "annotation": "Click the first button to get started"
    }
  ],
  "aiAnnotations": null
}
```

Use a colored `<div>` or a `data:image/svg+xml` placeholder as the screenshot since we don't have real PNGs for testing.

## Hard Constraints

- Single HTML file — no external CSS/JS/fonts/frameworks
- Works when opened directly in a browser (`file://` protocol)
- No npm, no build step, no TypeScript — just HTML + inline CSS + vanilla JS
- Keyboard accessible (← → Space for nav, M for mute, P for play/pause)
- Must handle edge cases: 0 steps, 1 step (no prev/next animation needed), missing annotation, missing boundingRect
- Progress bar segments must be keyboard-focusable

## Acceptance Criteria

1. Open `viewer/demo-viewer.html` in a browser
2. See a colored placeholder screenshot with a pulsing hotspot circle at the correct position
3. Click the hotspot → advances to next step with fade + cursor animation
4. Press ← → arrows → navigates correctly, wraps or stops at boundaries
5. Press Space → toggles play/pause
6. Progress bar updates, click a segment to jump to that step
7. Controls auto-hide after 2s, reappear on mouse move
8. Annotation text visible below step counter
9. All 8 steps from sample data navigable

## Reference

- `ARCHITECTURE.md` §6 — Viewer template spec and behaviors
- `src/sunsponge/demo_engine.py` — DemoSpec dataclass structure

## Deliverable

`viewer/demo-viewer.html` — single file, works standalone. Report: line count, browser tested, and a test log of all acceptance criteria.

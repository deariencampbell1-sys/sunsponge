# DemoForge — Architecture

A Supademo-style interactive demo tool. Record a click-through of any web product, AI processes it into a polished walkthrough, export a self-contained viewer page. Agent-commandable via MCP.

---

## 1. Demo Schema

The canonical format for a recorded demo. Steps are ordered, each step has a visual anchor (screenshot or rrweb snapshot reference) plus interaction metadata.

```typescript
interface DemoSpec {
  version: 1;
  id: string;                          // UUID
  name: string;
  goal: string;
  createdAt: string;                   // ISO 8601
  viewport: { width: number; height: number };
  startUrl: string;

  steps: DemoStep[];
  aiAnnotations: AIAnnotations;        // populated by the AI pipeline
}

interface DemoStep {
  index: number;                       // 0-based
  timestamp: number;                   // ms from recording start
  pageUrl: string;
  pageTitle: string;

  // Interaction
  interaction: {
    type: 'click' | 'input' | 'submit' | 'navigate' | 'scroll';
    target: {
      selector: string;                // CSS selector
      tagName: string;
      text?: string;                   // visible text, truncated 80 chars
      boundingRect?: {                 // at time of click, for hotspot calc
        x: number; y: number;
        width: number; height: number;
      };
    };
    hotspot: {                         // percentage-based, survives resize
      xPct: number;                    // 0-100, relative to boundingRect
      yPct: number;
    };
    value?: string;                    // for input steps (redacted for passwords)
  };

  // Visual state — one of:
  screenshotBase64?: string;           // PNG base64, captured by Playwright
  rrwebSnapshotRef?: string;           // reference into the rrweb event stream

  // AI-generated (populated post-recording)
  annotation?: string;                 // one-line natural-language description
  voiceoverBase64?: string;            // TTS audio for this step
  cursorPath?: CursorPoint[];          // smooth cursor path from previous hotspot
}

interface CursorPoint {
  x: number;                           // viewport-relative
  y: number;
  t: number;                           // ms offset within this step
}

interface AIAnnotations {
  summary: string;                     // 2-3 sentence flow summary
  style: 'snappy' | 'smooth' | 'professional';
  generatedAt: string;
  animationTimeline: AnimationKeyframe[];  // AI-generated instruction set
}

// The LLM generates this — the viewer executes it via panzoom
interface AnimationKeyframe {
  stepIndex: number;
  action: 'zoomTo' | 'panTo' | 'zoomToFit' | 'reset';
  target?: string;                     // CSS selector of the active element
  offset?: { x: number; y: number };  // hotspot percentage within target
  zoomLevel?: number;                  // 1.0 = 100%, 1.5 = 150%
  duration: number;                    // ms
  easing?: string;                     // 'ease-in-out' | 'cubic-bezier(0.4,0,0.2,1)'
}
```

---

## 2. System Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                       RECORD LAYER                              │
│                                                                 │
│  ┌──────────────────────┐    ┌──────────────────────────────┐  │
│  │  Chrome Extension     │    │  Playwright Recorder          │  │
│  │  (Manifest V3)        │    │  (server-side, headful)       │  │
│  │                       │    │                               │  │
│  │  · Captures clicks    │    │  · Injects overlay script     │  │
│  │  · Computes hotspots  │    │  · Captures screenshots       │  │
│  │  · Records rrweb      │    │  · Saves rrweb event stream   │  │
│  │  · Saves to local     │    │  · Exposes MCP tools          │  │
│  │    storage            │    │                               │  │
│  └──────────┬───────────┘    └──────────────┬────────────────┘  │
│             │                                │                   │
│             └───────────┬────────────────────┘                   │
│                         ▼                                        │
│             ┌───────────────────────┐                            │
│             │  DemoSpec JSON         │                            │
│             │  (screenshots base64,   │                            │
│             │   hotspots, selectors)  │                            │
│             └───────────┬─────────────┘                            │
└─────────────────────────┼──────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────────┐
│                       AI PIPELINE                                │
│                                                                  │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────┐ │
│  │ Vision    │   │ Script   │   │ TTS       │   │ Cursor Path  │ │
│  │ Analysis  │──▶│ Generator│──▶│ Synthesis │──▶│ Generator    │ │
│  │           │   │          │   │           │   │              │ │
│  │ Reads     │   │ Writes   │   │ Generates │   │ Bezier paths │ │
│  │ screenshots│  │ natural  │   │ per-step  │   │ between      │ │
│  │ Labels UI │   │ language │   │ voiceover │   │ hotspots     │ │
│  │ elements  │   │ narration│   │ audio     │   │              │ │
│  └──────────┘   └──────────┘   └──────────┘   └──────────────┘ │
│                                                                  │
│  Input:  DemoSpec (screenshots + click data)                     │
│  Output: DemoSpec (annotated, voiced, cursor-pathed)             │
└──────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────────┐
│                       VIEWER LAYER                               │
│                                                                  │
│  Self-contained HTML file. Zero dependencies.                    │
│                                                                  │
│  Features:                                                       │
│  · Full-viewport screenshot per step with fade transitions      │
│  · Pulsing SVG hotspot where user should click                  │
│  · Smooth cursor animation between hotspots (CSS keyframes)    │
│  · Voiceover audio plays per step                               │
│  · Step counter + progress bar                                  │
│  · Keyboard nav (←→ arrows, space)                              │
│  · Click hotspot to advance                                     │
│  · Auto-play mode with configurable timing                      │
│                                                                  │
│  Shareable: URL (GitHub Pages) or direct HTML file              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. MCP Tools (Agent Interface)

Any pi agent can command the demo pipeline via these tools:

```yaml
tools:
  - name: demo.record
    description: Start recording a product demo
    parameters:
      url: string          # URL to open
      name: string         # demo name
      goal: string         # what the flow demonstrates
    returns: { sessionId, message }

  - name: demo.stop
    description: Stop recording and run AI pipeline
    parameters:
      sessionId: string
    returns: { demoId, stepCount, status }

  - name: demo.status
    description: Check AI pipeline progress
    parameters:
      demoId: string
    returns: { status, step, total }

  - name: demo.list
    description: List all recorded demos
    returns: [{ demoId, name, stepCount, createdAt }]

  - name: demo.edit
    description: Edit a step's annotation or voiceover
    parameters:
      demoId: string
      stepIndex: number
      annotation?: string
      regenerateVoice?: boolean
    returns: { ok }

  - name: demo.export
    description: Export the demo as a viewer HTML file
    parameters:
      demoId: string
      format: 'html' | 'gif' | 'mp4'
    returns: { path, url }
```

Agent command flow:
```
User: "record a demo of the SunSponge onboarding flow"
Agent: demo.record("http://localhost:8787", "SunSponge Onboarding", "User signs up and captures first screenshot")
Agent: [user clicks through the flow in the Playwright window]
Agent: demo.stop()
Agent: [AI pipeline runs: vision → script → TTS → cursor paths]
Agent: demo.export("abc123", "html")
→ "Demo exported to /demos/sunsponge-onboarding.html"
```

---

## 4. Click Hotspot Capture Pattern

From research: rrweb captures absolute click `x, y` but we need percentage-based coordinates for responsive hotspots. The pattern integrated with rrweb:

```typescript
// Inject alongside rrweb record()
document.addEventListener('click', (e) => {
  const target = e.target as Element;
  if (!target) return;

  const rect = target.getBoundingClientRect();
  const selector = buildSelector(target);

  // Percentage-based — survives viewport resize
  const xPct = ((e.clientX - rect.left) / rect.width) * 100;
  const yPct = ((e.clientY - rect.top) / rect.height) * 100;

  // Store as custom event in rrweb stream
  record.addCustomEvent({
    tag: 'hotspot',
    payload: {
      selector,
      tagName: target.tagName.toLowerCase(),
      text: (target.textContent ?? '').trim().slice(0, 80),
      boundingRect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      hotspot: { xPct, yPct },
      pageUrl: location.href,
      timestamp: Date.now(),
    },
  });
}, true);
```

On replay, position the hotspot:
```typescript
const el = document.querySelector(step.selector);
const rect = el.getBoundingClientRect();
hotspot.style.left = `${rect.left + (rect.width * step.hotspot.xPct / 100)}px`;
hotspot.style.top  = `${rect.top  + (rect.height * step.hotspot.yPct / 100)}px`;
```

---

## 5. AI Pipeline — Detail

**Principle: The LLM is the director, not the animator.** It outputs a JSON animation timeline. The viewer's playback engine (panzoom + Web Animations API) executes it. This keeps animation smooth, interruptible, and bug-free.

### 5a. Vision Analysis
**Input:** Per-step screenshots + step metadata (selector, tag, text)
**Model:** Gemini Flash (fast, cheap, vision-capable)
**Output:** Per-step `{ elementLabel, actionDescription, uiContext }`

Example:
```
Input:  screenshot of a login form, selector="#email", tagName="input"
Output: { elementLabel: "email field", actionDescription: "enters their email address",
          uiContext: "login form with email and password fields" }
```

### 5b. Script Generator
**Input:** All vision outputs + flow metadata (name, goal)
**Model:** Gemini Pro or Claude
**Output:** Natural language narration per step, plus a 2-3 sentence summary

Uses journey-trace's `buildNarrativePrompt()` pattern:
```
You are analyzing a recorded user journey.
Flow: "SunSponge Onboarding"
Goal: User signs up and captures their first screenshot
Steps: [list of vision-analyzed steps]

Write: 1) A 2-3 sentence summary, 2) One natural sentence per step describing the action.
```

### 5c. TTS Synthesis
**Input:** Per-step narration text
**Engine:** ElevenLabs or Edge TTS (free)
**Output:** Per-step MP3/WAV base64, 2-5 seconds per step
**Style:** Matches the demo tone — `snappy` for SaaS, `smooth` for storytelling

### 5d. Animation Timeline Generator (LLM as Director)
**Input:** All vision outputs + step metadata + bounding rects
**Model:** Gemini Pro or Claude
**Output:** `AnimationKeyframe[]` — a JSON instruction set the viewer executes

The LLM does NOT animate. It writes the choreography:
```json
[
  { "stepIndex": 0, "action": "zoomTo", "target": "#email", "offset": { "x": 50, "y": 30 }, "zoomLevel": 1.5, "duration": 600 },
  { "stepIndex": 1, "action": "panTo", "target": "#password", "offset": { "x": 50, "y": 50 }, "duration": 400 },
  { "stepIndex": 2, "action": "zoomToFit", "target": ".confirmation-modal", "duration": 500, "easing": "ease-in-out" },
  { "stepIndex": 3, "action": "reset", "duration": 300 }
]
```
The viewer reads this JSON and drives panzoom via `instance.zoomAbs()` / `instance.moveTo()`.

### 5e. Cursor Path Generator
**Input:** Source hotspot (xPct1, yPct1) → Target hotspot (xPct2, yPct2)
**Output:** Array of `{ x, y, t }` points forming a smooth bezier curve

Simple cubic bezier with a slight arc (not a straight line). Physics: ease-in-out, 300-500ms duration. Generated client-side or precomputed server-side.

---

## 6. Viewer Template — Key Behaviors

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
│  │           [▶] [◀] [⏸]               │    │
│  └──────────────────────────────────────┘    │
└──────────────────────────────────────────────┘
```

Tech:
- Single HTML file (inline CSS + JS, no framework)
- Screenshots inline as base64 (or lazy-loaded from a data URL)
- Hotspot: SVG circle with CSS `@keyframes pulse` (scale 1 → 1.3, opacity 0.8 → 0.3)
- Cursor: CSS-animated div following precomputed bezier path via JS `requestAnimationFrame`
- Pan/Zoom: panzoom library drives the camera — reads `AnimationKeyframe[]` JSON, executes `instance.zoomAbs()` / `instance.moveTo()`
- Voiceover: `<audio>` element, auto-plays on step transition
- Transitions: CSS `opacity` fade 300ms between screenshots
- Keyboard: `←` previous, `→` next, `Space` play/pause
- Auto-play: advances every `N` seconds (configurable), pauses on hover
- Interruptible: panzoom and playback respond to user clicks — stop animation instantly on interaction

---

## 7. Technology Stack

| Layer | Choice | Why |
|---|---|---|
| Recording (extension) | Chrome MV3 + rrweb | Battle-tested by journey-trace, captures DOM state + clicks |
| Recording (server) | Playwright (Python, in SunSponge) | Reuse existing SunSponge Playwright infra |
| DOM serialization | rrweb `@rrweb/record` | Industry standard, 14k+ GitHub stars |
| Hotspot capture | Custom JS (standard DOM APIs) | No library needed — `getBoundingClientRect()` |
| Screenshot capture | Playwright `page.screenshot()` | Already in SunSponge's `capture_service.py` |
| AI Vision | Gemini 3.5 Flash | Fast, cheap, vision-capable, already routed through gw |
| AI Script | Gemini 3.5 Flash or 3.1 Pro | Via rhobear-gw |
| AI Animation Timeline | LLM as "director" | LLM outputs JSON keyframes, viewer executes via panzoom |
| TTS | ElevenLabs or Edge TTS | Edge TTS is free, ElevenLabs for quality |
| Pan/Zoom | panzoom (MIT) | Battle-tested, handles matrix math + touch normalization |
| Viewer | Vanilla HTML/CSS/JS | Self-contained, zero deps, shareable |
| MCP Server | Python (FastAPI, in SunSponge) | Reuse SunSponge's app.py |
| Export format | Single HTML file or ZIP | Portable, no platform lock-in |

---

## 8. Project Layout (within SunSponge)

```
sunsponge/
├── src/sunsponge/
│   ├── capture_service.py      # existing: batch screenshots
│   ├── demo_engine.py          # NEW: interactive demo recording
│   ├── demo_ai.py              # NEW: AI pipeline (vision, script, TTS, cursor)
│   ├── demo_mcp.py             # NEW: MCP tools (record, stop, edit, export)
│   ├── app.py                  # existing: FastAPI + extend with demo routes
│   └── ...
├── viewer/
│   └── demo-viewer.html        # NEW: self-contained viewer template
├── extension/                   # NEW: Chrome extension (optional path)
│   ├── manifest.json
│   ├── content.js              # rrweb + hotspot capture
│   └── ...
├── demos/                       # NEW: output directory for exported demos
│   └── .gitkeep
└── ARCHITECTURE.md              # this file
```

---

## 9. Implementation Phases

### Phase 1: Core Recorder (Playwright + Hotspots)
- Extend SunSponge's Playwright infra with a headful recording mode
- Inject overlay script that captures clicks, computes xPct/yPct, saves screenshots
- Save recording as DemoSpec JSON
- Test: record a flow on SunSponge's own capture UI

### Phase 2: Viewer (Manual Annotations)
- Build the self-contained viewer HTML template
- Render DemoSpec JSON: screenshots, hotspot circles, step nav
- Test: load a manual DemoSpec, verify hotspots positioned correctly at different viewport sizes

### Phase 3: AI Pipeline
- Wire vision analysis (Gemini Flash)
- Wire script generator (Gemini Pro)
- Wire TTS (Edge TTS or ElevenLabs)
- Wire cursor path generator
- Test: record → AI processes → viewer plays with narration and cursor

### Phase 4: MCP Tools
- Expose demo.* tools from SunSponge's app.py
- Test: agent says "record a demo of X" → recording happens → AI fires → export ready

### Phase 5: Chrome Extension (Optional)
- Port the recorder to a Chrome MV3 extension
- Use rrweb for DOM-level recording
- Export DemoSpec JSON to a companion server

---

## 10. Open Questions

1. **Screenshots vs rrweb replay?** Screenshots are simpler and avoid cross-origin/CSS-in-JS problems. rrweb gives pixel-perfect DOM replay but is fragile with shadow DOM, canvas, and WebGL. Recommended: **screenshots for MVP, rrweb as upgrade path.**

2. **TTS engine?** Edge TTS is free and decent quality. ElevenLabs is premium. Start with Edge, add ElevenLabs as a config option.

3. **Cursor path: CSS-only or JS?** CSS `offset-path` with `motion` works in modern browsers but doesn't support easing per segment. JS `requestAnimationFrame` with bezier math gives full control. Recommended: **JS for MVP.**

4. **Demo viewer: inline base64 or separate assets?** Inline base64 means a single file but large (10-20MB for 10-step demo). ZIP with separate screenshots is lighter. Recommended: **inline for ≤5 steps, ZIP for larger.**

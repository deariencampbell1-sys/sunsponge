# DOM Capture Research Findings

This report details the technical findings of researching `journey-trace`, `rrweb`, and `websnap` to build a Supademo-style interactive demo tool that captures DOM states on click and exports structured data for AI consumption.

---

## 1. journey-trace

`journey-trace` is a Chrome Extension that records user journeys, captures DOM states, intercepts API requests, and compiles them into a structured AI specification format.

### Export Schema (full JSON)
The exact data structure exported by `journey-trace` is defined in `src/types/spec.ts` as `JourneySpec`. 

```typescript
export interface JourneySpec {
  metadata: SessionMetadata;
  pages: PageSpec[];
  interactions: InteractionEvent[];
  navigationFlow: NavigationStep[];
  apiCalls: ApiCallSpec[];
  screenshots: Screenshot[];
  userAnnotations: Annotation[];
  generatedMarkdown: string;
  aiSystemPrompt: string;
  aiNarrative?: string;
  aiEnriched?: boolean;
}
```

Below is the exhaustive, inline TypeScript schema defining all nested interfaces in the `JourneySpec` payload (extracted from `src/types/spec.ts` lines 1 to 90):

```typescript
export interface SessionOptions {
  captureVideo: boolean;
  captureApiCalls: boolean;
  captureScreenshots: boolean;
}

export interface SessionMetadata {
  journeyId: string;
  flowName: string;
  flowGoal: string;
  startedAt: string;
  endedAt: string;
  durationSeconds: number;
  startUrl: string;
  userAgent: string;
  viewport: { width: number; height: number };
  options: SessionOptions;
}

export interface DetectedComponent {
  selector: string;
  tagName: string;
  role: string;
  text?: string;
  attributes: Record<string, string>;
}

export interface PageSpec {
  pageId: string;
  url: string;
  title: string;
  visitedAt: string;
  screenshot?: string; // Base64 PNG data URL
  components: DetectedComponent[];
}

export interface InteractionEvent {
  eventId: string;
  timestamp: number;
  type: 'click' | 'input' | 'select' | 'scroll' | 'submit' | 'navigate';
  target: {
    selector: string;
    tagName: string;
    text?: string;
    inputType?: string;
    value?: string;
  };
  pageUrl: string;
  userAnnotation?: string;
}

export interface NavigationStep {
  step: number;
  fromUrl: string;
  toUrl: string;
  toTitle: string;
  trigger: 'link' | 'form_submit' | 'js_redirect' | 'back_forward' | 'address_bar';
  timestamp: number;
}

export interface ApiCallSpec {
  callId: string;
  timestamp: number;
  method: string;
  url: string;
  requestHeaders: Record<string, string>;
  requestBody?: unknown;
  responseStatus: number;
  responseBody?: unknown;
  durationMs: number;
}

export interface Screenshot {
  screenshotId: string;
  timestamp: number;
  dataUrl: string;
  milestone?: string;
  pageUrl: string;
}

export interface Annotation {
  annotationId: string;
  timestamp: number;
  text: string;
  type: 'milestone' | 'note' | 'redact';
  targetEventId?: string;
}
```

### Click Capture Data
In `src/content/content.ts` (lines 66 to 82), click event capturing is registered during active recording on the document level (capturing phase):
```typescript
document.addEventListener('click', onDocumentClick, true);
```

When a user clicks, the listener captures the following data:
- **No coordinates** (neither `clientX`/`clientY` nor page coordinates are stored).
- **Selector:** Computed via `getCssSelector(target)`, which starts at the clicked element and walks up to `BODY`, returning a specific path with classes and `:nth-of-type` indexing when sibling tags match.
- **Tag Name:** Extracted using `target.tagName.toLowerCase()`.
- **Text Content:** Computed via `getVisibleText(target)` which trims and truncates content to 80 characters.
- **DOM Snapshot:** No individual DOM snapshot is embedded inside the interaction event. Instead, the raw event is recorded separately inside the `rrwebEvents` buffer (captured on the background page via messages).

The exact click handler function in `src/content/content.ts` is:
```typescript
function onDocumentClick(e: MouseEvent) {
  const target = e.target as Element | null;
  if (!target || !isActive || isPaused) return;

  const interaction: InteractionEvent = {
    eventId: uuidv4(),
    timestamp: Date.now(),
    type: 'click',
    target: {
      selector: getCssSelector(target),
      tagName: target.tagName.toLowerCase(),
      text: getVisibleText(target),
    },
    pageUrl: location.href,
  };
  sendInteraction(interaction);
}
```

### AI Export Format
The AI export format consists of three components generated in the browser extension:

1. **Structured Technical Narrative Prompt** (`src/ai/claude.ts` lines 86 to 119):
   Constructs a prompt that takes the high-level metadata, visited pages, click steps, and intercepted API calls, and asks Claude to generate a clean, concise technical markdown summary with three distinct sections: **Summary**, **Flow Steps**, and **Key UI Components**.

2. **System Prompts / Boilerplate Specifications** (`src/spec-generator/generator.ts` lines 145 to 198):
   Generates a structured system prompt (`spec.aiSystemPrompt`) with complete step-by-step instructions telling an AI coder model (like Claude) to implement a complete React/TypeScript/Tailwind/Express app replicating the exact specifications, complete with matching components and simulated API behaviors.

3. **Inline JSON Spec**:
   Appends a condensed JSON representation of the `JourneySpec` (including metadata, pages, navigation steps, and API calls) directly at the bottom of the code generation prompt.

### Chrome Extension APIs Used
The extension's configuration is managed via a Manifest V3 schema (`manifest.json` lines 11 to 20):

```json
  "manifest_version": 3,
  "name": "Journey Trace",
  "version": "1.0.0",
  "permissions": [
    "activeTab",
    "tabs",
    "scripting",
    "tabCapture",
    "offscreen",
    "storage",
    "sidePanel"
  ],
  "host_permissions": [
    "<all_urls>"
  ]
```

Specifically, the following browser capabilities are utilized in `src/background/service-worker.ts`:
- **`chrome.storage.local`**: For persisting `SessionState` across service worker recycles (crucial in Manifest V3 since background pages sleep), and for saving the user's Anthropic API key.
- **`chrome.offscreen`**: Launches an offscreen document (`offscreen/offscreen.html`) with the `USER_MEDIA` reason. This allows recording tab media streams inside a headless context.
- **`chrome.tabCapture`**: Utilizes `chrome.tabCapture.getMediaStreamId` to capture active tab video via a `MediaStream` passed directly to the offscreen worker's `MediaRecorder` API.
- **`chrome.tabs.captureVisibleTab`**: Captures on-demand PNG screenshots (Base64 data URLs) at milestones, page updates, and session starts (capped at 15 screenshots to avoid storage exhaustion).
- **`chrome.sidePanel`**: Programmatically opens the extension's side panel using `chrome.sidePanel.open({ tabId })` when a recording session is finalized.
- **`chrome.tabs.onUpdated`**: Hooks into page transition completions to append new pages to the sequence, capture intermediate screenshots, and re-initialize content recording scripts.

### Capture Scope
- **Form Fills:** Actively captured in `src/content/content.ts` (lines 84 to 103) by registering an `'input'` listener. It redacts password fields to `[REDACTED]` and caps standard inputs to 200 characters. Form submission events are caught via a `'submit'` listener.
- **Scrolls:** Not captured explicitly as custom interactions (no scroll listeners are bound in the content script), though scroll state changes are recorded in the raw `rrwebEvents` stream.
- **Hovers:** Not captured as interaction events.
- **Clicks:** Actively captured with custom selectors and visible text descriptors.

### Output fed to an AI Model (Example Prompt sent to Claude)
The exact prompt structure built by the generator for Claude Sonnet is as follows:

```markdown
You are a senior full-stack developer. Build a complete web application that replicates the following user flow.

## Target Tech Stack
- Frontend: React 18 + TypeScript + Tailwind CSS
- Backend: Node.js + Express
- Database: PostgreSQL
- API style: REST

## Requirements
1. Implement all pages and UI components listed in the specification
2. Implement all API endpoints shown with their request/response schemas
3. Match the navigation flow exactly
4. Handle all form validations and error states shown
5. Use semantic HTML and accessible ARIA attributes
6. Password fields must use type="password" and never be logged

## Journey Specification

**Flow:** SunSponge Onboarding Flow
**Goal:** User completes email sign-up and configures profile settings.

### Pages (2)
- Sign Up Page (http://localhost:3000/signup): button, input, form
- Dashboard Page (http://localhost:3000/dashboard): button, nav, header

### Flow Steps
1. Typed "jane@example.com" into input #email
2. Typed "[REDACTED]" into input #password
3. Submitted form at http://localhost:3000/signup
4. Clicked button "Confirm Account"

### API Endpoints
- POST http://localhost:3000/api/auth/register → 201
  Request: {"email":"jane@example.com","password":"[REDACTED]"}
- GET http://localhost:3000/api/user/profile → 200

### Full Specification (JSON)
```json
{
  "metadata": {
    "journeyId": "4a71d8fe-bf2e-4b20-af84-4860eb3a778b",
    "flowName": "SunSponge Onboarding Flow",
    "flowGoal": "User completes email sign-up and configures profile settings.",
    "startedAt": "2026-07-01T12:00:00.000Z",
    "endedAt": "2026-07-01T12:01:15.000Z",
    "durationSeconds": 75,
    "startUrl": "http://localhost:3000/signup",
    "userAgent": "Mozilla/5.0 ...",
    "viewport": { "width": 1920, "height": 1080 },
    "options": {
      "captureVideo": false,
      "captureApiCalls": true,
      "captureScreenshots": true
    }
  },
  "pages": [
    {
      "url": "http://localhost:3000/signup",
      "title": "Sign Up",
      "components": [
        {
          "selector": "#email",
          "tagName": "input",
          "role": "input",
          "text": "Email Address",
          "attributes": { "type": "email", "placeholder": "Email" }
        }
      ]
    }
  ],
  "navigationFlow": [
    {
      "step": 1,
      "fromUrl": "http://localhost:3000/signup",
      "toUrl": "http://localhost:3000/dashboard",
      "toTitle": "Dashboard",
      "trigger": "form_submit",
      "timestamp": 1780286415000
    }
  ],
  "apiCalls": [
    {
      "callId": "f784e1b2-04e3-4d4f-b6a2-93821ef9a7db",
      "timestamp": 1780286410000,
      "method": "POST",
      "url": "http://localhost:3000/api/auth/register",
      "requestHeaders": { "Content-Type": "application/json" },
      "requestBody": { "email": "jane@example.com", "password": "[REDACTED]" },
      "responseStatus": 201,
      "responseBody": { "ok": true, "userId": "usr_9281" },
      "durationMs": 142
    }
  ],
  "interactions": [
    {
      "eventId": "3c91d8fe-bf2e-4b20-af84-4860eb3a778b",
      "timestamp": 1780286405000,
      "type": "input",
      "target": {
        "selector": "#email",
        "tagName": "input",
        "value": "jane@example.com"
      },
      "pageUrl": "http://localhost:3000/signup"
    }
  ]
}
```
```

---

## 2. rrweb

`rrweb` (record and replay the web) is the industry standard tool for serializing DOM states and replaying interaction streams with pixel-perfect accuracy.

### Snapshot Data Structure
rrweb serializes the active web page's DOM tree into a flat, JSON-serializable representation of Node objects. This structure is defined in `packages/types/src/index.ts` (lines 800 to 911).

Nodes are assigned a unique, incremental, positive integer `id` mapped inside an internal mirror class (`IMirror`). 

A serialized node is structured as follows:

```typescript
export enum NodeType {
  Document = 0,
  DocumentType = 1,
  Element = 2,
  Text = 3,
  CDATA = 4,
  Comment = 5
}

export type serializedNode = (
  | documentNode
  | documentTypeNode
  | elementNode
  | textNode
  | cdataNode
  | commentNode
) & {
  rootId?: number;
  isShadowHost?: boolean;
  isShadow?: boolean;
};

export type serializedNodeWithId = serializedNode & { id: number };
```

Each specific sub-type defines its children as an array of IDs or child node objects:
- **`elementNode`**: Contains `tagName`, `attributes` (keys associated with string values, boolean attributes, or custom properties like `rr_scrollLeft`), and nested `childNodes: serializedNodeWithId[]`.
- **`textNode`**: Contains string content via `textContent`.
- **`documentNode`**: Represents the root document, wrapping child elements and standard compatibility modes (`compatMode`).

Example of a serialized element node (e.g., a button):
```json
{
  "type": 2,
  "tagName": "button",
  "attributes": {
    "class": "btn primary-btn",
    "type": "button",
    "id": "submit-action"
  },
  "childNodes": [
    {
      "id": 43,
      "type": 3,
      "textContent": "Submit Order"
    }
  ],
  "id": 42
}
```

### Event Stream Format
The rrweb event stream is a chronologically ordered array of objects. An event is modeled as `eventWithTime`:

```typescript
export type eventWithTime = eventWithoutTime & {
  timestamp: number;
  delay?: number;
};
```

A single **Click event** is represented inside the event stream as an incremental mouse interaction. It uses the `EventType.IncrementalSnapshot` (`3`) event type and the `IncrementalSource.MouseInteraction` (`2`) source.

Here is an exact JSON payload of a click event:
```json
{
  "type": 3,
  "timestamp": 1780286412000,
  "data": {
    "source": 2,
    "type": 2,
    "id": 42,
    "x": 35,
    "y": 18,
    "pointerType": 0
  }
}
```

Key mappings from enums inside `packages/types/src/index.ts`:
- **`type: 3`**: `EventType.IncrementalSnapshot`
- **`data.source: 2`**: `IncrementalSource.MouseInteraction`
- **`data.type: 2`**: `MouseInteractions.Click` (mappings: `MouseUp = 0`, `MouseDown = 1`, `Click = 2`, `ContextMenu = 3`)
- **`data.id`**: The unique serialized element node ID that was targeted (`42` refers to the button node above).
- **`data.x` / `data.y`**: Pixel coordinates of the click relative to the element's top-left boundary or the viewport.
- **`pointerType: 0`**: `PointerTypes.Mouse` (mappings: `Mouse = 0`, `Pen = 1`, `Touch = 2`)

### Replay Mechanism
Replay inside `@rrweb/replay` works by executing a hybrid strategy combining **snapshots** and **mutations**:

1. **Reconstruction**: On initial load or seeking, the replayer calls the `rebuild` library (from `@rrweb/snapshot`). It reconstructs a real, physical DOM tree inside a sandboxed, styled `<iframe>` starting from a `fullSnapshot` event.
2. **Mutation Replay**: For subsequent steps, the player does not destroy and rebuild the tree. Instead, it processes incremental event sequences and modifies the already-constructed DOM elements in-place using standard JS DOM manipulation methods (e.g. `appendChild`, `removeChild`, `setAttribute`, or CSS overrides).
3. **Timer & Synchronization**: Events are fed to an internal timer class (`timer.ts`) which schedules execution. For instant seeking, the timer executes those mutations synchronously up to the target timestamp (`isSync = true`).

### Visual State Extraction
It is **not** possible to directly extract a flattened PNG/JPEG "screenshot" file purely out of a JSON string. However, since the DOM is rendered as actual styled elements within an iframe, a "screenshot-like" visual state can be captured using the following mechanisms:

1. **Replayer Seeking:**
   Create an instance of `rrwebPlayer` programmatically, and use `player.goto(timestamp)` or pause at event `N`. This instantly applies mutations up to that precise state.

2. **Capturing via Browser Engine (Reliable & Standard):**
   Run the player inside a headless browser (Puppeteer, Playwright). Programmatically execute the seek, then use the browser's viewport capture API.
   - For example, `rrvideo` does exactly this: it runs Playwright chromium, sets up an HTML template with the replayer and event stream, calls `page.setContent()`, and captures frames (or records video to WebM).
   - In a Chrome Extension environment, once seeked, call `chrome.tabs.captureVisibleTab({ format: 'png' })`.

3. **In-Browser Canvas Capture (Library-based):**
   Render the replayer inside the page and run a visual DOM rasterizer such as `html2canvas` or `modern-screenshot` directly on the iframe’s container node:
   ```javascript
   import { domToPng } from 'modern-screenshot';
   const iframeNode = document.querySelector('.replayer-wrapper iframe');
   const base64Png = await domToPng(iframeNode);
   ```

---

## 3. websnap (bonus)

`websnap` is a high-performance web mirroring tool designed specifically for Single Page Applications (SPAs). It discovers and captures application states instead of simple URLs.

### SPA State Capture Mechanism
Modern SPAs replace page loads with dynamic DOM swaps. Traditional scrapers (like `wget` or `HTTrack`) only crawl links (`<a href="...">`), thereby failing to capture pages hidden behind JS routing, tabs, slide-outs, modals, and filters.

`websnap` solves this by treating the SPA as an **interactive state tree** and traversing it using a custom exploration engine:

1. **Daemon Architecture:**
   `websnap open <url>` spawns a background Node.js daemon that spins up a Playwright Chromium instance. Clients talk to this daemon using line-delimited JSON over a persistent TCP socket.

2. **ARIA-based Discovery:**
   Rather than querying simple anchor elements, `websnap` reads the browser's **Accessibility Tree (ARIA)** to find truly interactive elements (buttons, checkboxes, toggles, form fields). This provides highly stable selectors regardless of styling changes.

3. **Breadth-First Search (BFS) Traversal:**
   The exploration routine (`websnap auto`) performs a systematic BFS through the application:
   - For each state, it lists all discoverable interactive targets.
   - It performs actions (clicks, keypresses, select options, form fills) on a target.
   - It settled-waits and inspects if the DOM changed.
   
4. **State Identity & Cycle Detection:**
   To prevent infinite loops, `websnap` hashes the contents of the resulting page structure (using a SHA-256 hash of accessibility properties). If the hash matches an already visited state, it skips further traversal on that node.

5. **Path Backtracking:**
   If a dead end or repeat is reached, the browser resets or backtracks to parent states. Because SPAs might have unidirectional routing, backtracking is executed by reloading the parent/origin state and replaying the exact sequence of historical actions from the root.

6. **Interception-based Asset Capturing:**
   `websnap` does not crawl assets after generating the HTML. Instead, it hooks into Playwright's `page.route('**/*', ...)` to intercept every single asset (stylesheets, fonts, WebP, script chunks) during runtime exploration. These are written directly to a local cache folder.

7. **Relative HTML/CSS Rewriting:**
   A regex-based rewriter rewrites DOM attributes (`src`, `href`, `srcset`) and CSS files (`url()`, `@import`) on-the-fly to point to local relative mirrors (e.g. `_assets/domain_com/css/app.css`).

### Output Format
The resulting output is a completely offline-ready copy of the application's states. A typical bundle output directory is structured as:

```
output/
├── homepage.html                  # Main SPA landing state HTML
├── pricing.html                   # Sub-state reached on nav click
├── pricing--annual-toggle.html    # State after clicking the "Annual Billing" button
├── about.html                     # About page state
├── bundle.json                    # Machine-readable State Tree configuration
└── _assets/                       # Complete captured asset dependency tree
    ├── app_sunsponge_com/
    │   ├── assets/css/main.abc.css
    │   └── assets/js/app.def.js
    └── fonts_gstatic_com/
        └── s/inter/v13/abc.woff2
```

The master state-tree mapping is preserved inside `bundle.json`, defining all discovered state nodes, their accessibility hashes, local relative file links, and the interaction sequences (the "edges") required to transition between them.

---

## 4. Recommendation

To build a premium, robust, and highly reliable Supademo-style demo tool, we should integrate elements of all three tools into a unified architecture.

### Recommended System Architecture

```
                  ┌──────────────────────────────────────────────┐
                  │          Chrome Extension (MV3)              │
                  │  (User clicks → Capture click + coordinates) │
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼ (Post to Recorder)
                  ┌──────────────────────────────────────────────┐
                  │               @rrweb/record                  │
                  │  (Captures fullSnapshot + DOM mutations)    │
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼ (Sync event stream)
                  ┌──────────────────────────────────────────────┐
                  │                 Replayer                    │
                  │  (Rebuilds DOM inside Iframe, seeks to event)│
                  └──────────────────────┬───────────────────────┘
                                         │
                   ┌─────────────────────┴─────────────────────┐
                   ▼                                           ▼
┌──────────────────────────────────────┐   ┌─────────────────────────────────────┐
│          Visual Export               │   │              AI Export              │
│ - Seek to each click event N         │   │ - Intercept REST API Calls          │
│ - Call browser screenshot on Iframe  │   │ - Render Structured Markdown        │
│ - Output interactive step visual deck│   │ - Inject Page Components into LLM   │
└──────────────────────────────────────┘   └─────────────────────────────────────┘
```

### Technical Design Breakdown

#### 1. DOM Recording & Snapshotting: Use `rrweb`
- **Why:** Custom event listeners (like `journey-trace`'s click recorder) miss nested layouts, canvas elements, custom scrollbars, and styled hover states. Writing a raw DOM serializer from scratch is error-prone.
- **Implementation:** Load `@rrweb/record` inside the tab. It serializes the initial page (`fullSnapshot`) and logs all subsequent mouse, key, scrolling, and stylesheet rule modifications.

#### 2. Click Logging & Coordinates: Custom Extension Layer
- **Why:** `journey-trace` captures CSS selectors but lacks coordinates, which are essential for visual hotspot overlays (the hallmark of a Supademo tool).
- **Implementation:** Extend the extension click listener to capture pixel-offset ratios relative to the clicked element's bounding box:
  ```typescript
  const rect = target.getBoundingClientRect();
  const clickXPercent = (e.clientX - rect.left) / rect.width;
  const clickYPercent = (e.clientY - rect.top) / rect.height;
  ```
  Inject these as custom events into the rrweb stream using `rrweb.record.addCustomEvent` or store them inside a parallel interaction-step array.

#### 3. Structured Page State Mapping: Use `websnap` logic
- **Why:** The AI needs a clear view of distinct screens rather than a continuous stream of minor cursor shifts.
- **Implementation:** Whenever a click event occurs, compute an accessibility hash of the page DOM. If the hash is new, mark it as a "State Node". Group the subsequent mutations under this State Node until the next click.

#### 4. Instant Visual Capture: Iframe Replay + Screenshotting
- **Why:** High-fidelity visual cards for step-by-step decks require isolated screenshots of each step.
- **Implementation:** Seek the replayer to the timestamp of click event `N`. Use `modern-screenshot` or standard browser screenshot APIs to capture the iframe. Overlay a circular SVG pulsing hotspot at `(clickXPercent, clickYPercent)` of the targeted element's bounding container.

#### 5. AI Specs and Mockups Generation: Use `journey-trace` Prompting
- **Why:** Generating a fully functioning project from a flow is highly complex; having a structured, page-by-page component blueprint yields better results.
- **Implementation:** Parse the detected elements on each state page, aggregate intercepted API calls (both XMLHttpRequests and Fetch), and structure the markdown file list template. Send this to Claude or Gemini to generate pristine matching frontend assets.

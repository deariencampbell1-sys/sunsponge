# Phase 3 Brief: DemoForge AI Pipeline

## Goal
Build an AI processing pipeline that takes a raw DemoSpec JSON (screenshots + click data from Phase 1) and enriches it with: natural-language annotations per step, a voiceover audio track, cursor paths, and a pan/zoom animation timeline.

## Input
A `DemoSpec` JSON from the Phase 1 recorder — contains `steps[]` with `screenshotPath`, `interaction`, `pageUrl`, `pageTitle`.

## Output
The same `DemoSpec`, now with populated fields:
- `steps[].annotation` — one-sentence natural description of the user action
- `steps[].voiceoverBase64` — base64-encoded MP3/WAV audio for that sentence
- `steps[].cursorPath` — bezier points from previous hotspot to this one (if not first step)
- `aiAnnotations.animationTimeline[]` — pan/zoom/camera instructions per step
- `aiAnnotations.summary` — 2-3 sentence flow narrative

## Pipeline Stages

### Stage 1: Vision Analysis

For each step, send the screenshot to Gemini Flash with a structured prompt:

```
You are analyzing a screenshot from a product demo recording.

Page: {pageUrl}
Page title: {pageTitle}
User clicked: {selector} ({tagName}, text: "{text}")
Click position: {xPct}%, {yPct}% of element bounding box

Describe in one sentence what the user did, from their perspective.
Be specific — name the button/field/text they clicked.
Output ONLY the sentence, no other text.
```

Store as `step.annotation`.

**Model:** Gemini 3.5 Flash via rhobear-gw (fast, cheap, vision-capable)
**Concurrency:** 3 parallel requests (use `asyncio.gather` with semaphore)

### Stage 2: Script Generation (Summary)

Feed all annotations + metadata to Gemini Pro:

```
You are writing a product demo narration.

Demo: "{spec.name}"
Goal: "{spec.goal}"
Start page: {spec.startUrl}

These are the user actions, in order:
{for each step: Step N: {step.annotation}}

Write a 2-3 sentence summary of what this demo flow accomplishes.
Use present tense. Be concise. Output ONLY the summary.
```

Store as `aiAnnotations.summary`.

### Stage 3: TTS Synthesis

For each step's annotation text, generate spoken audio.

**Use Edge TTS (free, no API key):**
```python
import edge_tts

async def synthesize(text: str, output_path: Path) -> bytes:
    communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
    await communicate.save(str(output_path))
    return output_path.read_bytes()
```

Encode as base64, store in `step.voiceoverBase64`.

Style: Use a natural, professional voice. Rate: 1.0 (normal). Pitch: 0 (neutral).

Concurrency: 2 parallel (Edge TTS rate-limits aggressively above 2).

### Stage 4: Cursor Path Generator

For each step after the first, compute a smooth bezier cursor path from the previous hotspot to the current hotspot. Use pure math — no LLM needed.

```python
def compute_cursor_path(from_hotspot, to_hotspot, bounding_from, bounding_to, duration_ms=400):
    # Convert percentage hotspots to pixel positions
    from_x = bounding_from.x + (bounding_from.width * from_hotspot.xPct / 100)
    from_y = bounding_from.y + (bounding_from.height * from_hotspot.yPct / 100)
    to_x = bounding_to.x + (bounding_to.width * to_hotspot.xPct / 100)
    to_y = bounding_to.y + (bounding_to.height * to_hotspot.yPct / 100)

    # Compute control points for an upward-arc bezier
    dx, dy = to_x - from_x, to_y - from_y
    length = max(1, (dx**2 + dy**2) ** 0.5)
    offset = min(80, length * 0.35)
    px, py = -dy / length, dx / length  # perpendicular unit vector

    cp1 = (from_x + dx * 0.25 + px * offset, from_y + dy * 0.25 + py * offset)
    cp2 = (from_x + dx * 0.75 + px * offset, from_y + dy * 0.75 + py * offset)

    # Sample 20 points along the bezier
    points = []
    for i in range(20):
        t = i / 19
        u = 1 - t
        x = u**3 * from_x + 3 * u**2 * t * cp1[0] + 3 * u * t**2 * cp2[0] + t**3 * to_x
        y = u**3 * from_y + 3 * u**2 * t * cp1[1] + 3 * u * t**2 * cp2[1] + t**3 * to_y
        points.append({"x": round(x, 1), "y": round(y, 1), "t": round(t * duration_ms)})

    return points
```

Store as `step.cursorPath`.

### Stage 5: Animation Timeline Generator

Feed the step data to Gemini Pro to generate a pan/zoom choreography:

```
You are directing a camera for a product demo.

Viewport: {width}x{height}
Steps:
{for each step, include: selector, tagName, text, boundingRect x/y/w/h, hotspot xPct/yPct, annotation}

For each step, decide what camera action to take. Options:
- "zoomTo": zoom into a specific element to highlight it
- "panTo": pan the view to center a specific element
- "zoomToFit": fit the current focused element comfortably in view
- "reset": return to full-page view
- null: no camera change

Output a JSON array. Each entry must have: stepIndex, action, target (CSS selector), offset {x, y} (hotspot percentages), zoomLevel (1.0-2.0, only for zoomTo/zoomToFit), duration (ms, 300-800).

Example output:
[
  {"stepIndex": 0, "action": "zoomTo", "target": "#get-started", "offset": {"x": 50, "y": 50}, "zoomLevel": 1.5, "duration": 600},
  {"stepIndex": 1, "action": "panTo", "target": "input[name=email]", "offset": {"x": 35, "y": 50}, "duration": 400},
  {"stepIndex": 2, "action": null},
  {"stepIndex": 3, "action": "zoomToFit", "target": "button.cta-primary", "duration": 500},
  {"stepIndex": 4, "action": "reset", "duration": 400}
]

Output ONLY the JSON array, no other text.
```

Store as `aiAnnotations.animationTimeline`.

## File: `src/sunsponge/demo_ai.py`

```python
class DemoAI:
    """Enriches a DemoSpec with AI-generated annotations, voiceover, cursor paths, and animation timeline."""
    
    def __init__(self, model_vision="gemini-3.5-flash", model_text="gemini-3.5-flash"):
        ...
    
    async def enrich(self, spec: DemoSpec) -> DemoSpec:
        """Run the full pipeline. Returns the enriched spec."""
        ...
    
    async def _annotate_steps(self, spec: DemoSpec) -> None:
        """Stage 1: Vision analysis for per-step annotations."""
        ...
    
    async def _generate_summary(self, spec: DemoSpec) -> None:
        """Stage 2: Script generation for flow summary."""
        ...
    
    async def _synthesize_voiceover(self, spec: DemoSpec) -> None:
        """Stage 3: TTS for per-step voiceover."""
        ...
    
    def _compute_cursor_paths(self, spec: DemoSpec) -> None:
        """Stage 4: Bezier cursor paths (deterministic, no LLM)."""
        ...
    
    async def _generate_animation_timeline(self, spec: DemoSpec) -> None:
        """Stage 5: Pan/zoom choreography via LLM."""
        ...
```

### API for LLM calls

Use the `openai` Python client pointed at the rhobear-gw endpoint. Reuse the shared-key auth from the existing SunSponge setup:

```python
from openai import AsyncOpenAI
import os

# Auth: the rhobear-gw bearer token. Read from env, never hardcode.
# (Original brief contained a literal token; redacted here. The actual
# credential is treated as compromised since it appeared in plain text and
# should be rotated at the gateway.)
client = AsyncOpenAI(
    base_url=os.environ.get("RHOBEAR_GW_BASE_URL", "https://gw.rhobear.ai/v1"),
    api_key=os.environ["RHOBEAR_GW_API_KEY"],
)

async def llm_vision(prompt: str, image_base64: str) -> str:
    response = await client.chat.completions.create(
        model="gemini-3.5-flash",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
            ]
        }],
        max_tokens=200,
    )
    return response.choices[0].message.content.strip()

async def llm_text(prompt: str, max_tokens: int = 500) -> str:
    response = await client.chat.completions.create(
        model="gemini-3.5-flash",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()
```

### API Route

Add to `app.py`:

```
POST /api/demos/enrich
  Body: { "demoId": "abc123" }
  Returns: { "demoId": "abc123", "status": "enriching" }
  (Processing runs async — poll with GET /api/demos/sessions for status)

GET /api/demos/{demoId}
  Returns: the full DemoSpec JSON (with AI fields populated if done)
```

## Dependencies

Add to `requirements.txt`:
```
edge-tts>=6.0
openai>=1.0
```

## Hard Constraints

- Must use rhobear-gw for all LLM calls (no direct Vertex/Anthropic)
- Edge TTS for voiceover (free, no API key)
- Concurrency: max 3 parallel vision calls, max 2 parallel TTS calls
- Must handle empty steps gracefully (return empty annotations, no crash)
- Cursor paths must be deterministic (math, no LLM)
- Screenshots read from disk using `step.screenshotPath`

## Acceptance Criteria

1. Load a test DemoSpec (from `demos/` directory with real screenshots)
2. Run `POST /api/demos/enrich`
3. All steps have non-empty `annotation` fields
4. All steps have `voiceoverBase64` (valid MP3, plays audio)
5. Steps 1+ have `cursorPath` with 20+ points
6. `aiAnnotations.animationTimeline` has valid JSON array
7. `aiAnnotations.summary` is 2-3 sentences
8. Total pipeline time < 60s for a 5-step demo

## Deliverable

`src/sunsponge/demo_ai.py` + updated `app.py` with new routes + updated `requirements.txt`. Report: line counts, pipeline timing for a 5-step test demo, and a sample enriched DemoSpec JSON showing all populated fields.

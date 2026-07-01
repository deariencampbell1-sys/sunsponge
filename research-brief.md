# Research Brief: DOM Capture & AI Export Formats

## Goal
Research two open-source projects and extract the exact data formats they use.
We need this to build a Supademo-style interactive demo tool that captures
DOM state at each user click and exports structured data AI models can consume.

## Research Targets (in order)

### 1. journey-trace (github.com/elviego/journey-trace)
- Clone the repo. Read the full source.
- What is the **exact JSON schema** it exports? Paste the schema inline.
- How does it capture clicks? What data per click (coordinates, selector, element
  tag, text content, DOM snapshot)?
- What does the "AI export" / structured spec format look like? Is there a
  prompt template it generates?
- What Chrome Extension APIs does it use? (Manifest V3 details)
- Does it capture form fills, scrolls, hovers, or just clicks?
- **Critical:** What does its output look like when fed to an AI model? Show an
  example of the prompt/spec it would send to Claude.

### 2. rrweb (github.com/rrweb-io/rrweb)
- Read the docs and source for `rrweb-snapshot` and `@rrweb/record`.
- What is the **exact snapshot data structure**? (the serialized DOM format with
  unique IDs)
- What does a single click event look like in the rrweb event stream?
- How does the replay work — does it rebuild the DOM from snapshots, or replay
  mutations?
- What is the `fullSnapshot` event vs incremental mutations?
- **Critical:** Can we extract a "screenshot-like" visual state at any point in
  the recording? (i.e., can we capture the visual appearance at step N, not just
  the DOM?)

### 3. websnap (github.com/uirip/websnap) — bonus
- How does it capture SPA *states* (not pages)? What's the mechanism?
- What's the output format?

## Deliverable
A single markdown file saved to the workdir root: `research-findings.md`

Structure:
```
# DOM Capture Research Findings

## 1. journey-trace
### Export Schema (full JSON)
### Click Capture Data
### AI Export Format
### Chrome Extension APIs Used

## 2. rrweb
### Snapshot Data Structure
### Event Stream Format
### Replay Mechanism
### Visual State Extraction

## 3. websnap (bonus)
### SPA State Capture Mechanism

## 4. Recommendation
Which pieces to use, how they fit together for our demo tool.
```

## Hard Constraints
- Must clone and *read actual source code*, not just docs/READMEs.
- Paste schema examples inline — do not describe them.
- Include file paths + line numbers for key findings.
- No placeholder text, no "investigate further" hand-offs.

## Output
Save `research-findings.md` in the workdir. Report path + line count when done.

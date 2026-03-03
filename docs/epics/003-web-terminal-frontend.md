# Epic: Terminal-Styled Web Frontend

## Goal
Build a browser-accessible frontend at the Railway URL that mirrors the TUI's three-panel layout and terminal aesthetic. Anyone with the link can try LegacyLens — no install, no API keys, no clone. Hits the existing FastAPI endpoints on the same deployment.

## Scope (MVP)
- Single-page HTML/CSS/JS served by FastAPI (no build step, no framework)
- Terminal aesthetic: dark background, monospace font, green/amber accents, bordered panels
- Three-panel layout matching TUI: Answer (left), Call Graph (right), Source Code (bottom)
- Query input bar at bottom with placeholder text
- Streaming LLM responses (SSE endpoint → token-by-token display)
- Call graph rendered as collapsible tree (same data as TUI tree widget)
- Source code panel showing retrieved Fortran chunks with scores
- Status bar showing intent badge + READY/THINKING state
- Slash commands: `/explain`, `/deps`, `/impact` parsed client-side → correct API endpoint
- Clickable routine names in call graph → trigger `/deps` then `/explain`
- Mobile-responsive (stacked panels on narrow screens)
- Keyboard shortcut: Enter to submit query

## Non-Goals
- No React/Vue/Svelte — vanilla HTML/CSS/JS only (no build toolchain)
- No user accounts or authentication
- No persistent chat history (session-only, in-memory JS)
- No WebSocket — SSE is simpler and sufficient for streaming
- No offline mode or PWA
- No syntax highlighting for Fortran (monospace + green is enough)
- No exact pixel match with Textual TUI — same spirit, not identical

## Implementation Tasks

### Phase 1: Backend (SSE streaming + static file serving)

1. **Add SSE streaming endpoint to FastAPI** — `/api/stream` that accepts a query and yields `text/event-stream` tokens
   - Files: `app/main.py`
   - Steps:
     1. Add `StreamingResponse` import from `starlette.responses`
     2. Create `POST /api/stream` that accepts `{"question": str}`
     3. Route the query, retrieve chunks, assemble context
     4. Send `event: chunks` with JSON chunk data immediately
     5. Send `event: routing` with intent/routine_names
     6. Stream `event: token` for each LLM token
     7. Send `event: done` with final metadata
     8. Use intent-aware context budgets (same as TUI)
   - Verify: `curl -N -X POST https://localhost:8000/api/stream -H 'Content-Type: application/json' -d '{"question":"What does SPKEZ do?"}'` shows streaming events
   - Depends on: none

2. **Serve static files from FastAPI** — mount `/static` and serve `index.html` at `/`
   - Files: `app/main.py`, `static/` directory
   - Steps:
     1. Create `static/` directory
     2. Add `StaticFiles` mount for `/static`
     3. Add root route `/` that returns `FileResponse("static/index.html")`
   - Verify: `curl http://localhost:8000/` returns HTML
   - Depends on: none

### Phase 2: Frontend (can all run in parallel after Phase 1)

3. **Build HTML structure + terminal CSS** — three-panel layout with terminal styling
   - Files: `static/index.html`, `static/styles.css`
   - Steps:
     1. Create CSS grid layout: left panel (answer), right panel (call graph), bottom panel (source)
     2. Status bar at top (intent badge, state indicator)
     3. Query input bar above footer
     4. Terminal aesthetic: `#0d1117` background, `#c9d1d9` text, `#58a6ff` accents, `JetBrains Mono` / `Fira Code` / monospace font stack
     5. Panel borders with rounded corners and colored titles (matching TUI: green=answer, yellow=callgraph, blue=source)
     6. Responsive: stack panels vertically on `max-width: 768px`
     7. Scrollable panels with custom scrollbar styling
   - Verify: Open `static/index.html` in browser, visually matches terminal layout
   - Depends on: task 2

4. **Build JS: query submission + SSE streaming** — wire up input → API → streaming display
   - Files: `static/app.js`
   - Steps:
     1. Query input: Enter key submits, show "THINKING..." in status bar
     2. Parse slash commands: `/explain ROUTINE` → POST `/api/stream` with question="/explain ROUTINE"
     3. Connect to SSE endpoint with `fetch()` + `ReadableStream` reader
     4. On `event: chunks` → populate source panel
     5. On `event: routing` → update status bar intent badge
     6. On `event: token` → append to answer panel (render Markdown → HTML)
     7. On `event: done` → set status to READY
     8. Handle errors gracefully (show in answer panel)
     9. Auto-scroll answer panel as tokens arrive
   - Verify: Type query in browser, see streaming response
   - Depends on: tasks 1, 3

5. **Build JS: call graph tree** — render dependency data as collapsible tree
   - Files: `static/app.js` (or `static/tree.js`)
   - Steps:
     1. On `event: routing` with routine_names → call `/dependencies` API
     2. Render tree with `<details>/<summary>` elements (native HTML, no library)
     3. Root node = routine name, children = "Calls →" and "← Called by" sections
     4. Click on a leaf routine → call `/dependencies` for that routine (drill-down)
     5. Double-click or 'e' key on highlighted routine → call `/explain` via streaming
     6. Style tree nodes: routine names in bold cyan, category labels in dim
   - Verify: Query a routine, see call graph tree, click to drill down
   - Depends on: tasks 1, 3

6. **Build JS: source panel** — display retrieved chunks with metadata
   - Files: `static/app.js`
   - Steps:
     1. On `event: chunks` → render each chunk as a card
     2. Show: routine name, chunk type badge, file path, line range, relevance score bar
     3. Fortran code in `<pre>` blocks with monospace styling
     4. Truncate long chunks with "show more" toggle
     5. Sort by score descending
   - Verify: Query returns chunks displayed with scores and metadata
   - Depends on: tasks 1, 3

7. **Markdown rendering for answer panel** — convert LLM markdown to styled HTML
   - Files: `static/app.js`
   - Steps:
     1. Use `marked.js` (CDN, 8KB) for Markdown → HTML conversion
     2. Render incrementally as tokens arrive (re-render full accumulated text on each token batch)
     3. Style: headers in accent color, code blocks with dark background, tables with borders
     4. Batch token renders (every 50ms) to avoid DOM thrashing
   - Verify: Answer with headers, code blocks, and tables renders correctly
   - Depends on: task 4

### Phase 3: Integration + Polish

8. **Update Dockerfile + Railway config** — ensure static files are included in deployment
   - Files: `Dockerfile`, `requirements.txt` (if needed)
   - Steps:
     1. Add `COPY static/ static/` to Dockerfile
     2. Verify `aiofiles` is in requirements (needed for StaticFiles)
     3. Test build locally with `docker build`
   - Verify: `docker build .` succeeds, container serves index.html
   - Depends on: tasks 2-7

9. **Query history + keyboard shortcuts** — polish interactions
   - Files: `static/app.js`
   - Steps:
     1. Up/Down arrow in input cycles through query history (session array)
     2. Ctrl+L or Escape clears input
     3. Show help text on empty state: example queries, slash commands, keyboard shortcuts
     4. Disable input while query is in-flight (prevent double-submit)
   - Verify: Arrow keys cycle history, help text visible on load
   - Depends on: task 4

10. **Deploy + verify on Railway** — push, build, test live URL
    - Files: none (git push)
    - Steps:
      1. Git commit + push
      2. Wait for Railway build
      3. Test: open URL in browser, submit query, verify streaming
      4. Test: mobile viewport (Chrome DevTools responsive mode)
      5. Test: slash commands, call graph drill-down
    - Verify: `https://legacylens-production-9578.up.railway.app` shows web UI and handles queries
    - Depends on: tasks 8, 9

## Parallelism

```
Phase 1 (backend, parallel):
  Task 1: SSE endpoint           ← no deps
  Task 2: Static file serving    ← no deps

Phase 2 (frontend, parallel after Phase 1):
  Task 3: HTML + CSS             ← depends on task 2
  Task 4: Query + SSE JS         ← depends on tasks 1, 3
  Task 5: Call graph tree JS     ← depends on tasks 1, 3
  Task 6: Source panel JS        ← depends on tasks 1, 3
  Task 7: Markdown rendering     ← depends on task 4

Phase 3 (integration):
  Task 8: Dockerfile update      ← depends on tasks 2-7
  Task 9: History + shortcuts    ← depends on task 4
  Task 10: Deploy + verify       ← depends on tasks 8, 9
```

Since this is a solo build, effective order: 1 → 2 → 3 → 4+5+6+7 (interleaved) → 8 → 9 → 10

## Acceptance Criteria
- [ ] Opening the Railway URL in a browser shows a terminal-styled three-panel UI
- [ ] Typing a query and pressing Enter streams an LLM answer token-by-token
- [ ] Source panel shows retrieved Fortran chunks with scores
- [ ] Call graph panel shows routine dependencies as a collapsible tree
- [ ] `/explain SPKEZ` triggers the explain endpoint and streams the result
- [ ] `/deps FURNSH` shows dependency tree
- [ ] `/impact CHKIN` shows impact analysis
- [ ] Clicking a routine in the call graph drills down into its dependencies
- [ ] Status bar shows intent (EXPLAIN/DEPENDENCY/etc) and state (READY/THINKING)
- [ ] Works on mobile (panels stack vertically)
- [ ] No npm, no build step — just static files served by FastAPI
- [ ] Page loads in < 2 seconds on first visit

## Risks / Notes
- **SSE vs fetch streaming:** Modern browsers support `ReadableStream` from `fetch()`. SSE (`EventSource`) is simpler but only supports GET. We'll use `fetch()` with `text/event-stream` response for POST support.
- **Markdown rendering perf:** Re-rendering full markdown on every token is wasteful. Batch renders every 50ms to keep it smooth.
- **marked.js CDN:** Single external dependency (8KB). If CDN is a concern, vendor it into `static/`.
- **No syntax highlighting:** Keeping it simple — Fortran in monospace with terminal colors is readable enough and matches the aesthetic.
- **Mobile layout:** CSS grid with `@media` breakpoint is sufficient — no need for a responsive framework.
- **Estimated effort:** ~3-4 hours for a solo developer.

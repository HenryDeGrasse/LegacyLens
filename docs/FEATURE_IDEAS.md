# LegacyLens — Feature Ideas (10 → 3)

## All 10 Ideas

### 1. ⭐ Search Autocomplete + Routine List API
Add a `/api/routines` endpoint that returns all routine names from the call graph.
Wire up autocomplete in the web UI input with fuzzy matching.
**Impact: HIGH** — Discoverability is the #1 UX problem. Users don't know routine names.
**Effort: LOW** — Call graph already has all names. JS autocomplete is small.

### 2. Interactive D3.js Call Graph Visualization
Replace the text-based tree in the web UI with a force-directed graph.
Click to expand nodes, highlight paths.
**Impact: HIGH** — Very visual, very impressive.
**Effort: HIGH** — D3.js, layout, interactivity is a lot of frontend work.

### 3. ⭐ Startup Config Validation
Add `@app.on_event("startup")` that checks API keys, Pinecone connectivity,
call graph availability. Fail fast with clear error messages.
**Impact: MEDIUM** — Saves debugging time on deployment.
**Effort: LOW** — Simple checks.

### 4. ⭐ Code Complexity Metrics
Parse Fortran source to compute cyclomatic complexity, nesting depth, LOC,
parameter count per routine. Add `/metrics/{routine}` endpoint.
Integrate into explain output.
**Impact: MEDIUM** — Genuinely useful for understanding legacy code health.
**Effort: MEDIUM** — Parser already exists; need to add analysis.

### 5. Routine Comparison
Compare two routines side-by-side: purpose, parameters, dependencies, patterns.
`/compare?a=SPKEZ&b=SPKEZR`
**Impact: MEDIUM** — Useful but niche.
**Effort: MEDIUM**

### 6. Conversation Memory (Multi-turn)
Remember previous queries in a session and include prior Q&A in context.
**Impact: HIGH** — Multi-turn makes the AI 10x more useful.
**Effort: HIGH** — Token management, session state, context window balancing.

### 7. Export to Markdown/PDF
Export any analysis (explain, deps, impact, docgen) as a downloadable document.
**Impact: LOW** — Nice-to-have, the markdown output already exists.
**Effort: LOW**

### 8. Batch Doc Generation
Generate docs for all routines in a file or pattern at once.
**Impact: LOW** — Niche use case.
**Effort: MEDIUM** — Need to handle rate limits, progress.

### 9. Prompt Injection Detection
Add a simple classifier that detects injection attempts.
**Impact: LOW** — Defense-in-depth, system prompt already protects.
**Effort: MEDIUM**

### 10. Query Analytics Dashboard
Track query patterns, cache hit rates, popular routines, latency percentiles.
**Impact: LOW** — Dev-only value.
**Effort: MEDIUM**

---

## Ruthless Selection: TOP 3

### ❌ Rejected
- **#2 D3.js Graph** — Too much frontend effort for one session
- **#5 Comparison** — Useful but niche; explain already covers single routines well
- **#6 Conversation Memory** — Huge scope, token management is complex
- **#7 Export** — Markdown output already exists via `/docgen`
- **#8 Batch Docs** — Niche
- **#9 Prompt Injection** — System prompt + sanitization already handles this
- **#10 Analytics** — Dev-only value

### ✅ Selected

1. **Search Autocomplete + Routine List API** — Biggest UX win
2. **Startup Config Validation** — Prevents deployment pain  
3. **Code Complexity Metrics** — Unique analytical value

These three are: varied (UX / ops / analytics), well-scoped, and genuinely useful.

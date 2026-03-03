# LegacyLens Deep Dive — Summary

**Date:** March 3, 2026  
**Branch:** `fix/deep-dive-audit`  
**Commits:** 5

---

## Phase 1: Full Codebase Audit

Read every file in the repository — 29 Python source files, 4 frontend files (HTML/CSS/JS), Dockerfile, pyproject.toml, tests, and all documentation. Performed three successive passes looking for bugs, security issues, optimizations, and code smells.

---

## Phase 2: Bugs & Security Fixes (15 issues fixed)

### 🔴 Security — Critical

| # | Issue | Fix |
|---|-------|-----|
| 1 | **CORS misconfiguration**: `allow_origins=["*"]` + `allow_credentials=True` is an anti-pattern | Set `allow_credentials=False` |
| 2 | **No rate limiting**: API endpoints hit OpenAI with no throttle — anyone can burn the key | Added sliding-window rate limiter (30 req/min per IP) with memory-bounded IP tracking |
| 3 | **No input validation**: Could send megabyte-long query strings | Added Pydantic `Field` validators: `max_length=2000` on queries, depth capped at 10, top_k capped at 50 |
| 4 | **Internal errors exposed**: `HTTPException(500, detail=str(e))` leaked stack traces | Log exceptions server-side, return generic error messages |
| 5 | **MD5 for cache keys**: Cryptographically broken hash | Replaced with SHA-256 throughout |
| 6 | **XSS via LLM output**: `marked.parse()` → `innerHTML` could inject malicious HTML | Added `sanitizeHtml()` wrapper that strips `<script>`, `<iframe>`, `on*` handlers, `javascript:` hrefs |
| 7 | **Info leak in /health**: Exposed filesystem paths and CWD | Removed `static_dir`, `cwd`, `tried`, `exists` from responses |
| 8 | **Info leak in root route**: Debug info showed file paths | Removed debug output |
| 9 | **SSE error leak**: Raw exception messages sent to client | Replaced with generic error message |
| 10 | **Docker runs as root**: Default `python:3.12-slim` runs everything as root | Added `appuser:1000` non-root user |

### 🟡 Bugs — Functional

| # | Issue | Fix |
|---|-------|-----|
| 11 | **Thread safety**: `_embed_cache` and `_answer_cache` accessed from ThreadPoolExecutor without locks | Added `_embed_lock` and `_answer_lock` with proper check-then-set patterns |
| 12 | **Triple CallGraph**: `dependencies.py`, `impact.py`, and `services.py` each cached their own copy | Consolidated via `services.get_call_graph_obj()` singleton |
| 13 | **`/stats` created redundant Pinecone client**: Bypassed the shared singleton | Rewired to use `services.get_index()` |
| 14 | **`chunker.py` crash**: `__main__` block called `.split()` on list-typed patterns metadata | Added `isinstance` check for list vs string |
| 15 | **`scanner.py` file handle leak**: `p.open()` without `with` statement | Wrapped in `with` block |

### 🟢 Code Quality

| # | Issue | Fix |
|---|-------|-----|
| — | Dead code: `_display_query_result` in TUI (never called) | Removed |
| — | Dead code: `_fortran_highlight` in TUI (no-op) | Removed |
| — | Duplicate `import hashlib` in `generator.py` | Removed inner import |
| — | `print()` calls in `search.py` | Replaced with `logging.getLogger()` |
| — | `_logger` stale reference in `main.py` | Fixed to `logger` |
| — | Better embedding queries in `explain.py` and `docgen.py` | Changed bare name `"SPKEZ"` to semantic `"Explain the SPICE routine SPKEZ"` for better vector match |

---

## Phase 3: Feature Selection (10 → 3)

### 10 Ideas Considered

1. ⭐ **Search Autocomplete + Routine List API** — HIGH impact, LOW effort
2. Interactive D3.js Call Graph — HIGH impact, HIGH effort
3. ⭐ **Startup Config Validation** — MEDIUM impact, LOW effort
4. ⭐ **Code Complexity Metrics** — MEDIUM impact, MEDIUM effort
5. Routine Comparison — MEDIUM impact, MEDIUM effort
6. Conversation Memory — HIGH impact, HIGH effort
7. Export to Markdown/PDF — LOW impact, LOW effort
8. Batch Doc Generation — LOW impact, MEDIUM effort
9. Prompt Injection Detection — LOW impact, MEDIUM effort
10. Query Analytics Dashboard — LOW impact, MEDIUM effort

### Selection Criteria

- **Impact**: Does it solve a real user problem?
- **Effort**: Can it be implemented fully and tested in one session?
- **Variety**: Do the features cover different concerns (UX / ops / analytics)?

### Rejected

- **D3.js Graph**: Too much frontend effort for one session
- **Conversation Memory**: Token management and session state are complex
- **Comparison**: Niche use case — explain already covers single routines
- **Export/Batch/Injection/Analytics**: Low impact or niche

---

## Phase 4: Feature Implementation

### Feature 1: Search Autocomplete + Routine List API

**Backend:**
- `GET /api/routines?q=SPK&limit=8` — returns matching routine names from call graph
- Prefix match first, then substring match
- Input validation: query capped at 100 chars, limit clamped to [1, 100]

**Frontend:**
- Autocomplete dropdown appears after 2 characters, debounced at 150ms
- Keyboard navigation: ↑↓ to select, Enter to pick, Escape to dismiss
- Merged into existing keydown handler (no conflicts with query submission or history)
- Selecting a suggestion populates input with `What does {ROUTINE} do?`
- CRT-themed styling with phosphor glow

### Feature 2: Startup Config Validation

- `@app.on_event("startup")` validates API key format on boot
- Checks: `OPENAI_API_KEY` present and starts with `sk-`, `PINECONE_API_KEY` present, `PINECONE_INDEX` set
- Pre-warms call graph and logs routine/alias counts
- Non-fatal: logs errors but doesn't block startup (allows health check to report status)
- `/health` now reports `call_graph_loaded: true/false`

### Feature 3: Code Complexity Metrics

**Backend:** `POST /metrics` + `app/features/metrics.py`
- Static analysis of Fortran code retrieved from Pinecone
- **LOC breakdown**: total, code, comment, blank lines + comment ratio
- **Cyclomatic complexity**: estimated from branch points (IF, ELSE IF, DO, GOTO, SIGERR, RETURN)
- **Max nesting depth**: tracks IF THEN / DO nesting
- **Parameter count**: parsed from routine signature
- **Dependency stats**: call count and caller count from call graph
- **Human-readable ratings**: 
  - Complexity: LOW (≤5) / MEDIUM (≤10) / HIGH (≤20) / VERY HIGH
  - Size: SMALL (≤50) / MEDIUM (≤200) / LARGE (≤500) / VERY LARGE
- No LLM calls needed — pure code analysis

**CLI:** `legacylens metrics SPKEZ` — renders table with Rich

**TUI:** `/metrics ROUTINE` or `/m ROUTINE` — displays in answer panel

**Web UI:** `/metrics ROUTINE` slash command — renders markdown table

---

## File Changes Summary

| File | Changes |
|------|---------|
| `app/main.py` | Rate limiter, input validation, error sanitization, startup validation, `/api/routines`, `/metrics` |
| `app/services.py` | Thread-safe caches, SHA-256, `get_call_graph_obj()` singleton |
| `app/features/metrics.py` | **NEW** — Code complexity analysis |
| `app/features/dependencies.py` | Use shared CallGraph singleton |
| `app/features/impact.py` | Use shared CallGraph singleton |
| `app/features/explain.py` | Use shared singleton + better embedding queries |
| `app/features/docgen.py` | Use shared singleton + better embedding queries |
| `app/retrieval/generator.py` | SHA-256, removed duplicate import |
| `app/retrieval/search.py` | Proper logging instead of print() |
| `app/ingestion/scanner.py` | Fix file handle leak |
| `app/ingestion/chunker.py` | Fix list-typed patterns handling |
| `app/cli.py` | Added `metrics` subcommand |
| `app/tui.py` | Added `/metrics`, removed dead code |
| `static/app.js` | XSS sanitizer, autocomplete, `/metrics` command |
| `static/styles.css` | Autocomplete styles, METRICS intent color |
| `static/index.html` | Updated footer and examples |
| `Dockerfile` | Non-root user |
| `README.md` | Documented new features and endpoints |

---

## What's Not Changed (and Why)

- **Ingestion pipeline** (`ingest.py`, `embedder.py`, `loader.py`): These are one-time scripts with appropriate `print()` output for CLI visibility. No runtime bugs.
- **Fortran parser**: The regex-based parser is well-tested and handles SPICE's specific fixed-form format correctly. The edge cases (ENTRY points, continuation lines) are properly handled.
- **Test suite**: Tests are focused on TUI behavior which is unchanged in substance.
- **Frontend CRT effects**: These are cosmetic and well-implemented with correct barrel distortion math.

---

## Commit Log

```
bc18ae4 fix: harden autocomplete input validation, update README with new features
f7b4676 feat: add autocomplete, startup validation, and code metrics
05daf25 fix(security): XSS protection, rate limiter memory bound
9ff98b8 fix(security): second-pass hardening — info leaks, Docker non-root, logging
3dbe562 fix(security): harden API with rate limiting, input validation, thread safety
```

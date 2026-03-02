# Epic: Phase 5 — Interactive TUI & Project Polish

## Goal

Replace the fire-and-forget CLI with an interactive terminal UI, flatten the repository structure, and prepare submission artifacts (architecture doc, cost analysis, demo video).

## Why TUI over Web UI

The target users are developers working with Fortran 77 legacy code — they're already in a terminal. A TUI:

- **Eliminates context switching** — no browser, no URL, no page load
- **Reuses existing rendering** — Rich already handles syntax highlighting, tables, and Markdown
- **Enables interactive exploration** — browse call graph → drill into routine → see blast radius → generate docs, all in one session
- **Is a differentiator** — a polished TUI stands out vs. the typical React chat wrapper
- **Fits the theme** — a terminal-native tool for 1977-era Fortran feels right

The REST API remains available for programmatic access and any future web frontend.

## Scope

### 1. Flatten Repository Structure
- [ ] Move `backend/app/`, `backend/tests/`, `backend/pyproject.toml` to repo root
- [ ] Fix all relative paths (`../data/spice` → `data/spice`)
- [ ] Update Dockerfile, Railway config, `.gitignore`
- [ ] Update README — all commands run from repo root

### 2. Interactive TUI
- [ ] Search box with live input
- [ ] Router intent displayed as query is typed
- [ ] Results panel with syntax-highlighted Fortran chunks
- [ ] Answer panel with streaming LLM output (Markdown rendered)
- [ ] Tabbed views: Answer | Chunks | Call Graph | Patterns
- [ ] Drill-down: select a routine name in results → auto-query for explanation
- [ ] Dependency tree view (ASCII or Textual tree widget)
- [ ] Query history (up/down arrow)
- [ ] Status bar: latency, tokens, cached, model

### 3. Documentation
- [ ] RAG Architecture Document (1–2 pages)
- [ ] AI Cost Analysis (dev spend + production projections)
- [ ] Update README with TUI screenshots/usage

### 4. Demo
- [ ] Record 3–5 minute demo video (TUI walkthrough)
- [ ] Social media post

## Non-Goals

- Web frontend (REST API covers this; web UI can be added later)
- Authentication or rate limiting
- CI/CD pipeline
- Changing the embedding model or vector DB

## Tech Choice: Rich vs Textual

| | Rich | Textual |
|---|---|---|
| Already used | ✅ (CLI) | ❌ |
| Interactive widgets | ❌ (static output) | ✅ (input, tabs, trees, scrollable) |
| Streaming support | ✅ (Live) | ✅ (reactive) |
| Learning curve | Low (known) | Medium |

**Recommendation:** Use **Textual** for the full interactive TUI. It builds on Rich (same author, same rendering engine) and adds the widget/layout system needed for tabs, input boxes, and drill-down navigation.

## Dependencies

- `textual>=0.80.0` — TUI framework
- Everything else already in pyproject.toml

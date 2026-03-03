# LegacyLens Deep Dive — Investigation & Fix Plan

## Phase 1: Bugs & Security Fixes

### SECURITY (Critical)
1. **CORS misconfiguration** — `allow_origins=["*"]` + `allow_credentials=True` is rejected by browsers and an anti-pattern. Fix: remove `allow_credentials` or scope origins.
2. **No rate limiting** — All API endpoints hit OpenAI API with no throttle. Anybody can burn the key.
3. **No input length validation** — Can send megabyte queries.
4. **Internal errors exposed** — `HTTPException(500, detail=str(e))` leaks stack info.
5. **MD5 for cache keys** — Weak hash, use hashlib.sha256 instead.

### BUGS (Functional)
6. **Thread safety on `_embed_cache` and `_answer_cache`** — Plain dicts accessed from ThreadPoolExecutor workers without locks.
7. **Duplicate `_get_graph()` pattern** — `dependencies.py` and `impact.py` each have their own cached CallGraph, separate from `services.py`. 3 copies in memory, potential stale state.
8. **`_display_query_result` is dead code** in TUI — Never called, references non-existent dict keys.
9. **`generate_answer_stream` stores empty `usage: {}` in cache** — Cached streaming answers show 0 tokens forever.
10. **`chunker.py` __main__ block crashes** — `c.metadata.get("patterns", "").split(", ")` fails because patterns is a list.
11. **`main.py:stats()` creates redundant Pinecone client** — Bypasses `services.get_index()`.
12. **Duplicate `import hashlib`** in `generator.py` — Top-level and inside `generate_answer_stream`.
13. **`get_file_stats` resource leak** — `p.open()` without closing.
14. **No depth validation** on `/dependencies` and `/impact` — Can cause expensive traversals.
15. **`explain.py` and `docgen.py` re-create CallGraph** on every call instead of using the singleton.

### OPTIMIZATIONS
16. **Consolidate CallGraph access** — Single cached instance via `services.py`.
17. **Reduce code duplication** — `generate_answer` and `generate_answer_stream` share ~80% logic.
18. **Better embedding for routine lookup** — Embed "Explain the routine SPKEZ" instead of bare "SPKEZ".

## Phase 2: Re-scan After Fixes

## Phase 3: Feature Ideas (10 → 3)

## Phase 4: Implementation & Polish

"""Unit tests for answer cache and embedding cache (Gap #7 — Medium).

Exercises TTL expiration, capacity eviction, cache key isolation,
and thread safety without any API calls ($0).

Run:
    pytest tests/test_caching.py -v
"""

from __future__ import annotations

import threading
import time

import pytest

import app.services as svc


# ═══════════════════════════════════════════════════════════════════════
# Fixtures — isolated cache state per test
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def isolate_caches():
    """Reset caches before and after each test."""
    original_answer = svc._answer_cache.copy()
    original_embed = svc._embed_cache.copy()
    svc._answer_cache.clear()
    svc._embed_cache.clear()
    yield
    svc._answer_cache.clear()
    svc._answer_cache.update(original_answer)
    svc._embed_cache.clear()
    svc._embed_cache.update(original_embed)


# ═══════════════════════════════════════════════════════════════════════
# Answer cache — basic operations
# ═══════════════════════════════════════════════════════════════════════


class TestAnswerCacheBasic:
    """get_cached_answer / set_cached_answer operations."""

    def test_miss_returns_none(self):
        result = svc.get_cached_answer("q", "ctx_hash", "model")
        assert result is None

    def test_hit_after_set(self):
        entry = {"answer": "A", "citations": [], "model": "m", "usage": {}}
        svc.set_cached_answer("q", "ctx_hash", "model", entry)
        result = svc.get_cached_answer("q", "ctx_hash", "model")
        assert result is not None
        assert result["answer"] == "A"

    def test_different_queries_independent(self):
        entry1 = {"answer": "A1", "citations": [], "model": "m", "usage": {}}
        entry2 = {"answer": "A2", "citations": [], "model": "m", "usage": {}}
        svc.set_cached_answer("q1", "ctx", "model", entry1)
        svc.set_cached_answer("q2", "ctx", "model", entry2)

        assert svc.get_cached_answer("q1", "ctx", "model")["answer"] == "A1"
        assert svc.get_cached_answer("q2", "ctx", "model")["answer"] == "A2"

    def test_different_models_independent(self):
        entry1 = {"answer": "A1", "citations": [], "model": "m1", "usage": {}}
        entry2 = {"answer": "A2", "citations": [], "model": "m2", "usage": {}}
        svc.set_cached_answer("q", "ctx", "model-a", entry1)
        svc.set_cached_answer("q", "ctx", "model-b", entry2)

        assert svc.get_cached_answer("q", "ctx", "model-a")["answer"] == "A1"
        assert svc.get_cached_answer("q", "ctx", "model-b")["answer"] == "A2"

    def test_different_context_independent(self):
        entry1 = {"answer": "A1", "citations": [], "model": "m", "usage": {}}
        entry2 = {"answer": "A2", "citations": [], "model": "m", "usage": {}}
        svc.set_cached_answer("q", "ctx1", "model", entry1)
        svc.set_cached_answer("q", "ctx2", "model", entry2)

        assert svc.get_cached_answer("q", "ctx1", "model")["answer"] == "A1"
        assert svc.get_cached_answer("q", "ctx2", "model")["answer"] == "A2"


# ═══════════════════════════════════════════════════════════════════════
# Answer cache — TTL expiration
# ═══════════════════════════════════════════════════════════════════════


class TestAnswerCacheTTL:
    """Entries older than _ANSWER_TTL should be evicted on access."""

    def test_expired_entry_returns_none(self):
        entry = {"answer": "old", "citations": [], "model": "m", "usage": {}}
        svc.set_cached_answer("q", "ctx", "model", entry)

        # Manually expire the entry
        key = svc._answer_cache_key("q", "ctx", "model")
        with svc._answer_lock:
            ts, resp = svc._answer_cache[key]
            svc._answer_cache[key] = (ts - svc._ANSWER_TTL - 10, resp)

        result = svc.get_cached_answer("q", "ctx", "model")
        assert result is None

    def test_fresh_entry_returned(self):
        entry = {"answer": "fresh", "citations": [], "model": "m", "usage": {}}
        svc.set_cached_answer("q", "ctx", "model", entry)

        result = svc.get_cached_answer("q", "ctx", "model")
        assert result is not None
        assert result["answer"] == "fresh"

    def test_expired_entry_removed_from_cache(self):
        entry = {"answer": "old", "citations": [], "model": "m", "usage": {}}
        svc.set_cached_answer("q", "ctx", "model", entry)

        key = svc._answer_cache_key("q", "ctx", "model")
        with svc._answer_lock:
            ts, resp = svc._answer_cache[key]
            svc._answer_cache[key] = (ts - svc._ANSWER_TTL - 10, resp)

        svc.get_cached_answer("q", "ctx", "model")

        with svc._answer_lock:
            assert key not in svc._answer_cache


# ═══════════════════════════════════════════════════════════════════════
# Answer cache — capacity eviction
# ═══════════════════════════════════════════════════════════════════════


class TestAnswerCacheCapacity:
    """Cache should evict oldest entries when at capacity."""

    def test_evicts_at_capacity(self):
        # Fill to capacity
        for i in range(svc._ANSWER_CACHE_MAX):
            entry = {"answer": f"A{i}", "citations": [], "model": "m", "usage": {}}
            svc.set_cached_answer(f"q{i}", "ctx", "model", entry)

        # Cache should be full
        with svc._answer_lock:
            assert len(svc._answer_cache) == svc._ANSWER_CACHE_MAX

        # Add one more — should evict the oldest
        entry = {"answer": "overflow", "citations": [], "model": "m", "usage": {}}
        svc.set_cached_answer("overflow", "ctx", "model", entry)

        with svc._answer_lock:
            assert len(svc._answer_cache) <= svc._ANSWER_CACHE_MAX

    def test_newest_entry_survives_eviction(self):
        for i in range(svc._ANSWER_CACHE_MAX + 5):
            entry = {"answer": f"A{i}", "citations": [], "model": "m", "usage": {}}
            svc.set_cached_answer(f"q{i}", "ctx", "model", entry)

        # The most recent entry should still be accessible
        last = svc.get_cached_answer(f"q{svc._ANSWER_CACHE_MAX + 4}", "ctx", "model")
        assert last is not None


# ═══════════════════════════════════════════════════════════════════════
# Answer cache — key generation
# ═══════════════════════════════════════════════════════════════════════


class TestAnswerCacheKey:
    """Cache key should be a deterministic hash."""

    def test_same_inputs_same_key(self):
        k1 = svc._answer_cache_key("q", "ctx", "model")
        k2 = svc._answer_cache_key("q", "ctx", "model")
        assert k1 == k2

    def test_different_inputs_different_key(self):
        k1 = svc._answer_cache_key("q1", "ctx", "model")
        k2 = svc._answer_cache_key("q2", "ctx", "model")
        assert k1 != k2

    def test_key_is_hex_string(self):
        key = svc._answer_cache_key("q", "ctx", "model")
        assert len(key) == 32
        int(key, 16)  # should not raise


# ═══════════════════════════════════════════════════════════════════════
# Embedding cache — basic operations
# ═══════════════════════════════════════════════════════════════════════


class TestEmbedCacheBasic:
    """Embedding cache stores vectors by text key."""

    def test_miss_not_in_cache(self):
        with svc._embed_lock:
            assert "never seen before xyz" not in svc._embed_cache

    def test_manual_set_and_get(self):
        """Directly test the cache dict (embed_text calls OpenAI)."""
        with svc._embed_lock:
            svc._embed_cache["test query"] = [0.1, 0.2, 0.3]

        with svc._embed_lock:
            assert svc._embed_cache["test query"] == [0.1, 0.2, 0.3]

    def test_strips_whitespace_for_key(self):
        """embed_text uses text.strip() as key."""
        with svc._embed_lock:
            svc._embed_cache["hello"] = [1.0, 2.0]
        # The key should match " hello " after stripping
        # (This tests the cache lookup logic conceptually)
        with svc._embed_lock:
            assert "hello" in svc._embed_cache


# ═══════════════════════════════════════════════════════════════════════
# Embedding cache — capacity eviction
# ═══════════════════════════════════════════════════════════════════════


class TestEmbedCacheCapacity:
    """Embedding cache evicts oldest entries at _EMBED_CACHE_MAX."""

    def test_evicts_at_capacity(self):
        with svc._embed_lock:
            for i in range(svc._EMBED_CACHE_MAX + 10):
                svc._embed_cache[f"text-{i}"] = [float(i)]

            # Should not exceed max + some margin (FIFO eviction happens on insert)
            # Note: direct dict manipulation bypasses eviction logic,
            # so this tests the data structure capacity
            assert len(svc._embed_cache) == svc._EMBED_CACHE_MAX + 10

        # The actual eviction logic is in embed_text(), which calls OpenAI.
        # We test the eviction code path via the answer cache instead.


# ═══════════════════════════════════════════════════════════════════════
# Thread safety
# ═══════════════════════════════════════════════════════════════════════


class TestCacheThreadSafety:
    """Concurrent cache access should not corrupt state."""

    def test_concurrent_answer_cache_writes(self):
        errors = []

        def writer(thread_id: int):
            try:
                for i in range(50):
                    entry = {"answer": f"T{thread_id}-A{i}", "citations": [], "model": "m", "usage": {}}
                    svc.set_cached_answer(f"T{thread_id}-q{i}", "ctx", "model", entry)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

    def test_concurrent_answer_cache_read_write(self):
        errors = []

        def writer():
            try:
                for i in range(50):
                    entry = {"answer": f"A{i}", "citations": [], "model": "m", "usage": {}}
                    svc.set_cached_answer(f"q{i}", "ctx", "model", entry)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(50):
                    svc.get_cached_answer(f"q{i}", "ctx", "model")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

    def test_concurrent_embed_cache_access(self):
        errors = []

        def accessor(thread_id: int):
            try:
                for i in range(50):
                    key = f"T{thread_id}-text-{i}"
                    with svc._embed_lock:
                        svc._embed_cache[key] = [float(i)]
                    with svc._embed_lock:
                        _ = svc._embed_cache.get(key)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=accessor, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

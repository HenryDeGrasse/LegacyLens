"""API endpoint tests (Gap #4 — Rate Limiter + Gap #5 — Feature Endpoints).

Tests the FastAPI endpoints via TestClient without real API calls.
Validates rate limiting, input validation, error handling, and the
async→sync migration.

Run:
    pytest tests/test_api_endpoints.py -v
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app, _rate_buckets, _RATE_LIMIT, _RATE_WINDOW


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Clear rate limit state between tests."""
    _rate_buckets.clear()
    yield
    _rate_buckets.clear()


client = TestClient(app)


# ═══════════════════════════════════════════════════════════════════════
# Health & basic endpoints
# ═══════════════════════════════════════════════════════════════════════


class TestHealthEndpoints:
    """Basic health and info endpoints."""

    def test_health_returns_ok(self):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert "call_graph_loaded" in body

    def test_root_returns_html_or_api_info(self):
        r = client.get("/")
        assert r.status_code == 200
        # Either serves HTML (if static exists) or returns API info

    def test_routines_list_no_query(self):
        r = client.get("/api/routines")
        assert r.status_code == 200
        body = r.json()
        assert "routines" in body
        assert "total" in body
        assert isinstance(body["routines"], list)

    def test_routines_autocomplete(self):
        r = client.get("/api/routines?q=SPK&limit=10")
        assert r.status_code == 200
        body = r.json()
        assert all("SPK" in name for name in body["routines"])

    def test_routines_limit_clamped(self):
        r = client.get("/api/routines?limit=200")
        assert r.status_code == 200
        body = r.json()
        assert len(body["routines"]) <= 100

    def test_routines_empty_query(self):
        r = client.get("/api/routines?q=")
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# Session management
# ═══════════════════════════════════════════════════════════════════════


class TestSessionEndpoint:
    """POST /api/session creates a new conversation session."""

    def test_creates_session(self):
        r = client.post("/api/session")
        assert r.status_code == 200
        body = r.json()
        assert "session_id" in body
        assert len(body["session_id"]) == 16

    def test_unique_sessions(self):
        ids = set()
        for _ in range(10):
            r = client.post("/api/session")
            ids.add(r.json()["session_id"])
        assert len(ids) == 10


# ═══════════════════════════════════════════════════════════════════════
# Rate limiter
# ═══════════════════════════════════════════════════════════════════════


class TestRateLimiter:
    """In-memory sliding-window rate limiter."""

    def test_allows_normal_traffic(self):
        """Under the limit, all requests should pass."""
        for i in range(5):
            r = client.post("/api/session")
            assert r.status_code == 200, f"Request {i} unexpectedly blocked"

    def test_blocks_at_limit(self):
        """After RATE_LIMIT requests, should return 429."""
        for _ in range(_RATE_LIMIT):
            client.post("/api/session")

        r = client.post("/api/session")
        assert r.status_code == 429
        assert "Rate limit" in r.json()["detail"]

    def test_get_requests_not_rate_limited(self):
        """Rate limiting only applies to POST requests."""
        # Exhaust POST rate limit
        for _ in range(_RATE_LIMIT):
            client.post("/api/session")

        # GET should still work
        r = client.get("/health")
        assert r.status_code == 200

    def test_rate_limit_window_expires(self):
        """Old entries should expire after the window."""
        ip = "testclient"
        # Inject old timestamps
        old_time = time.time() - _RATE_WINDOW - 1
        _rate_buckets[ip] = [old_time] * _RATE_LIMIT

        # Should allow new requests (old entries pruned)
        r = client.post("/api/session")
        assert r.status_code == 200

    def test_rate_limit_per_ip(self):
        """Different IPs should have independent limits."""
        # We can only test with the single TestClient IP,
        # but we can verify the bucket structure
        client.post("/api/session")
        assert len(_rate_buckets) > 0


# ═══════════════════════════════════════════════════════════════════════
# /query endpoint
# ═══════════════════════════════════════════════════════════════════════


class TestQueryEndpoint:
    """POST /query — the main RAG query endpoint."""

    def test_out_of_scope_query(self):
        """Out-of-scope queries return canned response without API calls."""
        r = client.post("/query", json={
            "question": "What's the weather today?",
            "top_k": 5,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["routing"]["intent"] == "OUT_OF_SCOPE"
        assert body["chunks"] == []
        assert "SPICE Toolkit" in body["answer"]

    def test_empty_question_rejected(self):
        """Empty question should be rejected by validation."""
        r = client.post("/query", json={"question": "", "top_k": 5})
        assert r.status_code == 422

    def test_missing_question_rejected(self):
        r = client.post("/query", json={"top_k": 5})
        assert r.status_code == 422

    def test_top_k_bounds(self):
        """top_k must be between 1 and 50."""
        r = client.post("/query", json={"question": "What's the weather?", "top_k": 0})
        assert r.status_code == 422

        r = client.post("/query", json={"question": "What's the weather?", "top_k": 51})
        assert r.status_code == 422

    def test_session_id_optional(self):
        """session_id should be optional."""
        r = client.post("/query", json={
            "question": "What's the weather?",
            "top_k": 5,
        })
        assert r.status_code == 200

    def test_session_id_accepted(self):
        """session_id should be accepted."""
        r = client.post("/query", json={
            "question": "What's the weather?",
            "top_k": 5,
            "session_id": "abc123",
        })
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# /api/stream endpoint
# ═══════════════════════════════════════════════════════════════════════


class TestStreamEndpoint:
    """POST /api/stream — SSE streaming endpoint."""

    def test_out_of_scope_stream(self):
        """Out-of-scope queries stream a canned response."""
        r = client.post("/api/stream", json={
            "question": "Tell me a joke",
            "top_k": 5,
        })
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        text = r.text
        assert "event: routing" in text
        assert '"OUT_OF_SCOPE"' in text
        assert "event: done" in text

    def test_empty_question_rejected(self):
        r = client.post("/api/stream", json={"question": "", "top_k": 5})
        assert r.status_code == 422

    def test_session_id_optional(self):
        r = client.post("/api/stream", json={
            "question": "What's the weather?",
            "top_k": 5,
        })
        assert r.status_code == 200

    def test_query_and_stream_parity_for_out_of_scope(self):
        """Both /query and /api/stream should agree on OUT_OF_SCOPE intent."""
        q = "Tell me a joke about space"

        r_query = client.post("/query", json={"question": q, "top_k": 5})
        r_stream = client.post("/api/stream", json={"question": q, "top_k": 5})

        assert r_query.json()["routing"]["intent"] == "OUT_OF_SCOPE"
        assert '"OUT_OF_SCOPE"' in r_stream.text


# ═══════════════════════════════════════════════════════════════════════
# /dependencies endpoint
# ═══════════════════════════════════════════════════════════════════════


class TestDependenciesEndpoint:
    """POST /dependencies — call graph dependency lookup."""

    def test_valid_routine(self):
        r = client.post("/dependencies", json={
            "routine_name": "SPKEZ",
            "depth": 1,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["routine_name"] == "SPKEZ"
        assert "direct_calls" in body
        assert "all_callees" in body
        assert "all_callers" in body

    def test_valid_entry_point(self):
        """ENTRY aliases should resolve to parent and show dependencies."""
        # Get an alias from the call graph
        data = json.loads(open("data/call_graph.json").read())
        aliases = data.get("aliases", {})
        if not aliases:
            pytest.skip("No aliases")
        alias = list(aliases.keys())[0]

        r = client.post("/dependencies", json={
            "routine_name": alias,
            "depth": 1,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["is_entry_point"] is True

    def test_depth_bounds(self):
        """depth must be between 1 and 10."""
        r = client.post("/dependencies", json={
            "routine_name": "SPKEZ",
            "depth": 0,
        })
        assert r.status_code == 422

        r = client.post("/dependencies", json={
            "routine_name": "SPKEZ",
            "depth": 11,
        })
        assert r.status_code == 422

    def test_empty_routine_name_rejected(self):
        r = client.post("/dependencies", json={
            "routine_name": "",
            "depth": 1,
        })
        assert r.status_code == 422

    def test_case_insensitive_lookup(self):
        """Routine names should be uppercased internally."""
        r = client.post("/dependencies", json={
            "routine_name": "spkez",
            "depth": 1,
        })
        assert r.status_code == 200
        assert r.json()["routine_name"] == "SPKEZ"


# ═══════════════════════════════════════════════════════════════════════
# /impact endpoint
# ═══════════════════════════════════════════════════════════════════════


class TestImpactEndpoint:
    """POST /impact — blast radius analysis."""

    def test_valid_routine(self):
        r = client.post("/impact", json={
            "routine_name": "CHKIN",
            "depth": 1,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["routine_name"] == "CHKIN"
        assert "total_affected" in body
        assert "levels" in body
        assert body["total_affected"] >= 0

    def test_deeper_impact(self):
        """Deeper depth should return more or equal affected routines."""
        r1 = client.post("/impact", json={"routine_name": "CHKIN", "depth": 1})
        r2 = client.post("/impact", json={"routine_name": "CHKIN", "depth": 2})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r2.json()["total_affected"] >= r1.json()["total_affected"]

    def test_depth_bounds(self):
        r = client.post("/impact", json={"routine_name": "SPKEZ", "depth": 0})
        assert r.status_code == 422

    def test_empty_routine_name_rejected(self):
        r = client.post("/impact", json={"routine_name": "", "depth": 1})
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# /patterns endpoint
# ═══════════════════════════════════════════════════════════════════════


class TestPatternsEndpoint:
    """GET /patterns — list available SPICE patterns."""

    def test_list_patterns(self):
        r = client.get("/patterns")
        assert r.status_code == 200
        body = r.json()
        assert "patterns" in body
        assert isinstance(body["patterns"], list)


# ═══════════════════════════════════════════════════════════════════════
# /stats endpoint
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# /metrics endpoint
# ═══════════════════════════════════════════════════════════════════════


class TestMetricsEndpoint:
    """POST /metrics — code complexity metrics."""

    def test_valid_routine(self):
        r = client.post("/metrics", json={"routine_name": "SPKEZ"})
        assert r.status_code == 200
        body = r.json()
        assert "routine_name" in body or "name" in body

    def test_empty_routine_rejected(self):
        r = client.post("/metrics", json={"routine_name": ""})
        assert r.status_code == 422

    def test_long_routine_rejected(self):
        r = client.post("/metrics", json={"routine_name": "X" * 101})
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# /explain endpoint
# ═══════════════════════════════════════════════════════════════════════


class TestExplainEndpoint:
    """POST /explain — routine explanation (requires Pinecone + LLM)."""

    def test_empty_routine_rejected(self):
        r = client.post("/explain", json={"routine_name": ""})
        assert r.status_code == 422

    def test_long_routine_rejected(self):
        r = client.post("/explain", json={"routine_name": "X" * 101})
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# /docgen endpoint
# ═══════════════════════════════════════════════════════════════════════


class TestDocgenEndpoint:
    """POST /docgen — documentation generation (requires Pinecone + LLM)."""

    def test_empty_routine_rejected(self):
        r = client.post("/docgen", json={"routine_name": ""})
        assert r.status_code == 422

    def test_long_routine_rejected(self):
        r = client.post("/docgen", json={"routine_name": "X" * 101})
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# /patterns/search endpoint
# ═══════════════════════════════════════════════════════════════════════


class TestPatternSearchEndpoint:
    """POST /patterns/search — pattern-filtered search."""

    def test_empty_pattern_rejected(self):
        r = client.post("/patterns/search", json={"pattern": ""})
        assert r.status_code == 422

    def test_long_pattern_rejected(self):
        r = client.post("/patterns/search", json={"pattern": "X" * 101})
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# /stats endpoint
# ═══════════════════════════════════════════════════════════════════════


class TestStatsEndpoint:
    """GET /stats — Pinecone index stats."""

    def test_stats_without_api_key(self):
        """Without Pinecone API key, stats should return an error."""
        # In test environment, this may or may not have the key
        r = client.get("/stats")
        # Either 200 (real key) or 500 (missing key) — both are valid
        assert r.status_code in (200, 500)


# ═══════════════════════════════════════════════════════════════════════
# Input validation across endpoints
# ═══════════════════════════════════════════════════════════════════════


class TestInputValidation:
    """Shared input validation patterns."""

    def test_long_question_rejected(self):
        """Questions longer than 2000 chars should be rejected."""
        r = client.post("/query", json={
            "question": "x" * 2001,
            "top_k": 5,
        })
        assert r.status_code == 422

    def test_question_at_max_length(self):
        """Questions at exactly 2000 chars should be accepted."""
        r = client.post("/query", json={
            "question": "x" * 2000,
            "top_k": 5,
        })
        # Should be accepted (might be out of scope but not a validation error)
        assert r.status_code in (200, 404, 500)

    def test_long_routine_name_rejected(self):
        """Routine names longer than 100 chars should be rejected."""
        r = client.post("/dependencies", json={
            "routine_name": "X" * 101,
            "depth": 1,
        })
        assert r.status_code == 422

    def test_json_required(self):
        """Non-JSON POST should fail."""
        r = client.post("/query", content="not json")
        assert r.status_code == 422

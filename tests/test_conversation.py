"""Unit tests for ConversationStore multi-turn sessions (Gap #2 — Critical).

Exercises TTL eviction, capacity limits, history trimming, thread safety,
and message building without any API calls ($0).

Run:
    pytest tests/test_conversation.py -v
"""

from __future__ import annotations

import time
import threading
from unittest.mock import patch

import pytest

from app.retrieval.generator import (
    ConversationStore,
    _build_history_messages,
    MAX_HISTORY_TURNS,
    MAX_SESSIONS,
    SESSION_TTL_SECONDS,
)


# ═══════════════════════════════════════════════════════════════════════
# Basic operations
# ═══════════════════════════════════════════════════════════════════════


class TestConversationStoreBasics:
    """Core add_turn / get_history operations."""

    def test_new_session_id_unique(self):
        store = ConversationStore()
        ids = {store.new_session_id() for _ in range(100)}
        assert len(ids) == 100

    def test_new_session_id_format(self):
        store = ConversationStore()
        sid = store.new_session_id()
        assert len(sid) == 16
        assert sid.isalnum()

    def test_empty_history_for_unknown_session(self):
        store = ConversationStore()
        assert store.get_history("nonexistent") == []

    def test_add_and_get_single_turn(self):
        store = ConversationStore()
        sid = store.new_session_id()
        store.add_turn(sid, "What does SPKEZ do?", "SPKEZ computes state vectors.")
        history = store.get_history(sid)
        assert len(history) == 1
        assert history[0].question == "What does SPKEZ do?"
        assert history[0].answer == "SPKEZ computes state vectors."

    def test_add_multiple_turns(self):
        store = ConversationStore()
        sid = store.new_session_id()
        for i in range(3):
            store.add_turn(sid, f"Q{i}", f"A{i}")
        history = store.get_history(sid)
        assert len(history) == 3
        assert history[0].question == "Q0"
        assert history[2].question == "Q2"

    def test_multiple_sessions_independent(self):
        store = ConversationStore()
        sid1 = store.new_session_id()
        sid2 = store.new_session_id()
        store.add_turn(sid1, "Q1", "A1")
        store.add_turn(sid2, "Q2", "A2")
        assert len(store.get_history(sid1)) == 1
        assert len(store.get_history(sid2)) == 1
        assert store.get_history(sid1)[0].question == "Q1"
        assert store.get_history(sid2)[0].question == "Q2"


# ═══════════════════════════════════════════════════════════════════════
# History trimming (MAX_HISTORY_TURNS)
# ═══════════════════════════════════════════════════════════════════════


class TestHistoryTrimming:
    """History should be capped at MAX_HISTORY_TURNS (most recent kept)."""

    def test_trims_to_max_turns(self):
        store = ConversationStore()
        sid = store.new_session_id()
        for i in range(MAX_HISTORY_TURNS + 3):
            store.add_turn(sid, f"Q{i}", f"A{i}")

        history = store.get_history(sid)
        assert len(history) == MAX_HISTORY_TURNS

    def test_keeps_most_recent_turns(self):
        store = ConversationStore()
        sid = store.new_session_id()
        total = MAX_HISTORY_TURNS + 5
        for i in range(total):
            store.add_turn(sid, f"Q{i}", f"A{i}")

        history = store.get_history(sid)
        # Should keep the last MAX_HISTORY_TURNS turns
        expected_start = total - MAX_HISTORY_TURNS
        assert history[0].question == f"Q{expected_start}"
        assert history[-1].question == f"Q{total - 1}"

    def test_exactly_at_max_no_trim(self):
        store = ConversationStore()
        sid = store.new_session_id()
        for i in range(MAX_HISTORY_TURNS):
            store.add_turn(sid, f"Q{i}", f"A{i}")

        history = store.get_history(sid)
        assert len(history) == MAX_HISTORY_TURNS
        assert history[0].question == "Q0"


# ═══════════════════════════════════════════════════════════════════════
# TTL eviction (SESSION_TTL_SECONDS)
# ═══════════════════════════════════════════════════════════════════════


class TestTTLEviction:
    """Sessions older than TTL should be evicted on next access."""

    def test_expired_session_evicted(self):
        store = ConversationStore()
        sid = store.new_session_id()
        store.add_turn(sid, "Q", "A")

        # Manually expire the turn
        with store._lock:
            turns = store._sessions[sid]
            turns[0] = turns[0].__class__(
                question="Q",
                answer="A",
                timestamp=time.time() - SESSION_TTL_SECONDS - 10,
            )

        # Next get_history should evict
        history = store.get_history(sid)
        assert history == []

    def test_non_expired_session_kept(self):
        store = ConversationStore()
        sid = store.new_session_id()
        store.add_turn(sid, "Q", "A")

        # Session is fresh — should not be evicted
        history = store.get_history(sid)
        assert len(history) == 1

    def test_mixed_expiry(self):
        """Only expired sessions evicted; fresh ones kept."""
        store = ConversationStore()
        sid_old = store.new_session_id()
        sid_new = store.new_session_id()
        store.add_turn(sid_old, "Old", "Old answer")
        store.add_turn(sid_new, "New", "New answer")

        # Expire the old session
        with store._lock:
            turns = store._sessions[sid_old]
            turns[0] = turns[0].__class__(
                question="Old",
                answer="Old answer",
                timestamp=time.time() - SESSION_TTL_SECONDS - 10,
            )

        # Access triggers eviction
        assert store.get_history(sid_old) == []
        assert len(store.get_history(sid_new)) == 1


# ═══════════════════════════════════════════════════════════════════════
# Session capacity limit (MAX_SESSIONS)
# ═══════════════════════════════════════════════════════════════════════


class TestSessionCapacity:
    """When MAX_SESSIONS is hit, oldest session should be evicted."""

    def test_evicts_oldest_at_capacity(self):
        store = ConversationStore()

        # Fill to capacity
        session_ids = []
        for i in range(MAX_SESSIONS):
            sid = f"session-{i:04d}"
            store.add_turn(sid, f"Q{i}", f"A{i}")
            session_ids.append(sid)

        # Adding one more should evict session-0000
        store.add_turn("overflow-session", "Qx", "Ax")
        assert store.get_history("session-0000") == []
        assert len(store.get_history("overflow-session")) == 1

    def test_capacity_preserves_recent(self):
        store = ConversationStore()

        for i in range(MAX_SESSIONS):
            store.add_turn(f"s{i}", f"Q{i}", f"A{i}")

        # The last session should still be accessible
        last_sid = f"s{MAX_SESSIONS - 1}"
        assert len(store.get_history(last_sid)) == 1

    def test_move_to_end_on_reuse(self):
        """Reusing a session should move it to the end (most recently used)."""
        store = ConversationStore()

        # Add 3 sessions
        store.add_turn("first", "Q1", "A1")
        store.add_turn("second", "Q2", "A2")
        store.add_turn("third", "Q3", "A3")

        # Reuse "first" — it should move to the end
        store.add_turn("first", "Q1b", "A1b")

        with store._lock:
            keys = list(store._sessions.keys())
        # "first" should now be last
        assert keys[-1] == "first"
        assert keys[0] == "second"


# ═══════════════════════════════════════════════════════════════════════
# _build_history_messages
# ═══════════════════════════════════════════════════════════════════════


class TestBuildHistoryMessages:
    """_build_history_messages must produce OpenAI message format."""

    def test_no_session_returns_empty(self):
        assert _build_history_messages(None) == []
        assert _build_history_messages("") == []

    def test_with_history_returns_alternating_messages(self):
        # Use the module-level singleton store
        from app.retrieval.generator import _conversation_store

        sid = _conversation_store.new_session_id()
        _conversation_store.add_turn(sid, "Q1", "A1")
        _conversation_store.add_turn(sid, "Q2", "A2")

        messages = _build_history_messages(sid)
        assert len(messages) == 4
        assert messages[0] == {"role": "user", "content": "Q1"}
        assert messages[1] == {"role": "assistant", "content": "A1"}
        assert messages[2] == {"role": "user", "content": "Q2"}
        assert messages[3] == {"role": "assistant", "content": "A2"}

    def test_unknown_session_returns_empty(self):
        messages = _build_history_messages("nonexistent-session-id")
        assert messages == []


# ═══════════════════════════════════════════════════════════════════════
# Thread safety
# ═══════════════════════════════════════════════════════════════════════


class TestConversationStoreThreadSafety:
    """Concurrent access should not corrupt internal state."""

    def test_concurrent_writes_to_same_session(self):
        store = ConversationStore()
        sid = store.new_session_id()
        errors = []

        def writer(thread_id: int):
            try:
                for i in range(20):
                    store.add_turn(sid, f"T{thread_id}-Q{i}", f"T{thread_id}-A{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        history = store.get_history(sid)
        # Should be trimmed to MAX_HISTORY_TURNS
        assert len(history) <= MAX_HISTORY_TURNS

    def test_concurrent_writes_to_different_sessions(self):
        store = ConversationStore()
        errors = []

        def writer(thread_id: int):
            try:
                sid = f"thread-{thread_id}"
                for i in range(10):
                    store.add_turn(sid, f"Q{i}", f"A{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        # All 10 sessions should exist
        for t in range(10):
            history = store.get_history(f"thread-{t}")
            assert len(history) > 0

    def test_concurrent_read_write(self):
        store = ConversationStore()
        sid = store.new_session_id()
        errors = []

        def writer():
            try:
                for i in range(50):
                    store.add_turn(sid, f"Q{i}", f"A{i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(50):
                    store.get_history(sid)
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


# ═══════════════════════════════════════════════════════════════════════
# _max_tokens_for_query
# ═══════════════════════════════════════════════════════════════════════


class TestMaxTokensForQuery:
    """Token budget adapts by intent keywords."""

    def test_dependency_budget(self):
        from app.retrieval.generator import _max_tokens_for_query
        assert _max_tokens_for_query("what calls SPKEZ?") == 200

    def test_impact_budget(self):
        from app.retrieval.generator import _max_tokens_for_query
        assert _max_tokens_for_query("blast radius of CHKIN") == 250

    def test_explain_budget(self):
        from app.retrieval.generator import _max_tokens_for_query
        assert _max_tokens_for_query("explain how FURNSH works") == 300

    def test_default_budget(self):
        from app.retrieval.generator import _max_tokens_for_query
        assert _max_tokens_for_query("SPKEZ") == 200

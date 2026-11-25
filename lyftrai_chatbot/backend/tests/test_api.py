from __future__ import annotations

"""
Comprehensive test suite for Lyftr AI backend.
All LLM calls are mocked; no real Gemini API is invoked.
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_response
from app.config import settings


# ===========================================================================
# 1. Health endpoint
# ===========================================================================


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "keys_configured" in body
        assert "cache_enabled" in body

    def test_health_keys_configured_count(self, client):
        resp = client.get("/health")
        # Both env keys set in conftest
        assert resp.json()["keys_configured"] == 2

    def test_health_no_keys_returns_503(self, client):
        from app import config
        orig_1, orig_2 = config.settings.GEMINI_API_KEY_1, config.settings.GEMINI_API_KEY_2
        try:
            config.settings.GEMINI_API_KEY_1 = ""
            config.settings.GEMINI_API_KEY_2 = ""
            resp = client.get("/health")
            assert resp.status_code == 503
        finally:
            config.settings.GEMINI_API_KEY_1 = orig_1
            config.settings.GEMINI_API_KEY_2 = orig_2


# ===========================================================================
# 2. POST /chat – happy path
# ===========================================================================


class TestChatHappyPath:
    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_chat_returns_200(self, mock_llm, client):
        mock_llm.return_value = make_mock_response()
        resp = client.post("/chat", json={"session_id": "s-1", "message": "Hello"})
        assert resp.status_code == 200

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_chat_response_shape(self, mock_llm, client):
        mock_llm.return_value = make_mock_response(content="Hi there!")
        resp = client.post("/chat", json={"session_id": "s-2", "message": "Hello"})
        body = resp.json()
        assert body["session_id"] == "s-2"
        assert body["reply"] == "Hi there!"
        meta = body["metadata"]
        assert meta["resolution_source"] == "LIVE_INFERENCE"
        assert meta["model"] == settings.GEMINI_MODEL
        assert isinstance(meta["latency_ms"], int)
        assert meta["input_tokens"] == 10
        assert meta["output_tokens"] == 8
        assert meta["key_id"] == "key_1"
        assert meta["fallback_attempts"] == 0

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_chat_persists_messages(self, mock_llm, client):
        mock_llm.return_value = make_mock_response()
        client.post("/chat", json={"session_id": "s-persist", "message": "Store me"})
        resp = client.get("/sessions/s-persist/messages")
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_chat_uses_conversation_history(self, mock_llm, client):
        """Second message in session should include history in LLM call."""
        mock_llm.return_value = make_mock_response()
        sid = "s-history"
        client.post("/chat", json={"session_id": sid, "message": "First"})
        client.post("/chat", json={"session_id": sid, "message": "Second"})
        # On second call, messages list should contain the first exchange
        second_call_messages = mock_llm.call_args_list[1][1]["messages"]
        roles = [m["role"] for m in second_call_messages]
        assert "user" in roles
        assert "assistant" in roles

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_chat_with_system_prompt(self, mock_llm, client):
        mock_llm.return_value = make_mock_response()
        client.post(
            "/chat",
            json={
                "session_id": "s-sys",
                "message": "Hello",
                "system_prompt": "You are a helpful bot.",
            },
        )
        call_messages = mock_llm.call_args[1]["messages"]
        assert call_messages[0]["role"] == "system"
        assert call_messages[0]["content"] == "You are a helpful bot."


# ===========================================================================
# 3. POST /chat – validation errors
# ===========================================================================


class TestChatValidation:
    def test_empty_message_rejected(self, client):
        resp = client.post("/chat", json={"session_id": "s-v", "message": "   "})
        assert resp.status_code == 422

    def test_message_too_long(self, client):
        resp = client.post(
            "/chat", json={"session_id": "s-v", "message": "x" * 4001}
        )
        assert resp.status_code == 422

    def test_session_id_too_long(self, client):
        resp = client.post(
            "/chat", json={"session_id": "a" * 129, "message": "hi"}
        )
        assert resp.status_code == 422

    def test_missing_session_id(self, client):
        resp = client.post("/chat", json={"message": "hi"})
        assert resp.status_code == 422

    def test_missing_message(self, client):
        resp = client.post("/chat", json={"session_id": "s-v"})
        assert resp.status_code == 422

    def test_bad_json_body(self, client):
        resp = client.post(
            "/chat",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422


# ===========================================================================
# 4. Cache hit detection
# ===========================================================================


class TestCacheHit:
    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_cache_hit_resolution_source(self, mock_llm, client):
        mock_llm.return_value = make_mock_response(cache_hit=True)
        resp = client.post("/chat", json={"session_id": "s-cache", "message": "Cached?"})
        body = resp.json()
        assert body["metadata"]["resolution_source"] == "LLM_CACHE"
        assert body["metadata"]["key_id"] is None
        assert body["metadata"]["fallback_attempts"] == 0


# ===========================================================================
# 5. Failure simulation
# ===========================================================================


class TestFailureSimulation:
    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_simulate_key1_fails_key2_succeeds(self, mock_llm, client):
        """key_1 simulated 429 → key_2 used, fallback_attempts=1."""
        mock_llm.return_value = make_mock_response()
        with patch.dict(os.environ, {"ENABLE_FAILURE_SIMULATION": "true"}):
            # Reload settings
            from app import config
            config.settings.ENABLE_FAILURE_SIMULATION = True
            resp = client.post(
                "/chat",
                json={"session_id": "s-sim1", "message": "sim test"},
                headers={"X-Simulate-Failure": "key_1=429"},
            )
            config.settings.ENABLE_FAILURE_SIMULATION = False
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["key_id"] == "key_2"
        assert meta["fallback_attempts"] == 1

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_simulate_all_keys_fail_returns_503(self, mock_llm, client):
        """Both keys simulated → 503."""
        mock_llm.return_value = make_mock_response()
        with patch.dict(os.environ, {"ENABLE_FAILURE_SIMULATION": "true"}):
            from app import config
            config.settings.ENABLE_FAILURE_SIMULATION = True
            resp = client.post(
                "/chat",
                json={"session_id": "s-sim2", "message": "fail all"},
                headers={"X-Simulate-Failure": "key_1=429,key_2=503"},
            )
            config.settings.ENABLE_FAILURE_SIMULATION = False
        assert resp.status_code == 503
        assert resp.json()["detail"] == "all providers unavailable"

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_simulate_wildcard_all_keys_fail(self, mock_llm, client):
        """*=429 → all keys fail → 503."""
        mock_llm.return_value = make_mock_response()
        with patch.dict(os.environ, {"ENABLE_FAILURE_SIMULATION": "true"}):
            from app import config
            config.settings.ENABLE_FAILURE_SIMULATION = True
            resp = client.post(
                "/chat",
                json={"session_id": "s-sim3", "message": "wildcard fail"},
                headers={"X-Simulate-Failure": "*=429"},
            )
            config.settings.ENABLE_FAILURE_SIMULATION = False
        assert resp.status_code == 503

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_simulation_disabled_header_ignored(self, mock_llm, client):
        """When ENABLE_FAILURE_SIMULATION=false, header is ignored and call succeeds."""
        mock_llm.return_value = make_mock_response()
        from app import config
        config.settings.ENABLE_FAILURE_SIMULATION = False
        resp = client.post(
            "/chat",
            json={"session_id": "s-sim4", "message": "disabled sim"},
            headers={"X-Simulate-Failure": "*=429"},
        )
        assert resp.status_code == 200


# ===========================================================================
# 6. Session history endpoint
# ===========================================================================


class TestSessionHistory:
    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_session_history_ok(self, mock_llm, client):
        mock_llm.return_value = make_mock_response(content="Answer!")
        sid = "s-hist-ok"
        client.post("/chat", json={"session_id": sid, "message": "Question?"})
        resp = client.get(f"/sessions/{sid}/messages")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == sid
        msgs = body["messages"]
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Question?"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "Answer!"

    def test_session_not_found_returns_404(self, client):
        resp = client.get("/sessions/nonexistent-session-xyz/messages")
        assert resp.status_code == 404

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_session_history_metadata_included(self, mock_llm, client):
        mock_llm.return_value = make_mock_response()
        sid = "s-hist-meta"
        client.post("/chat", json={"session_id": sid, "message": "Meta test"})
        resp = client.get(f"/sessions/{sid}/messages")
        msgs = resp.json()["messages"]
        assistant_msg = next(m for m in msgs if m["role"] == "assistant")
        assert assistant_msg["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert assistant_msg["metadata"]["input_tokens"] == 10
        assert assistant_msg["metadata"]["output_tokens"] == 8

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_session_history_ordered_oldest_first(self, mock_llm, client):
        mock_llm.return_value = make_mock_response()
        sid = "s-order"
        client.post("/chat", json={"session_id": sid, "message": "First"})
        client.post("/chat", json={"session_id": sid, "message": "Second"})
        resp = client.get(f"/sessions/{sid}/messages")
        user_messages = [m for m in resp.json()["messages"] if m["role"] == "user"]
        assert user_messages[0]["content"] == "First"
        assert user_messages[1]["content"] == "Second"


# ===========================================================================
# 7. Failover on real API errors
# ===========================================================================


class TestRealFailover:
    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_key1_rate_limit_uses_key2(self, mock_llm, client):
        from litellm.exceptions import RateLimitError

        def side_effect(*args, **kwargs):
            if kwargs.get("api_key") == "test-key-1":
                raise RateLimitError(
                    message="Rate limited", model="gemini/gemini-2.0-flash", llm_provider="google"
                )
            return make_mock_response()

        mock_llm.side_effect = side_effect
        resp = client.post("/chat", json={"session_id": "s-rl", "message": "rate limit test"})
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["key_id"] == "key_2"
        assert meta["fallback_attempts"] == 1

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_both_keys_rate_limited_returns_503(self, mock_llm, client):
        from litellm.exceptions import RateLimitError

        mock_llm.side_effect = RateLimitError(
            message="Rate limited", model="gemini/gemini-2.0-flash", llm_provider="google"
        )
        resp = client.post("/chat", json={"session_id": "s-both-rl", "message": "both fail"})
        assert resp.status_code == 503
        assert resp.json()["detail"] == "all providers unavailable"

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_failed_request_not_persisted(self, mock_llm, client):
        from litellm.exceptions import RateLimitError

        mock_llm.side_effect = RateLimitError(
            message="Rate limited", model="gemini/gemini-2.0-flash", llm_provider="google"
        )
        sid = "s-no-persist"
        client.post("/chat", json={"session_id": sid, "message": "should not save"})
        # Session should not exist
        resp = client.get(f"/sessions/{sid}/messages")
        assert resp.status_code == 404


# ===========================================================================
# 8. Session isolation, cache key behavior, and simulation cache bypass
# ===========================================================================


class TestIsolationAndCacheBehavior:
    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_session_isolation(self, mock_llm, client):
        """Session A's history must not leak into Session B."""
        mock_llm.return_value = make_mock_response(content="Reply A")
        client.post("/chat", json={"session_id": "sess-a", "message": "Secret from A"})

        mock_llm.return_value = make_mock_response(content="Reply B")
        client.post("/chat", json={"session_id": "sess-b", "message": "Hello from B"})

        second_call_messages = mock_llm.call_args_list[1][1]["messages"]
        contents = [m["content"] for m in second_call_messages]
        assert "Secret from A" not in contents
        assert "Reply A" not in contents
        assert contents == ["Hello from B"]

        resp_b = client.get("/sessions/sess-b/messages")
        assert resp_b.status_code == 200
        b_contents = [m["content"] for m in resp_b.json()["messages"]]
        assert "Secret from A" not in b_contents

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_cache_hit_across_sessions_and_different_message_miss(self, mock_llm, client):
        """Identical message in a new session hits cache (key_id=None, fallback=0); different message misses."""
        import litellm

        assert litellm.cache is not None

        # Simulate LiteLLM InMemoryCache behavior keyed by (model, messages) without session_id
        in_memory_store: dict[str, object] = {}

        async def caching_acompletion(*args, **kwargs):
            cache_key = json.dumps(
                {"model": kwargs.get("model"), "messages": kwargs.get("messages")},
                sort_keys=True,
            )
            if kwargs.get("caching") and cache_key in in_memory_store:
                cached = make_mock_response(content="Paris", cache_hit=True)
                return cached
            resp = make_mock_response(
                content="Paris" if "France" in str(kwargs.get("messages")) else "Berlin",
                cache_hit=False,
            )
            if kwargs.get("caching"):
                in_memory_store[cache_key] = resp
            return resp

        mock_llm.side_effect = caching_acompletion

        # 1. First session -> cache miss (LIVE_INFERENCE)
        r1 = client.post("/chat", json={"session_id": "s-c1", "message": "Capital of France?"})
        assert r1.status_code == 200
        assert r1.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert r1.json()["metadata"]["key_id"] == "key_1"

        # 2. Different session, identical message -> cache hit (LLM_CACHE)
        r2 = client.post("/chat", json={"session_id": "s-c2", "message": "Capital of France?"})
        assert r2.status_code == 200
        meta2 = r2.json()["metadata"]
        assert meta2["resolution_source"] == "LLM_CACHE"
        assert meta2["key_id"] is None
        assert meta2["fallback_attempts"] == 0

        # 3. Different message -> cache miss (LIVE_INFERENCE)
        r3 = client.post("/chat", json={"session_id": "s-c3", "message": "Capital of Germany?"})
        assert r3.status_code == 200
        assert r3.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_simulated_request_bypasses_cache_with_nonce(self, mock_llm, client):
        """Simulated requests append a unique __simulation_nonce__ message so cache is bypassed."""
        mock_llm.return_value = make_mock_response()
        from app import config

        config.settings.ENABLE_FAILURE_SIMULATION = True
        try:
            resp = client.post(
                "/chat",
                json={"session_id": "s-nonce", "message": "Bypass cache"},
                headers={"X-Simulate-Failure": "key_1=429"},
            )
        finally:
            config.settings.ENABLE_FAILURE_SIMULATION = False

        assert resp.status_code == 200
        sent_messages = mock_llm.call_args[1]["messages"]
        assert any(
            m["role"] == "system" and m["content"].startswith("__simulation_nonce__=")
            for m in sent_messages
        )

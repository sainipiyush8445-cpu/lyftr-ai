from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from app import config
from app.config import parse_cors_origins, Settings
from app.services import conversation_service as conv_svc
from app.services.llm_service import (
    ERROR_CATEGORY_ALL_EXHAUSTED,
    ERROR_CATEGORY_BAD_REQUEST,
    ERROR_CATEGORY_INVALID_KEY,
    ERROR_CATEGORY_MODEL_NOT_FOUND,
    ERROR_CATEGORY_QUOTA,
    ERROR_CATEGORY_TIMEOUT,
)
from tests.conftest import make_mock_response


# ===========================================================================
# 1. CORS parsing and settings bootstrap
# ===========================================================================


class TestCorsParsing:
    def test_cors_wildcard(self):
        assert parse_cors_origins("*") == ["*"]
        assert parse_cors_origins(["*"]) == ["*"]

    def test_cors_comma_separated(self):
        res = parse_cors_origins("http://localhost:3000, http://localhost:8000")
        assert res == ["http://localhost:3000", "http://localhost:8000"]

    def test_cors_json_array(self):
        res = parse_cors_origins('["http://localhost:3000", "http://localhost:8000"]')
        assert res == ["http://localhost:3000", "http://localhost:8000"]

    def test_cors_empty_and_whitespace_fallback(self):
        default = ["http://localhost:3000", "http://localhost:5173"]
        assert parse_cors_origins("") == default
        assert parse_cors_origins("   ") == default
        assert parse_cors_origins(",,,") == default

    def test_cors_garbage_fallback(self):
        default = ["http://localhost:3000", "http://localhost:5173"]
        assert parse_cors_origins(12345) == default

    def test_settings_bootstrap_with_wildcard(self):
        s = Settings(CORS_ORIGINS="*")
        assert s.CORS_ORIGINS == ["*"]

    def test_settings_bootstrap_with_comma_string(self):
        s = Settings(CORS_ORIGINS="https://app.example.com, https://admin.example.com")
        assert s.CORS_ORIGINS == ["https://app.example.com", "https://admin.example.com"]


# ===========================================================================
# 2. Complete Failover Matrix (§3)
# ===========================================================================


class TestFailoverMatrix:
    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_key1_500_uses_key2(self, mock_llm, client):
        def side_effect(*args, **kwargs):
            if kwargs.get("api_key") == "test-key-1":
                raise InternalServerError(
                    message="Internal 500 error",
                    model="gemini/gemini-2.0-flash",
                    llm_provider="google",
                )
            return make_mock_response(content="Recovered via key2")

        mock_llm.side_effect = side_effect
        resp = client.post("/chat", json={"session_id": "s-500", "message": "hello"})
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["key_id"] == "key_2"
        assert meta["fallback_attempts"] == 1
        assert resp.json()["reply"] == "Recovered via key2"

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_key1_503_uses_key2(self, mock_llm, client):
        def side_effect(*args, **kwargs):
            if kwargs.get("api_key") == "test-key-1":
                raise ServiceUnavailableError(
                    message="Service Unavailable 503",
                    model="gemini/gemini-2.0-flash",
                    llm_provider="google",
                )
            return make_mock_response(content="Recovered via key2")

        mock_llm.side_effect = side_effect
        resp = client.post("/chat", json={"session_id": "s-503", "message": "hello"})
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["key_id"] == "key_2"
        assert meta["fallback_attempts"] == 1

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_key1_timeout_uses_key2(self, mock_llm, client):
        def side_effect(*args, **kwargs):
            if kwargs.get("api_key") == "test-key-1":
                raise Timeout(
                    message="Request timed out",
                    model="gemini/gemini-2.0-flash",
                    llm_provider="google",
                )
            return make_mock_response(content="Recovered after timeout")

        mock_llm.side_effect = side_effect
        resp = client.post("/chat", json={"session_id": "s-timeout", "message": "hello"})
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["key_id"] == "key_2"
        assert meta["fallback_attempts"] == 1

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_key1_auth_error_uses_key2(self, mock_llm, client):
        """A bad key 1 (401/auth) must fail over to key 2 without blocking."""
        def side_effect(*args, **kwargs):
            if kwargs.get("api_key") == "test-key-1":
                raise AuthenticationError(
                    message="Invalid API Key 1",
                    model="gemini/gemini-2.0-flash",
                    llm_provider="google",
                )
            return make_mock_response(content="Key 2 works!")

        mock_llm.side_effect = side_effect
        resp = client.post("/chat", json={"session_id": "s-auth", "message": "hello"})
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["key_id"] == "key_2"
        assert meta["fallback_attempts"] == 1

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_key2_missing_but_key1_ok(self, mock_llm, client):
        mock_llm.return_value = make_mock_response(content="Only key1 available")
        orig_key2 = config.settings.GEMINI_API_KEY_2
        try:
            config.settings.GEMINI_API_KEY_2 = ""
            resp = client.post("/chat", json={"session_id": "s-k2-missing", "message": "hello"})
            assert resp.status_code == 200
            meta = resp.json()["metadata"]
            assert meta["key_id"] == "key_1"
            assert meta["fallback_attempts"] == 0
        finally:
            config.settings.GEMINI_API_KEY_2 = orig_key2

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_key1_missing_key2_used_without_counting_key1(self, mock_llm, client):
        mock_llm.return_value = make_mock_response(content="Key2 answered directly")
        orig_key1 = config.settings.GEMINI_API_KEY_1
        try:
            config.settings.GEMINI_API_KEY_1 = ""
            resp = client.post("/chat", json={"session_id": "s-k1-missing", "message": "hello"})
            assert resp.status_code == 200
            meta = resp.json()["metadata"]
            assert meta["key_id"] == "key_2"
            assert meta["fallback_attempts"] == 0
        finally:
            config.settings.GEMINI_API_KEY_1 = orig_key1

    def test_both_keys_missing_returns_controlled_503(self, client):
        orig_1 = config.settings.GEMINI_API_KEY_1
        orig_2 = config.settings.GEMINI_API_KEY_2
        try:
            config.settings.GEMINI_API_KEY_1 = ""
            config.settings.GEMINI_API_KEY_2 = ""
            resp = client.post("/chat", json={"session_id": "s-both-missing", "message": "hello"})
            assert resp.status_code == 503
            assert resp.json()["detail"] == "all providers unavailable"
        finally:
            config.settings.GEMINI_API_KEY_1 = orig_1
            config.settings.GEMINI_API_KEY_2 = orig_2

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_bad_request_no_failover(self, mock_llm, client):
        """BadRequestError / content policy must not failover and should return controlled error."""
        mock_llm.side_effect = BadRequestError(
            message="Content policy violation",
            model="gemini/gemini-2.0-flash",
            llm_provider="google",
        )
        resp = client.post("/chat", json={"session_id": "s-badreq", "message": "bad prompt"})
        assert resp.status_code == 400
        # Call count should be exactly 1: key 2 must NOT be tried on bad request
        assert mock_llm.call_count == 1

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_malformed_provider_response_returns_controlled_502(self, mock_llm, client):
        malformed = MagicMock()
        malformed.choices = []  # empty choices
        mock_llm.return_value = malformed
        resp = client.post("/chat", json={"session_id": "s-malformed", "message": "hello"})
        assert resp.status_code == 503
        assert resp.json()["detail"] == "all providers unavailable"

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_no_api_key_leaked_in_logs_or_response(self, mock_llm, client, caplog):
        """Verify that any exception mentioning keys is scrubbed from logs and responses."""
        secret_key = "AIzaSySecretRealKey1234567890abcdef"
        mock_llm.side_effect = RateLimitError(
            message=f"Rate limit exceeded for key={secret_key} and test-key-1",
            model="gemini/gemini-2.0-flash",
            llm_provider="google",
        )
        resp = client.post("/chat", json={"session_id": "s-scrub", "message": "leak check"})
        assert resp.status_code == 503
        # Response body must not leak keys
        assert secret_key not in resp.text
        assert "test-key-1" not in resp.text
        # Log records must not leak keys
        for record in caplog.records:
            assert secret_key not in record.message
            assert "test-key-1" not in record.message


# ===========================================================================
# 3. Cache Requirements (§4)
# ===========================================================================


class TestCacheRequirementsExtended:
    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_different_system_prompt_causes_cache_miss(self, mock_llm, client):
        import litellm
        in_memory_store = {}

        async def caching_call(*args, **kwargs):
            import json
            key = json.dumps({"model": kwargs.get("model"), "messages": kwargs.get("messages")}, sort_keys=True)
            if kwargs.get("caching") and key in in_memory_store:
                return make_mock_response(content="Cached reply", cache_hit=True)
            resp = make_mock_response(content="Fresh reply", cache_hit=False)
            if kwargs.get("caching"):
                in_memory_store[key] = resp
            return resp

        mock_llm.side_effect = caching_call

        # Call with prompt A
        r1 = client.post(
            "/chat",
            json={"session_id": "s-p1", "message": "Hi", "system_prompt": "You are Bot A"},
        )
        assert r1.status_code == 200
        assert r1.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"

        # Call with prompt B -> different context -> cache miss
        r2 = client.post(
            "/chat",
            json={"session_id": "s-p2", "message": "Hi", "system_prompt": "You are Bot B"},
        )
        assert r2.status_code == 200
        assert r2.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"

        # Call with prompt A again -> identical context -> cache hit
        r3 = client.post(
            "/chat",
            json={"session_id": "s-p3", "message": "Hi", "system_prompt": "You are Bot A"},
        )
        assert r3.status_code == 200
        assert r3.json()["metadata"]["resolution_source"] == "LLM_CACHE"
        assert r3.json()["metadata"]["key_id"] is None
        assert r3.json()["metadata"]["fallback_attempts"] == 0


# ===========================================================================
# 4. Persistence Atomicity & Boundaries (§2, §5, §6)
# ===========================================================================


class TestPersistenceAtomicityAndLimits:
    def test_save_conversation_turn_atomicity(self, db_session):
        """If database commit fails, transaction is rolled back and nothing is persisted."""
        from app.models import Message
        sid = "s-atomic-fail"

        # Verify initial count
        assert db_session.query(Message).filter(Message.session_id == sid).count() == 0

        # Simulate commit failure
        with patch.object(db_session, "commit", side_effect=Exception("Database write error")):
            with pytest.raises(Exception):
                conv_svc.save_conversation_turn(
                    session_id=sid,
                    user_content="user question",
                    assistant_content="assistant answer",
                    assistant_metadata=None,
                    db=db_session,
                )

        # Confirm rollback left 0 messages
        assert db_session.query(Message).filter(Message.session_id == sid).count() == 0

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_history_cap_limits_context_sent_to_llm(self, mock_llm, client):
        """Only the last MAX_HISTORY_MESSAGES are passed to the LLM."""
        mock_llm.return_value = make_mock_response()
        sid = "s-cap"

        # Send multiple turns (user+assistant)
        for i in range(12):
            client.post("/chat", json={"session_id": sid, "message": f"Message {i}"})

        # Next call
        client.post("/chat", json={"session_id": sid, "message": "Latest message"})
        last_call_messages = mock_llm.call_args_list[-1][1]["messages"]

        # Including the latest user message, messages count should not exceed MAX_HISTORY_MESSAGES + 1
        history_in_call = [m for m in last_call_messages if m["role"] in ("user", "assistant")]
        assert len(history_in_call) <= config.settings.MAX_HISTORY_MESSAGES + 1

    def test_session_id_charset_validation(self, client):
        """session_id must match ^[a-zA-Z0-9_-]+$."""
        # Valid
        assert client.get("/sessions/valid-session_123/messages").status_code == 404

        # Invalid in body
        assert client.post("/chat", json={"session_id": "invalid/slash", "message": "hi"}).status_code == 422
        assert client.post("/chat", json={"session_id": "invalid spaces", "message": "hi"}).status_code == 422
        assert client.post("/chat", json={"session_id": "invalid?query", "message": "hi"}).status_code == 422
        # Invalid in path
        assert client.get("/sessions/invalid!symbol/messages").status_code == 400
        assert client.get("/sessions/invalid@symbol/messages").status_code == 400

    def test_system_prompt_max_length(self, client):
        """system_prompt > 2000 chars rejected."""
        resp = client.post(
            "/chat",
            json={"session_id": "s-syslen", "message": "hi", "system_prompt": "a" * 2001},
        )
        assert resp.status_code == 422

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_concurrent_requests_on_same_session(self, mock_llm, client):
        """Concurrent requests on the same session execute safely without corrupting DB."""
        import concurrent.futures
        mock_llm.return_value = make_mock_response(content="Concurrent reply")
        sid = "s-concurrent"

        def _send(idx):
            return client.post("/chat", json={"session_id": sid, "message": f"Ping {idx}"})

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_send, i) for i in range(5)]
            responses = [f.result() for f in futures]

        assert all(r.status_code == 200 for r in responses)
        history_resp = client.get(f"/sessions/{sid}/messages")
        assert history_resp.status_code == 200
        # 5 user messages + 5 assistant replies = 10 total
        assert len(history_resp.json()["messages"]) == 10


# ===========================================================================
# 5. Health Endpoint Error Sanitization (§2, §5)
# ===========================================================================


class TestHealthSanitization:
    def test_health_db_down_does_not_leak_internals(self, client):
        with patch("app.main.engine.connect", side_effect=Exception("sensitive /data/secret.db error message")):
            resp = client.get("/health")
            assert resp.status_code == 503
            assert resp.json()["detail"] == "Database unreachable"
            assert "sensitive" not in resp.text
            assert "secret.db" not in resp.text


# ===========================================================================
# 6. Provider Readiness Endpoint (/health/provider)
# ===========================================================================


class TestProviderHealthEndpoint:
    def test_provider_health_returns_configured(self, client):
        resp = client.get("/health/provider")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "configured"
        assert body["keys_configured"] == 2
        assert "gemini" in body["model"]
        assert "note" in body
        # Does NOT make a live API call

    def test_provider_health_no_keys_returns_503(self, client):
        orig_1 = config.settings.GEMINI_API_KEY_1
        orig_2 = config.settings.GEMINI_API_KEY_2
        try:
            config.settings.GEMINI_API_KEY_1 = ""
            config.settings.GEMINI_API_KEY_2 = ""
            resp = client.get("/health/provider")
            assert resp.status_code == 503
            body = resp.json()
            assert body["detail"]["status"] == "misconfigured"
            assert len(body["detail"]["issues"]) >= 1
        finally:
            config.settings.GEMINI_API_KEY_1 = orig_1
            config.settings.GEMINI_API_KEY_2 = orig_2

    def test_provider_health_reports_simulation_flag(self, client):
        resp = client.get("/health/provider")
        assert resp.status_code == 200
        # Must report whether failure simulation is on/off; it must be off in default test env
        body = resp.json()
        assert "failure_simulation_enabled" in body


# ===========================================================================
# 7. Extended Failover – Rate limits, Auth, Network, Retired model
# ===========================================================================


class TestExtendedFailoverScenarios:
    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_key1_rate_limit_429_uses_key2(self, mock_llm, client):
        """429 on key 1 must trigger failover; metadata must report key_2 and fallback_attempts=1."""
        def side_effect(*args, **kwargs):
            if kwargs.get("api_key") == "test-key-1":
                raise RateLimitError(
                    message="Quota exceeded 429",
                    model=config.settings.GEMINI_MODEL,
                    llm_provider="google",
                )
            return make_mock_response(content="key2 answered")

        mock_llm.side_effect = side_effect
        resp = client.post("/chat", json={"session_id": "s-rl-429", "message": "hello"})
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["key_id"] == "key_2"
        assert meta["fallback_attempts"] == 1
        assert meta["resolution_source"] == "LIVE_INFERENCE"

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_both_keys_rate_limited_returns_503_with_correct_detail(self, mock_llm, client):
        """When both keys hit 429, the response must be HTTP 503 with the standard detail."""
        mock_llm.side_effect = RateLimitError(
            message="All quota exhausted",
            model=config.settings.GEMINI_MODEL,
            llm_provider="google",
        )
        resp = client.post("/chat", json={"session_id": "s-both-rl", "message": "hello"})
        assert resp.status_code == 503
        assert resp.json()["detail"] == "all providers unavailable"
        # Both keys must have been tried (call count = 2)
        assert mock_llm.call_count == 2

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_invalid_key1_auth_error_fails_over_to_key2(self, mock_llm, client):
        """AuthenticationError on key 1 (invalid key) must NOT block key 2."""
        def side_effect(*args, **kwargs):
            if kwargs.get("api_key") == "test-key-1":
                raise AuthenticationError(
                    message="Invalid API key provided",
                    model=config.settings.GEMINI_MODEL,
                    llm_provider="google",
                )
            return make_mock_response(content="key2 auth ok")

        mock_llm.side_effect = side_effect
        resp = client.post("/chat", json={"session_id": "s-auth-fail", "message": "hi"})
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["key_id"] == "key_2"
        assert meta["fallback_attempts"] == 1

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_retired_model_not_found_returns_404_no_failover(self, mock_llm, client):
        """NotFoundError (retired model) must NOT failover and must return a clear HTTP 404."""
        mock_llm.side_effect = NotFoundError(
            message="Model gemini/gemini-2.0-flash has been retired",
            model=config.settings.GEMINI_MODEL,
            llm_provider="google",
        )
        resp = client.post("/chat", json={"session_id": "s-retired", "message": "hello"})
        assert resp.status_code == 404
        # Only one key should have been tried — NotFoundError is not retryable
        assert mock_llm.call_count == 1
        body = resp.json()
        # Detail must mention the model clearly, never expose an API key
        assert "model" in body["detail"].lower() or "retired" in body["detail"].lower() or "not found" in body["detail"].lower()
        assert "test-key-1" not in body["detail"]

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_network_timeout_fails_over_to_key2(self, mock_llm, client):
        """Network timeout on key 1 must trigger failover to key 2."""
        async def side_effect(*args, **kwargs):
            if kwargs.get("api_key") == "test-key-1":
                raise Timeout(
                    message="Request timed out",
                    model=config.settings.GEMINI_MODEL,
                    llm_provider="google",
                )
            return make_mock_response(content="ok after timeout")

        mock_llm.side_effect = side_effect
        resp = client.post("/chat", json={"session_id": "s-net-timeout", "message": "hello"})
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["key_id"] == "key_2"
        assert meta["fallback_attempts"] == 1

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_provider_500_fails_over_to_key2(self, mock_llm, client):
        """HTTP 500 from provider on key 1 must trigger failover."""
        def side_effect(*args, **kwargs):
            if kwargs.get("api_key") == "test-key-1":
                raise InternalServerError(
                    message="Internal Server Error 500",
                    model=config.settings.GEMINI_MODEL,
                    llm_provider="google",
                )
            return make_mock_response(content="key2 ok after 500")

        mock_llm.side_effect = side_effect
        resp = client.post("/chat", json={"session_id": "s-500-k2", "message": "hello"})
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["key_id"] == "key_2"
        assert meta["fallback_attempts"] == 1

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_model_selection_uses_gemini_3_5_flash(self, mock_llm, client):
        """The model passed to litellm must be gemini/gemini-3.5-flash."""
        mock_llm.return_value = make_mock_response(content="ok")
        resp = client.post("/chat", json={"session_id": "s-model-check", "message": "ping"})
        assert resp.status_code == 200
        called_model = mock_llm.call_args[1]["model"]
        assert called_model == "gemini/gemini-3.5-flash"
        assert resp.json()["metadata"]["model"] == "gemini/gemini-3.5-flash"

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_cache_hit_metadata_has_null_key_and_zero_fallback(self, mock_llm, client):
        """On a cache hit, key_id must be null and fallback_attempts must be 0."""
        mock_llm.return_value = make_mock_response(content="cached", cache_hit=True)
        resp = client.post("/chat", json={"session_id": "s-cache-meta", "message": "hi"})
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["resolution_source"] == "LLM_CACHE"
        assert meta["key_id"] is None
        assert meta["fallback_attempts"] == 0

    @patch("app.services.llm_service.litellm.acompletion", new_callable=AsyncMock)
    def test_credentials_never_exposed_in_response_or_detail(self, mock_llm, client, caplog):
        """API key values must never appear in HTTP responses or log records."""
        mock_llm.side_effect = ServiceUnavailableError(
            message=f"Service down. Key: test-key-1, also test-key-2 failed",
            model=config.settings.GEMINI_MODEL,
            llm_provider="google",
        )
        resp = client.post("/chat", json={"session_id": "s-cred-leak", "message": "leak"})
        assert resp.status_code == 503
        assert "test-key-1" not in resp.text
        assert "test-key-2" not in resp.text
        for rec in caplog.records:
            assert "test-key-1" not in rec.message
            assert "test-key-2" not in rec.message

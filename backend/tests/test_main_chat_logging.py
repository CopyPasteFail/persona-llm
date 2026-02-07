"""Integration-style tests for chat structured logging fields."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol, TypeGuard, cast

import pytest
from httpx import ASGITransport, AsyncClient

from api import rag_chat_orchestrator
from api import main as main_app_module
from api.types import ChatResponse, Citation, Usage

BASE_URL = "http://test"
CHAT_ENDPOINT = "/chat"
KEY_LOGIN_ENDPOINT = "/auth/key-login"
TEST_ACCESS_KEY = "test-access-key-logging"
TEST_QUESTION = "Tell me about your Kubernetes work."


class AccessKeyStore(Protocol):
    """Protocol for the auth key store fixture used by login flow tests."""

    def add_plain_key(
        self,
        plain_key: str,
        *,
        label: str | None = None,
        expires_at: datetime | None = None,
        revoked: bool = False,
    ) -> str: ...


def _is_chat_success_payload(message: object) -> TypeGuard[dict[str, Any]]:
    """Check whether a log message is the structured `chat.success` payload.

    Inputs:
    - message: Arbitrary log message object emitted by the app logger.

    Outputs:
    - `True` when the message is a dict and its `event` field matches
      `EVENT_CHAT_SUCCESS`; otherwise `False`.

    Edge cases:
    - Non-dict messages are rejected.
    - Dicts missing the `event` key are rejected.
    """

    if not isinstance(message, dict):
        return False

    payload: dict[str, Any] = cast(dict[str, Any], message)
    return payload.get("event") == main_app_module.EVENT_CHAT_SUCCESS


@pytest.mark.asyncio
async def test_chat_success_log_includes_signal_shadow_fields(
    access_key_store: AccessKeyStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify `chat.success` log includes additive llm-gating shadow fields.

    Inputs:
    - access_key_store: Fixture-backed in-memory auth store for login.
    - monkeypatch: Fixture used to stub orchestrator output.
    - caplog: Log capture fixture for structured payload assertions.

    Outputs:
    - None. Asserts the emitted `chat.success` payload includes llm-gating fields.

    Edge cases:
    - Uses a shadow decision that would skip LLM while gating is disabled to ensure
      logging captures telemetry without changing response behavior.
    """

    def _stub_run_rag_chat(
        *unused_args: Any, **unused_kwargs: Any
    ) -> rag_chat_orchestrator.ChatResult:
        """Return a deterministic successful chat result with signal shadow metadata."""
        return rag_chat_orchestrator.ChatResult(
            response=ChatResponse(
                answer="TLDR: stub answer\nWrap: stub wrap",
                citations=[Citation(id="chunk-1", text="stub citation")],
                usage=Usage(input_tokens=10, output_tokens=20, thoughts_tokens=None),
                input_token_limit=1000,
            ),
            selected_chunks=[{"id": "chunk-1", "score": 0.5, "bm25_score": 1.0}],
            normalized_question=TEST_QUESTION,
            usage_detail={"total_tokens": 30, "finish_reason": "STOP"},
            thinking_budget_tokens_effective=0,
            llm_gate_enabled=False,
            would_call_llm_if_gated=False,
            llm_gate_reason=rag_chat_orchestrator.llm_gate_reason_SCORE_BELOW_THRESHOLD,
            top1_weighted_score=0.5,
            top1_bm25_score=1.0,
            top1_vector_score=0.6,
            best_weighted_score=0.5,
            best_bm25_score=1.0,
            weighted_consensus_count =0,
            weighted_score_threshold=0.62,
            bm25_score_threshold=3.0,
        )

    access_key_store.add_plain_key(TEST_ACCESS_KEY, label="logging")
    monkeypatch.setattr(main_app_module, "is_ready", True)
    monkeypatch.setattr(rag_chat_orchestrator, "run_rag_chat", _stub_run_rag_chat)
    caplog.set_level(logging.INFO)

    transport = ASGITransport(app=main_app_module.app)
    async with AsyncClient(transport=transport, base_url=BASE_URL) as http_client:
        login_response = await http_client.post(
            KEY_LOGIN_ENDPOINT,
            json={"key": TEST_ACCESS_KEY},
        )
        access_token = login_response.json()["access_token"]

        response = await http_client.post(
            CHAT_ENDPOINT,
            json={"question": TEST_QUESTION},
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == 200

    success_payloads: list[dict[str, Any]] = [
        record.msg
        for record in caplog.records
        if _is_chat_success_payload(record.msg)
    ]
    assert len(success_payloads) == 1
    success_payload: dict[str, Any] = success_payloads[0]

    assert "llm_gate_enabled" in success_payload
    assert "would_call_llm_if_gated" in success_payload
    assert "llm_gate_reason" in success_payload
    assert "top1_weighted_score" in success_payload
    assert "top1_bm25_score" in success_payload
    assert "top1_vector_score" in success_payload
    assert "best_weighted_score" in success_payload
    assert "best_bm25_score" in success_payload
    assert "weighted_consensus_count " in success_payload
    assert "signal_top1_weighted_score" not in success_payload
    assert "signal_top1_bm25_score" not in success_payload
    assert "signal_top1_vector_score" not in success_payload
    assert "weighted_score_threshold" in success_payload
    assert "bm25_score_threshold" in success_payload
    assert success_payload["would_call_llm_if_gated"] is False
    assert (
        success_payload["llm_gate_reason"]
        == rag_chat_orchestrator.llm_gate_reason_SCORE_BELOW_THRESHOLD
    )

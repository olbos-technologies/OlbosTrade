"""Tests for the /api/research/assistant route (LLM + DB mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.research import AssistantRequest, research_assistant

from conftest import single_operator_conn

# Batch E2 gave these routes a `conn` so they can resolve a trade scope; with
# auth disabled it is never read. See conftest.single_operator_conn.
_CONN = single_operator_conn()



def _session(rows=None):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalars.return_value = MagicMock(all=lambda: rows or [])
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_assistant_empty_question():
    out = await research_assistant(AssistantRequest(question="   "), _CONN)
    assert out["error"] == "empty question"


@pytest.mark.asyncio
async def test_assistant_success():
    with patch("app.core.database.AsyncSessionLocal", return_value=_session([])), \
         patch("app.services.llm_provider.generate",
               return_value={"provider": "gemini", "model": "gemini-2.5-flash",
                             "answer": "Bull put spreads lead this quarter."}):
        out = await research_assistant(AssistantRequest(question="Best strategy?"), _CONN)
    assert out["provider"] == "gemini"
    assert "Bull put" in out["answer"]


@pytest.mark.asyncio
async def test_assistant_unavailable_gives_hint():
    from app.services.llm_provider import LLMUnavailable
    with patch("app.core.database.AsyncSessionLocal", return_value=_session([])), \
         patch("app.services.llm_provider.generate",
               side_effect=LLMUnavailable("no key")):
        out = await research_assistant(AssistantRequest(question="anything?"), _CONN)
    assert "unavailable" in out["error"] and "GEMINI_API_KEY" in out["hint"]


@pytest.mark.asyncio
async def test_assistant_generic_error():
    with patch("app.core.database.AsyncSessionLocal", return_value=_session([])), \
         patch("app.services.llm_provider.generate", side_effect=Exception("boom")):
        out = await research_assistant(AssistantRequest(question="q?"), _CONN)
    assert "assistant error" in out["error"]


@pytest.mark.asyncio
async def test_assistant_context_survives_db_error():
    # context builder swallows DB errors; generate still called → success
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("db down")), \
         patch("app.services.llm_provider.generate",
               return_value={"provider": "gemini", "model": "m", "answer": "ok"}):
        out = await research_assistant(AssistantRequest(question="q?"), _CONN)
    assert out["answer"] == "ok"

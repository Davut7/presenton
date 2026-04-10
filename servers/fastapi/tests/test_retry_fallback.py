"""Test retry and fallback model logic for LLM calls."""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LLM", "google")
os.environ.setdefault("APP_DATA_DIRECTORY", "/tmp/app_data")
os.environ.setdefault("TEMP_DIRECTORY", "/tmp/presenton")
os.environ.setdefault("DISABLE_IMAGE_GENERATION", "true")

import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.anyio


async def test_outline_retry_switches_to_fallback_after_5_failures():
    """After 5 consecutive 503 errors, outline generation should switch to fallback model."""
    from constants.llm import FALLBACK_GOOGLE_MODEL, DEFAULT_GOOGLE_MODEL

    call_log = []

    async def mock_stream_structured(model, messages, schema, strict=True, tools=None):
        call_log.append(model)
        if len(call_log) <= 5:
            raise Exception("503 UNAVAILABLE. high demand")
        yield '{"slides": [{"title": "Test", "content": "test"}]}'

    with patch("utils.llm_calls.generate_presentation_outlines.LLMClient") as MockClient, \
         patch("utils.llm_calls.generate_presentation_outlines.get_model", return_value=DEFAULT_GOOGLE_MODEL):
        instance = MockClient.return_value
        instance.stream_structured = mock_stream_structured
        instance.enable_web_grounding = lambda: False

        from utils.llm_calls.generate_presentation_outlines import generate_ppt_outline

        chunks = []
        async for chunk in generate_ppt_outline(content="Test", n_slides=1, language="English"):
            from fastapi import HTTPException
            if isinstance(chunk, HTTPException):
                pytest.fail(f"Got error: {chunk.detail}")
            chunks.append(chunk)

        assert len(chunks) > 0
        assert call_log[0] == DEFAULT_GOOGLE_MODEL
        assert call_log[5] == FALLBACK_GOOGLE_MODEL


async def test_outline_retry_reverts_from_fallback_on_404():
    """If fallback model returns 404, should revert to primary and keep retrying."""
    from constants.llm import FALLBACK_GOOGLE_MODEL, DEFAULT_GOOGLE_MODEL

    call_log = []

    async def mock_stream_structured(model, messages, schema, strict=True, tools=None):
        call_log.append(model)
        if len(call_log) <= 5:
            raise Exception("503 UNAVAILABLE. high demand")
        if model == FALLBACK_GOOGLE_MODEL:
            raise Exception("404 NOT_FOUND. This model is no longer available")
        yield '{"slides": [{"title": "Test", "content": "test"}]}'

    with patch("utils.llm_calls.generate_presentation_outlines.LLMClient") as MockClient, \
         patch("utils.llm_calls.generate_presentation_outlines.get_model", return_value=DEFAULT_GOOGLE_MODEL):
        instance = MockClient.return_value
        instance.stream_structured = mock_stream_structured
        instance.enable_web_grounding = lambda: False

        from utils.llm_calls.generate_presentation_outlines import generate_ppt_outline

        chunks = []
        async for chunk in generate_ppt_outline(content="Test", n_slides=1, language="English"):
            from fastapi import HTTPException
            if isinstance(chunk, HTTPException):
                pytest.fail(f"Got error: {chunk.detail}")
            chunks.append(chunk)

        assert len(chunks) > 0
        assert FALLBACK_GOOGLE_MODEL in call_log
        assert call_log[-1] == DEFAULT_GOOGLE_MODEL


async def test_outline_retry_exhausts_all_10_attempts():
    """After 10 failures, should yield an error, not crash."""
    from constants.llm import DEFAULT_GOOGLE_MODEL
    call_count = 0

    async def mock_stream_structured(model, messages, schema, strict=True, tools=None):
        nonlocal call_count
        call_count += 1
        raise Exception("503 UNAVAILABLE. high demand")
        # Make it an async generator
        yield  # never reached

    with patch("utils.llm_calls.generate_presentation_outlines.LLMClient") as MockClient, \
         patch("utils.llm_calls.generate_presentation_outlines.get_model", return_value=DEFAULT_GOOGLE_MODEL):
        instance = MockClient.return_value
        instance.stream_structured = mock_stream_structured
        instance.enable_web_grounding = lambda: False

        from utils.llm_calls.generate_presentation_outlines import generate_ppt_outline

        got_error = False
        async for chunk in generate_ppt_outline(content="Test", n_slides=1, language="English"):
            from fastapi import HTTPException
            if isinstance(chunk, HTTPException):
                got_error = True

        assert got_error, "Should have yielded an error after exhausting retries"
        assert call_count == 10


async def test_structure_retry_and_fallback():
    """Structure generation should retry and fallback on 503."""
    from constants.llm import FALLBACK_GOOGLE_MODEL, DEFAULT_GOOGLE_MODEL

    call_log = []

    async def mock_generate_structured(model, messages, response_format, strict=True):
        call_log.append(model)
        if len(call_log) <= 5:
            raise Exception("503 UNAVAILABLE. high demand")
        return {"slides": [0]}

    with patch("utils.llm_calls.generate_presentation_structure.LLMClient") as MockClient, \
         patch("utils.llm_calls.generate_presentation_structure.get_model", return_value=DEFAULT_GOOGLE_MODEL):
        instance = MockClient.return_value
        instance.generate_structured = mock_generate_structured

        mock_outline = MagicMock()
        mock_outline.slides = [MagicMock()]
        mock_outline.to_string = lambda: "test"

        mock_layout = MagicMock()
        mock_layout.slides = [MagicMock()]

        from utils.llm_calls.generate_presentation_structure import generate_presentation_structure
        result = await generate_presentation_structure(mock_outline, mock_layout)

        assert result is not None
        assert call_log[0] == DEFAULT_GOOGLE_MODEL
        assert call_log[5] == FALLBACK_GOOGLE_MODEL


async def test_slide_content_retry_and_fallback():
    """Slide content generation should retry and fallback on 503."""
    from constants.llm import FALLBACK_GOOGLE_MODEL, DEFAULT_GOOGLE_MODEL

    call_log = []

    async def mock_generate_structured(model, messages, response_format, strict=False):
        call_log.append(model)
        if len(call_log) <= 5:
            raise Exception("503 UNAVAILABLE. high demand")
        return {"title": "Test", "__speaker_note__": "note"}

    with patch("utils.llm_calls.generate_slide_content.LLMClient") as MockClient, \
         patch("utils.llm_calls.generate_slide_content.get_model", return_value=DEFAULT_GOOGLE_MODEL):
        instance = MockClient.return_value
        instance.generate_structured = mock_generate_structured

        mock_layout = MagicMock()
        mock_layout.json_schema = {"title": {"type": "string"}}

        mock_outline = MagicMock()
        mock_outline.content = "test content"

        from utils.llm_calls.generate_slide_content import get_slide_content_from_type_and_outline
        result = await get_slide_content_from_type_and_outline(mock_layout, mock_outline, "English")

        assert result is not None
        assert call_log[0] == DEFAULT_GOOGLE_MODEL
        assert call_log[5] == FALLBACK_GOOGLE_MODEL

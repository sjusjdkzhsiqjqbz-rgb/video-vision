#!/usr/bin/env python3
"""Tests for the StepFun Video Analysis Hermes tool."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


# ============================================================================
# Helpers
# ============================================================================


def _make_small_mp4(path: Path, size_mb: float = 1) -> Path:
    """Create a tiny valid MP4 file for testing. Uses dd + ffmpeg if available."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write a small MP4 header — this isn't a valid playback file but passes
    # extension check and ffprobe can read minimal metadata from it.
    mp4_header = (
        b"\x00\x00\x00\x1c\x66\x74\x79\x70\x6d\x70\x34\x32"
        b"\x00\x00\x00\x00\x6d\x70\x34\x32\x69\x73\x6f\x6d"
    )
    # Pad to ~size_mb
    target = int(size_mb * 1024 * 1024)
    with open(path, "wb") as f:
        f.write(mp4_header)
        remaining = target - len(mp4_header)
        f.write(b"\x00" * remaining)
    return path


# ============================================================================
# Schema tests
# ============================================================================


class TestSchema:
    def test_schema_has_required_fields(self):
        from stepfun_video import STEPFUN_VIDEO_SCHEMA

        assert STEPFUN_VIDEO_SCHEMA["name"] == "stepfun_video_analyze"
        assert "video_path" in STEPFUN_VIDEO_SCHEMA["parameters"]["properties"]
        assert "prompt" in STEPFUN_VIDEO_SCHEMA["parameters"]["properties"]
        assert "max_duration_seconds" in STEPFUN_VIDEO_SCHEMA["parameters"]["properties"]
        assert "max_base64_mb" in STEPFUN_VIDEO_SCHEMA["parameters"]["properties"]
        assert "model" in STEPFUN_VIDEO_SCHEMA["parameters"]["properties"]
        assert "reasoning_effort" in STEPFUN_VIDEO_SCHEMA["parameters"]["properties"]
        assert "video_path" in STEPFUN_VIDEO_SCHEMA["parameters"]["required"]

    def test_schema_reasoning_effort_enum(self):
        from stepfun_video import STEPFUN_VIDEO_SCHEMA

        re_prop = STEPFUN_VIDEO_SCHEMA["parameters"]["properties"]["reasoning_effort"]
        assert set(re_prop["enum"]) == {"low", "medium", "high"}
        assert re_prop["default"] == "medium"


# ============================================================================
# check_fn tests
# ============================================================================


class TestCheckRequirements:
    def test_returns_false_without_key(self, monkeypatch):
        monkeypatch.delenv("STEPFUN_API_KEY", raising=False)
        from stepfun_video import check_stepfun_requirements

        assert check_stepfun_requirements() is False

    def test_returns_true_with_key(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_API_KEY", "sk-test-123")
        from stepfun_video import check_stepfun_requirements

        assert check_stepfun_requirements() is True

    def test_returns_false_with_empty_key(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_API_KEY", "")
        from stepfun_video import check_stepfun_requirements

        assert check_stepfun_requirements() is False


# ============================================================================
# Validation tests
# ============================================================================


class TestValidation:
    def test_missing_video_path(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_API_KEY", "sk-test")
        from stepfun_video import stepfun_video_analyze_tool

        async def run():
            result = await stepfun_video_analyze_tool(video_path="")
            return result

        import asyncio

        result = asyncio.run(run())
        assert '"error"' in result
        assert "required" in result.lower()

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("STEPFUN_API_KEY", raising=False)
        from stepfun_video import stepfun_video_analyze_tool

        async def run():
            result = await stepfun_video_analyze_tool(video_path="/tmp/test.mp4")
            return result

        import asyncio

        result = asyncio.run(run())
        assert '"error"' in result
        assert "STEPFUN_API_KEY" in result

    def test_unsupported_extension(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_API_KEY", "sk-test")
        from stepfun_video import stepfun_video_analyze_tool

        async def run():
            with tempfile.NamedTemporaryFile(suffix=".wmv", delete=False) as f:
                f.write(b"fake wmv data")
                temp_path = f.name

            try:
                result = await stepfun_video_analyze_tool(video_path=temp_path)
                return result
            finally:
                Path(temp_path).unlink(missing_ok=True)

        import asyncio

        result = asyncio.run(run())
        assert '"error"' in result
        assert "Unsupported" in result

    def test_file_not_found(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_API_KEY", "sk-test")
        from stepfun_video import stepfun_video_analyze_tool

        async def run():
            result = await stepfun_video_analyze_tool(
                video_path="/nonexistent/path/video.mp4",
            )
            return result

        import asyncio

        result = asyncio.run(run())
        assert '"error"' in result
        assert "not found" in result.lower()

    def test_invalid_url_scheme(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_API_KEY", "sk-test")
        from stepfun_video import stepfun_video_analyze_tool

        async def run():
            result = await stepfun_video_analyze_tool(
                video_path="ftp://evil.com/video.mp4",
            )
            return result

        import asyncio

        result = asyncio.run(run())
        assert '"error"' in result
        assert "unsafe" in result.lower() or "invalid" in result.lower()


# ============================================================================
# API call tests (mocked)
# ============================================================================


class TestApiCall:
    def test_successful_analysis(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_API_KEY", "sk-test-123")
        monkeypatch.setenv("STEPFUN_BASE_URL", "https://api.stepfun.ai/v1")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {"message": {"content": "This video shows a cat playing."}}
            ],
            "model": "step-3.7-flash",
            "usage": {"total_tokens": 42},
        }

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
            import asyncio

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                f.write(b"\x00" * 100000)
                temp_path = f.name

            try:
                from stepfun_video import stepfun_video_analyze_tool

                result = asyncio.run(
                    stepfun_video_analyze_tool(
                        video_path=temp_path,
                        prompt="What is in this video?",
                    )
                )
                data = __import__("json").loads(result)
                assert data.get("success") is True
                assert "cat" in data.get("analysis", "")
            finally:
                Path(temp_path).unlink(missing_ok=True)

    def test_http_401(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_API_KEY", "sk-test-123")
        monkeypatch.setenv("STEPFUN_BASE_URL", "https://api.stepfun.ai/v1")

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
            import asyncio

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                f.write(b"\x00" * 100000)
                temp_path = f.name

            try:
                from stepfun_video import stepfun_video_analyze_tool

                result = asyncio.run(
                    stepfun_video_analyze_tool(video_path=temp_path, prompt="test")
                )
                data = __import__("json").loads(result)
                assert data.get("success") is False
                assert "authentication" in data.get("error", "").lower()
            finally:
                Path(temp_path).unlink(missing_ok=True)

    def test_http_413_size_error(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_API_KEY", "sk-test-123")
        monkeypatch.setenv("STEPFUN_BASE_URL", "https://api.stepfun.ai/v1")

        mock_response = MagicMock()
        mock_response.status_code = 413
        mock_response.text = "Payload too large"

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
            import asyncio

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                f.write(b"\x00" * 100000)
                temp_path = f.name

            try:
                from stepfun_video import stepfun_video_analyze_tool

                result = asyncio.run(
                    stepfun_video_analyze_tool(video_path=temp_path, prompt="test")
                )
                data = __import__("json").loads(result)
                assert data.get("success") is False
                assert "large" in data.get("error", "").lower()
            finally:
                Path(temp_path).unlink(missing_ok=True)

    def test_network_timeout(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_API_KEY", "sk-test-123")
        monkeypatch.setenv("STEPFUN_BASE_URL", "https://api.stepfun.ai/v1")
        monkeypatch.setenv("STEPFUN_VIDEO_TIMEOUT", "1")

        import httpx

        async def raise_timeout(*args, **kwargs):
            raise httpx.TimeoutException("timeout")

        with patch("httpx.AsyncClient.post", new=raise_timeout):
            import asyncio

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                f.write(b"\x00" * 100000)
                temp_path = f.name

            try:
                from stepfun_video import stepfun_video_analyze_tool

                result = asyncio.run(
                    stepfun_video_analyze_tool(video_path=temp_path, prompt="test")
                )
                data = __import__("json").loads(result)
                assert data.get("success") is False
                assert "timed out" in data.get("error", "").lower()
            finally:
                Path(temp_path).unlink(missing_ok=True)

    def test_stepfile_reference_bypasses_normalization(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_API_KEY", "sk-test-123")
        monkeypatch.setenv("STEPFUN_BASE_URL", "https://api.stepfun.ai/v1")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {"message": {"content": "Analysis from stepfile."}}
            ],
        }

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
            import asyncio
            from stepfun_video import stepfun_video_analyze_tool

            result = asyncio.run(
                stepfun_video_analyze_tool(
                    video_path="stepfile://file-abc123",
                    prompt="Analyze this.",
                )
            )
            data = __import__("json").loads(result)
            assert data.get("success") is True
            assert "stepfile" in data.get("analysis", "")


# ============================================================================
# Env resolution tests
# ============================================================================


class TestEnvResolution:
    def test_default_model(self, monkeypatch):
        monkeypatch.delenv("STEPFUN_VIDEO_MODEL", raising=False)
        from stepfun_video import _resolve_model

        assert _resolve_model("") == "step-3.7-flash"

    def test_env_model_override(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_VIDEO_MODEL", "step-3.7-flash-experimental")
        from stepfun_video import _resolve_model

        assert _resolve_model("") == "step-3.7-flash-experimental"

    def test_args_model_override(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_VIDEO_MODEL", "step-3.7-flash")
        from stepfun_video import _resolve_model

        assert _resolve_model("custom-model-v2") == "custom-model-v2"

    def test_default_base_url(self, monkeypatch):
        monkeypatch.delenv("STEPFUN_BASE_URL", raising=False)
        from stepfun_video import _resolve_base_url

        assert _resolve_base_url() == "https://api.stepfun.ai/v1"

    def test_custom_base_url(self, monkeypatch):
        monkeypatch.setenv("STEPFUN_BASE_URL", "https://api.stepfun.ai/step_plan/v1")
        from stepfun_video import _resolve_base_url

        assert _resolve_base_url() == "https://api.stepfun.ai/step_plan/v1"


# ============================================================================
# Registry tests
# ============================================================================


class TestRegistry:
    def test_tool_is_registered(self):
        import stepfun_video  # noqa: F401 — triggers registry.register()
        from tools.registry import registry as reg

        entry = reg.get_entry("stepfun_video_analyze")
        assert entry is not None
        assert entry.toolset == "stepfun_video"
        assert entry.emoji == "🎬"
        assert entry.is_async is True
        assert "STEPFUN_API_KEY" in entry.requires_env

    def test_handler_produces_awaitable(self):
        from stepfun_video import _handle_stepfun_video_analyze

        result = _handle_stepfun_video_analyze({
            "video_path": "/tmp/test.mp4",
            "prompt": "test",
        })
        # handler should return an awaitable
        assert hasattr(result, "__await__")

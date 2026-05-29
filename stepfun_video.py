#!/usr/bin/env python3
"""
StepFun Video Analysis Tool for Hermes Agent.

Directly calls the StepFun /chat/completions API with video_url content blocks
using the step-3.7-flash model. Bypasses Hermes' auxiliary vision router because
StepFun does not advertise video capabilities in its models endpoint.

Features:
- Local file paths and HTTP/HTTPS URLs
- Base64, direct URL, and stepfile:// references
- Automatic ffmpeg normalization (scale=1280, CRF 28, trim to duration)
- reasoning_effort control (low / medium / high)
- Files API integration for stepfile:// reuse
- Graceful error handling with actionable messages

Env vars:
    STEPFUN_API_KEY        (required)
    STEPFUN_BASE_URL       (default: https://api.stepfun.ai/step_plan/v1)
    STEPFUN_VIDEO_MODEL    (default: step-3.7-flash)
    STEPFUN_VIDEO_TIMEOUT  (default: 180)
    STEPFUN_VIDEO_TEMP     (default: 0.1)
    STEPFUN_VIDEO_MAX_TOKENS (default: 4000)
    VISION_TOOLS_DEBUG     (default: false)
"""

import base64
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from tools.debug_helpers import DebugSession
from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

_debug = DebugSession("stepfun_video", env_var="VISION_TOOLS_DEBUG")

# ============================================================================
# Constants
# ============================================================================

DEFAULT_BASE_URL = "https://api.stepfun.ai/step_plan/v1"
DEFAULT_MODEL = "step-3.7-flash"
DEFAULT_TIMEOUT = 180
DEFAULT_TEMPERATURE = 0.1
DEFAULT_MAX_TOKENS = 4000
DEFAULT_MAX_DURATION = 90
DEFAULT_MAX_BASE64_MB = 12.0
DEFAULT_REASONING_EFFORT = "medium"

# "ffmpeg -i sample.mp4" → Duration: 00:01:23.45
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+)(?:\.(\d+))?")

_SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".mpeg", ".mpg"}

_VIDEO_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
}


# ============================================================================
# Helpers
# ============================================================================


def _env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_float(key: str, default: float) -> float:
    val = os.getenv(key, "").strip()
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    val = os.getenv(key, "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _resolve_base_url() -> str:
    url = _env_str("STEPFUN_BASE_URL", DEFAULT_BASE_URL)
    return url.rstrip("/")


def _resolve_model(args_model: str) -> str:
    if args_model and args_model.strip():
        return args_model.strip()
    return _env_str("STEPFUN_VIDEO_MODEL", DEFAULT_MODEL)


def _get_duration_seconds(file_path: Path) -> Optional[float]:
    """Return video duration in seconds using ffprobe, or None on failure."""
    if not shutil.which("ffprobe"):
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def _detect_video_mime(video_path: Path) -> Optional[str]:
    ext = video_path.suffix.lower()
    return _VIDEO_MIME_TYPES.get(ext)


def _is_url(path: str) -> bool:
    return path.startswith(("http://", "https://"))


def _validate_url(url: str) -> bool:
    from urllib.parse import urlparse

    if not url.startswith(("http://", "https://")):
        return False
    parsed = urlparse(url)
    if not parsed.netloc:
        return False
    try:
        from tools.url_safety import is_safe_url
        return is_safe_url(url)
    except ImportError:
        return True


def _video_to_base64(video_path: Path, mime: str) -> str:
    data = video_path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _normalize_video(
    src: Path,
    dst: Path,
    max_duration: int,
    max_base64_mb: float,
) -> None:
    """Re-encode video with ffmpeg: scale=1280, CRF 28, AAC 96k, trim to max_duration."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required but not found in PATH")

    target_bytes = int(max_base64_mb * 1024 * 1024 * 0.75)

    cmd = [
        "ffmpeg", "-y",
        "-ss", "0",
        "-t", str(max_duration),
        "-i", str(src),
        "-vf", "scale=1280:-2",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "28",
        "-c:a", "aac",
        "-b:a", "96k",
        "-movflags", "+faststart",
        str(dst),
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)
        actual_size = dst.stat().st_size
        if actual_size > target_bytes:
            logger.warning(
                "Re-encoded video is %.1f MB (target %.1f MB). "
                "Consider shorter duration or lower CRF.",
                actual_size / (1024 * 1024),
                target_bytes / (1024 * 1024),
            )
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg re-encode timed out (120s)")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg re-encode failed: {e.stderr[:500]}")


async def _download_video(url: str, dst: Path, timeout: float = 120.0) -> Path:
    """Download video from URL with SSRF guards and redirect validation."""
    dst.parent.mkdir(parents=True, exist_ok=True)

    async def _redirect_guard(response):
        if response.is_redirect and response.next_request:
            redirect_url = str(response.next_request.url)
            try:
                from tools.url_safety import is_safe_url
                if not is_safe_url(redirect_url):
                    raise ValueError(f"Blocked redirect to private/internal address: {redirect_url}")
            except ImportError:
                pass

    try:
        from tools.website_policy import check_website_access
        blocked = check_website_access(url)
        if blocked:
            raise PermissionError(blocked["message"])
    except ImportError:
        pass

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        event_hooks={"response": [_redirect_guard]},
    ) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": "HermesAgent/1.0",
                "Accept": "video/*,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        dst.write_bytes(response.content)

    return dst


# ============================================================================
# Files API (stepfile://)
# ============================================================================


async def _upload_to_stepfun_files(
    video_path: Path,
    base_url: str,
    api_key: str,
) -> Optional[str]:
    """Upload video to StepFun Files API with purpose=storage. Returns file_id or None."""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            with open(video_path, "rb") as f:
                response = await client.post(
                    f"{base_url}/files",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (video_path.name, f, "application/octet-stream")},
                    data={"purpose": "storage"},
                )
            if response.status_code == 200:
                data = response.json()
                file_id = data.get("id")
                if file_id:
                    logger.info("Uploaded to StepFun Files API: %s", file_id)
                    return file_id
            logger.warning("Files API upload returned %s: %s", response.status_code, response.text[:500])
    except Exception as exc:
        logger.warning("Files API upload failed: %s", exc)
    return None


# ============================================================================
# Core tool
# ============================================================================


async def stepfun_video_analyze_tool(
    video_path: str,
    prompt: str = "",
    max_duration_seconds: int = DEFAULT_MAX_DURATION,
    max_base64_mb: float = DEFAULT_MAX_BASE64_MB,
    model: str = "",
    reasoning_effort: str = "",
) -> str:
    """Analyze a video via StepFun step-3.7-flash. Returns JSON.

    Supports: local files, HTTP(S) URLs, stepfile:// references.
    Videos are auto-normalized with ffmpeg (scale=1280, CRF 28).
    """
    prompt = (prompt or "").strip()
    if not prompt:
        prompt = "Describe this video in detail."

    model = _resolve_model(model)
    base_url = _resolve_base_url()
    api_key = _env_str("STEPFUN_API_KEY")
    timeout = _env_int("STEPFUN_VIDEO_TIMEOUT", DEFAULT_TIMEOUT)
    temperature = _env_float("STEPFUN_VIDEO_TEMP", DEFAULT_TEMPERATURE)
    max_tokens = _env_int("STEPFUN_VIDEO_MAX_TOKENS", DEFAULT_MAX_TOKENS)
    reason_effort = reasoning_effort.strip() or _env_str("STEPFUN_REASONING_EFFORT", DEFAULT_REASONING_EFFORT)

    if not api_key:
        return tool_error(
            "STEPFUN_API_KEY is not set. Add it to ~/.hermes/.env or export it.",
            success=False,
        )

    if not video_path or not video_path.strip():
        return tool_error("video_path is required", success=False)

    debug_data = {
        "video_path": video_path,
        "prompt": prompt[:200],
        "model": model,
    }

    src_file: Optional[Path] = None
    tmp_file: Optional[Path] = None
    normalized_path: Optional[Path] = None
    delete_src = False
    delete_tmp = False

    try:
        try:
            from tools.interrupt import is_interrupted
            if is_interrupted():
                return tool_error("Interrupted", success=False)
        except ImportError:
            pass

        # --- Step 1: resolve video source ---
        if video_path.startswith("stepfile://"):
            # Already a StepFun file reference — use directly, skip download
            video_url_ref = video_path
            src_file = None
            source_type = "stepfile"
            logger.info("Using stepfile:// reference: %s", video_path)
        elif _is_url(video_path):
            if not _validate_url(video_path):
                return tool_error(
                    f"Invalid or unsafe URL: {video_path}",
                    success=False,
                )
            # download
            tmp_dir = Path(tempfile.gettempdir()) / "hermes_stepfun_video"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            src_file = tmp_dir / f"download_{uuid.uuid4().hex[:8]}.mp4"
            await _download_video(video_path, src_file, timeout=timeout)
            delete_src = True
            source_type = "url"
            logger.info("Downloaded video: %.1f MB", src_file.stat().st_size / (1024 * 1024))
        else:
            # local path
            resolved = Path(os.path.expanduser(video_path)).resolve()
            if not resolved.is_file():
                return tool_error(
                    f"Video file not found: {video_path}",
                    success=False,
                )
            src_file = resolved
            source_type = "local"
            logger.info("Using local file: %s", src_file)

        # --- Step 2: validate and optionally normalize ---
        normalized_path: Optional[Path] = None

        if src_file is not None:
            ext = src_file.suffix.lower()
            if ext not in _SUPPORTED_VIDEO_EXTENSIONS:
                return tool_error(
                    f"Unsupported video format '{ext}'. "
                    f"Supported: {', '.join(sorted(_SUPPORTED_VIDEO_EXTENSIONS))}",
                    success=False,
                )

            # Check if normalization is needed
            orig_size = src_file.stat().st_size
            estimated_b64 = (orig_size * 4) // 3 + 100
            base64_limit = int(max_base64_mb * 1024 * 1024)
            duration = _get_duration_seconds(src_file)
            needs_normalize = estimated_b64 > base64_limit or (
                duration is not None and duration > max_duration_seconds
            ) or ext in {".mkv", ".mov", ".avi", ".webm"}

            if needs_normalize:
                tmp_dir = Path(tempfile.gettempdir()) / "hermes_stepfun_video"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                normalized_path = tmp_dir / f"normalized_{uuid.uuid4().hex[:8]}.mp4"
                logger.info(
                    "Normalizing video: %.1f MB, %s, duration=%s",
                    orig_size / (1024 * 1024),
                    ext,
                    f"{duration:.1f}s" if duration else "unknown",
                )
                _normalize_video(src_file, normalized_path, max_duration_seconds, max_base64_mb)
                delete_tmp = True
                actual_file = normalized_path
            else:
                actual_file = src_file

            orig_size = actual_file.stat().st_size
            orig_size_mb = orig_size / (1024 * 1024)
            logger.info("Video ready: %.1f MB", orig_size_mb)

            mime = _detect_video_mime(actual_file)
            if not mime:
                return tool_error(
                    f"Cannot detect MIME type for video: {actual_file.name}",
                    success=False,
                )

            # --- Step 3: build video reference ---
            # Strategy: try Files API for reuse, fall back to base64 or direct URL
            video_url_ref = None

            # Option A: For reused videos, upload via Files API → stepfile://
            # (skipped for one-off local files since upload takes time)
            if source_type == "local" and orig_size_mb > 5:
                try:
                    file_id = await _upload_to_stepfun_files(actual_file, base_url, api_key)
                    if file_id:
                        video_url_ref = f"stepfile://{file_id}"
                except Exception:
                    pass

            if not video_url_ref:
                # Option B: If it's a direct URL and small enough, pass through
                if source_type == "url" and not needs_normalize and orig_size_mb <= max_base64_mb:
                    video_url_ref = video_path
                else:
                    # Option C: Base64 encode
                    b64_url = _video_to_base64(actual_file, mime)
                    b64_mb = len(b64_url) / (1024 * 1024)
                    logger.info("Base64 payload: %.1f MB", b64_mb)

                    if b64_mb > max_base64_mb:
                        return tool_error(
                            f"Video base64 payload ({b64_mb:.1f} MB) exceeds limit "
                            f"({max_base64_mb:.0f} MB). Reduce max_duration_seconds, "
                            f"lower max_base64_mb, or use a shorter video.",
                            success=False,
                        )
                    video_url_ref = b64_url
        else:
            # stepfile:// — no local file to check
            orig_size = 0
            orig_size_mb = 0

        debug_data["video_ref_type"] = "stepfile" if video_url_ref.startswith("stepfile://") else (
            "url" if video_url_ref.startswith("http") else "base64"
        )
        debug_data["video_size_mb"] = round(orig_size_mb, 2)

        # --- Step 4: build API payload ---
        content_parts = [
            {
                "type": "video_url",
                "video_url": {"url": video_url_ref},
            },
            {
                "type": "text",
                "text": prompt,
            },
        ]

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": content_parts,
                }
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if reason_effort in ("low", "medium", "high"):
            payload["reasoning_effort"] = reason_effort

        # --- Step 5: call API ---
        url = f"{base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        logger.info("Calling StepFun API: model=%s", model)
        if _debug.active:
            _debug.log("request", {"url": url, "model": model, "payload_size": len(json.dumps(payload))})

        async with httpx.AsyncClient(timeout=float(timeout)) as client:
            response = await client.post(url, headers=headers, json=payload)

        if response.status_code == 200:
            data = response.json()
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                logger.info("StepFun response: %d chars", len(content))

                result = {
                    "success": True,
                    "analysis": content,
                    "model_used": data.get("model", model),
                    "usage": data.get("usage", {}),
                }
                if orig_size_mb:
                    result["file_size_mb"] = round(orig_size_mb, 2)
                if video_url_ref.startswith("data:"):
                    result["base64_size_mb"] = round(len(video_url_ref) / (1024 * 1024), 2)

                debug_data["success"] = True
                debug_data["response_chars"] = len(content)
                _debug.log_call("stepfun_video_analyze", debug_data)
                _debug.save()
                return tool_result(result)

            return tool_error("API returned no choices", success=False)

        # --- Step 6: error handling ---
        error_text = response.text[:1000]
        logger.error("StepFun API error %s: %s", response.status_code, error_text)

        if response.status_code == 401 or response.status_code == 403:
            return tool_error(
                "StepFun API authentication failed. Check your STEPFUN_API_KEY.",
                success=False,
                http_status=response.status_code,
            )
        if response.status_code == 413 or (
            response.status_code == 400 and "size" in error_text.lower()
        ):
            return tool_error(
                "Video too large for StepFun API. Reduce max_duration_seconds "
                "or max_base64_mb and retry.",
                success=False,
                http_status=response.status_code,
            )
        if response.status_code == 429:
            return tool_error(
                "StepFun API rate limit exceeded. Wait and retry.",
                success=False,
                http_status=response.status_code,
            )

        return tool_error(
            f"StepFun API error ({response.status_code}): {error_text[:300]}",
            success=False,
            http_status=response.status_code,
        )

    except httpx.TimeoutException:
        return tool_error(
            f"StepFun API request timed out ({timeout}s). "
            "Increase STEPFUN_VIDEO_TIMEOUT or use a shorter/smaller video.",
            success=False,
        )
    except httpx.NetworkError as e:
        return tool_error(
            f"Network error contacting StepFun API: {e}",
            success=False,
        )
    except Exception as exc:
        logger.exception("Unexpected error in stepfun_video_analyze_tool")
        return tool_error(f"Video analysis failed: {exc}", success=False)

    finally:
        for fp, should_del in [(src_file, delete_src), (normalized_path, delete_tmp)]:
            if fp and should_del and fp.exists():
                try:
                    fp.unlink()
                except Exception:
                    pass


# ============================================================================
# check_fn
# ============================================================================


def check_stepfun_requirements() -> bool:
    """Return True when STEPFUN_API_KEY is configured."""
    return bool(_env_str("STEPFUN_API_KEY"))


# ============================================================================
# Schema and registration
# ============================================================================


STEPFUN_VIDEO_SCHEMA = {
    "name": "stepfun_video_analyze",
    "description": (
        "Analyze a video using StepFun's step-3.7-flash model via the "
        "StepFun Chat Completions API. Accepts local file paths, HTTP(S) URLs, "
        "or stepfile:// references. Videos are auto-normalized with ffmpeg "
        "(scale to 1280px width, CRF 28, AAC 96k audio). Requires "
        "STEPFUN_API_KEY environment variable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "video_path": {
                "type": "string",
                "description": (
                    "Video source: local file path (e.g. /home/user/video.mp4), "
                    "HTTP/HTTPS URL, or stepfile://<file_id> reference. "
                    "Supported formats: mp4, webm, mov, avi, mkv, mpeg."
                ),
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Question or instruction for video analysis. "
                    "Be specific for best results. Default: 'Describe this video in detail.'"
                ),
                "default": "Describe this video in detail.",
            },
            "max_duration_seconds": {
                "type": "integer",
                "description": (
                    "Trim video to this many seconds if longer. "
                    "Default: 90. Lower for faster processing."
                ),
                "default": 90,
            },
            "max_base64_mb": {
                "type": "number",
                "description": (
                    "Hard size cap for base64-encoded payload in megabytes. "
                    "Default: 12.0. StepFun's proven safe limit is ~12 MB."
                ),
                "default": 12.0,
            },
            "model": {
                "type": "string",
                "description": (
                    "Override the model name. Defaults to STEPFUN_VIDEO_MODEL env var "
                    "or 'step-3.7-flash'."
                ),
                "default": "",
            },
            "reasoning_effort": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": (
                    "Step-3.7-flash reasoning effort level. "
                    "'low' for simple Q&A/summarization, "
                    "'medium' for general reasoning (default), "
                    "'high' for complex analysis."
                ),
                "default": "medium",
            },
        },
        "required": ["video_path"],
    },
}


def _handle_stepfun_video_analyze(args: Dict[str, Any], **kw: Any):
    return stepfun_video_analyze_tool(
        video_path=args.get("video_path", ""),
        prompt=args.get("prompt", ""),
        max_duration_seconds=args.get("max_duration_seconds", DEFAULT_MAX_DURATION),
        max_base64_mb=args.get("max_base64_mb", DEFAULT_MAX_BASE64_MB),
        model=args.get("model", ""),
        reasoning_effort=args.get("reasoning_effort", ""),
    )


registry.register(
    name="stepfun_video_analyze",
    toolset="stepfun_video",
    schema=STEPFUN_VIDEO_SCHEMA,
    handler=_handle_stepfun_video_analyze,
    check_fn=check_stepfun_requirements,
    is_async=True,
    emoji="🎬",
    requires_env=["STEPFUN_API_KEY"],
    description=(
        "Dedicated StepFun video analysis via step-3.7-flash. "
        "Bypasses Hermes capability detection — always available "
        "when STEPFUN_API_KEY is set."
    ),
)


# Backward-compatibility aliases for existing tests and code
stepfun_video_tool = stepfun_video_analyze_tool
_handle_stepfun_video = _handle_stepfun_video_analyze

# Old function name aliases
_detect_video_mime_type = _detect_video_mime
_video_to_base64_data_url = _video_to_base64

def _cleanup_temp_file(path: "Optional[Path]") -> None:
    if path and path.exists():
        try:
            path.unlink()
        except Exception:
            pass

_re_encode_video = _normalize_video

def _check_stepfun_video_requirements() -> "Optional[str]":
    """Return None if requirements are met, or an error message."""
    if not _env_str("STEPFUN_API_KEY"):
        return "STEPFUN_API_KEY is not set — StepFun video analysis is unavailable."
    return None

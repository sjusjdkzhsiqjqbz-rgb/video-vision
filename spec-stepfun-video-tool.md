# Hermes Video Analysis Tool — Specification

## Problem Statement

Hermes Agent ships with a built-in `video_analyze` tool, but it is **opt-in** and **hidden at runtime** when the active LLM provider does not advertise video capabilities in its models endpoint. StepFun's API does not return video support flags, so `video_analyze` never appears in the tool list even when the `video` toolset is enabled.

Direct API testing proves that StepFun **can** process short video clips (up to ~12 MB base64 / ~9 MB MP4 at 1280px CRF28) via the `/chat/completions` endpoint using the `video_url` content type. The blocker is purely the capability-detection gate in Hermes, not the provider itself.

## Goal

Create a first-party Hermes tool (or MCP server) that:
1. Accepts local video file paths or URLs
2. Normalizes them to a size/duration that StepFun accepts
3. Sends them to the StepFun `/chat/completions` endpoint with `video_url` content
4. Returns the model's analysis as text
5. Integrates seamlessly into the existing Hermes tool ecosystem

## Non-Goals

- Do **not** attempt to patch Hermes core capability detection
- Do **not** create a generic video-analysis MCP server; keep it StepFun-aware
- Do **not** process videos larger than the proven safe limit (~12 MB base64) without explicit user opt-in to chunking

## Architecture Options

### Option A: Native Hermes Tool (preferred)

Add a new tool file under `tools/stepfun_video.py` (or similar) that:
- Reads `STEPFUN_API_KEY` and `STEPFUN_BASE_URL` from `.env`
- Defaults to the Step Plan base URL (`/step_plan/v1`)
- Accepts parameters: `video_path` (local file or URL), `prompt` (text), `max_duration` (seconds, default 90), `max_base64_mb` (default 12)
- Internally re-encodes with ffmpeg if needed: scale=1280, CRF 28, AAC 96k
- Base64-encodes and posts to `/chat/completions`
- Returns `{success, analysis, model_used, file_size_mb, base64_size_mb}`

Registration: add `stepfun_video` to `CONFIGURABLE_TOOLSETS` in `tools_config.py`, gated behind a new `stepfun_video` toolset key (off by default).

### Option B: Hermes MCP Server (fallback if core changes are undesirable)

Standalone stdio MCP server (`mcp-stepfun-video`) that:
- Exposes one tool: `analyze_video`
- Reads the same env vars
- Does the same ffmpeg normalization
- Returns JSON text

Config entry:
```yaml
mcp_servers:
  stepfun-video:
    command: "npx"
    args: ["-y", "mcp-stepfun-video"]
    env:
      STEPFUN_API_KEY: "${STEPFUN_API_KEY}"
      STEPFUN_BASE_URL: "https://api.stepfun.ai/step_plan/v1"
```

## Detailed Spec for Option A

### File Layout

```
hermes-agent/
  tools/
    stepfun_video.py          # new
    tools_config.py           # patch: add toolset + tool entry
  tests/
    tools/
      test_stepfun_video.py   # new
```

### Tool Schema

```python
STEPFUN_VIDEO_SCHEMA = {
    "name": "stepfun_video_analyze",
    "description": (
        "Analyze a video via StepFun Step Plan API. "
        "Accepts local file paths or HTTP URLs. "
        "Videos are automatically re-encoded to fit API limits (~12 MB base64). "
        "Requires STEPFUN_API_KEY and STEPFUN_BASE_URL in .env."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "video_path": {
                "type": "string",
                "description": "Local path or HTTP/HTTPS URL to the video file."
            },
            "prompt": {
                "type": "string",
                "description": "Question or instruction for the video analysis.",
                "default": "Describe this video in detail."
            },
            "max_duration_seconds": {
                "type": "integer",
                "description": "Trim video to this duration if longer.",
                "default": 90
            },
            "max_base64_mb": {
                "type": "number",
                "description": "Hard cap on base64 payload size in megabytes.",
                "default": 12.0
            },
            "model": {
                "type": "string",
                "description": "Override the model name.",
                "default": ""
            }
        },
        "required": ["video_path"]
    }
}
```

### Implementation Contract

1. **Input validation**
   - Reject empty `video_path`
   - If URL, validate scheme (http/https) and run through existing `check_website_access`
   - If local path, resolve `~` and verify file exists

2. **Normalization (ffmpeg)**
   - If file exceeds limits, re-encode to `/tmp/hermes_video_<uuid>.mp4`:
     ```
     ffmpeg -y -i <src> -ss 0 -t <max_duration> -vf "scale=1280:-2" \
       -c:v libx264 -preset fast -crf 28 -c:a aac -b:a 96k -movflags +faststart <dst>
     ```
   - Detect MIME via extension; supported: mp4, webm, mov, avi, mkv
   - If re-encoded size still exceeds `max_base64_mb` after base64 expansion (×1.33), raise clear error suggesting shorter duration or lower resolution

3. **API call**
   - Base URL: `os.getenv("STEPFUN_BASE_URL", "https://api.stepfun.ai/step_plan/v1")`
   - Key: `os.getenv("STEPFUN_API_KEY")`
   - Model: parameter override > `STEPFUN_VIDEO_MODEL` env > `step-3.7-flash`
   - Endpoint: `POST {base_url}/chat/completions`
   - Payload: standard OpenAI chat schema with `video_url` content block
   - Timeout: 180s default, configurable via `STEPFUN_VIDEO_TIMEOUT` env
   - Temperature: 0.1 default, configurable via `STEPFUN_VIDEO_TEMPERATURE` env
   - Max tokens: 4000 default

4. **Response handling**
   - On 200: return text content from `choices[0].message.content`
   - On 413 / 400: if error mentions size, suggest reducing `max_duration_seconds` or `max_base64_mb`
   - On 401/403: suggest checking `STEPFUN_API_KEY`
   - On network error: retry once with backoff (2s), then fail

5. **Cleanup**
   - Delete temp re-encoded files after success or failure
   - Never delete user-supplied files

6. **Logging / Debug**
   - Log original size, re-encoded size, base64 size, model used, duration
   - Respect `VISION_TOOLS_DEBUG=true` for verbose output

### Registration in tools_config.py

Add entry:
```python
("stepfun_video", "🎬 StepFun Video Analysis", "stepfun_video_analyze (Step Plan only, ~12 MB base64 cap)"),
```

Add to a new toolset or reuse `video` key but force-register regardless of provider capability flag. The cleanest approach is a **separate toolset** `stepfun_video` so users explicitly opt in.

Update `_DEFAULT_OFF_TOOLSETS` to include `"stepfun_video"`.

### Tests (pytest)

Cover:
- Happy path: 10s MP4 under limit → 200 response with non-empty text
- Oversized file: auto-trim succeeds
- Oversized after trim: raises `ValueError` with actionable message
- Invalid URL scheme: raises `ValueError`
- Missing API key: raises `RuntimeError`
- API 401: raises `PermissionError`
- API 413: raises `ValueError` with size hint

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `STEPFUN_API_KEY` | Auth | *(required)* |
| `STEPFUN_BASE_URL` | API root | `https://api.stepfun.ai/step_plan/v1` |
| `STEPFUN_VIDEO_MODEL` | Model override | `step-3.7-flash` |
| `STEPFUN_VIDEO_TIMEOUT` | Request timeout (s) | `180` |
| `STEPFUN_VIDEO_TEMPERATURE` | Sampling temp | `0.1` |
| `STEPFUN_VIDEO_MAX_BASE64_MB` | Hard cap | `12` |
| `VISION_TOOLS_DEBUG` | Verbose logging | `false` |

### User-Facing CLI

```bash
# Enable once
hermes tools enable stepfun_video

# Use in chat
hermes chat --toolsets stepfun_video
```

### Example Prompts Users Can Give the Tool

- "What is happening in this video?" (default)
- "Describe the choreography step by step."
- "Is this a meme? Explain the reference."
- "What anime is this from and what is the comedic context?"

### Risks / Limitations

- Base64 payload bloat means large files must be trimmed/compressed
- StepFun may silently change size limits; tool should fail loudly with the actual HTTP error
- Only works with Step Plan base URL; standard `/v1` may bill separately (user has confirmed Step Plan subscription)
- No streaming; single-shot request only

### Future Enhancements (out of scope for v1)

- Chunked analysis for long videos (split into segments, merge summaries)
- Thumbnail extraction + parallel vision analysis as fallback when API rejects video
- Cache decoded base64 to avoid re-encoding unchanged files

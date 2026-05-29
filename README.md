# stepfun_video — StepFun Video Analysis for Hermes Agent

Directly calls the StepFun `/chat/completions` API with `video_url` content blocks
using the `step-3.7-flash` model. Bypasses Hermes' capability-detection gate.

## Features

- **Local files & URLs** — pass a file path or HTTP/HTTPS URL
- **stepfile:// support** — upload once via Files API, reuse with `stepfile://<id>`
- **Auto-normalization** — ffmpeg re-encodes oversized videos (scale=1280, CRF 28, trim)
- **reasoning_effort** — `low` / `medium` / `high` on step-3.7-flash
- **Full API power** — temperature, max_tokens, model override, base64 fallback

## Installation

```bash
# 1. Copy the tool into your hermes-agent tools directory
cp stepfun_video.py ~/.hermes/tools/

# 2. Set your API key
echo 'STEPFUN_API_KEY=sk-your-key-here' >> ~/.hermes/.env

# 3. (Optional) Set your base URL if using Step Plan
echo 'STEPFUN_BASE_URL=https://api.stepfun.ai/step_plan/v1' >> ~/.hermes/.env

# 4. Enable the toolset
hermes tools enable stepfun_video

# 5. Use in chat
hermes chat --toolsets stepfun_video
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `STEPFUN_API_KEY` | *(required)* | StepFun API key |
| `STEPFUN_BASE_URL` | `https://api.stepfun.ai/step_plan/v1` | API base URL |
| `STEPFUN_VIDEO_MODEL` | `step-3.7-flash` | Model override |
| `STEPFUN_VIDEO_TIMEOUT` | `180` | Request timeout (seconds) |
| `STEPFUN_VIDEO_TEMP` | `0.1` | Sampling temperature |
| `STEPFUN_VIDEO_MAX_TOKENS` | `4000` | Max output tokens |
| `STEPFUN_REASONING_EFFORT` | `medium` | Default reasoning effort |
| `VISION_TOOLS_DEBUG` | `false` | Verbose debug logging |

## Tool Parameters

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `video_path` | string | yes | — | Local path, URL, or `stepfile://` reference |
| `prompt` | string | no | `"Describe this video in detail."` | Analysis question |
| `max_duration_seconds` | integer | no | `90` | Trim video to this duration |
| `max_base64_mb` | number | no | `12.0` | Hard size cap in MB |
| `model` | string | no | env/default | Model override |
| `reasoning_effort` | string | no | `medium` | `low` / `medium` / `high` |

## Example Usage

```
User: What's happening in /home/user/clip.mp4?
Agent: *calls stepfun_video_analyze(video_path="/home/user/clip.mp4", prompt="Describe this video in detail.")*
Agent: The video shows a cat jumping onto a desk...

User: Summarize https://example.com/demo.mp4 with high reasoning
Agent: *calls stepfun_video_analyze(video_path="https://example.com/demo.mp4", reasoning_effort="high")*
```

## Architecture

```
user message → Hermes Agent → stepfun_video_analyze tool
                                  ├─ resolve source (local/url/stepfile)
                                  ├─ ffmpeg normalize if needed
                                  ├─ upload to Files API (opt.)
                                  └─ POST /v1/chat/completions
                                       ├─ video_url content block
                                       └─ text content block
                                  → returns { success, analysis, usage }
```

## Video Requirements (StepFun)

- Format: MP4, QuickTime (.mov), Matroska (.mkv)
- Max size: 128 MB (original), ~12 MB (base64)
- Max duration: 5 minutes
- The tool auto-normalizes to fit these limits

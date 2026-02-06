---
name: youtube-transcript
description: Fetch transcripts from YouTube videos for summarization, analysis, or content extraction. Use when asked to get a YouTube transcript, summarize a YouTube video, extract content from a video, or analyze what someone said in a video. Triggers on YouTube URLs, video IDs, or requests like "get the transcript of...", "summarize this YouTube video", "what does this video say about...".
---

# YouTube Transcript Fetcher

Fetch transcripts from YouTube videos using the `youtube_transcript_api` CLI tool.

## Setup

1. Check if installed: `which youtube_transcript_api`
2. If not installed:
   ```bash
   uv tool install youtube-transcript-api
   ```

## Extract Video ID

Extract the video ID from various YouTube URL formats:

| URL Format | Video ID |
|------------|----------|
| `https://www.youtube.com/watch?v=dQw4w9WgXcQ` | `dQw4w9WgXcQ` |
| `https://youtu.be/dQw4w9WgXcQ` | `dQw4w9WgXcQ` |
| `https://www.youtube.com/embed/dQw4w9WgXcQ` | `dQw4w9WgXcQ` |

The video ID is the 11-character alphanumeric string.

## Proxy Support

If `WEBSHARE_PROXY_USERNAME` and `WEBSHARE_PROXY_PASSWORD` environment variables are set, **always** add proxy flags to avoid IP-based rate limiting:

```bash
youtube_transcript_api VIDEO_ID \
  --languages en de es \
  --webshare-proxy-username "$WEBSHARE_PROXY_USERNAME" \
  --webshare-proxy-password "$WEBSHARE_PROXY_PASSWORD"
```

Check before each invocation:
```bash
if [ -n "$WEBSHARE_PROXY_USERNAME" ] && [ -n "$WEBSHARE_PROXY_PASSWORD" ]; then
  # Use --webshare-proxy-username and --webshare-proxy-password flags
fi
```

If the variables are **not set**, run commands without proxy flags.

## Commands

### Fetch Transcript
```bash
# Without proxy
youtube_transcript_api VIDEO_ID

# With proxy (preferred — use when env vars are set)
youtube_transcript_api VIDEO_ID \
  --webshare-proxy-username "$WEBSHARE_PROXY_USERNAME" \
  --webshare-proxy-password "$WEBSHARE_PROXY_PASSWORD"
```

### Specify Language (with fallback)
```bash
youtube_transcript_api VIDEO_ID --languages en de es
```
Tries English first, then German, then Spanish.

### List Available Transcripts
```bash
youtube_transcript_api --list-transcripts VIDEO_ID
```

### Translate Transcript
```bash
youtube_transcript_api VIDEO_ID --languages en --translate de
```

### Output Formats
```bash
# JSON (default)
youtube_transcript_api VIDEO_ID --format json

# Plain text
youtube_transcript_api VIDEO_ID --format text

# SRT subtitles
youtube_transcript_api VIDEO_ID --format srt

# WebVTT subtitles
youtube_transcript_api VIDEO_ID --format webvtt
```

### Filter Transcript Types
```bash
# Only manual captions (higher quality)
youtube_transcript_api VIDEO_ID --exclude-generated

# Only auto-generated
youtube_transcript_api VIDEO_ID --exclude-manually-created
```

## Workflow

1. Check if `$WEBSHARE_PROXY_USERNAME` and `$WEBSHARE_PROXY_PASSWORD` are set
2. Extract video ID from URL
3. List available transcripts to check languages: `--list-transcripts`
4. Fetch transcript in preferred language (with proxy flags if env vars are set)
5. Process the output (summarize, analyze, extract quotes)

## Output Structure

JSON output contains segments with:
- `text`: The spoken content
- `start`: Start time in seconds
- `duration`: Segment duration in seconds

```json
[
  {"text": "Hello everyone", "start": 0.0, "duration": 1.5},
  {"text": "Welcome to the video", "start": 1.5, "duration": 2.0}
]
```

## Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `TranscriptsDisabled` | Video owner disabled captions | Cannot fetch - no workaround |
| `NoTranscriptFound` | No captions in requested language | Try `--list-transcripts` to see available |
| `VideoUnavailable` | Private, deleted, or region-locked | Check video accessibility |

## Tips

- Use `--format text` for LLM summarization (cleaner input)
- Prefer `--exclude-generated` when available for better accuracy
- For videos starting with `-`, escape: `"\-VIDEO_ID"`

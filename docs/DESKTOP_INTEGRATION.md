# Desktop Integration

Documentation for espanso text expansion, ulauncher extension, and speech-to-text transcription.

## Text Expansion with espanso

espanso provides LLM-powered text expansion in any application.

### Triggers

| Trigger | Mode | Clipboard | Description |
|---------|------|-----------|-------------|
| `:llm:` | simple | no | Quick query without tools |
| `:llmc:` | simple | yes | Simple mode with clipboard context |
| `:@:` | assistant | no | Full inline-assistant with tools |
| `:@c:` | assistant | yes | Inline-assistant with clipboard context |

### Usage

1. Type a trigger (e.g., `:@:`) in any text field
2. Enter your question in the popup dialog
3. The AI response replaces the trigger text

### Requirements

- llm-assistant daemon running (auto-started on first use)
- Uses Unix socket communication (no HTTP server needed)

### Troubleshooting

```bash
# Check/start service
espanso service register && espanso start

# Check status
espanso status

# View logs
espanso log

# Reload config
espanso restart
```

### File Locations

| Path | Purpose |
|------|---------|
| `~/.config/espanso/` | Configuration directory |
| `~/.config/espanso/match/packages/espanso-llm/` | LLM integration package |
| `espanso-llm/` | Source files (repository) |

## Ulauncher Extension

Ulauncher extension for quick AI access via application launcher.

### Keywords

| Keyword | Mode | Clipboard | Description |
|---------|------|-----------|-------------|
| `llm` | simple | no | Quick AI query without tools |
| `llmc` | simple | yes | Simple mode with clipboard context |
| `@` | assistant | no | Full assistant with tools |
| `@c` | assistant | yes | Assistant with clipboard context |

### Features

- Streaming responses with live UI updates
- Tool execution feedback (shows "[Executing Python...]", etc.)
- Slash commands: `/new`, `/status`, `/help`
- Copy: Enter = plain text, Alt+Enter = markdown
- Persistent conversations within session

### Usage

1. Launch Ulauncher (Ctrl+Space or Meta key)
2. Type `llm what is 2+2?` for simple query
3. Type `@ explain this error` for assistant mode
4. Press Enter to copy response

### File Locations

| Path | Purpose |
|------|---------|
| `~/.local/share/ulauncher/extensions/ulauncher-llm/` | Extension (symlinked) |
| `ulauncher-llm/` | Source files (repository) |

### Troubleshooting

- Extension not appearing: Restart Ulauncher
- Daemon not running: Extension auto-starts it, or run `llm-assistant --daemon`
- Check status: `@ /status`

## Speech-to-Text Transcription

Uses onnx-asr with NVIDIA's Parakeet TDT model for file transcription.

### Components

| Component | Purpose |
|-----------|---------|
| Handy | System-wide STT application (.deb, x86_64 only) |
| onnx-asr | Speech recognition library (ONNX Runtime backend) |
| transcribe | CLI wrapper script |
| pydub | Audio format conversion (requires ffmpeg) |

### Usage

```bash
# Basic transcription
transcribe recording.mp3

# Save to file
transcribe video.mp4 -o transcript.txt

# Suppress progress
transcribe meeting.m4a 2>/dev/null | less
```

### Supported Languages (25)

Bulgarian, Croatian, Czech, Danish, Dutch, English, Estonian, Finnish, French, German, Greek, Hungarian, Italian, Latvian, Lithuanian, Maltese, Polish, Portuguese, Romanian, Slovak, Slovenian, Spanish, Swedish, Russian, Ukrainian

### Model Information

- Model: `nemo-parakeet-tdt-0.6b-v3` INT8 quantized (600M parameters)
- Size: ~670MB total
- Location: `~/.local/share/com.pais.handy/models/parakeet-tdt-0.6b-v3-int8/`
- Downloaded during installation (not on first use)

### Handy Integration

When Handy is running:
- Provides OS-level voice input accessible from any application
- llm-assistant's built-in voice input is automatically disabled
- Both use the same shared INT8 model

### Supported Formats

- Native: WAV
- Converted via pydub/ffmpeg: mp3, mp4, m4a, flac, ogg, webm

### More Languages

For 99+ language support (including Asian languages):
```bash
uv tool install whisper-ctranslate2
```

## PipeWire VM Audio Fix

For VM environments with audio issues, the installer creates:
- Location: `~/.config/wireplumber/wireplumber.conf.d/50-alsa-config.conf`
- Auto-generated only in VM environments

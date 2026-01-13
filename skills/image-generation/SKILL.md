---
name: image-generation
description: AI image generation via Google Gemini using the imagemage CLI. Use when generating images, editing photos, creating app icons, making patterns/textures, producing storyboard sequences, generating technical diagrams/flowcharts, or restoring old photos. Triggers on requests like "generate an image of...", "create an icon for...", "make a diagram showing...", "edit this image to...", or any visual asset creation needs.
---

# Imagemage - Gemini Image Generation CLI

Single-binary Go CLI for AI image generation via Google Gemini.

## Setup

1. Check if already installed: `which imagemage`
2. If not in PATH, copy the bundled binary:
   ```bash
   cp scripts/imagemage ~/.local/bin/
   chmod +x ~/.local/bin/imagemage
   ```

Requires `GEMINI_API_KEY` environment variable (also accepts `GOOGLE_API_KEY`).

## Commands

### Generate Images
```bash
imagemage generate "prompt" [flags]
```

| Flag | Description |
|------|-------------|
| `-c, --count N` | Number of images (default: 1) |
| `-o, --output DIR` | Output directory |
| `-s, --style "style"` | Style guidance (e.g., "watercolor", "neon") |
| `-a, --aspect-ratio` | 1:1, 16:9, 9:16, 4:3, 3:4 |
| `-f, --frugal` | Use cheaper Flash model |
| `--slide` | Optimized for slides (4K, 16:9) |

```bash
imagemage generate "mountain at sunset" --aspect-ratio="16:9"
imagemage generate "app splash" --style="minimal" -o ./assets
```

### Edit Images
```bash
imagemage edit input.png "instruction" [-o output.png] [-i extra.png]
```

```bash
imagemage edit photo.png "make black and white"
imagemage edit scene.png "add person on left" -i person.png
```

### Generate Icons
```bash
imagemage icon "prompt" [--sizes "64,128,256"] [--type app-icon|favicon|ui-element]
```

### Create Patterns
```bash
imagemage pattern "prompt" [--type seamless|tiled|texture] [-s "style"]
```

### Generate Diagrams
```bash
imagemage diagram "description" [--type flowchart|architecture|sequence|entity-relationship]
```

### Create Story Sequences
```bash
imagemage story "narrative" [-f frames] [-s "style"]
```

### Restore Photos
```bash
imagemage restore old_photo.jpg -o restored.png
```

## Prompt Writing

**Core principle**: Describe the scene narratively, don't list keywords.

| Good | Bad |
|------|-----|
| "A close-up portrait shot with 85mm lens, golden hour lighting" | "portrait, golden hour, close-up" |
| "Ornate elven plate armor with silver leaf patterns" | "fantasy armor" |

- Use `--style` flag for style guidance, not the main prompt
- Use `--aspect-ratio` flag for dimensions (model ignores dimension text in prompts)
- Use `--frugal` for drafts, default Pro model for final output
- For detailed prompt strategies, see [references/prompt-guide.md](references/prompt-guide.md)

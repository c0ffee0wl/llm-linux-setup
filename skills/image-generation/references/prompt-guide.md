# Gemini Image Generation Prompt Guide

Reference: https://ai.google.dev/gemini-api/docs/image-generation#prompt-guide

## Core Principle

**Describe the scene, don't just list keywords.** Narrative, descriptive paragraphs consistently outperform disconnected word lists.

## Seven Prompting Strategies

### 1. Photorealistic Scenes
Use photography terminology: camera angles, lens types, lighting, fine details.
- "85mm portrait lens," "golden hour light," "soft bokeh background"
- Mention specific lighting setups and surface textures

### 2. Stylized Illustrations & Stickers
Be explicit about artistic style, request transparent backgrounds.
- "bold outlines," "cel-shading," specific color palettes
- Describe the design aesthetic clearly

### 3. Accurate Text in Images
- Describe font style descriptively
- Specify overall design aesthetic
- Keep text short and prominent

### 4. Product Mockups
Use studio photography language:
- Lighting setups, camera angles, surface details
- Clean, professional presentation focus

### 5. Minimalist & Negative Space
- Position subjects strategically
- Create empty canvas space for text overlays
- Use subtle lighting for sophisticated backgrounds

### 6. Sequential Art
- Combine character consistency with scene description
- Excellent for comic panels and storyboards

### 7. Grounding with Google Search
- Generate images from real-time data
- Weather, news, current events benefit from factual grounding

## Seven Editing Strategies

1. **Adding/Removing Elements** - Preserve original style, lighting, perspective
2. **Inpainting** - Change specific elements while maintaining unchanged areas
3. **Style Transfer** - Render content in different artistic styles
4. **Advanced Composition** - Combine multiple reference images into new scenes
5. **Detail Preservation** - Describe critical features extensively during edits
6. **Sketches to Finished** - Refine rough drawings into polished images
7. **Character Consistency** - Generate 360-degree views by iterating angles

## Best Practices

| Practice | Example |
|----------|---------|
| **Hyper-Specific Details** | "Ornate elven plate armor with silver leaf patterns" beats "fantasy armor" |
| **Context & Intent** | "logo for high-end skincare" outperforms "logo" |
| **Iterative Refinement** | "Warmer lighting?" or "More serious expression?" |
| **Step-by-Step** | Break complex scenes into sequential instructions |
| **Semantic Negatives** | Describe desired outcomes positively rather than listing exclusions |
| **Camera Control** | Use photographic language to direct composition |

## Prompt Examples by Use Case

### Photorealistic Portrait
```
A close-up portrait of an elderly man with weathered skin and kind eyes,
shot with an 85mm lens at f/1.8, golden hour lighting from the left,
soft bokeh background of autumn leaves, warm color grading
```

### Product Photography
```
Professional product shot of a glass perfume bottle on white marble surface,
dramatic rim lighting from behind, soft fill light from front,
slight reflection on surface, clean minimal composition, 4K quality
```

### Illustration Style
```
Whimsical children's book illustration of a fox reading under a tree,
watercolor style with soft edges, muted autumn palette,
hand-drawn quality with visible brush strokes
```

### Technical Diagram
```
Clean flowchart showing CI/CD pipeline: code commit triggers build,
then automated tests, then staging deployment, then production.
Use blue boxes with white text, arrows connecting stages,
modern flat design style, white background
```

## Key Limitations

- Best performance: English, German, Spanish, French, Hindi, Indonesian, Italian, Japanese, Korean, Portuguese, Russian, Ukrainian, Vietnamese, Turkish, Chinese, Arabic
- No audio/video inputs for image generation
- 2.5 Flash: up to 3 images work best
- All outputs include SynthID watermarks

## Model Selection

| Model | Best For |
|-------|----------|
| **Pro** (default) | Professional assets, complex reasoning, 1K-4K resolution |
| **Flash** (`--frugal`) | Speed, high-volume tasks, drafts, 1024px resolution |

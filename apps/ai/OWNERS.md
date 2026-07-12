# AI + Infra — Person 3

Everything in `apps/ai/` and `infra/` is yours.

## What you own

### ai/services/ — the AI pipeline
- **gemini.py** — Prompt structuring (raw prompt -> structured edit plan with 3 variants). Entity identification from bbox crop. Keyframe entity search (batch 10 keyframes per request, retry on 429). Quality scoring (3 frames per variant, structured output {visual_coherence, prompt_adherence}).
- **runway.py** — Video-to-video generation via Runway Gen-4 API. One call per variant (3 parallel). Handle bbox spatial grounding via prompt text.
- **elevenlabs.py** — Text-to-speech narration for before/after reveals. Gemini generates script, ElevenLabs renders audio.
- **entity_tracker.py** — Entity tracking across video segments. Extract reference crop from bbox, identify via Gemini vision, search keyframes, build continuity packs.
- **ffmpeg.py** — Clip extraction, fps normalization, crossfade stitching (shared with backend but you own the logic).

### ai/prompts/ — prompt templates
- Gemini edit plan prompt template
- Gemini entity identification prompt
- Gemini keyframe search prompt
- Gemini quality scoring prompt
- Narration script generation prompt

### infra/ — deployment
- Vultr setup (backend + workers + storage)
- Docker / docker-compose
- Environment config
- Domain setup (fiebatt.tech)

## Day 1 blockers (spike these first)
1. **Runway spatial grounding** — does Runway Gen-4 honor bbox region emphasis via prompt text? If not, fallback to full-frame generation or switch to Stable Video Diffusion + ControlNet.
2. **Gemini edit plan output** — validate structured output schema works reliably.

## Key interactions
- Backend (Person 2) imports your services and calls them from route handlers
- You define the function signatures, they wire them into endpoints
- Entity search runs async after accept, results go to continuity pack UI (Person 1)

## Don't touch
- `apps/web/`, `apps/backend/app/api/`, `apps/backend/app/models/`

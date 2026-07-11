# Backend — Person 2

Everything in this folder is yours. FastAPI + Python.

## What you own
- All API route handlers (app/api/routes/)
- Pydantic request/response schemas (app/schemas/)
- Database models + migrations (app/models/)
- Async job queue + workers (app/workers/)
- Video upload, validation (reject > 2 min), storage
- ffmpeg clip extraction, segment stitching, crossfade, fps normalization
- Export pipeline (ffmpeg concat, async job)
- Session management (UUID tokens, ephemeral, not durable across restarts)

## API endpoints you serve
| Endpoint | Method | What it does |
|---|---|---|
| /api/upload | POST | Accept video, validate length, probe fps/duration |
| /api/projects/{id} | GET | Return project state |
| /api/generate | POST | Enqueue generation job (calls into ai/ services) |
| /api/jobs/{id} | GET | Poll job status + variants |
| /api/accept | POST | Apply variant to timeline, trigger entity search |
| /api/entities/{id} | GET | Return entity appearances |
| /api/propagate | POST | Enqueue propagation jobs |
| /api/propagate/{id} | GET | Poll propagation status |
| /api/timeline/{id} | GET | Return current timeline segments |
| /api/narrate | POST | Call ElevenLabs service (in ai/) |
| /api/export | POST | Enqueue export job |
| /api/export/{id} | GET | Poll export status |

## Key interactions
- You import services from `apps/ai/` (Person 3) — gemini, runway, elevenlabs, entity tracker
- Frontend (Person 1) consumes your API — coordinate on schema changes
- Audio: replaced segments = muted, originals = passthrough

## Don't touch
- `apps/frontend/`, `ai/services/`, `infra/`

# iris

prompt-driven video editor for localized reality edits and continuity propagation.

## repo layout

- `frontend/` — vite/react/typescript app
- `backend/` — fastapi api, models, workers, export pipeline
- `ai/` — provider adapters, prompts, ai-side tests
- `gpu-worker/` — optional sam/clip worker
- `infra/` — dockerfiles, compose, deploy helpers

## local dev

prereqs:

- node 20+
- python 3.11+ with backend deps installed
- ffmpeg on path

install frontend deps:

```bash
npm install --prefix frontend
```

frontend from repo root:

```bash
npm run dev
```

frontend directly:

```bash
npm --prefix frontend run dev
```

backend:

```bash
./scripts/dev_backend.sh
```

the frontend lives in `frontend/`, but vite reads env from the repo root so the existing root `.env` still works.
the root `package.json` is just a thin wrapper so you can keep using the usual root commands without stuffing the actual app back into repo root.

compose stack from repo root:

```bash
docker compose -f infra/docker-compose.yml up --build frontend backend db
```

optional gpu worker profile:

```bash
docker compose -f infra/docker-compose.yml --profile gpu up --build gpu-worker
```

the compose backend defaults to the local `db` container and to `http://gpu-worker:8001` when that gpu profile is running. if you set `DATABASE_URL` or `GPU_WORKER_URL` in the repo root `.env`, those override the compose defaults so you can point the same stack at vultr services.

## common commands

frontend typecheck:

```bash
npm run lint
```

frontend prod build:

```bash
npm run build
```

ai tests:

```bash
pytest ai/tests -q
```

smoke flow:

```bash
./scripts/smoke.sh
```

## env notes

- root `.env.example` is the starting point for local config
- `VITE_*` vars are consumed by the frontend
- backend, ai, and storage vars are consumed by fastapi workers/services
- local dev can run with `USE_AI_STUBS=true`; the real demo path wants `USE_AI_STUBS=false`
- `VIDEO_GEN_PROVIDER=wan` uses DashScope Wan source-video editing; `VIDEO_GEN_PROVIDER=veo` uses Google Veo via `GEMINI_API_KEY`; `VIDEO_GEN_PROVIDER=happyhorse` uses the DashScope HappyHorse fallback
- `DATABASE_URL` can stay local for quick hacking or point at vultr managed postgres for the demo/deploy story
- `VULTR_S3_*` controls media publishing; when unset, backend falls back to the local `/media` mount
- `GPU_WORKER_URL` points at the sam/clip worker, either `http://localhost:8001` locally or the vultr gpu box in demo mode

## ai observability

the backend now mounts the ai observability router directly, so the main app exposes:

- `GET /api/health` — backend liveness
- `GET /api/ai/health` — ai service counters, latency rollups, estimated cost, gpu worker reachability
- `GET /api/ai/timeline?last_n=50` — recent ai call timeline for live-demo debugging
- `GET /api/ai/stream` — sse stream for a lightweight admin/dashboard panel

quick smoke checks:

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/ai/health
curl "http://localhost:8000/api/ai/timeline?last_n=10"
```

browser sse snippet:

```js
const es = new EventSource("http://localhost:8000/api/ai/stream");
es.addEventListener("init", (event) => console.log("init", JSON.parse(event.data)));
es.onmessage = (event) => console.log("tick", JSON.parse(event.data));
```

## demo story

for a concrete judge-facing setup:

1. run the frontend locally or via compose.
2. run the backend with `USE_AI_STUBS=false`.
3. point `DATABASE_URL` at vultr managed postgres so job/project state survives restarts.
4. point `VULTR_S3_*` at vultr object storage so uploads, keyframes, variants, and exports come from durable urls instead of the local `/media` mount.
5. point `GPU_WORKER_URL` at the vultr gpu worker, or bring up the local compose gpu profile if you have the worker image + checkpoint available.
6. keep `/api/ai/health`, `/api/ai/timeline`, and `/api/ai/stream` open during the demo so provider latency, error rate, and gpu reachability are visible instead of hand-waved.

the local gpu profile assumes the sam checkpoint is present at `gpu-worker/checkpoints/sam2.1_hiera_small.pt`. if that file is missing, the worker can still boot and answer `/health`, but sam requests will fail when first used.

## iris CLI + agent skill

install the CLI:

```bash
pip install iris-edit
iris auth login --base-url http://localhost:8000
```

install the agent skill (one-liner for any claude code user):

```bash
mkdir -p ~/.claude/skills/iris-edit && curl -sL https://raw.githubusercontent.com/stephenhungg/iris/main/cli/SKILL.md -o ~/.claude/skills/iris-edit/SKILL.md
```

for other agents (codex, openclaw, cursor), paste the contents of `cli/SKILL.md` as a system prompt.

docs: [docs.useiris.tech](https://docs.useiris.tech)

## current rough edges

- frontend build currently warns about a large bundle
- the gpu worker compose profile is explicit on purpose; default local dev should still work without docker gpu/runtime setup
- real-provider demos still depend on valid provider keys plus a reachable gpu worker target

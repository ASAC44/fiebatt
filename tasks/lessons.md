# lessons

- when the repo has a real frontend app, keep it inside `apps/frontend/` and leave the repo root as orchestration, docs, and config only.
- if a route should only exist for signed-in users, gate it explicitly at the route layer and preserve intent through auth. don't let anonymous fallthrough create fake "works sometimes" navigation.
- when cleaning up visuals during a merge, verify whether a weird-looking landing element is intentional product direction before deleting it. hero pieces like the `ascii` landing effect are allowed to be opinionated on purpose.
- if a visual spacing tweak changes basically nothing, stop nudging outer wrappers and inspect the actual rendered composition. the gap may be coming from the asset/layout model itself, not the nearest css margin.
- when the backend schema declares a field as `dict` (python), the frontend type must be `Record<string, string>` or a proper object type — not `string`. `res.json()` will parse it as a JS object, so the TS type needs to match runtime reality.
- variant types from the backend include `id`, `index`, `status`, and `error` fields. always declare these in the frontend type even if you don't immediately use them — they're needed for progressive reveal, error handling, and keying.
- the EDL `hydrate` action must clear all transient edit state (bbox, mask, identified, identifying) to prevent stale data from leaking across project sessions.
- integration tests using `httpx.AsyncClient` with `ASGITransport` don't trigger the fastapi lifespan. db tables and job runner must be set up explicitly in a fixture.
- if a “vibe” flow still requires the user to think in raw edl/source-range semantics, it is not actually a vibe flow yet. validate the first-run path against backend constraints like segment length before treating the interaction as product-ready.

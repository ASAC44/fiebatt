# Adaptive, Context-Aware Video Edit Continuity Plan

## Status

Proposed architecture. This document describes the intended replacement for the current fixed-window generation flow; it is not an implementation record.

Plan rebased conceptually onto repository structure at `b6f799c`: canonical product paths are `apps/api`, `apps/web`, and `apps/api/vision-worker`.

## Goal

Produce localized edits that enter and leave original footage without visible jumps in subject pose, subject motion, camera motion, background, lighting, or color. The system must support:

- a local edit near the playhead;
- an explicitly selected time range;
- an edit applied to every occurrence of a selected entity;
- occurrences longer than one provider request;
- multiple disjoint occurrences across a reel;
- provider-specific duration and conditioning limits.

Cost matters, but continuity and preserving unedited footage take priority over blindly minimizing the generated duration.

## Current behavior and failure mode

For source clips longer than five seconds, the editor currently selects a fixed three-second window centered around the playhead. The backend extracts exactly that range, generates exactly that duration, replaces exactly that range, and hard-cuts back to original footage.

There are no pre-roll or post-roll handles. Wan and HappyHorse can receive the source video inside the requested range, but they do not see motion outside it. Veo can receive a last-frame constraint only for an explicit eight-second request. Export applies trailing color matching, then uses hard cuts.

Current local generation does not search all entity occurrences before rendering. However, accepting any bbox-tracked edit automatically starts a one-frame-per-second full-reel entity search so the UI can offer propagation. That asynchronous search does not block the first edit, but it still spends vision time/tokens even when the user never requested a global change.

This architecture creates two discontinuities:

1. original footage to generated footage at the edit start;
2. generated footage back to original footage at the edit end.

The ending discontinuity is often stronger because generation can finish with a different pose, velocity, gait phase, or camera trajectory from the original continuation.

## What the removed HappyHorse bridge did

The removed bridge was a special-case two-generation workflow:

1. Generate the requested action clip.
2. Take one still image from the generated clip at the intended action endpoint.
3. Use that still as the starting reference for a second generated clip.
4. Ask the second generation to make the subject resume walking.
5. Blend the two generated clips for 0.18 seconds.
6. Replace the requested range plus roughly three seconds of following original footage.

In simple terms, it did not teach the first generation how to meet the original future motion. It invented a new future after the edit and kept that invented future on the timeline.

Useful idea:

- Allocate time after an action for the subject to return to stable motion.

Unsafe implementation details:

- prompts were hard-coded to a man jumping and returning to walking;
- it activated only for a narrow HappyHorse sequenced-motion path;
- continuation came from one generated still, not the original future video;
- it replaced footage beyond the user's requested range;
- the join relied on a crossfade, which can create double images and ghosting;
- it had no pre-roll context;
- it did not verify pose or velocity against the original footage where generation ended.

Do not restore this function. Reuse only its general lesson: an action may need a completion/recovery interval, and this interval must be planned explicitly.

## Core design: separate intent, occurrence, edit, and context

Never overload one timeline range with four meanings. Represent these separately:

```text
context_start   edit_start                 edit_end   context_end
      |              |                         |           |
      | pre-handle   | requested modification | post-handle
      | preserve     | may change              | preserve
```

Definitions:

- **Intent scope:** local, explicit range, selected occurrences, or all occurrences.
- **Occurrence span:** interval where tracked target exists in one continuous shot/track.
- **Edit core:** interval where requested visual or motion change is allowed.
- **Generation context:** edit core plus adaptive pre-roll and post-roll handles.
- **Boundary anchors:** exact full frames and motion measurements at generation-context boundaries.
- **Subject reference:** playhead frame, bbox, mask, identity embedding, and optional accepted style reference. This is not a boundary frame.
- **Selection artifact:** persisted seed timestamp, normalized bbox, SAM mask, contour, score, entity description, crop/reference URLs, and source revision used by planning and generation.

## Fit with existing product architecture

The plan should extend existing systems rather than build a parallel editing stack.

### Bbox overlay

Keep current normalized bbox overlay as primary user target signal. It already accounts for letterboxing and displays SAM-refined contours. Change its result from transient frontend state into a persisted `SelectionArtifact` that can be referenced by plan and generation jobs.

Selection should be locked to its seed source timestamp. Scrubbing after selection must not silently reinterpret same bbox against a different frame; either keep original seed or explicitly invalidate/reselect it.

### SAM2

Reuse current single-frame SAM2 mask instead of running segmentation again in generation worker. Current vision worker uses SAM2 image prediction, not video tracking. Add a video-tracking endpoint/capability for adaptive bounds and optional mask propagation; do not assume this functionality already exists.

Wan VACE already accepts one seed mask plus `mask_frame_id` and performs provider-native target tracking. Prefer that native path when available. Do not generate dense masks merely to duplicate provider tracking.

Dense masks are still useful for:

- finding frame-accurate target presence;
- validating localization;
- optional post-generation compositing when output target can also be reliably segmented.

### Entity identification

Current Gemini bbox identification gives useful description/category/attributes. Reuse result for prompt grounding and global identity search. Do not require or repeat this VLM call for an obvious local action when bbox/mask and prompt already identify target sufficiently; it may run lazily or in parallel for UI enrichment.

### CLIP embeddings

Current vision worker already supports single and batched CLIP embeddings. Use them for cached coarse global candidate retrieval in global PR. Do not invoke them for ordinary local one-time edits.

### Preview frames and strips

Reuse current preview extraction helpers for user/agent inspection. Automated planning should use internal cached frames and numeric motion/shot analysis rather than publishing a large preview strip and sending every frame through language-model context.

### Provider adapters

Extend current capability table and Wan/HappyHorse/Veo adapters. Do not add provider selection logic separately in frontend, agent, planner, and worker. Planner selects against one backend capability service; all entry points submit plan ID.

### Jobs and SSE

Reuse current job runner, variant records, job events, polling, and SSE. Add planning, tracking, validation, occurrence, and chunk stages to same job model instead of inventing a second orchestration transport.

### Timeline and EDL

Current `replace_range`, generated segments, timeline builder, preview, and export already support arbitrary ranges. Backend timeline must become authoritative after acceptance: return committed range/segments and rehydrate frontend instead of relying on duplicate optimistic range math.

### Scoring, color, and export

Reuse current scoring and color services as inputs to new validator. Extend from still-frame/color checks to multi-frame pose/flow/camera checks. Keep export renderer deterministic; seam quality should be solved before acceptance, not hidden during export.

### Storage and caching

Use current storage abstraction/S3 for masks, reference crops, embeddings, tracks, and validation artifacts. Cache keys must include project/source revision, timestamp/range, bbox, model version, and relevant parameters so stale artifacts cannot target changed timeline media.

## Proposed request model

Create a planner result before any paid video generation:

```json
{
  "scope": "local | explicit_range | selected_occurrences | all_occurrences",
  "target": {
    "entity_id": "optional",
    "seed_ts": 10.0,
    "seed_bbox": { "x": 0.2, "y": 0.1, "w": 0.3, "h": 0.7 }
  },
  "prompt_plan": {
    "change_type": "appearance | removal | replacement | motion | scene",
    "action_phases": ["optional structured phases"],
    "estimated_action_seconds": 3.2,
    "requires_recovery_motion": true
  },
  "occurrences": [
    {
      "track_id": "track-1",
      "shot_id": "shot-4",
      "occurrence_start": 7.8,
      "occurrence_end": 14.1,
      "edit_start": 8.6,
      "edit_end": 12.2,
      "context_start": 7.8,
      "context_end": 13.4,
      "confidence": 0.94
    }
  ]
}
```

The UI must show proposed edit cores and generation ranges before generation. User can adjust scope or range when planner confidence is low.

## Stage 1: parse prompt and determine scope

Use prompt, explicit timeline selection, playhead, and bbox together.

Priority:

1. Explicit user time range.
2. Explicit occurrence scope such as “everywhere,” “throughout,” “every time this person appears,” or “only this shot.”
3. Active timeline selection.
4. Bbox target at playhead.
5. Local occurrence containing playhead.

Default ambiguous requests to local scope. Applying an edit everywhere increases cost and changes more footage, so global scope must come from explicit language or explicit UI selection.

Prompt planner should extract:

- target entity or region;
- appearance edit versus motion edit;
- action phases and repetition count;
- whether original motion should resume afterward;
- estimated minimum time needed for action plus recovery;
- preservation constraints;
- whether change should persist while entity remains visible or occur only once.

Do not use a keyword-only motion regex as authoritative planning. It can be a fast hint, but structured planner output must drive range selection.

## Efficiency policy: spend analysis only when scope requires it

Optimize three different costs:

1. video-generation calls and generated seconds;
2. vision/tracking compute and wall-clock latency;
3. language/vision model tokens.

Use a correctness-first, cost-second objective. The cheapest plan that misses motion recovery or produces a visible seam is not useful, but global analysis for a local action is also waste.

### Fast scope gate

Before entity search or dense tracking, classify the request:

- **Explicit range:** inspect only selected range plus boundary context.
- **Local one-time action:** inspect lazily around playhead until action and handles fit.
- **Persistent local change:** find current continuous occurrence, because change lasts while target remains visible.
- **Selected occurrences:** inspect only user-selected candidate occurrences.
- **Global change:** run reel-wide occurrence discovery only for explicit language such as “everywhere,” “throughout,” or “every time.”

Example: “make this person jump” with playhead and bbox must not search whole reel. It needs only enough local frames for preparation, jump, landing, recovery, and both boundary handles.

### Reuse work instead of repeating model calls

- Reuse prompt interpretation already produced by agent/planner; do not call a second LLM to classify same request.
- Cache shot boundaries, sampled keyframes, embeddings, identity descriptions, and accepted tracks by project/source hash.
- Use deterministic range math and provider capability lookup after structured intent exists.
- Use cheap shot/motion signals first; call VLM only for ambiguous identity, scope, or action timing.
- Start dense tracking from bbox/SAM seed and stop as soon as local evidence budget is satisfied.
- Run coarse global retrieval before dense global tracking; densely track only candidates.
- Do not start full-reel entity discovery automatically after every local acceptance; trigger it only from explicit global intent or a user action such as “find other occurrences.”
- Batch candidate-frame vision requests and avoid sending redundant full-resolution frames as model context.
- Estimate generation count and total generated seconds before paid work.

### Lazy local expansion

For a one-time local action:

1. Start with playhead and bbox seed.
2. Estimate minimum action phases/duration from structured prompt.
3. Inspect a small neighborhood around playhead.
4. Expand backward/forward only until preparation, action, recovery, and handles fit.
5. Stop at shot cut, target loss, source boundary, or sufficient evidence.
6. If sufficient duration cannot be found, ask for range adjustment or use a provider-compatible fallback.

This is usually cheaper than discovering complete occurrence bounds. Complete occurrence tracking is required only when prompt semantics make whole occurrence relevant.

## Stage 2: find natural occurrence spans

### Local scope

Start from bbox and playhead:

1. Segment target at playhead using SAM.
2. Classify whether prompt needs one action subrange or complete current occurrence.
3. For a one-time action, track backward/forward lazily only until action phases, recovery, and context handles fit.
4. For a persistent change, track complete current occurrence at video frame rate or a sufficiently dense tracking rate.
5. Stop or split on:
   - shot cut;
   - target lost beyond an occlusion tolerance;
   - identity confidence below threshold;
   - a different instance replacing the target;
   - source clip/timeline boundary.
6. Produce either bounded local action track or complete occurrence containing playhead, according to prompt semantics.

### Global scope

Use a two-pass search:

1. **Coarse recall:** CLIP/VLM/keyframes find candidate regions across reel.
2. **Dense confirmation:** initialize tracker around each candidate and track both directions to obtain frame-accurate occurrence bounds.
3. Merge duplicate/overlapping tracks for same entity within same shot.
4. Keep disjoint appearances as separate occurrences.

Current one-frame-per-second VLM hits are sufficient only for candidate discovery. They are not accurate edit boundaries.

### Selecting necessary occurrence amount

Occurrence duration does not automatically equal edit duration.

- Persistent appearance change (“make shirt red while visible”): edit core normally covers complete occurrence.
- One-time motion (“make them jump once”): choose enough time around playhead for preparation, action, landing, and recovery; do not modify entire occurrence.
- Removal/replacement: cover frames where target is present, with occlusion-aware tracking.
- Explicit range: honor range, but warn if it cuts through required action phase or shot boundary.

Natural shot and motion boundaries are preferred over fixed seconds.

## Stage 3: choose adaptive handles

Handles provide temporal evidence and a safe place for generated motion to settle. Their lengths must be adaptive.

Inputs:

- subject velocity and acceleration near core boundaries;
- camera motion;
- shot boundaries;
- occlusions;
- prompt action phases;
- provider maximum duration;
- provider conditioning capabilities;
- available unchanged footage around core.

Guidelines:

- include enough frames to estimate pose and velocity on both sides;
- extend post-handle through action landing/recovery when prompt changes motion;
- never cross a hard shot cut merely to reach a target duration;
- handles should remain unedited by prompt contract;
- if provider cannot fit core plus adequate handles, chunk the occurrence;
- a fixed minimum/maximum may be used as a safety clamp, not as primary window selection.

## Stage 4: build provider-neutral conditioning

Every generation chunk should carry separate inputs:

- padded source video;
- edit-core offsets relative to padded clip;
- full-frame start boundary anchor;
- full-frame end boundary anchor;
- target reference from playhead;
- tracked masks across frames when supported;
- original motion summaries before and after core;
- prior accepted style/identity reference for global propagation;
- prompt instructing provider to preserve handles exactly.

Provider adapter decides what can actually be sent:

- **Wan tracked local edit:** preferred for short padded chunks that fit tracked-mask limit.
- **Wan video edit:** preferred when padded chunk exceeds mask limit but fits Wan source-video limit.
- **HappyHorse video edit:** source-video fallback for longer chunks.
- **Veo first/last frame:** fallback for supported eight-second interpolation jobs, not preferred for source edits because it receives still endpoints rather than source motion.
- **Mesh API Veo:** image-conditioned fallback; lowest expected temporal fidelity for this use case.

Never pass subject crop as a first boundary frame. Boundary frame, subject reference, style reference, and mask are different fields.

## Stage 5: generate one occurrence when it fits

For a single provider-compatible occurrence:

1. Extract padded source range `[context_start, context_end]`.
2. Compute edit-core offsets inside padded clip.
3. Send source, target conditioning, and preservation contract.
4. Generate padded output.
5. Validate handles against original footage.
6. Validate prompt adherence inside core.
7. If valid, use generated padded output only according to commit policy below.

### Commit policy

Capability-gated policy for localized masked edits:

- when generated target can be segmented/tracked reliably, composite generated target region over original source;
- keep original pixels outside tracked mask;
- spatially feather mask edge without temporally crossfading whole frames.

Do not composite a changed pose using only original-source masks. A jump can move limbs outside source mask, causing clipping. If output-mask confidence is low, keep provider-native localized output and rely on handle preservation/validation.

Fallback policy for full-frame/provider outputs:

- commit padded output only when both handles pass strict preservation and motion checks;
- otherwise retry or reject;
- do not silently replace surrounding footage that changed unexpectedly.

## Stage 6: chunk long occurrences

Chunk only when core plus adequate handles exceeds chosen provider limit.

### Chunk planning

1. Prefer shot cuts, occlusion gaps, low-motion moments, or stable poses as seams.
2. Keep each provider request within its total duration limit, including handles.
3. Give every chunk pre-roll and post-roll context.
4. Make edit cores non-overlapping where possible.
5. Allow generation-context overlap between adjacent chunks.
6. Use same identity/style reference and same structured prompt plan across chunks.

Example:

```text
Occurrence: 0---------------------------------------------20s

Chunk A context: 0-------------8
Chunk A core:       1-------7

Chunk B context:             6-------------14
Chunk B core:                 7---------13

Chunk C context:                         12-------------20
Chunk C core:                              13--------19
```

### Important merge rule

Do not independently generate chunks and blindly crossfade them. Two different generated bodies or backgrounds blended together produce ghosting.

Preferred merge order:

1. Generate first chunk.
2. Validate it.
3. Derive next chunk's starting identity/pose anchor from accepted preceding output when provider supports it.
4. Still provide original source context and tracked masks for next chunk.
5. Compare both candidate outputs across overlap.
6. Select a seam at the best matching frame/pose/flow point.
7. Use one side of overlap at seam; do not average mismatched frames.
8. Regenerate downstream chunk if no valid seam exists.

For mask-composited edits, merge tracked masks and keep original background throughout. This greatly reduces inter-chunk drift.

## Stage 7: validate before acceptance

Current generation scoring samples interior frames. New validation must include surrounding original footage and every seam.

### Technical checks

- exact duration;
- fps, resolution, pixel format, and timestamps;
- no missing/frozen tail frames;
- audio duration and sync.

### Handle preservation checks

- perceptual image difference outside target mask;
- target identity consistency;
- background/camera transform consistency;
- lighting/color difference;
- unexpected edits in handles.

### Boundary motion checks

Compare several frames, not one still:

- subject keypoint position and velocity;
- optical-flow direction and magnitude;
- camera global motion;
- target scale and trajectory;
- gait/action phase;
- occlusion ordering.

Measure:

```text
original pre-handle -> generated core
generated core -> original post-handle
generated chunk A -> generated chunk B
```

### Decision policy

- Pass: expose variant for acceptance.
- Soft fail: retry with corrective boundary instructions.
- Provider limitation: route to source-video-capable provider or use smaller chunks.
- Hard fail after retry budget: show continuity warning and do not auto-apply.

No generated segment should be accepted automatically solely because prompt adherence is high.

## Stage 8: multiple occurrences

For “everywhere” scope:

1. Present detected occurrence list and confidence before generation.
2. Let user exclude false matches when useful.
3. Generate one reference occurrence first.
4. User accepts identity/style result.
5. Apply accepted style/reference to remaining occurrences.
6. Plan handles and chunks independently per occurrence because shot and motion differ.
7. Validate each occurrence independently.
8. Apply successful results selectively; never fail whole reel because one occurrence fails.

Current propagation already models separate appearance results, but its one-second keyframe ranges must be replaced by dense tracked occurrence ranges before it can drive final-quality edits.

## Data and job model changes

Add persistent planning entities or equivalent job payloads:

- `EditIntent`
  - raw prompt;
  - structured scope;
  - target identity;
  - action phases;
  - preservation requirements.
- `OccurrenceTrack`
  - entity/track ID;
  - shot ID;
  - frame-accurate start/end;
  - confidence;
  - masks/boxes or references to tracking artifacts.
- `GenerationChunk`
  - edit core;
  - generation context;
  - boundary anchors;
  - provider and capability decision;
  - dependency on preceding chunk;
  - validation status and metrics.
- `BoundaryValidation`
  - seam timestamps;
  - visual, motion, camera, and identity scores;
  - retry reason;
  - accepted/rejected result.

Timeline segment range must reflect committed media range, not merely requested prompt range.

## API and UI changes

### Planning API

Add a non-generating operation that returns:

- interpreted scope;
- detected occurrences;
- proposed edit cores;
- context ranges;
- chunk plan;
- selected provider and reason;
- estimated generation count/cost;
- warnings and low-confidence decisions.

### Generation API

Generation should accept plan/chunk IDs instead of recomputing ranges independently. This prevents frontend, agent, and worker range logic from drifting.

### Editor UI

Show distinct timeline overlays:

- target occurrence;
- edit core;
- context handles;
- chunk boundaries;
- validation status.

Allow user to switch:

- this occurrence;
- selected occurrences;
- all occurrences;
- explicit time range.

Do not hide provider duration errors behind a fixed three-second frontend default.

## Delivery plan: three feature pull requests plus cleanup

Two feature PRs are technically possible, but the first would combine selection persistence, new video tracking, schemas, planning API, provider routing, generation behavior, validation, timeline semantics, and frontend UX. That is too much causal surface for one reliable review.

Recommended stack:

1. PR 1 establishes reusable selection and adaptive local planning without changing accepted media semantics.
2. PR 2 changes local generation/acceptance and delivers the visible seam fix.
3. PR 3 adds explicit global occurrences and long-range chunk orchestration.
4. Final cleanup PR reorganizes code/docs/scripts after behavior stabilizes.

Each commit should remain reviewable, tested, and safe to revert. Feature flags keep partially merged foundations inactive until their consuming PR lands.

### Pull Request 1: selection artifacts and adaptive local planning

**Outcome:** “make this person jump” reuses bbox/SAM selection, examines only necessary local footage, produces an inspectable core/context plan, and performs no full-reel search.

#### Commit 1 — add baseline range and seam regression fixtures

- Add walking, camera-pan, fast-motion, occlusion, near-edge, and shot-cut fixtures.
- Capture current fixed-window behavior in tests.
- Add deterministic tests for timeline/source timestamp conversion and replacement ranges.
- Add seam metric test helpers without changing production flow.

#### Commit 2 — persist reusable bbox/SAM selection artifacts

- Store seed source timestamp, bbox, mask URL, contour, SAM score, crop/reference, source revision, and optional identified entity.
- Reuse live preview SAM result in planning/generation instead of recomputing it.
- Lock selection to seed timestamp; explicit reselection/invalidation on source change.
- Add artifact ownership, cache-key, stale-source, and cleanup tests.

#### Commit 3 — model intent, core, context, and provider capabilities explicitly

- Add structured `EditIntent`, `EditCore`, `GenerationContext`, and boundary-anchor schemas.
- Extend provider capabilities with total-duration, mask-duration, source-video, first/last-frame, and reference-image fields.
- Keep old API fields temporarily through compatibility mapping.
- Add schema and provider-routing unit tests.

#### Commit 4 — add cost-aware scope gate and prompt planner contract

- Classify explicit range, local action, persistent local change, selected occurrences, and global scope.
- Default ambiguous prompts to local.
- Reuse agent/planner interpretation instead of duplicate LLM calls.
- Make post-accept full-reel entity discovery lazy for local scope; preserve it behind explicit “find other occurrences” action.
- Return estimated analysis work, generation calls, and generated seconds.
- Add prompt-scope fixtures including “jump,” “change shirt while visible,” and “everywhere.”

#### Commit 5 — add SAM2 video-tracking capability

- Add bounded video-tracking endpoint to vision worker, distinct from existing image segmentation.
- Seed from persisted selection mask/bbox.
- Return per-frame boxes/masks, confidence, lost/occluded states, and processed range.
- Support cancellation and maximum-frame/time budgets.
- Add CPU/stub fallback and deterministic tracker contract tests.

#### Commit 6 — implement lazy local range resolution

- Detect nearby shot boundaries using inexpensive frame-change analysis.
- Start from playhead bbox/SAM target.
- Track only until action phases, recovery, and handles fit.
- Track complete current occurrence only for persistent-change semantics.
- Cache shot and local tracking artifacts.
- Add tests proving local jump does not trigger global keyframe/entity search.

#### Commit 7 — expose non-generating edit plan API

- Add plan endpoint returning scope, core, context, provider choice, duration, confidence, and warnings.
- Persist plan/chunk payload so worker does not recompute ranges differently.
- Validate provider constraints before paid generation.
- Add API authorization, ownership, invalid-range, and low-confidence tests.

#### Commit 8 — add frontend plan preview and unify agent/direct planning

- Display selection, edit core, context handles, confidence, provider, and cost estimate.
- Let user adjust proposed local range before generation.
- Make direct editor and agent submit same plan ID.
- Remove agent full-timeline default conflict.
- Keep current fixed-range generation behind feature flag until PR 2.

### Pull Request 2: context-aware local generation and seam validation

**Outcome:** planned local edits generate with protected two-sided context, use correct boundary inputs, validate motion at both seams, and commit one authoritative backend range.

#### Commit 1 — separate subject reference from boundary anchors

- Introduce distinct subject-reference timestamp/image/mask fields.
- Extract full-frame start and end anchors from actual context boundaries.
- Stop calling playhead crop a first frame.
- Route Veo first/last inputs only from full boundary frames.
- Add adapter tests for Wan, HappyHorse, Veo, and Mesh API Veo payloads.

#### Commit 2 — generate padded local clips with protected handles

- Extract `[context_start, context_end]`, not only edit core.
- Express allowed edit offsets relative to padded clip.
- Prompt source-edit providers to preserve pre/post handles.
- Prefer Wan tracked-mask path when full padded duration fits; otherwise choose source-video fallback using capabilities.
- Conform duration/audio without losing context alignment.
- Add worker tests for video-start/end clamping and provider fallback.

#### Commit 3 — add multi-frame boundary and handle validator

- Reuse existing scoring/color utilities as components.
- Compare pose, optical flow, camera motion, identity, background, and color over several frames.
- Validate pre-handle, post-handle, protected pixels, duration, fps, and frozen tails.
- Add deterministic scorer fixtures and thresholds.

#### Commit 4 — add corrective retry and provider fallback policy

- Add pass, corrective retry, provider fallback, and hard-fail states.
- Retry with exact boundary failure evidence.
- Cap retries/generated seconds.
- Prevent acceptance of hard-failed variants unless explicit override policy allows it.

#### Commit 5 — add capability-gated localized compositing

- Segment/track generated target before compositing; never use source mask alone for changed pose.
- Composite only when output-mask confidence passes threshold.
- Keep original background/camera/audio where possible.
- Fall back to provider-native localized output when compositing is unsafe.
- Add clipping, halo, limb-motion, and mask-loss fixtures.

#### Commit 6 — make backend acceptance range authoritative

- Persist requested core separately from generated context and committed media range.
- Return authoritative timeline segments after acceptance.
- Rehydrate frontend EDL instead of duplicating optimistic split math.
- Add no-gap/no-overlap preview, reopen, timeline, and export tests.

#### Commit 7 — switch frontend generation to planned local flow

- Remove fixed three-second window as authoritative selection.
- Show validation/retry/provider-fallback state.
- Make provider selection/duration errors visible before submission.
- Preserve explicit time-range workflow.

#### Commit 8 — add local-flow telemetry and rollout controls

- Emit scope, analysis duration, generated seconds, provider, retries, and seam scores.
- Add feature flag and fixed-range fallback for rollout.
- Compare local planned flow against fixed-window baseline.
- Add end-to-end local quality suite.

#### Pre-merge task — raise and link user-visible continuity issue

Create one GitHub issue after PR 2 implementation and review are complete, but before merging it.

Suggested title:

> Eliminate visible motion seams at local AI edit boundaries

Issue must document:

- reproduction: accept a localized generated edit inside a longer source clip and play across both boundaries;
- observed result: visible jump, especially where generated clip returns to original footage;
- root cause: exact-window generation without outside motion context, incorrect/weak endpoint conditioning, and no multi-frame seam gate;
- expected result: adaptive core/context planning, correct boundary anchors, protected handles, motion-aware validation, and authoritative committed range;
- acceptance criteria tied to PR 2 regression fixtures and seam thresholds;
- explicit note that global occurrence search/chunking remains PR 3 scope.

Then:

1. Add issue number to PR 2 description using `Closes #<issue-number>`.
2. Confirm PR 2 checks and review remain green after description update.
3. Merge PR 2.
4. Verify GitHub automatically closes issue and links merged PR.

Do not create issue for PR 1: it is internal foundation and does not independently resolve user-visible bug. Do not defer this issue to PR 3: local seam bug should be closed once PR 2 solves it.

### Pull Request 3: global occurrences and long-range chunk orchestration

**Outcome:** explicit “everywhere” edits find all target appearances, refine them into frame-accurate tracks, split only oversized occurrences, condition chunks sequentially, and apply successful results independently.

#### Commit 1 — add cached coarse occurrence discovery

- Reuse project keyframes and embeddings.
- Run CLIP/VLM candidate retrieval only for explicit global/selected-occurrence scope.
- Store candidate timestamp, confidence, and identity evidence.
- Add false-positive, duplicate, and cache-hit tests.

#### Commit 2 — refine candidates into dense occurrence tracks

- Seed SAM/tracker around each candidate.
- Track backward/forward to shot cut, target loss, or identity threshold.
- Merge duplicate candidate tracks inside same shot.
- Persist frame-accurate occurrence bounds and tracking artifacts.
- Add occlusion, re-entry, multiple-instance, and shot-cut tests.

#### Commit 3 — add occurrence selection and reference-first workflow

- Expose detected occurrences with confidence and generation estimate.
- Support this occurrence, selected occurrences, and all occurrences.
- Generate one reference occurrence first and require acceptance before fan-out.
- Keep failed/unchecked occurrences original.
- Add API and UI state tests.

#### Commit 4 — implement provider-aware long-range chunk planner

- Split only when core plus adequate handles exceeds provider limit.
- Prefer shot cuts, occlusion gaps, low-motion states, and stable poses.
- Create non-overlapping edit cores with overlapping generation contexts.
- Guarantee intended-frame coverage without duplicate committed frames.
- Add property-style tests across durations and provider constraints.

#### Commit 5 — add sequential chunk dependencies and conditioning

- Generate and validate chunk N before chunk N+1.
- Carry accepted identity/style and ending pose anchor forward where supported.
- Retain original source context and tracked masks for every chunk.
- Retry only failed chunk and its dependent successors.
- Add resume/idempotency and partial-failure tests.

#### Commit 6 — select seams from overlap without blind crossfade

- Compare adjacent outputs across overlapping contexts.
- Choose cut at best pose/flow/camera match.
- Use one output at seam rather than averaging incompatible frames.
- Regenerate downstream chunk when no seam passes threshold.
- Add ghosting and inter-chunk continuity fixtures.

#### Commit 7 — upgrade propagation to occurrence-specific planned jobs

- Replace one-second keyframe hit ranges with dense occurrence tracks.
- Plan handles/provider/chunks independently per occurrence.
- Reuse accepted reference style while preserving shot-specific motion.
- Apply successful results selectively and retain originals for failures.
- Add multi-occurrence timeline conflict tests.

#### Commit 8 — add global progress, review, and selective apply UI

- Show per-occurrence and per-chunk status, confidence, cost, and seam scores.
- Allow exclude, retry, apply-one, and apply-all-passing actions.
- Warn without blocking export when failed occurrence remains original.
- Add interrupted-session recovery tests.

#### Commit 9 — complete end-to-end quality suite and rollout controls

- Run local, global, long-track, occlusion, shot-cut, and provider-fallback scenarios.
- Compare latency, token use, generated seconds, retries, and seam quality against PR 2 local baseline.
- Add concurrency/cost caps for global fan-out.
- Document operational thresholds and rollback path.

### Pull Request 4: cleanup, repository organization, and documentation

Run only after behavioral PRs stabilize.

- Remove feature-flagged fixed-window path and compatibility fields after rollout confidence.
- Consolidate duplicated planner/generation helpers and dead bridge code.
- Keep canonical application code under `apps/api`, `apps/web`, and `apps/api/vision-worker`.
- Move maintained product/architecture/runbook material into `docs/`.
- Move executable operational/development utilities into `scripts/`.
- Update imports, Docker/CI references, ownership files, README, plugin references, and links atomically.
- Avoid mixing new behavior into this PR; tests should prove organization-only changes preserve functionality.

## Tests required

### Unit tests

- prompt scope parsing;
- occurrence-to-core selection;
- handle clamping at video and shot boundaries;
- provider-duration planning;
- chunk coverage without gaps or duplicate committed frames;
- source/timeline timestamp conversion;
- first/last/subject-reference separation.

### Integration tests

- local edit with one occurrence;
- global edit with several disjoint occurrences;
- long occurrence split across provider limits;
- bbox target temporarily occluded;
- edit near video start/end;
- shot cut near playhead;
- provider fallback;
- failed middle chunk retry;
- no source URL available;
- timeline accept, reopen, preview, and export consistency.

### Quality regression tests

- pose continuity at both boundaries;
- velocity continuity at both boundaries;
- camera-motion continuity;
- background preservation outside mask;
- no crossfade ghosting;
- consistent identity across chunks and occurrences.

## Decisions adopted by this plan

1. Default unspecified scope is local, not global.
2. Natural occurrence/action boundaries replace fixed three-second selection.
3. Handles exist on both sides and are adaptive.
4. Edit core and generation context remain distinct.
5. Subject reference and boundary anchors remain distinct.
6. Source-video editing is preferred over still-image generation.
7. Mask compositing is capability-gated and used only when generated-target tracking is reliable.
8. Chunking is provider-aware and sequentially validated.
9. Crossfade is not the default seam fix.
10. Boundary validation happens before acceptance.
11. Global propagation is occurrence-specific and selectively applicable.
12. Full-video generation is only an optimization for short, compatible cases—not the default architecture.

## Open product decisions

These do not block architecture, but should be decided before final UX implementation:

1. Should low-confidence occurrence plans require explicit confirmation, or only display a warning?
2. What maximum automatic retry count and cost budget should one user edit receive?
3. Should “everywhere” generation run all occurrences immediately, or generate one reference occurrence and require acceptance before fan-out? This plan recommends reference-first.
4. When one global occurrence fails validation, should export remain allowed with a visible warning? This plan recommends yes, while keeping failed occurrence original.
5. Should users see advanced core/context/chunk controls by default, or only through an expandable planning view?

## Success criteria

- No fixed default edit duration controls normal local edits.
- Preview bbox/SAM work is persisted and reused instead of recomputed.
- Every generated request has explicit core and context ranges.
- Both boundaries receive motion-aware validation.
- Provider constraints are resolved during planning, before paid generation.
- Long tracks cover intended frames without gaps or accidental duplicate replacement.
- Unedited pixels remain original whenever localized compositing is available.
- Global edits expose per-occurrence results and failures.
- Timeline preview and export use identical committed ranges.

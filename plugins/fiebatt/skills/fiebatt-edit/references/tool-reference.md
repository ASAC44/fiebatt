# Fiebatt tool reference

## Read operations

- `account_status`: authentication, provider readiness, and settings URL.
- `list_projects`: projects owned by the authenticated user.
- `get_project`: source media, timeline, and known entities.
- `get_job_status`: generation status and result variants.
- `get_export_status`: export status and the finished export URL.
- `get_timeline`: current ordered timeline.
- `preview_frame` and `preview_strip`: visual inspection URLs.
- `list_entities`: known entities and their appearances.

## Upload

- `prepare_upload(filename, content_type, size_bytes)` returns an upload ID plus a short-lived HTTP upload request.
- `complete_upload(upload_id)` verifies the stored object, probes the media, and creates a project.

Never reuse an expired upload request. A failed completion does not imply the upload request is still valid.

## Editing

- `analyze_video(project_id, fps)` returns structured scenes, entities, and suggestions.
- `identify_region(project_id, frame_ts, bbox)` confirms the selected subject.
- `generate_edit(project_id, start_ts, end_ts, bbox, prompt, provider)` starts an asynchronous generation job.
- `score_variant(variant_id)` evaluates a completed variant.
- `score_continuity(project_id, variant_id)` checks how an edit fits its neighboring shots.
- `accept_variant(job_id, variant_index)` mutates the timeline.
- `remix_variant(variant_id, prompt)` starts a refinement.
- `snapshot_timeline(project_id)` returns the rollback snapshot ID.
- `revert_timeline(project_id, snapshot_id)` restores that snapshot.
- `propagate_edit(entity_id, source_variant_url, prompt, auto_apply)` starts continuity work.
- `export_video(project_id)` starts an export job.

Bounding boxes use normalized `x`, `y`, `w`, and `h` values from 0 through 1. Generation ranges must follow limits returned by the service.

## Job states

Treat `pending` and `processing` as non-terminal. Treat `done` as successful and `error` as failed. Surface warnings, provider, model, and per-variant errors from the job result.

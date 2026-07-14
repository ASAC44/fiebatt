---
name: fiebatt-edit
description: Analyze and edit videos with the hosted Fiebatt service, including uploads, scene analysis, localized generation, variant comparison, timeline operations, continuity propagation, narration, previews, and export. Use when a user asks to edit, change, inspect, remix, grade, or export a video, or to continue an existing Fiebatt project.
---

# Fiebatt video editing

Use the `fiebatt` MCP tools. Do not require the local Fiebatt CLI or a repository checkout.

## Prepare

1. Call `account_status` before the first operation.
2. If authentication is required, tell the user to complete the browser sign-in opened by Codex.
3. If provider setup is incomplete, direct the user to the returned HTTPS settings URL. Never ask the user to paste provider keys into chat.
4. For an existing project, call `list_projects` and confirm the intended project when the choice is ambiguous.

## Upload a local video

1. Inspect the local file and reject unsupported or clearly oversized inputs before uploading.
2. Call `prepare_upload` with the filename, MIME type, and byte size.
3. Upload the file to the returned short-lived URL with the exact HTTP method and headers in the response. Do not add Fiebatt credentials to that request.
4. Call `complete_upload` with the upload ID.
5. Treat the returned project ID as the active project.

## Edit workflow

1. Call `analyze_video` when the user needs scene understanding or edit suggestions.
2. Summarize scenes, entities, and suggested edit windows. Ask the user to choose when intent is not already precise.
3. Call `snapshot_timeline` before the first timeline mutation.
4. For localized edits, inspect a preview and identify the target region before generation.
5. Confirm the prompt, time range, region, and provider before calling `generate_edit`. Generation can incur charges against the user's provider account.
6. Poll `get_job_status` until it reaches `done` or `error`; do not repeatedly submit equivalent generation jobs.
7. Present variant URLs and scores. Recommend a result, but call `accept_variant` only after the user chooses.
8. Prefer `remix_variant` when a result is close and needs refinement.
9. Preview the affected range and inspect continuity before export.
10. Offer propagation when the edited entity appears elsewhere. Call propagation tools only after confirmation.
11. Call `export_video` only after the user approves the final timeline, then poll `get_export_status` with the returned job ID.

## Safety and recovery

- Treat generation, accept, remix, grade, timeline mutation, propagation, narration, and export as mutating operations.
- Never expose OAuth tokens, signed upload URLs after use, provider credentials, or internal storage keys.
- Keep all project and job identifiers from tool results; never invent identifiers.
- On a failed mutation, report the tool error and current project state before retrying.
- Use `revert_timeline` with the saved snapshot only after explicit confirmation.
- Do not claim an edit or export succeeded until its job reports a terminal successful status.

Read [tool-reference.md](references/tool-reference.md) when selecting tool arguments or handling job states. Read [troubleshooting.md](references/troubleshooting.md) when authentication, provider setup, uploads, or generation fail.

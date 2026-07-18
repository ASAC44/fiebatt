/**
 * thin wrapper around backend fetch. calls carry:
 *   - X-Session-Id: anonymous per-browser id for the backend Session row
 *   - Authorization: Bearer <jwt access token> if the user is signed in
 */

import { redirectToLogin } from "@/lib/auth";

const SESSION_KEY = "fiebatt.session_id";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public detail?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function getSessionId(): string {
  let sid = localStorage.getItem(SESSION_KEY);
  if (!sid) {
    sid = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, sid);
  }
  return sid;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Session-Id", getSessionId());
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(path, { ...init, headers, credentials: "include" });
  if (res.status === 401) {
    redirectToLogin();
    throw new Error("401 Unauthorized");
  }
  if (!res.ok) {
    const payload = await res.json().catch(() => null) as {
      detail?: string | Record<string, unknown>;
    } | null;
    const detail = payload?.detail;
    if (detail && typeof detail === "object") {
      const message = typeof detail.message === "string"
        ? detail.message
        : `${res.status} ${res.statusText}`;
      throw new ApiError(
        res.status,
        message,
        typeof detail.code === "string" ? detail.code : undefined,
        detail,
      );
    }
    throw new ApiError(
      res.status,
      typeof detail === "string" ? detail : `${res.status} ${res.statusText}`,
    );
  }
  return res.json();
}

// ─── types (subset, matches backend schemas) ──────────────────────────

export type BBox = { x: number; y: number; w: number; h: number };

export type UploadResp = {
  project_id: string;
  video_url: string;
  duration: number;
  fps: number;
  width?: number;
  height?: number;
};

export type UploadResponse = UploadResp;

export type Me = {
  session_id: string;
  user_id: string | null;
  email: string | null;
  signed_in: boolean;
};

export type AuthResponse = {
  access_token: string;
  token_type: "bearer";
  user: {
    id: string;
    email: string;
  };
};

export type ProjectListItem = {
  project_id: string;
  name: string;
  video_url: string;
  duration: number;
  fps: number;
  width: number;
  height: number;
  created_at: string;
};

export type JobStatus = "pending" | "processing" | "done" | "error";

export type Variant = {
  id: string;
  index: number;
  status: JobStatus;
  url: string | null;
  description: string | null;
  visual_coherence: number | null;
  prompt_adherence: number | null;
  error: string | null;
};

export type JobResp = {
  job_id: string;
  kind?: string;
  status: JobStatus;
  variants: Variant[];
  error: string | null;
  created_at?: string | null;
  accepted?: boolean;
  /** authoritative edit window accepted by the backend */
  start_ts: number | null;
  end_ts: number | null;
  provider?: string | null;
  model?: string | null;
  edit_mode?: string | null;
  warnings?: string[];
  execution_window?: GenerationExecutionWindow | null;
  continuity_validation?: ContinuityValidation | null;
  selected_seams?: {
    passed: boolean;
    media_start: number;
    media_end: number;
    timeline_start: number;
    timeline_end: number;
    entry?: { score: number; source_timestamp: number; media_timestamp: number } | null;
    exit?: { score: number; source_timestamp: number; media_timestamp: number } | null;
  } | null;
  generation_quality_state?: string | null;
  generation_quality_evidence?: string[];
  generation_attempts?: number | null;
  generated_seconds?: number | null;
  provider_attempts?: string[];
  localized_compositing?: Array<{ applied?: boolean; reason?: string }>;
  local_flow_telemetry?: Record<string, unknown> | null;
};

export type GenerationExecutionWindow = {
  adaptive: boolean;
  core_start: number;
  core_end: number;
  context_start: number;
  context_end: number;
  edit_start_offset: number;
  edit_end_offset: number;
  pre_handle: number;
  post_handle: number;
};

export type ContinuityValidation = {
  passed: boolean;
  sampled_frames: number;
  metrics: Record<string, number | null>;
  issues: Array<{
    code: string;
    value: number;
    threshold: number;
    boundary: string | null;
  }>;
};

export type JobResponse = JobResp;


export type GenerateReq = {
  project_id: string;
  target_clip_id?: string;
  plan_id?: string;
  start_ts: number;
  end_ts: number;
  bbox: BBox;
  prompt: string;
  reference_frame_ts: number;
};

export type GenerateRequest = GenerateReq;

export type EditPlanResp = {
  plan_id: string;
  project_id: string;
  selection_id: string;
  scope: "local" | "explicit_range" | "selected_occurrences" | "all_occurrences";
  intent: {
    raw_prompt: string;
    change_type: "appearance" | "removal" | "replacement" | "motion" | "scene";
    duration_policy: "bounded_action" | "continuous_occurrence" | "trajectory_continuation" | "explicit_range" | "all_occurrences";
    temporal_behavior: "temporary" | "persistent_state" | "future_changing_motion";
    action_phases: string[];
    estimated_action_seconds: number;
    requires_recovery_motion: boolean;
  };
  edit_core: { start_ts: number; end_ts: number };
  generation_context: {
    start_ts: number;
    end_ts: number;
    edit_core: { start_ts: number; end_ts: number };
  };
  occurrence_start: number;
  occurrence_end: number;
  provider: string;
  provider_reason: string;
  estimate: {
    analysis_mode: string;
    analysis_duration_ms: number;
    frames_inspected: number;
    expected_generation_calls: number;
    expected_generated_seconds: number;
    requires_global_discovery: boolean;
  };
  confidence: number;
  warnings: string[];
  status: string;
  adaptive_generation_enabled: boolean;
};

export type HealthResp = {
  ok: boolean;
  features?: {
    adaptive_edit_planning?: boolean;
    global_edit_planning?: boolean;
    hard_failed_acceptance_override?: boolean;
  };
};

export type CreateEditPlanReq = {
  project_id: string;
  selection_id: string;
  prompt: string;
  explicit_start_ts?: number;
  explicit_end_ts?: number;
  source_start_ts?: number;
  source_end_ts?: number;
};

export type AcceptResp = {
  segment_id: string;
  entity_job_id: string | null;
  timeline: TimelineResp;
};

export type AcceptResponse = AcceptResp;

export type ProjectEntitySummary = {
  id: string;
  description: string;
  category: string | null;
  appearance_count: number;
};

export type ProjectSegment = {
  id: string;
  start_ts: number;
  end_ts: number;
  source: "original" | "generated";
  url: string;
  variant_id: string | null;
  order_index: number;
};

export type ProjectResp = {
  project_id: string;
  name: string;
  video_url: string;
  duration: number;
  fps: number;
  width: number;
  height: number;
  segments: ProjectSegment[];
  entities: ProjectEntitySummary[];
};

export type ProjectDetail = ProjectResp;

export type TimelineSegment = {
  start_ts: number;
  end_ts: number;
  source: "original" | "generated";
  url: string;
  audio: boolean;
  segment_id?: string | null;
  media_start_ts?: number;
  media_end_ts?: number;
  media_duration?: number;
};

/** One clip in a saved EDL snapshot. Mirrors the frontend's Clip shape
 * exactly — we round-trip them through the backend without re-shaping. */
export type PersistedClip = {
  id: string;
  kind: "source" | "generated";
  url: string;
  source_start: number;
  source_end: number;
  media_duration: number;
  volume: number;
  label?: string | null;
  project_id?: string | null;
  source_asset_id?: string | null;
  generated_from_clip_id?: string | null;
};

export type PersistedAsset = {
  id: string;
  kind: "source" | "generated";
  url: string;
  duration: number;
  fps: number;
  project_id: string;
  label: string;
};

export type PersistedEDL = {
  clips: PersistedClip[];
  sources: PersistedAsset[];
  /** epoch seconds, stamped by the server on save. */
  updated_at?: number | null;
};

export type TimelineResp = {
  project_id: string;
  duration: number;
  segments: TimelineSegment[];
  /** When present, client should use this instead of `segments` — it's the
   * exact EDL the user had on screen at their last save. Null on reels
   * that were never manually edited. */
  edl: PersistedEDL | null;
};

export type TimelineSaveResp = {
  project_id: string;
  updated_at: number;
};

export type MaskResp = {
  contour: [number, number][]; // normalized 0-1 points forming the mask outline
  contours?: [number, number][][]; // disconnected subject components
  selection_id?: string | null;
  mask_url?: string | null;
  subject_reference_url?: string | null;
  score?: number | null;
};

export type IdentifyResp = {
  description: string;    // "silver sedan car"
  category: string;       // "vehicle"
  attributes: Record<string, string>;  // { color: "silver", type: "sedan" }
  mask?: { contour: [number, number][] };  // SAM mask if GPU available
};

export type AppearanceResp = {
  id: string;
  segment_id: string | null;
  start_ts: number;
  end_ts: number;
  keyframe_url: string | null;
  confidence: number;
};

export type EntityResp = {
  entity_id: string;
  description: string;
  category: string | null;
  reference_crop_url: string | null;
  appearances: AppearanceResp[];
};

export type DiscoveryJobResp = {
  job_id: string;
  reused: boolean;
};

export type PropagateReq =
  | {
      global_plan_id: string;
    }
  | {
      entity_id: string;
      source_variant_url: string;
      prompt: string;
      auto_apply?: boolean;
    };

export type PropagateResp = {
  propagation_job_id: string;
  global_plan_id?: string | null;
};

export type PropagationResultResp = {
  id: string;
  appearance_id: string;
  segment_id: string | null;
  variant_url: string | null;
  status: JobStatus;
  applied: boolean;
};

export type PropagationStatusResp = {
  propagation_job_id: string;
  status: JobStatus;
  error: string | null;
  results: PropagationResultResp[];
};

export type GlobalEditChunkResp = {
  chunk_id: string;
  index: number;
  edit_start: number;
  edit_end: number;
  context_start: number;
  context_end: number;
  provider: string;
  split_reason: string;
  status: string;
  attempts: number;
  output_url: string | null;
  error: string | null;
};

export type GlobalEditOccurrenceResp = {
  appearance_id: string;
  start_ts: number;
  end_ts: number;
  confidence: number;
  status: string;
  output_url: string | null;
  error: string | null;
  chunks: GlobalEditChunkResp[];
};

export type GlobalEditPlanResp = {
  plan_id: string;
  project_id: string;
  entity_id: string;
  reference_segment_id: string;
  scope: "selected_occurrences" | "all_occurrences";
  requested_provider: string;
  prompt: string;
  occurrences: GlobalEditOccurrenceResp[];
  estimate: {
    occurrence_count: number;
    expected_generation_calls: number;
    expected_generated_seconds: number;
    mean_track_confidence: number;
    reference_accepted: boolean;
  };
  status: string;
};

export type GlobalEditApplyResp = {
  plan_id: string;
  segment_ids: string[];
  timeline: TimelineResp;
};

// ─── endpoints ────────────────────────────────────────────────────────

export function me(): Promise<Me> {
  return request<Me>("/api/me");
}

export function signup(email: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>("/api/auth/signup", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function login(email: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function logout(): Promise<void> {
  const res = await fetch("/api/auth/logout", {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok && res.status !== 204) throw new Error("Could not log out.");
}

export function listProjects(): Promise<ProjectListItem[]> {
  return request<ProjectListItem[]>("/api/projects");
}

export function getHealth(): Promise<HealthResp> {
  return request<HealthResp>("/api/health");
}

export function getProject(project_id: string): Promise<ProjectResp> {
  return request<ProjectResp>(`/api/projects/${project_id}`);
}

export function deleteProject(project_id: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/api/projects/${project_id}`, { method: "DELETE" });
}

export function updateProject(project_id: string, name: string): Promise<ProjectListItem> {
  return request<ProjectListItem>(`/api/projects/${project_id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export async function upload(file: File): Promise<UploadResp> {
  const fd = new FormData();
  fd.append("file", file);
  return request<UploadResp>("/api/upload", { method: "POST", body: fd });
}

export const uploadProject = upload;

export function generate(req: GenerateReq): Promise<{ job_id: string }> {
  return request("/api/generate", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function createEditPlan(req: CreateEditPlanReq): Promise<EditPlanResp> {
  return request("/api/edit-plans", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export const generateVariant = generate;

export function getJob(id: string): Promise<JobResp> {
  return request(`/api/jobs/${id}`);
}

export function listGenerationJobs(
  projectId: string,
  limit = 10,
): Promise<JobResp[]> {
  return request(`/api/projects/${projectId}/generation-jobs?limit=${limit}`);
}

export function accept(job_id: string, variant_index: number): Promise<AcceptResp> {
  return request("/api/accept", {
    method: "POST",
    body: JSON.stringify({ job_id, variant_index }),
  });
}

export const acceptVariant = accept;

export function getEntity(entity_id: string): Promise<EntityResp> {
  return request<EntityResp>(`/api/entities/${entity_id}`);
}

export function discoverOccurrences(segment_id: string): Promise<DiscoveryJobResp> {
  return request<DiscoveryJobResp>(
    `/api/segments/${segment_id}/discover-occurrences`,
    { method: "POST" },
  );
}

export function propagate(req: PropagateReq): Promise<PropagateResp> {
  return request<PropagateResp>("/api/propagate", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function createGlobalEditPlan(req: {
  entity_id: string;
  reference_segment_id: string;
  scope: "selected_occurrences" | "all_occurrences";
  occurrence_ids?: string[];
}): Promise<GlobalEditPlanResp> {
  return request<GlobalEditPlanResp>("/api/global-edit-plans", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function getGlobalEditPlan(planId: string): Promise<GlobalEditPlanResp> {
  return request<GlobalEditPlanResp>(`/api/global-edit-plans/${planId}`);
}

export function applyGlobalEditPlan(planId: string): Promise<GlobalEditApplyResp> {
  return request<GlobalEditApplyResp>(`/api/global-edit-plans/${planId}/apply`, {
    method: "POST",
  });
}

export function getPropagation(propagation_job_id: string): Promise<PropagationStatusResp> {
  return request<PropagationStatusResp>(`/api/propagate/${propagation_job_id}`);
}

export function applyPropagationResult(
  propagation_job_id: string,
  result_id: string,
): Promise<PropagationResultResp> {
  return request<PropagationResultResp>(
    `/api/propagate/${propagation_job_id}/apply/${result_id}`,
    { method: "POST" },
  );
}

export function getTimeline(project_id: string): Promise<TimelineResp> {
  return request(`/api/timeline/${project_id}`);
}

/** Persist a full EDL snapshot. Last-writer-wins; idempotent; safe to call
 * on debounce from every edit. */
export function saveTimeline(
  project_id: string,
  clips: PersistedClip[],
  sources: PersistedAsset[],
): Promise<TimelineSaveResp> {
  return request<TimelineSaveResp>(`/api/timeline/${project_id}`, {
    method: "PUT",
    body: JSON.stringify({ clips, sources }),
  });
}

export function getMask(
  projectId: string,
  frameTs: number,
  bbox: BBox,
  targetClipId?: string | null,
  signal?: AbortSignal,
): Promise<MaskResp> {
  return request<MaskResp>("/api/mask", {
    method: "POST",
    signal,
    body: JSON.stringify({ project_id: projectId, frame_ts: frameTs, bbox, target_clip_id: targetClipId ?? null }),
  });
}

/** identify the object inside a bbox region — Qwen vision + optional SAM mask */
export function identifyRegion(
  projectId: string,
  frameTs: number,
  bbox: BBox,
  targetClipId?: string | null,
  signal?: AbortSignal,
): Promise<IdentifyResp> {
  return request<IdentifyResp>("/api/identify", {
    method: "POST",
    signal,
    body: JSON.stringify({ project_id: projectId, frame_ts: frameTs, bbox, target_clip_id: targetClipId ?? null }),
  });
}

export type NarrateResp = {
  audio_url: string;
};

export function narrate(variantId: string, description?: string): Promise<NarrateResp> {
  return request<NarrateResp>("/api/narrate", {
    method: "POST",
    body: JSON.stringify({
      variant_id: variantId,
      ...(description != null ? { description } : {}),
    }),
  });
}

export type ExportResp = {
  export_job_id: string;
};

export type ExportStatusResp = {
  export_job_id: string;
  status: JobStatus;
  export_url: string | null;
  /** Signed URL that forces an in-browser file save (Content-Disposition:
   * attachment). Populated once status === "done". Use this for a
   * download button, use `export_url` for the `<video>` preview. */
  download_url: string | null;
  error: string | null;
};

export function exportVideo(project_id: string): Promise<ExportResp> {
  return request<ExportResp>("/api/export", {
    method: "POST",
    body: JSON.stringify({ project_id }),
  });
}

export function getExportStatus(export_job_id: string): Promise<ExportStatusResp> {
  return request<ExportStatusResp>(`/api/export/${export_job_id}`);
}

// ─── SSE: generation "thought process" stream ─────────────────────────

export type JobStreamEvent = {
  ts: number;
  stage: string;
  msg: string;
  terminal?: boolean;
  data?: Record<string, unknown>;
};

/**
 * Subscribe to a job's event stream (structured LLM/gen/ffmpeg logs).
 * Uses fetch + ReadableStream so we can attach auth headers EventSource
 * can't. Returns an `AbortController` — call `.abort()` to disconnect.
 */
export function streamJobEvents(
  jobId: string,
  handlers: {
    onEvent: (event: JobStreamEvent) => void;
    onError?: (err: unknown) => void;
    onClose?: () => void;
  },
): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const headers = new Headers();
      headers.set("X-Session-Id", getSessionId());
      headers.set("Accept", "text/event-stream");
      const res = await fetch(`/api/jobs/${jobId}/stream`, {
        headers,
        signal: controller.signal,
        credentials: "include",
      });
      if (!res.ok || !res.body) {
        throw new Error(`${res.status} ${res.statusText}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line.
        let idx: number;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const dataLines = frame
            .split("\n")
            .filter((line) => line.startsWith("data: "))
            .map((line) => line.slice(6));
          if (!dataLines.length) continue;
          const payload = dataLines.join("\n");
          try {
            const event = JSON.parse(payload) as JobStreamEvent;
            handlers.onEvent(event);
            if (event.terminal) {
              controller.abort();
              handlers.onClose?.();
              return;
            }
          } catch {
            // malformed frame — skip silently, don't kill the stream.
          }
        }
      }
      handlers.onClose?.();
    } catch (e) {
      if ((e as { name?: string }).name === "AbortError") return;
      handlers.onError?.(e);
    }
  })();

  return controller;
}

/** poll a job until it reaches done|error, emitting intermediate states. */
export async function pollJob(
  id: string,
  onUpdate: (j: JobResp) => void,
  intervalMs = 800,
): Promise<JobResp> {
  while (true) {
    const j = await getJob(id);
    onUpdate(j);
    if (j.status === "done" || j.status === "error") return j;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

/** poll an export job until it reaches done|error, emitting intermediate states. */
export async function pollExport(
  exportJobId: string,
  onUpdate: (job: ExportStatusResp) => void,
  intervalMs = 1200,
): Promise<ExportStatusResp> {
  while (true) {
    const job = await getExportStatus(exportJobId);
    onUpdate(job);
    if (job.status === "done" || job.status === "error") return job;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// ─── conversations ───────────────────────────────────────────────────

export interface ConversationResp {
  id: string;
  project_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatMessageResp {
  id: string;
  conversation_id: string;
  role: string;
  content: Record<string, unknown>;
  created_at: string;
}

export function listConversations(projectId: string): Promise<ConversationResp[]> {
  return request<ConversationResp[]>(`/api/projects/${projectId}/conversations`);
}

export function createConversation(projectId: string): Promise<ConversationResp> {
  return request<ConversationResp>(`/api/projects/${projectId}/conversations`, {
    method: "POST",
  });
}

export function getConversationMessages(conversationId: string): Promise<ChatMessageResp[]> {
  return request<ChatMessageResp[]>(`/api/conversations/${conversationId}/messages`);
}

export function deleteConversation(conversationId: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/api/conversations/${conversationId}`, {
    method: "DELETE",
  });
}

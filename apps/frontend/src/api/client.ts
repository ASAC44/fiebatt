/**
 * thin wrapper around backend fetch. calls carry:
 *   - X-Session-Id: anonymous per-browser id for the backend Session row
 *   - Authorization: Bearer <supabase access token> if the user is signed in
 */

import { supabase } from "../lib/supabase";

const SESSION_KEY = "fiebatt.session_id";

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

  // attach the supabase JWT when we have one. backend can verify it with
  // SUPABASE_JWT_SECRET (HS256) and trust user_id / email from the claims.
  try {
    const { data } = await supabase.auth.getSession();
    const token = data.session?.access_token;
    if (token) headers.set("Authorization", `Bearer ${token}`);
  } catch {
    // supabase client not ready — fall through as anonymous.
  }

  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
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
};

export type Me = {
  session_id: string;
  user_id: string | null;
  email: string | null;
  signed_in: boolean;
};

export type ProjectListItem = {
  project_id: string;
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
  status: JobStatus;
  variants: Variant[];
  error: string | null;
  /** authoritative edit window accepted by the backend */
  start_ts: number | null;
  end_ts: number | null;
};


export type GenerateReq = {
  project_id: string;
  start_ts: number;
  end_ts: number;
  bbox: BBox;
  prompt: string;
  reference_frame_ts: number;
};

export type AcceptResp = {
  segment_id: string;
  entity_job_id: string | null;
};

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

export type PropagateReq = {
  entity_id: string;
  source_variant_url: string;
  prompt: string;
  auto_apply?: boolean;
};

export type PropagateResp = {
  propagation_job_id: string;
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

// ─── endpoints ────────────────────────────────────────────────────────

export function me(): Promise<Me> {
  return request<Me>("/api/me");
}

export function listProjects(): Promise<ProjectListItem[]> {
  return request<ProjectListItem[]>("/api/projects");
}

export function getProject(project_id: string): Promise<ProjectResp> {
  return request<ProjectResp>(`/api/projects/${project_id}`);
}

export function deleteProject(project_id: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/api/projects/${project_id}`, { method: "DELETE" });
}

export async function upload(file: File): Promise<UploadResp> {
  const fd = new FormData();
  fd.append("file", file);
  return request<UploadResp>("/api/upload", { method: "POST", body: fd });
}

export function generate(req: GenerateReq): Promise<{ job_id: string }> {
  return request("/api/generate", { method: "POST", body: JSON.stringify(req) });
}

export function getJob(id: string): Promise<JobResp> {
  return request(`/api/jobs/${id}`);
}

export function accept(job_id: string, variant_index: number): Promise<AcceptResp> {
  return request("/api/accept", {
    method: "POST",
    body: JSON.stringify({ job_id, variant_index }),
  });
}

export function getEntity(entity_id: string): Promise<EntityResp> {
  return request<EntityResp>(`/api/entities/${entity_id}`);
}

export function propagate(req: PropagateReq): Promise<PropagateResp> {
  return request<PropagateResp>("/api/propagate", {
    method: "POST",
    body: JSON.stringify(req),
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
  signal?: AbortSignal,
): Promise<MaskResp> {
  return request<MaskResp>("/api/mask", {
    method: "POST",
    signal,
    body: JSON.stringify({ project_id: projectId, frame_ts: frameTs, bbox }),
  });
}

/** identify the object inside a bbox region — Qwen vision + optional SAM mask */
export function identifyRegion(
  projectId: string,
  frameTs: number,
  bbox: BBox,
  signal?: AbortSignal,
): Promise<IdentifyResp> {
  return request<IdentifyResp>("/api/identify", {
    method: "POST",
    signal,
    body: JSON.stringify({ project_id: projectId, frame_ts: frameTs, bbox }),
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
      try {
        const { data } = await supabase.auth.getSession();
        const token = data.session?.access_token;
        if (token) headers.set("Authorization", `Bearer ${token}`);
      } catch {
        // anonymous — proceed without auth
      }

      const res = await fetch(`/api/jobs/${jobId}/stream`, {
        headers,
        signal: controller.signal,
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

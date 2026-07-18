/* eslint-disable react-hooks/exhaustive-deps, react-hooks/refs */
/**
 * useAgentStream — SSE streaming hook for the agent chat endpoint.
 *
 * Connects to POST /api/agent/chat and dispatches parsed SSE events
 * into the AgentProvider reducer. Handles abort/cleanup, auth headers
 * (same pattern as api/client.ts), and incremental token streaming.
 */
import { useCallback, useEffect, useRef } from "react";

import {
  createConversation,
  deleteConversation,
  getJob,
  getConversationMessages,
  getSessionId,
  listGenerationJobs,
  listConversations,
  streamJobEvents,
  type ChatMessageResp,
  type JobStreamEvent,
} from "@/lib/api";
import { redirectToLogin } from "@/lib/auth";
import { cleanAgentText } from "@/lib/agent-text";
import {
  useAgent,
  type AgentAction,
  type AgentMessage,
  type PromptPlan,
  type SuggestedEdit,
  type VariantPreview,
} from "@/stores/agent";

// ─── types ────────────────────────────────────────────────────────────

interface SendMessageOptions {
  projectId: string;
  message: string;
  // Editor context — the chat UI knows the live project state; forwarding
  // it prevents Gemini from asking "what's the project_id?" or "where is
  // the man?" when the user already has a bbox drawn and a playhead set.
  playheadTs?: number | null;
  duration?: number | null;
  bbox?: { x: number; y: number; w: number; h: number } | null;
  selectionId?: string | null;
  targetClipId?: string | null;
}

/**
 * Next rewrites are fine for normal JSON requests, but they can buffer a
 * long-lived SSE response in dev. Use the browser-reachable API directly
 * for local development so tokens and tool events arrive incrementally.
 */
function agentChatUrl(): string {
  if (typeof window !== "undefined" &&
      (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")) {
    return `http://${window.location.hostname}:8000/api/agent/chat`;
  }
  return "/api/agent/chat";
}

type PendingAgentTurn = {
  startedAt: number;
  prompt: string;
  jobId?: string;
};

const PENDING_TURN_PREFIX = "fiebatt.pending-agent-turn.";
const PLAN_RECOVERY_TIMEOUT_MS = 7 * 60 * 1000;

function pendingTurnKey(projectId: string): string {
  return `${PENDING_TURN_PREFIX}${projectId}`;
}

function readPendingTurn(projectId: string): PendingAgentTurn | null {
  try {
    const raw = localStorage.getItem(pendingTurnKey(projectId));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<PendingAgentTurn>;
    if (typeof value.startedAt !== "number" || typeof value.prompt !== "string") {
      return null;
    }
    return value as PendingAgentTurn;
  } catch {
    return null;
  }
}

function writePendingTurn(projectId: string, turn: PendingAgentTurn): void {
  localStorage.setItem(pendingTurnKey(projectId), JSON.stringify(turn));
}

function clearPendingTurn(projectId: string): void {
  localStorage.removeItem(pendingTurnKey(projectId));
}

function generationActivity(event: JobStreamEvent): string {
  if (event.stage.startsWith("chunk_")) {
    return event.msg.replace(/\.\.\./g, "…");
  }
  const labels: Record<string, string> = {
    queued: "preparing edit window…",
    extract_clip: "preparing source context…",
    extract_frame: "capturing subject reference…",
    plan_start: "refining edit instructions…",
    plan_done: "edit instructions ready…",
    gen_start: "sending edit to video model…",
    gen_submit: "video model accepted render…",
    gen_done: "video render received…",
    score_start: "checking visual quality and transitions…",
    continuity_validation_done: "transition check complete…",
    continuity_validation_unavailable: "finishing quality review…",
    seam_match_done: "matching source and edit frames…",
    seam_match_unavailable: "could not find safe cut frames…",
    gen_retry: "improving first render…",
    gen_provider_fallback: "trying backup video model…",
    attempt_failed: "render attempt could not be used; checking recovery…",
    gen_retry_rejected: "keeping stronger render…",
    done: "preview ready…",
  };
  if (event.stage === "gen_poll") return event.msg.replace(/\.\.\./g, "…");
  return labels[event.stage] ?? event.msg.replace(/\.\.\./g, "…");
}

// ─── hook ─────────────────────────────────────────────────────────────

export function useAgentStream(projectId?: string | null) {
  const { state, dispatch } = useAgent();
  const abortRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);
  const generationWatchRef = useRef<AbortController | null>(null);
  const watchedJobIdRef = useRef<string | null>(null);
  const loadedRef = useRef<string | null>(null);
  const conversationHydrationRef = useRef<Promise<void> | null>(null);
  const conversationIdRef = useRef(state.conversationId);
  conversationIdRef.current = state.conversationId;

  const watchGeneration = useCallback(async (jobId: string) => {
    if (watchedJobIdRef.current === jobId) return;
    generationWatchRef.current?.abort();
    const watch = new AbortController();
    generationWatchRef.current = watch;
    watchedJobIdRef.current = jobId;
    dispatch({ type: "resume_job_watch", activity: "video job queued…" });
    dispatch({
      type: "update_generation_progress",
      jobId,
      stage: "queued",
      text: "video job queued",
    });
    let detailedProgressAt = 0;
    let detailedStage = "";
    let lastHeartbeatAt = 0;
    let previewSignature = "";
    let retrySignature = "";
    let persistedProgressSignature = "";
    let pollFailures = 0;
    const eventStream = streamJobEvents(jobId, {
      onEvent: (event) => {
        detailedProgressAt = Date.now();
        detailedStage = event.stage;
        const text = generationActivity(event);
        dispatch({ type: "set_activity", activity: text });
        const now = Date.now();
        const periodicStage = event.stage === "gen_poll" || event.stage === "chunk_poll";
        if (!periodicStage || now - lastHeartbeatAt >= 30_000) {
          lastHeartbeatAt = now;
          dispatch({
            type: "update_generation_progress",
            jobId,
            stage: event.stage,
            text: text.replace(/…$/, ""),
            complete: event.stage === "done",
          });
        }
      },
    });
    const watchStartedAt = Date.now();
    try {
      while (!watch.signal.aborted) {
        let job;
        try {
          job = await getJob(jobId);
          pollFailures = 0;
        } catch {
          pollFailures += 1;
          const reconnectText =
            "Status connection interrupted; the backend render is still running";
          dispatch({ type: "set_activity", activity: `${reconnectText}…` });
          if (pollFailures === 1 || pollFailures % 6 === 0) {
            dispatch({
              type: "update_generation_progress",
              jobId,
              stage: "status_reconnect",
              text: reconnectText,
            });
          }
          await new Promise((resolve) =>
            window.setTimeout(resolve, Math.min(5000, 1000 * pollFailures)),
          );
          continue;
        }
        if (watch.signal.aborted) return;
        const ready = job.variants.filter((variant) => variant.url);
        const failed = job.variants.filter((variant) => variant.status === "error");
        if (job.retry_state) {
          const nextRetrySignature = JSON.stringify(job.retry_state);
          if (nextRetrySignature !== retrySignature) {
            retrySignature = nextRetrySignature;
            dispatch({
              type: "update_retry_control",
              jobId,
              status: job.retry_state.status,
              retryAt: job.retry_state.retry_at,
              evidence: job.retry_state.evidence,
              correction: job.retry_state.correction,
            });
          }
        }
        if (job.progress_state) {
          const nextProgressSignature = JSON.stringify(job.progress_state);
          if (nextProgressSignature !== persistedProgressSignature) {
            persistedProgressSignature = nextProgressSignature;
            dispatch({
              type: "update_generation_progress",
              jobId,
              stage: job.progress_state.stage,
              text: job.progress_state.message,
              complete: job.progress_state.status !== "running",
              failed: job.progress_state.status === "failed",
            });
          }
        }
        const nextPreviewSignature = ready
          .map((variant) => `${variant.id}:${variant.url}`)
          .join("|");
        if (ready.length > 0 && nextPreviewSignature !== previewSignature) {
          previewSignature = nextPreviewSignature;
          dispatch({
            type: "add_variant_preview",
            jobId,
            variants: ready,
            timelineStart: job.selected_seams?.timeline_start ?? job.execution_window?.core_start ?? job.start_ts,
            timelineEnd: job.selected_seams?.timeline_end ?? job.execution_window?.core_end ?? job.end_ts,
            mediaStart: job.selected_seams?.media_start ?? job.execution_window?.edit_start_offset ?? 0,
            mediaEnd:
              job.selected_seams?.media_end ??
              job.execution_window?.edit_end_offset ??
              (job.start_ts != null && job.end_ts != null
                ? job.end_ts - job.start_ts
                : null),
          });
          dispatch({
            type: "set_activity",
            activity: ready.length === 1 ? "first pass ready — reviewing it now…" : "corrected pass ready — finishing review…",
          });
        }
        if (job.status === "done" || job.status === "error") {
          if (projectId) clearPendingTurn(projectId);
          if (ready.length > 0) {
            dispatch({
              type: "add_variant_preview",
              jobId,
              variants: ready,
              timelineStart: job.selected_seams?.timeline_start ?? job.execution_window?.core_start ?? job.start_ts,
              timelineEnd: job.selected_seams?.timeline_end ?? job.execution_window?.core_end ?? job.end_ts,
              mediaStart: job.selected_seams?.media_start ?? job.execution_window?.edit_start_offset ?? 0,
              mediaEnd:
                job.selected_seams?.media_end ??
                job.execution_window?.edit_end_offset ??
                (job.start_ts != null && job.end_ts != null
                  ? job.end_ts - job.start_ts
                  : null),
            });
            dispatch({ type: "set_activity", activity: "preview ready — choose a variant" });
            dispatch({
              type: "update_generation_progress",
              jobId,
              stage: "done",
              text: "preview ready",
              complete: true,
            });
          } else {
            dispatch({
              type: "update_generation_progress",
              jobId,
              stage: "failed",
              text:
                job.failure_state?.user_message ||
                job.error ||
                failed[0]?.error ||
                "This render could not be completed. Your source video and timeline are unchanged.",
              complete: true,
              failed: true,
            });
            dispatch({ type: "set_activity", activity: "render ended safely" });
          }
          return;
        }
        if (Date.now() - detailedProgressAt > 8000) {
          const stageStartedAt = detailedProgressAt || watchStartedAt;
          const elapsed = Math.max(1, Math.round((Date.now() - stageStartedAt) / 1000));
          const reviewing =
            detailedStage.startsWith("score") ||
            detailedStage.startsWith("continuity");
          const heartbeat =
            job.status === "processing"
              ? reviewing
                ? `checking quality and transitions · ${elapsed}s elapsed…`
                : `video model rendering · ${elapsed}s elapsed…`
              : "waiting for video model…";
          dispatch({
            type: "set_activity",
            activity: heartbeat,
          });
          if (Date.now() - lastHeartbeatAt >= 30_000) {
            lastHeartbeatAt = Date.now();
            dispatch({
              type: "update_generation_progress",
              jobId,
              stage: reviewing ? "quality_wait" : "render_wait",
              text: heartbeat.replace(/…$/, ""),
            });
          }
        }
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
      }
    } catch {
      if (!watch.signal.aborted) {
        dispatch({
          type: "update_generation_progress",
          jobId,
          stage: "status_unavailable",
          text: "Live status paused. The backend may still be rendering; reopen this project to reconnect.",
          failed: true,
          complete: true,
        });
      }
    } finally {
      eventStream.abort();
      if (generationWatchRef.current === watch) {
        generationWatchRef.current = null;
        watchedJobIdRef.current = null;
        dispatch({ type: "end_stream" });
      }
    }
  }, [dispatch, projectId]);

  // hydrate the most recent conversation for this project on mount
  useEffect(() => {
    if (!projectId) return;
    if (loadedRef.current === projectId) return;

    // clear messages when switching projects
    if (loadedRef.current !== null) {
      dispatch({ type: "clear_messages" });
    }
    loadedRef.current = projectId;

    const hydration = (async () => {
      try {
        const convos = await listConversations(projectId);
        if (convos.length === 0) {
          // create a fresh conversation
          const convo = await createConversation(projectId);
          dispatch({ type: "set_conversation_id", id: convo.id });
          // Job restoration still runs below; conversations and generated
          // previews have separate durable sources.
        } else {
          const latest = convos[0];
          dispatch({ type: "set_conversation_id", id: latest.id });

          if (latest.message_count > 0) {
            const msgs = await getConversationMessages(latest.id);
            const hydrated = msgs
              .map(dbMessageToAgentMessage)
              .filter(Boolean) as AgentMessage[];
            const persistable = hydrated.filter((m) => {
              if (m.type === "error") return false;
              if (m.type === "tool_call" && m.status === "error") return false;
              if (m.type === "suggestion") return false;
              if (m.type === "prompt_plan") return false;
              return true;
            });
            dispatch({ type: "hydrate_messages", messages: persistable });
          }
        }

        const jobsResponse = await listGenerationJobs(projectId, 10);
        // A stale proxy response must not break the entire chat on reopen.
        // Treat anything other than the documented array as an empty job list;
        // the next poll or refresh can restore the preview normally.
        const jobs = Array.isArray(jobsResponse) ? jobsResponse : [];
        const latestJob = jobs.find(
          (job) =>
            !job.accepted &&
            (job.status === "pending" ||
              job.status === "processing" ||
              job.status === "error" ||
              (job.status === "done" && job.variants.some((variant) => variant.url))),
        );
        if (latestJob) {
          const ready = latestJob.variants.filter((variant) => variant.url);
          if (ready.length > 0 && latestJob.status === "done") {
            dispatch({
              type: "add_variant_preview",
              jobId: latestJob.job_id,
              variants: ready,
              timelineStart:
                latestJob.execution_window?.core_start ?? latestJob.start_ts,
              timelineEnd: latestJob.execution_window?.core_end ?? latestJob.end_ts,
              mediaStart: latestJob.execution_window?.edit_start_offset ?? 0,
              mediaEnd:
                latestJob.execution_window?.edit_end_offset ??
                (latestJob.start_ts != null && latestJob.end_ts != null
                  ? latestJob.end_ts - latestJob.start_ts
                  : null),
            });
          } else if (
            latestJob.status === "pending" ||
            latestJob.status === "processing" ||
            latestJob.status === "error"
          ) {
            void watchGeneration(latestJob.job_id);
          }
        }
      } catch (err) {
        // non-fatal — just start fresh
        console.warn("[agent] failed to load conversation:", err);
      }
    })();
    conversationHydrationRef.current = hydration;
  }, [projectId, dispatch, watchGeneration]);

  const sendMessage = useCallback(
    async ({
      projectId,
      message,
      playheadTs,
      duration,
      bbox,
      selectionId,
      targetClipId,
    }: SendMessageOptions) => {
      // Do not create duplicate backend jobs while one turn is active.
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      const controller = new AbortController();
      abortRef.current = controller;
      let timedOut = false;
      let sawDone = false;
      let generationQueued = false;
      const timeout = window.setTimeout(() => {
        timedOut = true;
        dispatch({
          type: "add_notice",
          message: "This request is taking longer than expected. A queued video render keeps running in the backend and can be restored from this project.",
        });
        controller.abort();
      }, 7 * 60 * 1000);

      // Optimistic UI: show the user message immediately
      writePendingTurn(projectId, { startedAt: Date.now(), prompt: message });
      dispatch({ type: "add_user_message", text: message });
      dispatch({ type: "start_stream" });

      try {
        // ── auth headers (mirrors api/client.ts) ──────────────────
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
          "X-Session-Id": getSessionId(),
        };
        // Build conversation history from messages already in state.
        // We snapshot *before* the user message we just dispatched
        // (reducer runs async from our perspective) so we send the
        // full prior context.  The backend receives `message` as the
        // new turn plus `history` for context.
        const history = state.messages
          .filter(
            (m) =>
              m.type === "user" || (m.type === "agent" && !m.streaming),
          )
          .map((m) => ({
            role: m.type === "user" ? ("user" as const) : ("model" as const),
            text: (m as { text: string }).text,
          }));

        const response = await fetch(agentChatUrl(), {
          method: "POST",
          credentials: "include",
          headers,
          signal: controller.signal,
          body: JSON.stringify({
            project_id: projectId,
            message,
            conversation_id: conversationIdRef.current,
            history,
            playhead_ts: playheadTs ?? null,
            duration: duration ?? null,
            bbox: bbox ?? null,
            selection_id: selectionId ?? null,
            target_clip_id: targetClipId ?? null,
          }),
        });

        if (!response.ok) {
          if (response.status === 401) {
            redirectToLogin();
          }
          const text = await response.text().catch(() => "");
          throw new Error(`${response.status}: ${text}`);
        }

        if (!response.body) {
          throw new Error("Response body is empty — streaming not supported");
        }

        // ── SSE stream parser ─────────────────────────────────────
        console.log(`[agent stream] OPEN project=${projectId} msg=${message.slice(0, 80)}`);
        dispatch({ type: "set_activity", activity: "connected — agent is thinking…" });
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        // currentEvent MUST live across chunks: SSE frames (event: …\n
        // data: …\n\n) frequently straddle TCP packet boundaries, so
        // resetting it per chunk silently drops any data: line whose
        // event: header landed in the previous chunk. This was the
        // root cause of "variant_ready never arrives on the client".
        let currentEvent = "";
        let chunkCount = 0;
        let eventCount = 0;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          chunkCount++;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          // Keep the last (possibly incomplete) line in the buffer
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEvent = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              const dataStr = line.slice(6);
              try {
                const data = JSON.parse(dataStr) as Record<string, unknown>;
                eventCount++;
                if (currentEvent === "done") sawDone = true;
                handleSSEEvent(dispatch, currentEvent, data, message);
                if (currentEvent === "suggestion") {
                  const edit = data.edit as { job_id?: string } | undefined;
                  if (edit?.job_id) {
                    generationQueued = true;
                    const pending = readPendingTurn(projectId);
                    writePendingTurn(projectId, {
                      startedAt: pending?.startedAt ?? Date.now(),
                      prompt: pending?.prompt ?? message,
                      jobId: edit.job_id,
                    });
                    void watchGeneration(edit.job_id);
                  }
                }
              } catch (err) {
                console.warn(
                  "[agent stream] malformed JSON in SSE data line (skipping)",
                  { line: dataStr.slice(0, 200), err },
                );
              }
              // consumed; wait for the next `event:` line to set it
              currentEvent = "";
            }
            // Blank lines delimit SSE events (spec). We just reset
            // currentEvent on data consumption above, which is sufficient.
          }
        }
        if (!sawDone && !controller.signal.aborted) {
          dispatch({
            type: "add_notice",
            message: generationQueued
              ? "The planning connection closed, but the video render is still running below."
              : "The request connection closed before an edit was queued. Nothing was changed on the timeline.",
          });
        }
        console.log(
          `[agent stream] CLOSE chunks=${chunkCount} events=${eventCount}`,
        );
      } catch (err: unknown) {
        const isAbort = (err as Error).name === "AbortError";
        console[isAbort ? "log" : "error"](
          `[agent stream] ${isAbort ? "ABORTED" : "ERRORED"}:`,
          err,
        );
        if (!isAbort && !timedOut) {
          dispatch({
            type: "add_notice",
            message: "The request could not be completed. Nothing was changed on the timeline; check the selection and try again.",
          });
        }
      } finally {
        window.clearTimeout(timeout);
        if (sawDone && !generationQueued) clearPendingTurn(projectId);
        // Keep the input/status card alive while the detached generation
        // watcher is polling for a preview after the chat stream ends.
        if (!generationWatchRef.current) dispatch({ type: "end_stream" });
        abortRef.current = null;
        inFlightRef.current = false;
      }
    },
    [state.messages, state.conversationId, dispatch, watchGeneration],
  );

  // A page change only closes browser delivery. The backend agent turn and
  // generation keep running. On return, find the job created for that turn
  // and restore polling (including a preview that finished while away).
  useEffect(() => {
    if (!projectId) return;
    const pending = readPendingTurn(projectId);
    if (!pending) return;

    let cancelled = false;
    const recover = async () => {
      // Conversation hydration replaces message rows. Wait for it before
      // adding a recovered preview so a slower history request cannot erase it.
      await conversationHydrationRef.current;
      if (cancelled) return;
      dispatch({
        type: "resume_job_watch",
        activity: pending.jobId ? "restoring video job…" : "finishing edit plan…",
      });
      const deadline = pending.startedAt + PLAN_RECOVERY_TIMEOUT_MS;
      while (!cancelled && Date.now() <= deadline) {
        try {
          if (pending.jobId) {
            await watchGeneration(pending.jobId);
            return;
          }
          const jobs = await listGenerationJobs(projectId, 10);
          if (cancelled) return;
          const earliest = pending.startedAt - 30_000;
          const candidate = jobs.find((job) => {
            if (job.accepted) return false;
            const createdAt = job.created_at ? Date.parse(job.created_at) : 0;
            return createdAt >= earliest;
          });
          if (candidate) {
            writePendingTurn(projectId, { ...pending, jobId: candidate.job_id });
            await watchGeneration(candidate.job_id);
            return;
          }
        } catch {
          // Network or planning may still be settling. Retry until deadline.
        }
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
      }
      if (!cancelled) {
        clearPendingTurn(projectId);
        dispatch({
          type: "add_notice",
          message: "No queued edit could be restored. Nothing was changed on the timeline; you can safely try again.",
        });
      }
    };

    void recover();
    return () => {
      cancelled = true;
    };
  }, [dispatch, projectId, watchGeneration]);

  useEffect(() => () => {
    abortRef.current?.abort();
    generationWatchRef.current?.abort();
  }, []);

  const stopStream = useCallback(() => {
    abortRef.current?.abort();
    generationWatchRef.current?.abort();
    inFlightRef.current = false;
    dispatch({ type: "end_stream" });
  }, [dispatch]);

  const clearChat = useCallback(async () => {
    abortRef.current?.abort();
    if (projectId) clearPendingTurn(projectId);
    const oldId = state.conversationId;
    dispatch({ type: "clear_messages" });

    // create a new conversation server-side, delete the old one
    if (projectId) {
      try {
        const convo = await createConversation(projectId);
        dispatch({ type: "set_conversation_id", id: convo.id });
        // best-effort cleanup of old conversation
        deleteConversation(oldId).catch(() => {});
      } catch {
        // fallback to local-only ID if backend is unreachable
        dispatch({ type: "set_conversation_id", id: crypto.randomUUID() });
      }
    } else {
      dispatch({ type: "set_conversation_id", id: crypto.randomUUID() });
    }
  }, [dispatch, state.conversationId, projectId]);

  return {
    messages: state.messages,
    streaming: state.streaming,
    analysis: state.analysis,
    activity: state.activity,
    conversationId: state.conversationId,
    sendMessage,
    stopStream,
    clearChat,
  };
}

// ─── DB message → AgentMessage converter ─────────────────────────────

function dbMessageToAgentMessage(msg: ChatMessageResp): AgentMessage | null {
  const content = msg.content as Record<string, unknown>;
  const ts = new Date(msg.created_at).getTime();

  switch (msg.role) {
    case "user":
      return { type: "user", text: (content.text as string) ?? "", ts };
    case "agent":
      return {
        type: "agent",
        text: cleanAgentText((content.text as string) ?? ""),
        ts,
        streaming: false,
      };
    case "tool_call":
      return {
        type: "tool_call",
        id: (content.id as string) ?? "",
        tool: (content.tool as string) ?? "",
        args: content.args,
        status: (content.status as "done" | "error") ?? "done",
        result: content.result,
        ts,
      };
    case "suggestion":
      return {
        type: "suggestion",
        edit: content.edit as SuggestedEdit,
        ts,
      };
    case "error":
      return {
        type: "error",
        message: (content.message as string) ?? "",
        ts,
      };
    default:
      return null;
  }
}

// ─── SSE event dispatcher ─────────────────────────────────────────────

// Track which tool a given tool_call_id belongs to, so that when
// tool_call_end arrives (which carries only the id) we can fire
// side-effects keyed on the tool name — e.g. refreshing the
// timeline after accept_variant lands a new segment server-side.
const _toolsById = new Map<string, string>();

// Compact, grep-able log so you can scan the console and reconstruct
// the full server→client story for a single agent turn. Gated behind a
// module flag in case we want to silence it in prod later.
const DEBUG_SSE = true;
function _logSSE(event: string, data: Record<string, unknown>): void {
  if (!DEBUG_SSE) return;
  const bits: string[] = [];
  for (const key of ["id", "tool", "status", "job_id"]) {
    if (key in data) bits.push(`${key}=${String(data[key])}`);
  }
  if (Array.isArray(data.variants)) {
    const vs = data.variants as Array<Record<string, unknown>>;
    const ready = vs.filter((v) => typeof v.url === "string" && v.url).length;
    bits.push(`variants=${vs.length} ready=${ready}`);
  }
  if (typeof data.text === "string") bits.push(`text_len=${data.text.length}`);
  if ("error" in data) bits.push(`error=${String(data.error).slice(0, 80)}`);
  if (data.result && typeof data.result === "object") {
    const keys = Object.keys(data.result as object).sort().join(",");
    bits.push(`result_keys=${keys}`);
  }
  console.log(`[agent sse] ${event}`, bits.join(" "), data);
}

function handleSSEEvent(
  dispatch: React.Dispatch<AgentAction>,
  event: string,
  data: Record<string, unknown>,
  currentUserPrompt = "",
): void {
  _logSSE(event, data);

  switch (event) {
    case "token":
      dispatch({ type: "set_activity", activity: "understanding your request…" });
      dispatch({ type: "append_token", text: data.text as string });
      break;

    case "tool_call_start": {
      const id = data.id as string;
      const tool = data.tool as string;
      _toolsById.set(id, tool);
      const activities: Record<string, string> = {
        create_edit_plan: "planning edit window…",
        identify_region: "identifying selected subject…",
        generate_edit: "starting video render…",
      };
      dispatch({
        type: "set_activity",
        activity: activities[tool] ?? `${tool.replace(/_/g, " ")}…`,
      });
      dispatch({
        type: "tool_call_start",
        id,
        tool,
        args: data.args,
      });
      break;
    }

    case "tool_call_progress":
      dispatch({
        type: "tool_call_progress",
        id: data.id as string,
        progress: data.progress as string,
      });
      break;

    case "tool_call_end": {
      const id = data.id as string;
      const status = data.status as "done" | "error";
      // Prefer the tool name the server sends on tool_call_end itself —
      // the module-level ``_toolsById`` map used to be the only source
      // but it would silently empty out if the hook unmounted / the
      // page got HMR-reloaded between the start and end events,
      // causing the timeline-refresh dispatch to quietly skip.
      const tool = (data.tool as string | undefined) ?? _toolsById.get(id);
      _toolsById.delete(id);

      dispatch({
        type: "tool_call_end",
        id,
        result: data.result,
        status,
      });
      dispatch({ type: "set_activity", activity: status === "done" ? "continuing…" : "tool failed" });

      // Mutating tools need the frontend EDL to re-hydrate from the
      // server. Broadcast a window event so the Studio shell can
      // refetch the timeline without this hook knowing about it.
      if (status === "done" && tool) {
        const mutating = new Set([
          "accept_variant",
          "split_segment",
          "trim_segment",
          "delete_segment",
          "color_grade",
          "revert_timeline",
        ]);
        if (mutating.has(tool)) {
          console.log("[agent sse] dispatching fiebatt:timeline-refresh after", tool);
          window.dispatchEvent(
            new CustomEvent("fiebatt:timeline-refresh", { detail: { tool } }),
          );
        } else {
          console.log("[agent sse] tool_call_end non-mutating, no refresh:", tool);
        }
      } else {
          console.warn(
          `[agent sse] tool_call_end had no tool name (id=${id} status=${status}) — fiebatt:timeline-refresh NOT dispatched`,
        );
      }
      break;
    }

    case "suggestion":
      dispatch({
        type: "add_suggestion",
        edit: data.edit as SuggestedEdit,
      });
      break;

    case "variant_ready": {
      const variants = data.variants as VariantPreview[];
      const urlCount = Array.isArray(variants)
        ? variants.filter((v) => v && v.url).length
        : 0;
      console.log(
        `[agent sse] variant_ready job=${data.job_id} variants=${variants?.length ?? 0} urls=${urlCount}`,
        variants,
      );
      if (!Array.isArray(variants) || urlCount === 0) {
        console.warn(
          "[agent sse] variant_ready arrived but no variant carried a URL — this should not happen, backend is now supposed to gate on url presence",
        );
      }
      dispatch({
        type: "add_variant_preview",
        jobId: data.job_id as string,
        variants,
        timelineStart: (data.start_ts as number | undefined) ?? null,
        timelineEnd: (data.end_ts as number | undefined) ?? null,
        mediaStart: (data.media_start_ts as number | undefined) ?? null,
        mediaEnd: (data.media_end_ts as number | undefined) ?? null,
      });
      break;
    }

    case "prompt_plan_started": {
      const jobId = (data.job_id as string | undefined) ?? "";
      if (!jobId) break;
      dispatch({ type: "set_activity", activity: "rewriting your prompt…" });
      dispatch({
        type: "prompt_plan_started",
        jobId,
        userPrompt: currentUserPrompt || (data.user_prompt as string | undefined) || "",
      });
      break;
    }

    case "prompt_plan": {
      const jobId = (data.job_id as string | undefined) ?? "";
      const plan = data.plan as PromptPlan | undefined;
      if (!jobId || !plan) break;
      dispatch({ type: "prompt_plan_ready", jobId, plan });
      break;
    }

    case "gen_dispatch": {
      const jobId = (data.job_id as string | undefined) ?? "";
      if (!jobId) break;
      const strategy = (data.strategy as string | undefined) ?? "video model";
      dispatch({ type: "set_activity", activity: `rendering with ${strategy}…` });
      dispatch({ type: "prompt_plan_dispatched", jobId, vendor: strategy });
      break;
    }

    case "generation_failed": {
      const rawErr = (data.error as string | undefined) ?? "no variants produced";
      console.warn(`[agent sse] generation_failed job=${data.job_id}: ${rawErr}`);
      const niceErr = /rate.?limit|quota|429|RESOURCE_EXHAUSTED/i.test(rawErr)
        ? "The video model is temporarily busy. Your source video and timeline are unchanged."
        : "This render could not be completed. Your source video and timeline are unchanged.";
      dispatch({
        type: "update_generation_progress",
        jobId: (data.job_id as string | undefined) ?? "generation",
        stage: "failed",
        text: niceErr,
        complete: true,
        failed: true,
      });
      dispatch({ type: "set_activity", activity: "render ended safely" });
      break;
    }

    case "done":
      // Stream finished — end_stream is called in the finally block
      break;

    case "error":
      console.error("[agent sse] server error event", data);
      dispatch({
        type: "add_notice",
        message: "The edit could not be prepared. Nothing was changed; check the selection and try again.",
      });
      break;

    default: {
      console.warn(`[agent sse] UNHANDLED event="${event}"`, data);
    }
  }
}

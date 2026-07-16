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
  listConversations,
  type ChatMessageResp,
} from "@/lib/api";
import { redirectToLogin } from "@/lib/auth";
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
}

/**
 * Next rewrites are fine for normal JSON requests, but they can buffer a
 * long-lived SSE response in dev. Use the browser-reachable API directly
 * for local development so tokens and tool events arrive incrementally.
 */
function agentChatUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "");
  if (configured && !configured.includes("://api:")) {
    return `${configured}/api/agent/chat`;
  }
  if (typeof window !== "undefined" &&
      (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")) {
    return `http://${window.location.hostname}:8000/api/agent/chat`;
  }
  return "/api/agent/chat";
}

// ─── hook ─────────────────────────────────────────────────────────────

export function useAgentStream(projectId?: string | null) {
  const { state, dispatch } = useAgent();
  const abortRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);
  const generationWatchRef = useRef<AbortController | null>(null);
  const loadedRef = useRef<string | null>(null);
  const conversationIdRef = useRef(state.conversationId);
  conversationIdRef.current = state.conversationId;

  // hydrate the most recent conversation for this project on mount
  useEffect(() => {
    if (!projectId) return;
    if (loadedRef.current === projectId) return;

    // clear messages when switching projects
    if (loadedRef.current !== null) {
      dispatch({ type: "clear_messages" });
    }
    loadedRef.current = projectId;

    (async () => {
      try {
        const convos = await listConversations(projectId);
        if (convos.length === 0) {
          // create a fresh conversation
          const convo = await createConversation(projectId);
          dispatch({ type: "set_conversation_id", id: convo.id });
          return;
        }

        // load the most recent conversation
        const latest = convos[0];
        dispatch({ type: "set_conversation_id", id: latest.id });

        if (latest.message_count > 0) {
          const msgs = await getConversationMessages(latest.id);
          const hydrated = msgs
            .map(dbMessageToAgentMessage)
            .filter(Boolean) as AgentMessage[];
          // Drop transient UI-only rows (errors from older sessions, stale
          // tool_call cards whose jobs no longer exist, and suggestion
          // cards tied to dead jobs). These used to leak through and
          // show a red "generation failed" card the instant the user
          // reopened the reel, even though the current backend is healthy.
          const persistable = hydrated.filter((m) => {
            if (m.type === "error") return false;
            if (m.type === "tool_call" && m.status === "error") return false;
            if (m.type === "suggestion") return false;
            // prompt_plan cards are tied to a live job_id; there's no
            // point re-showing the "prompt rewrite" for a job the
            // user already accepted/dismissed sessions ago.
            if (m.type === "prompt_plan") return false;
            return true;
          });
          dispatch({ type: "hydrate_messages", messages: persistable });
        }
      } catch (err) {
        // non-fatal — just start fresh
        console.warn("[agent] failed to load conversation:", err);
      }
    })();
  }, [projectId, dispatch]);

  const sendMessage = useCallback(
    async ({
      projectId,
      message,
      playheadTs,
      duration,
      bbox,
      selectionId,
    }: SendMessageOptions) => {
      // Do not create duplicate backend jobs while one turn is active.
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      const controller = new AbortController();
      abortRef.current = controller;
      let timedOut = false;
      let sawDone = false;
      const timeout = window.setTimeout(() => {
        timedOut = true;
        dispatch({
          type: "add_error",
          message: "The backend agent timed out after 7 minutes. The video provider may still be rendering; check the project timeline before retrying.",
        });
        controller.abort();
      }, 7 * 60 * 1000);

      const watchGeneration = async (jobId: string) => {
        generationWatchRef.current?.abort();
        const watch = new AbortController();
        generationWatchRef.current = watch;
        dispatch({ type: "set_activity", activity: "video job queued…" });
        try {
          while (!watch.signal.aborted) {
            const job = await getJob(jobId);
            if (watch.signal.aborted) return;
            const ready = job.variants.filter((variant) => variant.url);
            const failed = job.variants.filter((variant) => variant.status === "error");
            if (job.status === "done" || job.status === "error") {
              if (ready.length > 0) {
                dispatch({ type: "add_variant_preview", jobId, variants: ready });
                dispatch({ type: "set_activity", activity: "preview ready — choose a variant" });
              } else {
                dispatch({
                  type: "add_error",
                  message: job.error || failed[0]?.error || "Video generation failed without a usable preview.",
                });
              }
              return;
            }
            dispatch({
              type: "set_activity",
              activity: `${job.status === "processing" ? "rendering" : "queued"} · ${ready.length}/${job.variants.length || 1} previews ready…`,
            });
            await new Promise((resolve) => window.setTimeout(resolve, 1500));
          }
        } catch (error) {
          if (!watch.signal.aborted) {
            dispatch({
              type: "add_error",
              message: `Could not read generation status: ${error instanceof Error ? error.message : String(error)}`,
            });
          }
        } finally {
          if (generationWatchRef.current === watch) {
            generationWatchRef.current = null;
            dispatch({ type: "end_stream" });
          }
        }
      };

      // Optimistic UI: show the user message immediately
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
                handleSSEEvent(dispatch, currentEvent, data);
                if (currentEvent === "suggestion") {
                  const edit = data.edit as { job_id?: string } | undefined;
                  if (edit?.job_id) void watchGeneration(edit.job_id);
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
            type: "add_error",
            message: "The backend stream ended before it reported completion. Nothing was applied to the timeline.",
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
            type: "add_error",
            message: err instanceof Error ? err.message : String(err),
          });
        }
      } finally {
        window.clearTimeout(timeout);
        // Keep the input/status card alive while the detached generation
        // watcher is polling for a preview after the chat stream ends.
        if (!generationWatchRef.current) dispatch({ type: "end_stream" });
        abortRef.current = null;
        inFlightRef.current = false;
      }
    },
    [state.messages, state.conversationId, dispatch],
  );

  const stopStream = useCallback(() => {
    abortRef.current?.abort();
    generationWatchRef.current?.abort();
    inFlightRef.current = false;
    dispatch({ type: "end_stream" });
  }, [dispatch]);

  const clearChat = useCallback(async () => {
    abortRef.current?.abort();
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
        text: (content.text as string) ?? "",
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
): void {
  _logSSE(event, data);

  switch (event) {
    case "token":
      dispatch({ type: "set_activity", activity: "agent is thinking…" });
      dispatch({ type: "append_token", text: data.text as string });
      break;

    case "tool_call_start": {
      const id = data.id as string;
      const tool = data.tool as string;
      _toolsById.set(id, tool);
      dispatch({ type: "set_activity", activity: `${tool.replace(/_/g, " ")}…` });
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
        userPrompt: (data.user_prompt as string | undefined) ?? "",
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
        ? "generation is rate-limited right now. try again in a minute or swap to the stub ai provider."
        : `generation failed: ${rawErr}`;
      dispatch({ type: "add_error", message: niceErr });
      dispatch({ type: "set_activity", activity: "generation failed" });
      break;
    }

    case "done":
      // Stream finished — end_stream is called in the finally block
      break;

    case "error":
      console.error("[agent sse] server error event", data);
      dispatch({ type: "add_error", message: data.message as string });
      break;

    default: {
      console.warn(`[agent sse] UNHANDLED event="${event}"`, data);
    }
  }
}

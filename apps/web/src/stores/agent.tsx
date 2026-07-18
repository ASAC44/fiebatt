/**
 * Agent — conversation state for the AI chat panel.
 *
 * Mirrors the Context + useReducer pattern from edl.tsx. The agent
 * maintains a linear message list (user turns, streamed agent replies,
 * tool calls, suggestions, errors) plus an optional VideoAnalysis
 * snapshot that the backend produces after ingesting a video.
 */
import {
  createContext,
  useContext,
  useMemo,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";
import { cleanAgentText, hasAgentToolMarkup } from "@/lib/agent-text";

// ─── types ────────────────────────────────────────────────────────────

export interface SuggestedEdit {
  job_id?: string;
  start_ts: number;
  end_ts: number;
  bbox_hint?: { x: number; y: number; w: number; h: number };
  suggestion: string;
  rationale?: string;
}

export interface VideoAnalysis {
  project_id: string;
  duration: number;
  overall_description: string;
  scenes: Array<{ start_ts: number; end_ts: number; description: string }>;
  entities: Array<{
    name: string;
    category: string;
    appearances: Array<{
      start_ts: number;
      end_ts: number;
      bbox_hint?: object;
    }>;
  }>;
  mood_arc: Array<{ ts: number; mood: string }>;
  suggested_edits: SuggestedEdit[];
}

export interface VariantPreview {
  id: string;
  index: number;
  url: string | null;
  description: string | null;
  visual_coherence: number | null;
  prompt_adherence: number | null;
  attempt_label?: string | null;
  quality_state?: string | null;
  quality_evidence?: string[];
}

export interface GenerationProgressEntry {
  stage: string;
  text: string;
  ts: number;
}

// ── prompt-rewrite layer payload ─────────────────────────────────────
//
// The model takes the user's raw one-liner and turns it into a structured
// brief: intent, tone, conditioning strategy, and a video generation prompt.
// The VibeStudio chat surfaces this so users can actually SEE the expansion.
export interface PromptPlan {
  description: string | null;
  intent: string | null;
  conditioning_strategy: string | null;
  tone: string | null;
  color_grading: string | null;
  region_emphasis: string | null;
  prompt: string | null;
  prompt_for_veo: string | null;  // backward compat
}

export type AgentMessage =
  | { type: "user"; text: string; ts: number }
  | {
      type: "agent";
      text: string;
      ts: number;
      streaming?: boolean;
      toolMarkupSuppressed?: boolean;
    }
  | {
      type: "tool_call";
      id: string;
      tool: string;
      args: unknown;
      status: "pending" | "running" | "done" | "error";
      progress?: string;
      result?: unknown;
      ts: number;
    }
  | { type: "analysis"; progress: number; total: number; ts: number }
  | {
      type: "variant_preview";
      jobId: string;
      variants: VariantPreview[];
      timelineStart?: number | null;
      timelineEnd?: number | null;
      mediaStart?: number | null;
      mediaEnd?: number | null;
      ts: number;
    }
  | {
      // prompt-rewrite layer card. Starts out "rewriting…" when the
      // backend emits ``prompt_plan_started`` and fills in once
      // ``prompt_plan`` lands with the structured generation brief.
      type: "prompt_plan";
      jobId: string;
      userPrompt: string;
      plan: PromptPlan | null;
      vendor: string | null;
      ts: number;
    }
  | {
      type: "generation_progress";
      jobId: string;
      entries: GenerationProgressEntry[];
      complete: boolean;
      ts: number;
    }
  | { type: "suggestion"; edit: SuggestedEdit; accepted?: boolean; ts: number }
  | { type: "error"; message: string; ts: number };

export interface AgentState {
  messages: AgentMessage[];
  conversationId: string;
  streaming: boolean;
  activity: string | null;
  analysis: VideoAnalysis | null;
}

// ─── actions ──────────────────────────────────────────────────────────

export type AgentAction =
  | { type: "add_user_message"; text: string }
  | { type: "start_stream" }
  | { type: "resume_job_watch"; activity: string }
  | { type: "set_activity"; activity: string | null }
  | { type: "append_token"; text: string }
  | { type: "end_stream" }
  | { type: "tool_call_start"; id: string; tool: string; args: unknown }
  | { type: "tool_call_progress"; id: string; progress: string }
  | {
      type: "tool_call_end";
      id: string;
      result: unknown;
      status: "done" | "error";
    }
  | { type: "add_suggestion"; edit: SuggestedEdit }
  | {
      type: "update_generation_progress";
      jobId: string;
      stage: string;
      text: string;
      complete?: boolean;
    }
  | { type: "accept_suggestion"; ts: number }
  | { type: "dismiss_suggestion"; ts: number }
  | {
      type: "add_variant_preview";
      jobId: string;
      variants: VariantPreview[];
      timelineStart?: number | null;
      timelineEnd?: number | null;
      mediaStart?: number | null;
      mediaEnd?: number | null;
    }
  | {
      // Kick off a "rewriting prompt…" card for a generate job.
      type: "prompt_plan_started";
      jobId: string;
      userPrompt: string;
    }
  | {
      // Fill in the card with the final rewritten prompt.
      type: "prompt_plan_ready";
      jobId: string;
      plan: PromptPlan;
    }
  | {
      // Vendor dispatch marker — attaches the strategy/conditioning
      // note onto an existing plan card.
      type: "prompt_plan_dispatched";
      jobId: string;
      vendor: string;
    }
  | { type: "set_analysis"; analysis: VideoAnalysis }
  | { type: "add_error"; message: string }
  | { type: "clear_messages" }
  | { type: "set_conversation_id"; id: string }
  | { type: "hydrate_messages"; messages: AgentMessage[] };

// ─── reducer ──────────────────────────────────────────────────────────

function agentReducer(state: AgentState, action: AgentAction): AgentState {
  const now = Date.now();

  switch (action.type) {
    case "add_user_message":
      return {
        ...state,
        messages: [
          ...state.messages,
          { type: "user", text: action.text, ts: now },
        ],
      };

    case "start_stream":
      return {
        ...state,
        streaming: true,
        activity: "connecting to backend…",
        messages: [
          ...state.messages,
          { type: "agent", text: "", ts: now, streaming: true },
        ],
      };

    case "resume_job_watch":
      return {
        ...state,
        streaming: true,
        activity: action.activity,
      };

    case "set_activity":
      return { ...state, activity: action.activity };

    case "append_token": {
      const msgs = [...state.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.type === "agent" && last.streaming) {
        if (last.toolMarkupSuppressed) return state;
        const combined = last.text + action.text;
        msgs[msgs.length - 1] = {
          ...last,
          text: cleanAgentText(combined),
          toolMarkupSuppressed: hasAgentToolMarkup(combined),
        };
      }
      return { ...state, messages: msgs };
    }

    case "end_stream": {
      const msgs = state.messages.map((m) =>
        m.type === "agent" && m.streaming ? { ...m, streaming: false } : m,
      );
      // Remove empty agent messages that never received tokens
      const filtered = msgs.filter(
        (m) => !(m.type === "agent" && m.text === ""),
      );
      return { ...state, streaming: false, activity: null, messages: filtered };
    }

    case "tool_call_start":
      return {
        ...state,
        messages: [
          ...state.messages,
          {
            type: "tool_call",
            id: action.id,
            tool: action.tool,
            args: action.args,
            status: "running",
            ts: now,
          },
        ],
      };

    case "tool_call_progress": {
      return {
        ...state,
        activity: action.progress,
        messages: state.messages.map((m) =>
          m.type === "tool_call" && m.id === action.id
            ? { ...m, progress: action.progress }
            : m,
        ),
      };
    }

    case "tool_call_end": {
      const msgs = state.messages.map((m) =>
        m.type === "tool_call" && m.id === action.id
          ? { ...m, status: action.status, result: action.result }
          : m,
      );
      return { ...state, messages: msgs };
    }

    case "add_suggestion":
      return {
        ...state,
        messages: [
          ...state.messages,
          { type: "suggestion", edit: action.edit, ts: now },
        ],
      };

    case "update_generation_progress": {
      const existing = state.messages.find(
        (message) =>
          message.type === "generation_progress" && message.jobId === action.jobId,
      );
      const previousEntries =
        existing?.type === "generation_progress" ? existing.entries : [];
      const last = previousEntries[previousEntries.length - 1];
      const entries =
        last?.stage === action.stage && last.text === action.text
          ? previousEntries
          : [
              ...previousEntries,
              { stage: action.stage, text: action.text, ts: now },
            ].slice(-10);
      const progress = {
        type: "generation_progress" as const,
        jobId: action.jobId,
        entries,
        complete: action.complete ?? false,
        ts: existing?.ts ?? now,
      };
      return {
        ...state,
        messages: existing
          ? state.messages.map((message) =>
              message.type === "generation_progress" && message.jobId === action.jobId
                ? progress
                : message,
            )
          : [...state.messages, progress],
      };
    }

    case "accept_suggestion": {
      const msgs = state.messages.map((m) =>
        m.type === "suggestion" && m.ts === action.ts
          ? { ...m, accepted: true }
          : m,
      );
      return { ...state, messages: msgs };
    }

    case "dismiss_suggestion": {
      const msgs = state.messages.map((m) =>
        m.type === "suggestion" && m.ts === action.ts
          ? { ...m, accepted: false }
          : m,
      );
      return { ...state, messages: msgs };
    }

    case "add_variant_preview":
      return {
        ...state,
        messages: [
          ...state.messages.filter(
            (message) =>
              message.type !== "variant_preview" ||
              message.jobId !== action.jobId,
          ),
          {
            type: "variant_preview",
            jobId: action.jobId,
            variants: action.variants,
            timelineStart: action.timelineStart,
            timelineEnd: action.timelineEnd,
            mediaStart: action.mediaStart,
            mediaEnd: action.mediaEnd,
            ts: now,
          },
        ],
      };

    case "prompt_plan_started": {
      // Replace any existing plan card for this job (edge case: a retry
      // on the same job_id should not leave a stale stub card around).
      const filtered = state.messages.filter(
        (m) => !(m.type === "prompt_plan" && m.jobId === action.jobId),
      );
      return {
        ...state,
        messages: [
          ...filtered,
          {
            type: "prompt_plan",
            jobId: action.jobId,
            userPrompt: action.userPrompt,
            plan: null,
            vendor: null,
            ts: now,
          },
        ],
      };
    }

    case "prompt_plan_ready": {
      // Merge the rewritten plan onto the existing stub, or create a
      // fresh card if ``prompt_plan_started`` was never seen (e.g. the
      // plan bridge timed out on the start event but caught the end).
      const existing = state.messages.find(
        (m) => m.type === "prompt_plan" && m.jobId === action.jobId,
      );
      if (existing) {
        return {
          ...state,
          messages: state.messages.map((m) =>
            m.type === "prompt_plan" && m.jobId === action.jobId
              ? { ...m, plan: action.plan }
              : m,
          ),
        };
      }
      return {
        ...state,
        messages: [
          ...state.messages,
          {
            type: "prompt_plan",
            jobId: action.jobId,
            userPrompt: [...state.messages]
              .reverse()
              .find((message) => message.type === "user")?.text ?? "",
            plan: action.plan,
            vendor: null,
            ts: now,
          },
        ],
      };
    }

    case "prompt_plan_dispatched": {
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.type === "prompt_plan" && m.jobId === action.jobId
            ? { ...m, vendor: action.vendor }
            : m,
        ),
      };
    }

    case "set_analysis":
      return { ...state, analysis: action.analysis };

    case "add_error":
      return {
        ...state,
        streaming: false,
        activity: null,
        messages: [
          ...state.messages,
          { type: "error", message: action.message, ts: now },
        ],
      };

    case "clear_messages":
      return { ...state, messages: [], analysis: null };

    case "set_conversation_id":
      return { ...state, conversationId: action.id };

    case "hydrate_messages":
      return { ...state, messages: action.messages };

    default:
      return state;
  }
}

// ─── context ──────────────────────────────────────────────────────────

const initialState: AgentState = {
  messages: [],
  conversationId: crypto.randomUUID(),
  streaming: false,
  activity: null,
  analysis: null,
};

const AgentCtx = createContext<{
  state: AgentState;
  dispatch: Dispatch<AgentAction>;
} | null>(null);

export function AgentProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(agentReducer, initialState);
  const value = useMemo(() => ({ state, dispatch }), [state]);
  return <AgentCtx.Provider value={value}>{children}</AgentCtx.Provider>;
}

export function useAgent() {
  const ctx = useContext(AgentCtx);
  if (!ctx) throw new Error("useAgent must be inside AgentProvider");
  return ctx;
}

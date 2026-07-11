/* eslint-disable react/no-unescaped-entities */
import { useCallback, useEffect, useRef, useState } from "react";

import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Badge } from "@/components/ui/badge";
import {
  Message,
  MessageContent,
} from "@/components/ui/message";
import { useAgentStream } from "@/hooks/useAgentStream";
import { useEDL, totalDuration } from "@/stores/edl";
import {
  useAgent,
  type AgentMessage,
  type PromptPlan,
  type SuggestedEdit,
  type VariantPreview,
} from "@/stores/agent";
import { AgentInput } from "./AgentInput";
import { ToolCallCard } from "./ToolCallCard";

// ─── types ────────────────────────────────────────────────────────────

interface AgentChatProps {
  demoMode?: boolean;
  projectId: string | null;
}

// ─── component ────────────────────────────────────────────────────────

export function AgentChat({ demoMode = false, projectId }: AgentChatProps) {
  const { messages: liveMessages, streaming: liveStreaming, sendMessage } = useAgentStream(demoMode ? null : projectId);
  const { state: edlState } = useEDL();
  const { state: agentState, dispatch: agentDispatch } = useAgent();
  const [demoBusy, setDemoBusy] = useState(false);
  const demoTimersRef = useRef<number[]>([]);
  const messagesRef = useRef<HTMLDivElement>(null);
  const messages = demoMode ? agentState.messages : liveMessages;
  const streaming = demoMode ? demoBusy : liveStreaming;
  const lastMessage = messages[messages.length - 1];
  const lastAgentText = lastMessage?.type === "agent" ? lastMessage.text : "";

  const clearDemoTimers = useCallback(() => {
    demoTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    demoTimersRef.current = [];
  }, []);

  useEffect(() => clearDemoTimers, [clearDemoTimers]);

  useEffect(() => {
    clearDemoTimers();
    if (demoMode) {
      agentDispatch({ type: "clear_messages" });
    }
  }, [demoMode, agentDispatch, clearDemoTimers]);

  const scheduleDemo = useCallback((delay: number, fn: () => void) => {
    const timer = window.setTimeout(fn, delay);
    demoTimersRef.current.push(timer);
  }, []);

  const sendDemoMessage = useCallback((text: string) => {
    clearDemoTimers();
    setDemoBusy(true);
    const jobId = `demo-${Date.now()}`;

    agentDispatch({ type: "add_user_message", text });

    scheduleDemo(350, () => {
      agentDispatch({ type: "prompt_plan_started", jobId, userPrompt: text });
    });
    scheduleDemo(1450, () => {
      agentDispatch({
        type: "prompt_plan_ready",
        jobId,
        plan: {
          description: "Create a polished before/after demo pass with cleaner pacing and a more cinematic movement beat.",
          intent: "demo comparison",
          conditioning_strategy: "preserve_subject_motion",
          tone: "clean, confident, presentation-ready",
          color_grading: "neutral contrast with subtle warmth",
          region_emphasis: "full frame",
          prompt: "Keep the subject movement natural while improving the edit pacing and visual polish for a side-by-side comparison.",
          prompt_for_veo: null,
        },
      });
    });
    scheduleDemo(2400, () => {
      agentDispatch({ type: "prompt_plan_dispatched", jobId, vendor: "fiebatt" });
      agentDispatch({
        type: "tool_call_start",
        id: `${jobId}-analyze`,
        tool: "analyze_video",
        args: { project_id: projectId ?? "demo", playhead_ts: edlState.playhead },
      });
    });
    scheduleDemo(4200, () => {
      agentDispatch({
        type: "tool_call_end",
        id: `${jobId}-analyze`,
        status: "done",
        result: { scene_count: 3, subject: "walking subject", confidence: 0.94 },
      });
      agentDispatch({
        type: "tool_call_start",
        id: `${jobId}-generate`,
        tool: "generate_edit",
        args: { prompt: text, mode: "demo", compare_ready: true },
      });
    });
    scheduleDemo(7200, () => {
      agentDispatch({
        type: "tool_call_end",
        id: `${jobId}-generate`,
        status: "done",
        result: {
          variants: [
            {
              id: `${jobId}-variant-0`,
              index: 0,
              status: "done",
              url: "/demo-comparison.mp4",
              description: "Demo-ready comparison render",
            },
          ],
        },
      });
      agentDispatch({
        type: "add_variant_preview",
        jobId,
        variants: [
          {
            id: `${jobId}-variant-0`,
            index: 0,
            url: "/demo-comparison.mp4",
            description: "Demo-ready comparison render",
            visual_coherence: 0.96,
            prompt_adherence: 0.93,
          },
        ],
      });
    });
    scheduleDemo(8600, () => {
      agentDispatch({ type: "start_stream" });
    });

    const response = "Done. I prepared a demo render and queued it for comparison. Use Compare to show the original and modified versions side by side.";
    const responseWords = response.split(" ");
    responseWords.forEach((word, index) => {
      scheduleDemo(9000 + index * 210, () => {
        agentDispatch({ type: "append_token", text: `${word}${index === responseWords.length - 1 ? "" : " "}` });
      });
    });
    scheduleDemo(9000 + responseWords.length * 210 + 500, () => {
      agentDispatch({ type: "end_stream" });
      setDemoBusy(false);
    });
  }, [agentDispatch, clearDemoTimers, edlState.playhead, projectId, scheduleDemo]);

  useEffect(() => {
    const node = messagesRef.current;
    if (!node) return;
    window.requestAnimationFrame(() => {
      node.scrollTop = node.scrollHeight;
    });
  }, [messages.length, streaming, lastAgentText]);

  const handleSend = useCallback(
    (text: string) => {
      if (demoMode) {
        sendDemoMessage(text);
        return;
      }
      if (!projectId) return;
      // Snapshot the live editor state so the agent knows what "here" and
      // "now" mean. Without this the backend Gemini asks the user for
      // project_id / bbox even though the UI already has both.
      void sendMessage({
        projectId,
        message: text,
        playheadTs: edlState.playhead,
        duration: totalDuration(edlState.clips),
        bbox: edlState.bbox ?? null,
      });
    },
    [demoMode, projectId, sendMessage, sendDemoMessage, edlState.playhead, edlState.clips, edlState.bbox],
  );

  // Suggestion cards are a "generating…" status note — the user can't
  // accept from here because the render almost certainly isn't done yet.
  // The real accept lives on VariantPreviewCard once variants arrive.
  const handleDismissSuggestion = useCallback(
    (ts: number) => {
      agentDispatch({ type: "dismiss_suggestion", ts });
    },
    [agentDispatch],
  );

  // Variant card "apply" — user picked a specific finished variant. Ask
  // the agent to call accept_variant with that index so the generated
  // clip lands on the timeline. Studio listens for the completion event
  // (fiebatt:timeline-refresh) and re-hydrates the EDL from the server.
  const handleApplyVariant = useCallback(
    (jobId: string, variantIndex: number) => {
      if (demoMode) {
        sendDemoMessage(`Apply variant ${variantIndex} from ${jobId}.`);
        return;
      }
      if (!projectId) return;
      void sendMessage({
        projectId,
        message: `Accept variant ${variantIndex} for job ${jobId} and apply it to the timeline. After the tool succeeds, confirm it's applied in one sentence.`,
        playheadTs: edlState.playhead,
        duration: totalDuration(edlState.clips),
        bbox: edlState.bbox ?? null,
      });
    },
    [demoMode, projectId, sendMessage, sendDemoMessage, edlState.playhead, edlState.clips, edlState.bbox],
  );

  return (
    <>
      <style>{`
        @keyframes agent-cursor-blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
        @keyframes fiebatt-text-shimmer {
          0% { background-position: 200% 50%; }
          100% { background-position: -200% 50%; }
        }
        .fiebatt-shimmer-text {
          background: linear-gradient(90deg, var(--muted-foreground), var(--foreground), var(--muted-foreground));
          background-size: 200% 100%;
          -webkit-background-clip: text;
          background-clip: text;
          color: transparent;
          animation: fiebatt-text-shimmer 1.7s linear infinite;
        }
        .agent-chat {
          display: flex;
          flex-direction: column;
          height: 100%;
          background: var(--bg);
          overflow: hidden;
        }
        .agent-chat__messages {
          display: flex;
          flex: 1;
          min-height: 0;
          flex-direction: column;
          gap: 12px;
          overflow-y: auto;
          padding: 12px 18px;
          scrollbar-gutter: stable;
        }
        .agent-chat__empty {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          flex: 1;
          gap: 10px;
          color: var(--ink-ghost);
          font-family: var(--f-mono);
          font-size: 15px;
          font-weight: 500;
          text-align: center;
          padding: 24px;
        }
        .agent-chat__empty-hint {
          font-size: 13px;
          font-weight: 400;
          color: var(--ink-fade);
          max-width: 260px;
          line-height: 1.5;
        }

        /* ── message bubbles ─────────────────────────── */

        .msg--agent-cursor {
          display: inline-block;
          width: 6px;
          height: 13px;
          background: var(--ink-fade);
          margin-left: 2px;
          vertical-align: text-bottom;
          animation: agent-cursor-blink 0.8s step-end infinite;
        }
        .msg--error {
          color: var(--destructive);
        }
        .msg--tool {
          align-self: stretch;
          max-width: 100%;
        }
        .msg--suggestion {
          align-self: stretch;
          max-width: 100%;
        }

        /* ── suggestion card ─────────────────────────── */

        .suggestion-card {
          padding: 2px 0;
        }
        .suggestion-card__label {
          display: inline-flex;
          align-items: center;
          gap: 7px;
          border: 0;
          background: transparent;
          color: var(--muted-foreground);
          font: inherit;
          font-size: 15px;
          line-height: 1.4;
          padding: 2px 0;
          cursor: pointer;
        }
        .suggestion-card__label::after {
          content: "›";
          opacity: 0;
          transform: translateX(-2px);
          transition: opacity 140ms ease, transform 140ms ease;
        }
        .suggestion-card:hover .suggestion-card__label::after {
          opacity: 0.55;
          transform: translateX(1px);
        }
        .suggestion-card__text {
          font-size: 14px;
          line-height: 1.5;
          color: var(--muted-foreground);
          margin: 8px 0 6px 0;
        }
        .suggestion-card__rationale {
          font-size: 13px;
          color: var(--muted-foreground);
          line-height: 1.4;
          margin: 0 0 8px 0;
        }
        .suggestion-card__range {
          font-size: 12px;
          color: var(--muted-foreground);
          margin-bottom: 8px;
        }
        .suggestion-card__actions {
          display: flex;
          gap: 6px;
        }
        .suggestion-card__btn {
          font-family: var(--f-mono);
          font-size: 9px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          padding: 4px 10px;
          border-radius: 3px;
          border: 1px solid var(--edge);
          background: transparent;
          color: var(--ink-dim);
          cursor: pointer;
          transition: all var(--dur-s) var(--ease);
        }
        .suggestion-card__btn:hover {
          background: rgba(255, 255, 255, 0.05);
          border-color: var(--edge-2);
        }
        .suggestion-card__btn--accept {
          background: rgba(126, 231, 135, 0.08);
          border-color: rgba(126, 231, 135, 0.2);
          color: rgba(126, 231, 135, 0.9);
        }
        .suggestion-card__btn--accept:hover {
          background: rgba(126, 231, 135, 0.15);
          border-color: rgba(126, 231, 135, 0.35);
        }
        .suggestion-card__resolved {
          font-family: var(--f-mono);
          font-size: 9px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          padding: 3px 8px;
          border-radius: 3px;
        }
        .suggestion-card__resolved--accepted {
          background: rgba(126, 231, 135, 0.1);
          color: rgba(126, 231, 135, 0.7);
        }
        .suggestion-card__resolved--dismissed {
          background: rgba(255, 255, 255, 0.04);
          color: var(--ink-ghost);
        }

        /* ── variant preview ─────────────────────────── */

        .variant-preview {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .variant-preview__label {
          font-size: 13px;
          color: var(--ink-fade);
        }
        .variant-preview__grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
          gap: 12px;
        }
        .variant-preview__thumb {
          aspect-ratio: 16 / 9;
          border-radius: 8px;
          background: var(--panel-2);
          border: 1px solid var(--edge);
          overflow: hidden;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .variant-preview__thumb video {
          width: 100%;
          height: 100%;
          object-fit: cover;
        }
        .variant-preview__placeholder {
          font-size: 13px;
          color: var(--ink-ghost);
        }
        .variant-preview__desc {
          font-size: 13px;
          color: var(--ink-fade);
          text-align: center;
          margin-top: 6px;
        }

        /* ── prompt plan brief ─ */

        .prompt-plan {
          display: flex;
          flex-direction: column;
          gap: 12px;
          padding: 0;
          border: 0;
          background: transparent;
        }
        .prompt-plan__head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }
        .prompt-plan__badge {
          color: var(--muted-foreground);
          font-size: 15px;
        }
        .prompt-plan__lane {
          display: grid;
          grid-template-columns: 48px 1fr;
          gap: 12px;
          align-items: start;
        }
        .prompt-plan__lane-k {
          font-size: 12px;
          color: var(--ink-ghost);
          padding-top: 2px;
        }
        .prompt-plan__user {
          color: var(--ink-fade, rgba(255, 255, 255, 0.7));
          font-size: 12px;
          line-height: 1.45;
          font-style: italic;
        }
        .prompt-plan__content {
          color: var(--foreground);
          font-size: 12px;
          line-height: 1.55;
          padding: 2px 0;
        }
        .prompt-plan__loading {
          display: inline-flex;
          gap: 4px;
          color: var(--ink-ghost);
          font-family: var(--f-mono);
          font-size: 10px;
          letter-spacing: 0.08em;
          padding: 2px 0;
        }
        .prompt-plan__loading span {
          animation: prompt-plan-dot 1.1s ease-in-out infinite;
        }
        .prompt-plan__loading span:nth-child(2) { animation-delay: 0.18s; }
        .prompt-plan__loading span:nth-child(3) { animation-delay: 0.36s; }
        @keyframes prompt-plan-dot {
          0%, 80%, 100% { opacity: 0.25; transform: translateY(0); }
          40% { opacity: 1; transform: translateY(-1px); }
        }
        .prompt-plan__meta {
          display: flex;
          flex-wrap: wrap;
          gap: 6px 8px;
        }
        .prompt-plan__chip-k {
          color: var(--ink-ghost);
          margin-right: 5px;
        }
      `}</style>

      <div className="agent-chat">
        {/* messages */}
        <div ref={messagesRef} className="agent-chat__messages">
          {messages.length === 0 && (
            <div className="agent-chat__empty">
              <span>no conversation yet</span>
              <span className="agent-chat__empty-hint">
                describe an edit you want to make and the agent will handle the rest
              </span>
            </div>
          )}

          {messages.map((msg, i) => (
            <MessageRenderer
              key={`${msg.type}-${msg.ts}-${i}`}
              message={msg}
              onDismissSuggestion={handleDismissSuggestion}
              onApplyVariant={handleApplyVariant}
            />
          ))}
        </div>

        {/* input */}
        <AgentInput
          onSend={handleSend}
          disabled={!demoMode && !projectId}
          streaming={streaming}
        />
      </div>
    </>
  );
}

// ─── message renderer ─────────────────────────────────────────────────

function MessageRenderer({
  message,
  onDismissSuggestion,
  onApplyVariant,
}: {
  message: AgentMessage;
  onDismissSuggestion: (ts: number) => void;
  onApplyVariant: (jobId: string, variantIndex: number) => void;
}) {
  switch (message.type) {
    case "user":
      return (
        <Message align="end">
          <MessageContent>
            <Bubble align="end" variant="default">
              <BubbleContent>{message.text}</BubbleContent>
            </Bubble>
          </MessageContent>
        </Message>
      );

    case "agent":
      return (
        <Message align="start" className="w-full">
          <MessageContent className="w-full">
            <div className="w-full px-3 py-2 text-sm leading-relaxed text-muted-foreground">
              <div className="whitespace-pre-wrap">
                {message.text}
                {message.streaming && <span className="msg--agent-cursor" />}
              </div>
            </div>
          </MessageContent>
        </Message>
      );

    case "tool_call":
      return (
        <div className="msg msg--tool">
          <ToolCallCard
            id={message.id}
            tool={message.tool}
            args={message.args}
            status={message.status}
            result={message.result}
          />
        </div>
      );

    case "variant_preview":
      return (
        <div className="msg msg--tool">
          <VariantPreviewCard
            jobId={message.jobId}
            variants={message.variants}
            onApply={onApplyVariant}
          />
        </div>
      );

    case "prompt_plan":
      return (
        <div className="msg msg--tool">
          <PromptPlanCard
            userPrompt={message.userPrompt}
            plan={message.plan}
          />
        </div>
      );

    case "suggestion":
      return (
        <div className="msg msg--suggestion">
          <SuggestionCard
            edit={message.edit}
            accepted={message.accepted}
            onDismiss={() => onDismissSuggestion(message.ts)}
          />
        </div>
      );

    case "error":
      return (
        <Message align="start">
          <MessageContent>
            <Bubble align="start" variant="destructive">
              <BubbleContent>{message.message}</BubbleContent>
            </Bubble>
          </MessageContent>
        </Message>
      );

    case "analysis":
      return null;

    default:
      return null;
  }
}

// ─── suggestion card ──────────────────────────────────────────────────

function SuggestionCard({
  edit,
  accepted,
  onDismiss,
}: {
  edit: SuggestedEdit;
  accepted?: boolean;
  onDismiss: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const resolved = accepted != null;

  return (
    <div className="suggestion-card">
      <button
        type="button"
        className={`suggestion-card__label ${resolved ? "" : "fiebatt-shimmer-text"}`}
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        generating edit
      </button>

      {expanded && (
        <>
          <p className="suggestion-card__text">{edit.suggestion}</p>
          {edit.rationale && (
            <p className="suggestion-card__rationale">{edit.rationale}</p>
          )}
          <p className="suggestion-card__range">
            {formatTimestamp(edit.start_ts)} - {formatTimestamp(edit.end_ts)}
          </p>
        </>
      )}

      {resolved ? (
        <span
          className={`suggestion-card__resolved ${
            accepted
              ? "suggestion-card__resolved--accepted"
              : "suggestion-card__resolved--dismissed"
          }`}
        >
          {accepted ? "accepted" : "dismissed"}
        </span>
      ) : (
        expanded && <div className="suggestion-card__actions">
          <span className="suggestion-card__rationale" style={{ margin: 0, flex: 1 }}>
            rendering… apply from the variant preview once it's ready
          </span>
          <button
            type="button"
            className="suggestion-card__btn"
            onClick={onDismiss}
            title="hide this card"
          >
            dismiss
          </button>
        </div>
      )}
    </div>
  );
}

// ─── variant preview ──────────────────────────────────────────────────

function VariantPreviewCard({
  jobId,
  variants,
  onApply,
}: {
  jobId: string;
  variants: VariantPreview[];
  onApply: (jobId: string, variantIndex: number) => void;
}) {
  console.log(
    `[VariantPreviewCard] render job=${jobId} variants=${variants?.length ?? 0}`,
    variants,
  );
  return (
    <div className="variant-preview">
      <span className="variant-preview__label">
        {variants.length} variant{variants.length !== 1 ? "s" : ""} ready
      </span>
      <div className="variant-preview__grid">
        {variants.map((v) => (
          <div key={v.id}>
            <div className="variant-preview__thumb">
              {v.url ? (
                <video
                  src={v.url}
                  muted
                  loop
                  playsInline
                  onMouseEnter={(e) => void e.currentTarget.play().catch(() => {})}
                  onMouseLeave={(e) => {
                    e.currentTarget.pause();
                    e.currentTarget.currentTime = 0;
                  }}
                />
              ) : (
                <span className="variant-preview__placeholder">loading...</span>
              )}
            </div>
            {v.description && (
              <p className="variant-preview__desc">{v.description}</p>
            )}
            {v.url && (
              <button
                type="button"
                className="suggestion-card__btn suggestion-card__btn--accept"
                style={{ marginTop: 4, width: "100%" }}
                onClick={() => onApply(jobId, v.index)}
                title="apply this variant to the timeline"
              >
                apply
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── prompt plan card ────────────────────────────────────────────────
//
// Surfaces the prompt-rewriting layer so users can SEE what the AI
// turned their one-liner into before it gets dispatched for generation.
// Without this the whole "intelligent prompt expansion" value prop is
// invisible to the user and the edit feels like a black box. Shows the
// original request, the rewritten brief, and plan chips.

function PromptPlanCard({
  userPrompt,
  plan,
}: {
  userPrompt: string;
  plan: PromptPlan | null;
}) {
  const ready = plan != null;
  const chips: Array<{ k: string; v: string }> = [];
  if (plan) {
    if (plan.intent)
      chips.push({ k: "intent", v: plan.intent });
    if (plan.conditioning_strategy)
      chips.push({
        k: "condition",
        v: plan.conditioning_strategy.replace(/_/g, " "),
      });
    if (plan.tone) chips.push({ k: "tone", v: plan.tone });
    if (plan.region_emphasis)
      chips.push({ k: "region", v: plan.region_emphasis });
    if (plan.color_grading)
      chips.push({ k: "grade", v: plan.color_grading });
  }

  return (
    <div className="prompt-plan">
      <div className="prompt-plan__head">
        <span className="prompt-plan__badge">
          {ready ? "generation brief" : "rewriting prompt"}
        </span>
      </div>

      {userPrompt && (
        <div className="prompt-plan__lane">
          <span className="prompt-plan__lane-k">you</span>
          <p className="prompt-plan__user">{userPrompt}</p>
        </div>
      )}

      <div className="prompt-plan__lane">
        <span className="prompt-plan__lane-k">qwen</span>
        {ready ? (
          <p className="prompt-plan__content">
            {plan.prompt || plan.prompt_for_veo || plan.description || "(no prompt returned)"}
          </p>
        ) : (
          <span className="prompt-plan__loading" aria-label="rewriting prompt">
            <span>•</span>
            <span>•</span>
            <span>•</span>
          </span>
        )}
      </div>

      {chips.length > 0 && (
        <div className="prompt-plan__meta">
          {chips.map((c) => (
            <Badge
              key={c.k}
              variant="outline"
              className="h-6 rounded-full border-border/70 bg-transparent px-2.5 text-[11px] font-normal normal-case text-muted-foreground"
            >
              <span className="prompt-plan__chip-k">{c.k}</span>
              {c.v}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── helpers ─────────────────────────────────────────────────────────

function formatTimestamp(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

import { useCallback, useEffect, useRef } from "react";

import { useAgentStream } from "../hooks/useAgentStream";
import { useEDL, totalDuration } from "../stores/edl";
import {
  useAgent,
  type AgentMessage,
  type PromptPlan,
  type SuggestedEdit,
  type VariantPreview,
} from "../stores/agent";
import { AgentInput } from "./AgentInput";
import { ToolCallCard } from "./ToolCallCard";

// ─── types ────────────────────────────────────────────────────────────

interface AgentChatProps {
  projectId: string | null;
}

// ─── component ────────────────────────────────────────────────────────

export function AgentChat({ projectId }: AgentChatProps) {
  const { messages, streaming, sendMessage, clearChat } = useAgentStream(projectId);
  const { state: edlState } = useEDL();
  const { dispatch: agentDispatch } = useAgent();
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // auto-scroll to bottom on new messages (debounced to avoid jank during streaming)
  const scrollTimerRef = useRef<number>(0);
  useEffect(() => {
    clearTimeout(scrollTimerRef.current);
    scrollTimerRef.current = window.setTimeout(() => {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, 100);
  }, [messages.length, streaming]);

  const handleSend = useCallback(
    (text: string) => {
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
    [projectId, sendMessage, edlState.playhead, edlState.clips, edlState.bbox],
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
  // (iris:timeline-refresh) and re-hydrates the EDL from the server.
  const handleApplyVariant = useCallback(
    (jobId: string, variantIndex: number) => {
      if (!projectId) return;
      void sendMessage({
        projectId,
        message: `Accept variant ${variantIndex} for job ${jobId} and apply it to the timeline. After the tool succeeds, confirm it's applied in one sentence.`,
        playheadTs: edlState.playhead,
        duration: totalDuration(edlState.clips),
        bbox: edlState.bbox ?? null,
      });
    },
    [projectId, sendMessage, edlState.playhead, edlState.clips, edlState.bbox],
  );

  return (
    <>
      <style>{`
        @keyframes agent-cursor-blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
        .agent-chat {
          display: flex;
          flex-direction: column;
          height: 100%;
          background: var(--bg);
          border-left: 1px solid var(--edge);
          overflow: hidden;
        }
        .agent-chat__header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 10px 12px;
          border-bottom: 1px solid var(--edge);
          flex-shrink: 0;
        }
        .agent-chat__title {
          font-family: var(--f-mono);
          font-size: 10px;
          font-weight: 500;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          color: var(--ink-fade);
        }
        .agent-chat__clear {
          font-family: var(--f-mono);
          font-size: 9px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: var(--ink-ghost);
          padding: 3px 8px;
          border-radius: 3px;
          border: 1px solid transparent;
          background: none;
          cursor: pointer;
          transition: all var(--dur-s) var(--ease);
        }
        .agent-chat__clear:hover {
          color: var(--ink-fade);
          border-color: var(--edge);
          background: rgba(255, 255, 255, 0.03);
        }
        .agent-chat__messages {
          flex: 1;
          overflow-y: auto;
          padding: 12px;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .agent-chat__messages::-webkit-scrollbar { width: 6px; }
        .agent-chat__messages::-webkit-scrollbar-track { background: transparent; }
        .agent-chat__messages::-webkit-scrollbar-thumb {
          background: var(--panel-3);
          border-radius: 3px;
        }
        .agent-chat__empty {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          flex: 1;
          gap: 8px;
          color: var(--ink-ghost);
          font-family: var(--f-mono);
          font-size: 11px;
          text-align: center;
          padding: 24px;
        }
        .agent-chat__empty-hint {
          font-size: 10px;
          color: var(--ink-fade);
          max-width: 200px;
          line-height: 1.5;
        }

        /* ── message bubbles ─────────────────────────── */

        .msg {
          max-width: 88%;
          word-break: break-word;
        }
        .msg--user {
          align-self: flex-end;
          background: var(--panel-2);
          border: 1px solid var(--edge);
          border-radius: 8px 8px 2px 8px;
          padding: 8px 10px;
          font-family: var(--f-mono);
          font-size: 12px;
          line-height: 1.5;
          color: var(--ink);
        }
        .msg--agent {
          align-self: flex-start;
          background: var(--panel);
          border: 1px solid var(--edge);
          border-radius: 8px 8px 8px 2px;
          padding: 8px 10px;
          font-family: var(--f-mono);
          font-size: 12px;
          line-height: 1.5;
          color: var(--ink-dim);
          white-space: pre-wrap;
        }
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
          align-self: flex-start;
          background: rgba(255, 107, 107, 0.08);
          border: 1px solid rgba(255, 107, 107, 0.2);
          border-radius: 6px;
          padding: 8px 10px;
          font-family: var(--f-mono);
          font-size: 11px;
          line-height: 1.5;
          color: #ff6b6b;
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
          background: var(--panel);
          border: 1px solid var(--edge);
          border-left: 3px solid var(--ink-fade);
          border-radius: 4px;
          padding: 10px 12px;
        }
        .suggestion-card__label {
          font-family: var(--f-mono);
          font-size: 9px;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          color: var(--ink-fade);
          margin-bottom: 6px;
        }
        .suggestion-card__text {
          font-family: var(--f-mono);
          font-size: 11px;
          line-height: 1.5;
          color: var(--ink-dim);
          margin: 0 0 6px 0;
        }
        .suggestion-card__rationale {
          font-family: var(--f-mono);
          font-size: 10px;
          color: var(--ink-ghost);
          line-height: 1.4;
          margin: 0 0 8px 0;
        }
        .suggestion-card__range {
          font-family: var(--f-mono);
          font-size: 9px;
          color: var(--ink-fade);
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
          gap: 6px;
        }
        .variant-preview__label {
          font-family: var(--f-mono);
          font-size: 9px;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          color: var(--ink-fade);
        }
        .variant-preview__grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
          gap: 6px;
        }
        .variant-preview__thumb {
          aspect-ratio: 16 / 9;
          border-radius: 4px;
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
          font-family: var(--f-mono);
          font-size: 9px;
          color: var(--ink-ghost);
        }
        .variant-preview__desc {
          font-family: var(--f-mono);
          font-size: 9px;
          color: var(--ink-fade);
          text-align: center;
          margin-top: 2px;
        }

        /* ── prompt plan card (gemini prompt rewriter) ─ */

        .prompt-plan {
          display: flex;
          flex-direction: column;
          gap: 10px;
          padding: 14px;
          border-radius: 6px;
          border: 1px solid rgba(195, 167, 104, 0.22);
          background:
            linear-gradient(180deg, rgba(195, 167, 104, 0.05), rgba(195, 167, 104, 0.015)),
            var(--panel-2, rgba(20, 18, 16, 0.6));
          position: relative;
          overflow: hidden;
        }
        .prompt-plan::before {
          content: "";
          position: absolute;
          inset: 0 auto 0 0;
          width: 2px;
          background: linear-gradient(180deg,
            rgba(195, 167, 104, 0),
            rgba(195, 167, 104, 0.65) 45%,
            rgba(195, 167, 104, 0));
          opacity: 0.9;
        }
        .prompt-plan__head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }
        .prompt-plan__badge {
          font-family: var(--f-mono);
          font-size: 9px;
          letter-spacing: 0.14em;
          text-transform: uppercase;
          color: rgba(195, 167, 104, 0.9);
          display: inline-flex;
          align-items: center;
          gap: 6px;
        }
        .prompt-plan__badge::before {
          content: "";
          width: 6px;
          height: 6px;
          border-radius: 999px;
          background: rgba(195, 167, 104, 0.85);
          box-shadow: 0 0 8px rgba(195, 167, 104, 0.55);
        }
        .prompt-plan__vendor {
          font-family: var(--f-mono);
          font-size: 9px;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          color: var(--ink-ghost);
          padding: 3px 7px;
          border: 1px solid var(--edge);
          border-radius: 999px;
        }
        .prompt-plan__lane {
          display: grid;
          grid-template-columns: 56px 1fr;
          gap: 10px;
          align-items: start;
        }
        .prompt-plan__lane-k {
          font-family: var(--f-mono);
          font-size: 9px;
          letter-spacing: 0.14em;
          text-transform: uppercase;
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
          color: rgba(255, 244, 220, 0.94);
          font-size: 12px;
          line-height: 1.55;
          border-left: 1px solid rgba(195, 167, 104, 0.35);
          padding: 2px 0 2px 10px;
        }
        .prompt-plan__loading {
          display: inline-flex;
          gap: 4px;
          color: var(--ink-ghost);
          font-family: var(--f-mono);
          font-size: 10px;
          letter-spacing: 0.08em;
          padding: 2px 0 2px 10px;
          border-left: 1px solid rgba(195, 167, 104, 0.35);
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
        .prompt-plan__chip {
          font-family: var(--f-mono);
          font-size: 9px;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          color: var(--ink-fade);
          background: rgba(255, 255, 255, 0.03);
          border: 1px solid var(--edge);
          padding: 3px 7px;
          border-radius: 999px;
        }
        .prompt-plan__chip-k {
          color: var(--ink-ghost);
          margin-right: 5px;
        }
      `}</style>

      <div className="agent-chat">
        {/* header */}
        <div className="agent-chat__header">
          <span className="agent-chat__title">iris agent</span>
          {messages.length > 0 && (
            <button className="agent-chat__clear" onClick={clearChat}>
              clear
            </button>
          )}
        </div>

        {/* messages */}
        <div className="agent-chat__messages" ref={scrollRef}>
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

          <div ref={bottomRef} />
        </div>

        {/* input */}
        <AgentInput
          onSend={handleSend}
          disabled={!projectId}
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
        <div className="msg msg--user">{message.text}</div>
      );

    case "agent":
      return (
        <div className="msg msg--agent">
          {message.text}
          {message.streaming && <span className="msg--agent-cursor" />}
        </div>
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
            vendor={message.vendor}
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
        <div className="msg msg--error">{message.message}</div>
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
  const resolved = accepted != null;

  return (
    <div className="suggestion-card">
      <p className="suggestion-card__label">generating edit</p>
      <p className="suggestion-card__text">{edit.suggestion}</p>
      {edit.rationale && (
        <p className="suggestion-card__rationale">{edit.rationale}</p>
      )}
      <p className="suggestion-card__range">
        {formatTimestamp(edit.start_ts)} - {formatTimestamp(edit.end_ts)}
      </p>

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
        <div className="suggestion-card__actions">
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
  // eslint-disable-next-line no-console
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
  vendor,
}: {
  userPrompt: string;
  plan: PromptPlan | null;
  vendor: string | null;
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
        {ready && vendor && (
          <span className="prompt-plan__vendor">→ {vendor}</span>
        )}
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
            <span key={c.k} className="prompt-plan__chip">
              <span className="prompt-plan__chip-k">{c.k}</span>
              {c.v}
            </span>
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

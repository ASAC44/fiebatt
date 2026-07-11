import { useCallback, useRef, useState, type KeyboardEvent } from "react";

// ─── types ────────────────────────────────────────────────────────────

interface AgentInputProps {
  onSend: (message: string) => void;
  disabled?: boolean;
  streaming?: boolean;
}

// ─── component ────────────────────────────────────────────────────────

export function AgentInput({ onSend, disabled, streaming }: AgentInputProps) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || disabled || streaming) return;
    onSend(trimmed);
    setValue("");
  }, [value, disabled, streaming, onSend]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const placeholderText = disabled
    ? "load a project to start editing"
    : streaming
      ? "agent is working..."
      : "describe an edit...";

  const isInactive = disabled || streaming;

  return (
    <>
      <style>{`
        @keyframes agent-input-blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
        .agent-input-wrap {
          display: flex;
          align-items: flex-end;
          gap: 8px;
          padding: 10px 12px;
          background: var(--panel-2);
          border-top: 1px solid var(--edge);
          transition: border-color var(--dur-s) var(--ease);
        }
        .agent-input-wrap:focus-within {
          border-top-color: rgba(255, 255, 255, 0.15);
        }
        .agent-input-area {
          flex: 1;
          min-height: 20px;
          max-height: 120px;
          padding: 0;
          margin: 0;
          background: transparent;
          border: none;
          outline: none;
          color: var(--ink);
          font-family: var(--f-mono);
          font-size: 12px;
          line-height: 1.5;
          resize: none;
          caret-color: var(--ink);
        }
        .agent-input-area::placeholder {
          color: var(--ink-fade);
        }
        .agent-input-area:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
        .agent-input-send {
          flex-shrink: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          width: 24px;
          height: 24px;
          border-radius: 4px;
          background: rgba(255, 255, 255, 0.06);
          border: 1px solid var(--edge);
          color: var(--ink-fade);
          font-size: 12px;
          cursor: pointer;
          transition: all var(--dur-s) var(--ease);
        }
        .agent-input-send:hover:not(:disabled) {
          background: rgba(255, 255, 255, 0.1);
          color: var(--ink);
          border-color: var(--edge-2);
        }
        .agent-input-send:disabled {
          opacity: 0.3;
          cursor: not-allowed;
        }
        .agent-input-streaming {
          display: flex;
          align-items: center;
          gap: 6px;
          font-family: var(--f-mono);
          font-size: 10px;
          color: var(--ink-fade);
          letter-spacing: 0.05em;
        }
        .agent-input-streaming-dot {
          width: 4px;
          height: 4px;
          border-radius: 50%;
          background: var(--ink-fade);
          animation: agent-input-blink 1.2s ease-in-out infinite;
        }
      `}</style>

      <div className="agent-input-wrap">
        {streaming ? (
          <div className="agent-input-streaming">
            <span className="agent-input-streaming-dot" />
            <span>agent is working...</span>
          </div>
        ) : (
          <textarea
            ref={inputRef}
            className="agent-input-area"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholderText}
            disabled={isInactive}
            rows={1}
            onInput={(e) => {
              const target = e.currentTarget;
              target.style.height = "auto";
              target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
            }}
          />
        )}

        <button
          className="agent-input-send"
          onClick={handleSend}
          disabled={isInactive || !value.trim()}
          aria-label="send message"
          title="Send (Enter)"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path
              d="M1 11L11 6L1 1V5L8 6L1 7V11Z"
              fill="currentColor"
            />
          </svg>
        </button>
      </div>
    </>
  );
}

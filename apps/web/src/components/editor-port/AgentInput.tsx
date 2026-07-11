import { useCallback, useRef, useState, type KeyboardEvent } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

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
    <div className="mx-3 mb-3 rounded-lg border border-border bg-background p-2">
      <style>{`
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
      `}</style>
      <div className="relative">
        {streaming ? (
          <div className="flex min-h-28 items-start px-2 py-2 text-sm">
            <span className="fiebatt-shimmer-text">agent is working...</span>
          </div>
        ) : (
          <Textarea
            ref={inputRef}
            className="min-h-28 max-h-32 resize-none border-0 bg-transparent px-2 pb-12 text-sm shadow-none focus-visible:ring-0 dark:bg-transparent"
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

        <Button
          className="absolute right-0 bottom-0"
          size="sm"
          onClick={handleSend}
          disabled={isInactive || !value.trim()}
          aria-label="send message"
          title="Send (Enter)"
        >
          Send
        </Button>
      </div>
    </div>
  );
}

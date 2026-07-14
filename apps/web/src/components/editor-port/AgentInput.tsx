import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

// ─── types ────────────────────────────────────────────────────────────

interface AgentInputProps {
  onSend: (message: string) => void;
  disabled?: boolean;
  streaming?: boolean;
  activity?: string | null;
  onStop?: () => void;
}

// ─── component ────────────────────────────────────────────────────────

export function AgentInput({ onSend, disabled, streaming, activity, onStop }: AgentInputProps) {
  const [value, setValue] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const startedAtRef = useRef<number | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!streaming) {
      startedAtRef.current = null;
      return;
    }
    startedAtRef.current = Date.now();
    const timer = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - (startedAtRef.current ?? Date.now())) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [streaming]);

  const handleSend = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || disabled || streaming) return;
    setElapsed(0);
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
    : "describe an edit...";

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
        {streaming && (
          <div className="mb-2 rounded-md border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-xs">
            <div className="flex items-center justify-between gap-3">
              <span className="flex min-w-0 items-center gap-2 font-medium text-foreground">
                <span className="size-2 shrink-0 animate-pulse rounded-full bg-amber-400" />
                <span className="truncate">{activity || "working with the backend…"}</span>
              </span>
              <span className="shrink-0 font-mono text-muted-foreground">{formatElapsed(elapsed)}</span>
            </div>
            <p className="mt-1 text-muted-foreground">
              rendering continues as a detached backend job
            </p>
          </div>
        )}
        <Textarea
            ref={inputRef}
            className="min-h-28 max-h-32 resize-none border-0 bg-transparent px-2 pb-12 text-sm shadow-none focus-visible:ring-0 dark:bg-transparent"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholderText}
            disabled={disabled || streaming}
            rows={1}
            onInput={(e) => {
              const target = e.currentTarget;
              target.style.height = "auto";
              target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
            }}
          />

        <Button
          className="absolute right-0 bottom-0"
          size="sm"
          onClick={streaming ? onStop : handleSend}
          disabled={disabled || (!streaming && !value.trim())}
          aria-label={streaming ? "stop agent" : "send message"}
          title={streaming ? "Stop agent" : "Send (Enter)"}
        >
          {streaming ? "Stop" : "Send"}
        </Button>
      </div>
    </div>
  );
}

function formatElapsed(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

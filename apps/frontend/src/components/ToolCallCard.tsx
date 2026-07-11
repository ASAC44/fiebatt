import { useState } from "react";

function safeStringify(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(obj);
  }
}

// ─── types ────────────────────────────────────────────────────────────

interface ToolCallCardProps {
  id: string;
  tool: string;
  args: unknown;
  status: "pending" | "running" | "done" | "error";
  result?: unknown;
}

// ─── tool display names ──────────────────────────────────────────────

const TOOL_LABELS: Record<string, string> = {
  analyze_video: "analyzing video",
  identify_region: "identifying region",
  generate_edit: "generating edit",
  get_job_status: "checking job",
  accept_variant: "accepting variant",
  get_timeline: "loading timeline",
  export_video: "exporting",
};

// ─── status config ───────────────────────────────────────────────────

const STATUS_CONFIG: Record<
  ToolCallCardProps["status"],
  { color: string; icon: string; label: string }
> = {
  pending: { color: "rgba(255,255,255,0.25)", icon: "...", label: "queued" },
  running: { color: "#6cb6ff", icon: "", label: "running" },
  done: { color: "#7ee787", icon: "", label: "done" },
  error: { color: "#ff6b6b", icon: "", label: "error" },
};

// ─── component ────────────────────────────────────────────────────────

export function ToolCallCard({ id, tool, args, status, result }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  const config = STATUS_CONFIG[status];
  const displayName = TOOL_LABELS[tool] ?? tool.replace(/_/g, " ");

  const hasVariants =
    result != null &&
    typeof result === "object" &&
    "variants" in (result as Record<string, unknown>);

  // Count only variants that actually finished with a URL — otherwise
  // "1 variant(s) ready" is a lie when the provider rate-limits or the worker
  // errored, and the user stares at nothing wondering where the
  // preview is.
  const resultSummary = hasVariants
    ? (() => {
        const variants =
          ((result as Record<string, unknown>).variants as
            | Array<Record<string, unknown>>
            | undefined) ?? [];
        const ready = variants.filter((v) => typeof v.url === "string" && v.url);
        const errored = variants.filter((v) => v.status === "error");
        if (ready.length > 0) {
          return `${ready.length} variant(s) ready`;
        }
        if (errored.length > 0) {
          const errBlurb = (errored[0].error as string | undefined) ?? "unknown";
          return `generation failed: ${errBlurb.slice(0, 120)}`;
        }
        return `${variants.length} variant(s), none ready yet`;
      })()
    : null;

  return (
    <>
      <style>{`
        @keyframes tool-shimmer {
          0% { background-position: -200px 0; }
          100% { background-position: 200px 0; }
        }
        @keyframes tool-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
        @keyframes tool-spin {
          to { transform: rotate(360deg); }
        }
        .tool-card {
          position: relative;
          margin: 4px 0;
          border-radius: 4px;
          background: var(--panel);
          border: 1px solid var(--edge);
          overflow: hidden;
          cursor: pointer;
          transition: background var(--dur-s) var(--ease);
        }
        .tool-card:hover {
          background: var(--panel-2);
        }
        .tool-card__accent {
          position: absolute;
          left: 0;
          top: 0;
          bottom: 0;
          width: 3px;
        }
        .tool-card__accent--running {
          animation: tool-pulse 1.5s ease-in-out infinite;
        }
        .tool-card__header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 8px 10px 8px 14px;
          min-height: 36px;
        }
        .tool-card__name {
          font-family: var(--f-mono);
          font-size: 10px;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          color: var(--ink-dim);
        }
        .tool-card__status {
          display: flex;
          align-items: center;
          gap: 5px;
          font-family: var(--f-mono);
          font-size: 9px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
        }
        .tool-card__spinner {
          width: 10px;
          height: 10px;
          border: 1.5px solid rgba(108, 182, 255, 0.3);
          border-top-color: #6cb6ff;
          border-radius: 50%;
          animation: tool-spin 0.8s linear infinite;
        }
        .tool-card__check {
          font-size: 11px;
          line-height: 1;
        }
        .tool-card__shimmer {
          position: absolute;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: linear-gradient(
            90deg,
            transparent 0%,
            rgba(255, 255, 255, 0.02) 40%,
            rgba(255, 255, 255, 0.04) 50%,
            rgba(255, 255, 255, 0.02) 60%,
            transparent 100%
          );
          background-size: 200px 100%;
          animation: tool-shimmer 1.8s ease-in-out infinite;
          pointer-events: none;
        }
        .tool-card__body {
          padding: 0 10px 8px 14px;
          border-top: 1px solid var(--edge);
          margin-top: 0;
        }
        .tool-card__section-label {
          font-family: var(--f-mono);
          font-size: 9px;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          color: var(--ink-fade);
          margin: 8px 0 4px 0;
        }
        .tool-card__json {
          font-family: var(--f-mono);
          font-size: 10px;
          line-height: 1.5;
          color: var(--ink-dim);
          white-space: pre-wrap;
          word-break: break-all;
          max-height: 160px;
          overflow-y: auto;
          margin: 0;
          padding: 6px 8px;
          background: var(--recessed);
          border-radius: 3px;
        }
        .tool-card__summary {
          font-family: var(--f-mono);
          font-size: 10px;
          color: var(--ink-dim);
          padding: 4px 0;
        }
      `}</style>

      <div
        className="tool-card"
        onClick={() => setExpanded(!expanded)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setExpanded(!expanded);
          }
        }}
        aria-expanded={expanded}
        aria-label={`Tool call: ${displayName} — ${config.label}`}
      >
        <div
          className={`tool-card__accent ${status === "running" ? "tool-card__accent--running" : ""}`}
          style={{ background: config.color }}
        />

        {status === "running" && <div className="tool-card__shimmer" />}

        <div className="tool-card__header">
          <span className="tool-card__name">{displayName}</span>
          <span className="tool-card__status" style={{ color: config.color }}>
            {status === "running" && <span className="tool-card__spinner" />}
            {status === "done" && <span className="tool-card__check">&#10003;</span>}
            {status === "error" && <span className="tool-card__check">&#10005;</span>}
            {status === "pending" && <span>...</span>}
            <span>{config.label}</span>
          </span>
        </div>

        {expanded && (
          <div className="tool-card__body">
            <p className="tool-card__section-label">arguments</p>
            <pre className="tool-card__json">
              {safeStringify(args)}
            </pre>

            {result != null && (
              <>
                <p className="tool-card__section-label">result</p>
                {resultSummary ? (
                  <p className="tool-card__summary">{resultSummary}</p>
                ) : (
                  <pre className="tool-card__json">
                    {safeStringify(result)}
                  </pre>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </>
  );
}

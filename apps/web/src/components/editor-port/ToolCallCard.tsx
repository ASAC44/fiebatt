import { useState } from "react";

function safeStringify(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(obj);
  }
}

interface ToolCallCardProps {
  id: string;
  tool: string;
  args: unknown;
  status: "pending" | "running" | "done" | "error";
  progress?: string;
  result?: unknown;
}

const TOOL_LABELS: Record<string, string> = {
  load_project_context: "loading project context",
  analyze_video: "analyzing video",
  identify_region: "identifying region",
  clip_retrieval: "matching prompt to frames",
  sam2_region_lock: "locking subject mask",
  continuity_pass: "checking continuity",
  generate_edit: "generating edit",
  render_variants: "rendering variants",
  score_variants: "scoring variants",
  get_job_status: "checking job",
  accept_variant: "accepting variant",
  get_timeline: "loading timeline",
  export_video: "exporting",
};

const STATUS_LABELS: Record<ToolCallCardProps["status"], string> = {
  pending: "queued",
  running: "working",
  done: "done",
  error: "error",
};

const STATUS_CLASSES: Record<ToolCallCardProps["status"], string> = {
  pending: "tool-status__state--pending",
  running: "tool-status__state--running",
  done: "tool-status__state--done",
  error: "tool-status__state--error",
};

export function ToolCallCard({ tool, args, status, progress, result }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  const displayName = TOOL_LABELS[tool] ?? tool.replace(/_/g, " ");

  const hasVariants =
    result != null &&
    typeof result === "object" &&
    "variants" in (result as Record<string, unknown>);

  const resultSummary = hasVariants
    ? (() => {
        const variants =
          ((result as Record<string, unknown>).variants as
            | Array<Record<string, unknown>>
            | undefined) ?? [];
        const ready = variants.filter((v) => typeof v.url === "string" && v.url);
        const errored = variants.filter((v) => v.status === "error");
        if (ready.length > 0) return `${ready.length} variant ready`;
        if (errored.length > 0) {
          const errBlurb = (errored[0].error as string | undefined) ?? "unknown";
          return `generation failed: ${errBlurb.slice(0, 120)}`;
        }
        return `${variants.length} variant queued`;
      })()
    : null;

  return (
    <>
      <style>{`
        .tool-status {
          padding: 2px 0;
        }
        .tool-status__trigger {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          width: 100%;
          border: 0;
          background: transparent;
          color: var(--muted-foreground);
          font: inherit;
          font-size: 15px;
          line-height: 1.4;
          padding: 2px 0;
          cursor: pointer;
          text-align: left;
        }
        .tool-status__name {
          display: inline-flex;
          align-items: center;
          gap: 7px;
          min-width: 0;
        }
        .tool-status__name::after {
          content: "›";
          opacity: 0;
          transform: translateX(-2px);
          transition: opacity 140ms ease, transform 140ms ease;
        }
        .tool-status:hover .tool-status__name::after {
          opacity: 0.55;
          transform: translateX(1px);
        }
        .tool-status__state {
          color: var(--muted-foreground);
          opacity: 0.58;
          font-size: 13px;
          text-align: right;
        }
        .tool-status__state--done {
          color: #7ee787;
          opacity: 0.95;
        }
        .tool-status__state--error {
          color: var(--destructive);
          opacity: 0.95;
        }
        .tool-status__body {
          margin-top: 8px;
          display: grid;
          gap: 8px;
          padding-left: 24px;
          color: var(--muted-foreground);
          font-size: 13px;
          line-height: 1.45;
        }
        .tool-status__label {
          margin: 0 0 4px;
          color: var(--muted-foreground);
          opacity: 0.72;
          font-size: 13px;
        }
        .tool-status__json {
          margin: 0;
          max-height: 160px;
          overflow: auto;
          white-space: pre-wrap;
          word-break: break-word;
          color: var(--muted-foreground);
          opacity: 0.72;
          font-size: 13px;
          line-height: 1.45;
        }
      `}</style>

      <div className="tool-status">
        <button
          type="button"
          className="tool-status__trigger"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
        >
          <span className="tool-status__name">{displayName}</span>
          <span
            className={`tool-status__state ${STATUS_CLASSES[status]} ${
              status === "running" || status === "pending" ? "fiebatt-shimmer-text" : ""
            }`}
          >
            {STATUS_LABELS[status]}
          </span>
        </button>

        {expanded && (
          <div className="tool-status__body">
            <div>
              <p className="tool-status__label">details</p>
              <pre className="tool-status__json">{safeStringify(args)}</pre>
            </div>

            {result != null && (
              <div>
                <p className="tool-status__label">result</p>
                {resultSummary ? (
                  <p className="tool-status__json">{resultSummary}</p>
                ) : (
                  <pre className="tool-status__json">{safeStringify(result)}</pre>
                )}
              </div>
            )}
          </div>
        )}
        {status === "running" && progress && (
          <div className="tool-status__body">{progress}</div>
        )}
      </div>
    </>
  );
}

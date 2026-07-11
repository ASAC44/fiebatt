import type { ContinuityDashboardController } from "./useContinuityDashboard";

export function ContinuityStatusBadge({
  continuity,
}: {
  continuity: ContinuityDashboardController;
}) {
  const { projectEntityCount, discovery, propagation, propagationCounts } = continuity;

  let label = `${projectEntityCount} tracked`;
  let tone = "rgba(255,255,255,0.08)";

  if (discovery.status === "pending" || discovery.status === "processing") {
    label = "scan running";
    tone = "rgba(255, 196, 87, 0.2)";
  } else if (propagation.status === "pending" || propagation.status === "processing") {
    label = `pack ${propagationCounts.processing}/${propagationCounts.total || "?"}`;
    tone = "rgba(255, 196, 87, 0.2)";
  } else if (propagation.status === "ready" && propagationCounts.total > 0) {
    label = `${propagationCounts.applied}/${propagationCounts.total} applied`;
    tone = "rgba(126, 231, 135, 0.18)";
  } else if (discovery.status === "error" || propagation.status === "error") {
    label = "continuity error";
    tone = "rgba(255, 107, 107, 0.2)";
  } else if (projectEntityCount === 1) {
    label = "1 tracked";
  }

  return (
    <div
      className="mono"
      title="continuity status"
      style={{
        padding: "4px 10px",
        borderRadius: 999,
        border: "1px solid rgba(255,255,255,0.1)",
        background: tone,
        color: "rgba(255,255,255,0.75)",
        fontSize: 10,
        letterSpacing: "0.1em",
        textTransform: "uppercase",
      }}
    >
      {label}
    </div>
  );
}

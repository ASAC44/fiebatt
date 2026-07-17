/* eslint-disable @next/next/no-img-element */

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import type { EntityResp, PropagationResultResp } from "@/lib/api";
import type { ContinuityDashboardController } from "./useContinuityDashboard";

function fmtTime(seconds: number) {
  const total = Math.max(0, Math.floor(seconds));
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return `${mins}:${String(secs).padStart(2, "0")}`;
}

function appearanceLabel(entity: EntityResp, appearanceId: string) {
  const appearance = entity.appearances.find((item) => item.id === appearanceId);
  if (!appearance) return "unknown appearance";
  return `${fmtTime(appearance.start_ts)}-${fmtTime(appearance.end_ts)}`;
}

function appearanceMeta(entity: EntityResp, appearanceId: string) {
  const appearance = entity.appearances.find((item) => item.id === appearanceId);
  if (!appearance) return null;
  return `${Math.round(appearance.confidence * 100)}% confidence`;
}

function resultTone(result: PropagationResultResp) {
  if (result.applied) return "rgba(126, 231, 135, 0.12)";
  if (result.status === "error") return "rgba(255, 107, 107, 0.12)";
  if (result.status === "done") return "rgba(255, 255, 255, 0.05)";
  return "rgba(255, 196, 87, 0.1)";
}

function resultBorder(result: PropagationResultResp) {
  if (result.applied) return "1px solid rgba(126, 231, 135, 0.25)";
  if (result.status === "error") return "1px solid rgba(255, 107, 107, 0.25)";
  return "1px solid rgba(255,255,255,0.08)";
}

export function ContinuityPanel({
  continuity,
}: {
  continuity: ContinuityDashboardController;
}) {
  const {
    globalEnabled,
    globalPlan,
    selectedAppearanceIds,
    latestEntity,
    acceptedEdit,
    discovery,
    propagation,
    propagationCounts,
    hasPropagatableAppearances,
    startDiscovery,
    prepareGlobalPlan,
    startPropagation,
    toggleAppearance,
    applyPropagation,
    applyAllPropagation,
    clearLatestEntity,
  } = continuity;

  const readyToApply = propagation.results.filter(
    (result) => result.status === "done" && !result.applied,
  );
  const globalChunks = globalPlan?.occurrences.flatMap((occurrence) => occurrence.chunks) ?? [];
  const generatedChunks = globalChunks.filter((chunk) => chunk.status === "generated").length;

  const isActive = discovery.status !== "idle" || latestEntity !== null;

  return (
    <section
      style={{
        marginTop: 14,
        padding: isActive ? 16 : 12,
        borderRadius: 12,
        border: latestEntity
          ? "1px solid rgba(126, 231, 135, 0.15)"
          : discovery.status === "processing"
            ? "1px solid rgba(255, 196, 87, 0.15)"
            : "1px solid rgba(255,255,255,0.06)",
        background: latestEntity
          ? "rgba(126, 231, 135, 0.03)"
          : "rgba(255,255,255,0.02)",
        display: "grid",
        gap: 14,
        transition: "all 0.3s ease",
      }}
    >
      {/* header */}
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <div>
          <div
            className="mono"
            style={{
              fontSize: 10,
              letterSpacing: "0.14em",
              color: latestEntity ? "rgba(126, 231, 135, 0.7)" : "var(--ink-fade)",
              textTransform: "uppercase",
            }}
          >
            causal editing
          </div>
          <div style={{ fontSize: 13, color: "var(--ink)", marginTop: 2 }}>
            {discovery.status === "idle" &&
              (acceptedEdit
                ? "local edit accepted — search other occurrences only if needed"
                : "accept a variant to enable occurrence search")}
            {discovery.status === "pending" && "continuity scan queued..."}
            {discovery.status === "processing" && "scanning the reel for matching appearances..."}
            {discovery.status === "ready" &&
              (latestEntity
                ? `${latestEntity.description} found in ${latestEntity.appearances.length} location${latestEntity.appearances.length !== 1 ? "s" : ""}`
                : "continuity entity loaded")}
            {discovery.status === "error" && "continuity scan failed"}
          </div>
        </div>

        {(latestEntity || discovery.status === "error") && (
          <Button
            className="mono"
            onClick={clearLatestEntity}
            size="sm"
            style={{ flexShrink: 0 }}
            variant="ghost"
          >
            clear
          </Button>
        )}
      </div>

      {/* accepted edit context */}
      {acceptedEdit && !latestEntity && (
        <div
          className="mono"
          style={{
            fontSize: 11,
            color: "var(--ink-fade)",
            display: "grid",
            gap: 4,
          }}
        >
          <span>segment {acceptedEdit.segmentId.slice(0, 8)}</span>
          <span>prompt: {acceptedEdit.prompt || "untitled edit"}</span>
        </div>
      )}

      {acceptedEdit && !latestEntity &&
        (discovery.status === "idle" || discovery.status === "error") && (
          <Button
            onClick={() => void startDiscovery()}
            className="w-full justify-center font-mono text-[11px] text-amber-200"
            variant="outline"
          >
            find other occurrences · scans full reel
          </Button>
        )}

      {/* discovery error */}
      {discovery.error && (
        <Alert className="mono text-[11px]" variant="destructive">
          <AlertDescription>{discovery.error}</AlertDescription>
        </Alert>
      )}

      {/* entity details */}
      {latestEntity && (
        <>
          {/* entity card */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: latestEntity.reference_crop_url ? "80px 1fr" : "1fr",
              gap: 14,
              alignItems: "start",
              padding: 12,
              borderRadius: 10,
              background: "rgba(255,255,255,0.03)",
              border: "1px solid rgba(255,255,255,0.06)",
            }}
          >
            {latestEntity.reference_crop_url && (
              <img
                src={latestEntity.reference_crop_url}
                alt={latestEntity.description}
                style={{
                  width: 80,
                  height: 80,
                  objectFit: "cover",
                  borderRadius: 10,
                  border: "1px solid rgba(255,255,255,0.1)",
                }}
              />
            )}

            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ color: "var(--ink)", fontSize: 14, fontWeight: 500 }}>
                {latestEntity.description}
              </div>
              {latestEntity.category && (
                <div
                  className="mono"
                  style={{
                    fontSize: 10,
                    color: "var(--ink-fade)",
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                  }}
                >
                  {latestEntity.category}
                </div>
              )}
              <div className="mono" style={{ fontSize: 11, color: "rgba(126, 231, 135, 0.7)" }}>
                {latestEntity.appearances.length} appearance{latestEntity.appearances.length !== 1 ? "s" : ""} across the reel
              </div>
            </div>
          </div>

          {/* propagation plan */}
          {hasPropagatableAppearances && propagation.status === "idle" && (
            <Button
              onClick={() => void (globalEnabled ? prepareGlobalPlan() : startPropagation())}
              className="w-full justify-center font-mono text-[12px] tracking-[0.06em] text-emerald-200"
              variant="outline"
            >
              {globalEnabled
                ? `review plan for ${selectedAppearanceIds.length} selected appearance${selectedAppearanceIds.length !== 1 ? "s" : ""}`
                : `propagate change across ${latestEntity.appearances.length} appearance${latestEntity.appearances.length !== 1 ? "s" : ""}`}
            </Button>
          )}

          {globalEnabled && globalPlan && propagation.status === "planned" && (
            <div
              style={{
                display: "grid",
                gap: 10,
                padding: 12,
                borderRadius: 10,
                border: "1px solid rgba(126, 231, 135, 0.2)",
                background: "rgba(126, 231, 135, 0.05)",
              }}
            >
              <div className="mono" style={{ fontSize: 11, color: "var(--ink)" }}>
                {globalPlan.estimate.occurrence_count} appearance{globalPlan.estimate.occurrence_count !== 1 ? "s" : ""}
                {" · "}{globalPlan.estimate.expected_generation_calls} generation call{globalPlan.estimate.expected_generation_calls !== 1 ? "s" : ""}
                {" · "}{globalPlan.estimate.expected_generated_seconds.toFixed(1)} generated seconds
              </div>
              <div className="mono" style={{ fontSize: 10, color: "var(--ink-fade)", lineHeight: 1.5 }}>
                uses the accepted edit as the visual reference, carries edited motion between long chunks,
                and validates every boundary before results can be applied.
              </div>
              <Button
                onClick={() => void startPropagation()}
                className="w-full justify-center font-mono text-[11px] text-emerald-200"
                variant="outline"
              >
                generate reviewed plan
              </Button>
            </div>
          )}

          {/* appearances list */}
          <div style={{ display: "grid", gap: 8 }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 8,
              }}
            >
              <div className="mono" style={{ fontSize: 10, letterSpacing: "0.12em", color: "var(--ink-fade)", textTransform: "uppercase" }}>
                appearances
              </div>
            </div>
            {hasPropagatableAppearances && propagation.status !== "idle" && !globalEnabled && (
              <Button
                className="self-start font-mono text-[10px]"
                onClick={() => void startPropagation()}
                disabled={propagation.status === "pending" || propagation.status === "processing"}
                size="sm"
                variant="ghost"
              >
                {propagation.status === "ready" ? "regenerate" : "building..."}
              </Button>
            )}

            {latestEntity.appearances.length === 0 ? (
              <div className="mono" style={{ fontSize: 11, color: "var(--ink-fade)" }}>
                no extra appearances were found yet.
              </div>
            ) : (
              <div style={{ display: "grid", gap: 4 }}>
                {latestEntity.appearances.slice(0, 8).map((appearance) => {
                  const selected = selectedAppearanceIds.includes(appearance.id);
                  const selectionLocked = propagation.status === "pending" || propagation.status === "processing" || propagation.status === "ready";
                  return (
                  <label
                    key={appearance.id}
                    className="mono"
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      gap: 12,
                      fontSize: 11,
                      color: "var(--ink-fade)",
                      padding: "6px 9px",
                      borderRadius: 6,
                      background: "rgba(255,255,255,0.03)",
                      cursor: globalEnabled && !selectionLocked ? "pointer" : "default",
                    }}
                  >
                    <span style={{ display: "flex", gap: 7, alignItems: "center" }}>
                      {globalEnabled && (
                        <Checkbox
                          checked={selected}
                          disabled={selectionLocked}
                          onCheckedChange={() => toggleAppearance(appearance.id)}
                        />
                      )}
                      {fmtTime(appearance.start_ts)}-{fmtTime(appearance.end_ts)}
                    </span>
                    <span>{Math.round(appearance.confidence * 100)}%</span>
                  </label>
                  );
                })}
                {latestEntity.appearances.length > 8 && (
                  <div className="mono" style={{ fontSize: 10, color: "var(--ink-fade)", paddingLeft: 4 }}>
                    +{latestEntity.appearances.length - 8} more
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}

      {/* propagation pack */}
      {(propagation.status !== "idle" || propagation.results.length > 0 || propagation.error) && (
        <div style={{ display: "grid", gap: 10 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: 12,
            }}
          >
            <div>
              <div
                className="mono"
                style={{ fontSize: 10, letterSpacing: "0.12em", color: "var(--ink-fade)", textTransform: "uppercase" }}
              >
                continuity pack
              </div>
              <div style={{ fontSize: 12, color: "var(--ink)", marginTop: 2 }}>
                {propagation.status === "pending" && "queueing propagation jobs..."}
                {propagation.status === "planned" && "plan ready for review"}
                {propagation.status === "processing" && (
                  <>
                    {globalEnabled && globalChunks.length > 0
                      ? `generated ${generatedChunks}/${globalChunks.length} chunks`
                      : `processing ${propagationCounts.processing}/${propagationCounts.total || latestEntity?.appearances.length || 0}`}
                    <span style={{ display: "inline-block", marginLeft: 8 }}>
                      <ProgressDots />
                    </span>
                  </>
                )}
                {propagation.status === "ready" &&
                  `${propagationCounts.ready} variant${propagationCounts.ready !== 1 ? "s" : ""} ready · ${propagationCounts.applied} applied`}
                {propagation.status === "error" && "propagation failed"}
              </div>
            </div>

            {readyToApply.length > 0 && (globalEnabled || readyToApply.length > 1) && (
              <Button
                className="mono"
                onClick={() => void applyAllPropagation()}
                disabled={propagation.applyingIds.length > 0}
                size="sm"
                style={{ flexShrink: 0 }}
                variant="outline"
              >
                {globalEnabled ? `apply reviewed results (${readyToApply.length})` : `apply all (${readyToApply.length})`}
              </Button>
            )}
          </div>

          {/* propagation progress bar */}
          {(propagation.status === "pending" || propagation.status === "processing") && (
            <div style={{
              height: 3,
              borderRadius: 2,
              background: "rgba(255,255,255,0.06)",
              overflow: "hidden",
            }}>
              <div style={{
                height: "100%",
                borderRadius: 2,
                background: "rgba(255, 196, 87, 0.5)",
                width: propagationCounts.total > 0
                  ? `${Math.round(((propagationCounts.ready + propagationCounts.errors) / propagationCounts.total) * 100)}%`
                  : "15%",
                transition: "width 0.8s ease",
              }} />
            </div>
          )}

          {propagation.error && (
            <div style={{ display: "grid", gap: 8 }}>
              <Alert className="mono text-[11px]" variant="destructive">
                <AlertDescription>{propagation.error}</AlertDescription>
              </Alert>
              {globalEnabled && (
                <Button
                  className="mono"
                  onClick={() => void (globalPlan ? startPropagation() : prepareGlobalPlan())}
                  size="sm"
                  variant="outline"
                >
                  {globalPlan ? "retry failed work" : "review plan again"}
                </Button>
              )}
            </div>
          )}

          {/* results grid */}
          {latestEntity && propagation.results.length > 0 && (
            <div style={{ display: "grid", gap: 8 }}>
              {propagation.results.map((result) => {
                const isApplying = propagation.applyingIds.includes(result.id);
                return (
                  <div
                    key={result.id}
                    style={{
                      display: "grid",
                      gap: 8,
                      padding: 10,
                      borderRadius: 10,
                      background: resultTone(result),
                      border: resultBorder(result),
                      transition: "all 0.3s ease",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        gap: 12,
                        alignItems: "center",
                      }}
                    >
                      <div>
                        <div className="mono" style={{ fontSize: 11, color: "var(--ink)" }}>
                          {appearanceLabel(latestEntity, result.appearance_id)}
                        </div>
                        <div className="mono" style={{ fontSize: 10, color: "var(--ink-fade)" }}>
                          {appearanceMeta(latestEntity, result.appearance_id) || result.status}
                        </div>
                      </div>
                      <div
                        className="mono"
                        style={{
                          fontSize: 10,
                          padding: "3px 8px",
                          borderRadius: 999,
                          background: result.applied
                            ? "rgba(126, 231, 135, 0.15)"
                            : result.status === "error"
                              ? "rgba(255, 107, 107, 0.15)"
                              : "rgba(255,255,255,0.06)",
                          color: result.applied
                            ? "rgba(126, 231, 135, 0.85)"
                            : result.status === "error"
                              ? "rgba(255, 107, 107, 0.85)"
                              : "var(--ink-fade)",
                        }}
                      >
                        {result.applied ? "applied" : result.status}
                      </div>
                    </div>

                    {result.variant_url && (
                      <video
                        src={result.variant_url}
                        muted
                        loop
                        playsInline
                        controls
                        style={{
                          width: "100%",
                          borderRadius: 8,
                          background: "rgba(0,0,0,0.25)",
                        }}
                      />
                    )}

                    {result.status === "done" && !result.applied && !globalEnabled && (
                      <Button
                        className="mono"
                        onClick={() => void applyPropagation(result.id)}
                        disabled={isApplying}
                        size="sm"
                        style={{ justifySelf: "start" }}
                        variant="outline"
                      >
                        {isApplying ? "applying..." : "apply to timeline"}
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {propagation.results.length > 0 && (
            <div className="mono" style={{ fontSize: 10, color: "var(--ink-fade)", lineHeight: 1.5 }}>
              {globalEnabled
                ? "review every result first. applying writes the complete set to the saved EDL in one operation and refreshes this editor."
                : "apply only the results you want. each accepted result is added to the saved timeline."}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function ProgressDots() {
  return (
    <span style={{ display: "inline-flex", gap: 3 }}>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          style={{
            width: 3,
            height: 3,
            borderRadius: "50%",
            background: "rgba(255, 196, 87, 0.6)",
            animation: `continuity-dot 1.2s ease-in-out ${i * 0.2}s infinite`,
          }}
        />
      ))}
      <style>{`
        @keyframes continuity-dot {
          0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
          40% { opacity: 1; transform: scale(1.2); }
        }
      `}</style>
    </span>
  );
}

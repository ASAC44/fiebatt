/**
 * useAgentEdlBridge — watches agent tool call completions and refreshes
 * the EDL timeline when edits land on the backend.
 *
 * This bridges the gap between AgentChat (which talks to the backend via
 * SSE) and the EDL store (which owns the timeline state for Preview,
 * Scrubber, and Timeline components). Without this, agent edits are
 * invisible to the video player.
 */
import { useEffect, useRef } from "react";

import { getTimeline, type TimelineSegment } from "../api/client";
import { useAgent, type AgentMessage } from "../stores/agent";
import {
  useEDL,
  newMediaAsset,
  type Clip,
  type MediaAsset,
} from "../stores/edl";

/** Tools whose completion means the timeline has changed on the backend. */
const TIMELINE_TOOLS = new Set([
  "accept_variant",
  "split_segment",
  "trim_segment",
  "delete_segment",
  "color_grade",
  "revert_timeline",
  "batch_accept",
]);

export function useAgentEdlBridge(
  projectId: string | null,
  project?: { videoUrl: string; duration: number; fps: number; label?: string },
) {
  const { state: agentState } = useAgent();
  const { state: edlState, dispatch: edlDispatch } = useEDL();
  const lastCountRef = useRef(0);

  useEffect(() => {
    if (!projectId || !project) return;

    const msgs = agentState.messages;
    if (msgs.length <= lastCountRef.current) return;

    // only look at new messages since last check
    const newMsgs = msgs.slice(lastCountRef.current);
    lastCountRef.current = msgs.length;

    const needsRefresh = newMsgs.some(
      (m: AgentMessage) =>
        m.type === "tool_call" &&
        m.status === "done" &&
        TIMELINE_TOOLS.has(m.tool),
    );

    if (!needsRefresh) return;

    // re-fetch timeline from backend and rebuild EDL clips
    getTimeline(projectId)
      .then((tl) => {
        const sourceUrl =
          tl.segments.find((s) => s.source === "original")?.url ??
          project.videoUrl;

        const sourceAsset = newMediaAsset({
          url: sourceUrl,
          duration: project.duration,
          fps: project.fps,
          projectId,
          label: project.label ?? projectId.slice(0, 8),
          kind: "source",
        });

        const clips: Clip[] =
          tl.segments.length > 0
            ? tl.segments.map((seg) =>
                segmentToClip(seg, projectId, project.duration, sourceAsset),
              )
            : [
                {
                  id: crypto.randomUUID(),
                  kind: "source",
                  url: sourceAsset.url,
                  sourceStart: 0,
                  sourceEnd: sourceAsset.duration,
                  mediaDuration: sourceAsset.duration,
                  volume: 1,
                  projectId,
                  sourceAssetId: sourceAsset.id,
                  label: sourceAsset.label,
                },
              ];

        // collect generated assets for the library
        const genAssets: MediaAsset[] = [];
        const seen = new Set<string>();
        for (const seg of tl.segments) {
          if (seg.source !== "generated" || seen.has(seg.url)) continue;
          seen.add(seg.url);
          const span = Math.max(0.01, seg.end_ts - seg.start_ts);
          genAssets.push(
            newMediaAsset({
              url: seg.url,
              duration: span,
              fps: project.fps,
              projectId,
              label: `ai edit ${genAssets.length + 1}`,
              kind: "generated",
            }),
          );
        }

        edlDispatch({
          type: "hydrate",
          sources: [sourceAsset, ...genAssets],
          clips,
        });
      })
      .catch((err) => {
        console.warn("[agent-edl-bridge] timeline refresh failed:", err);
      });
  }, [agentState.messages.length, projectId, project, edlDispatch]);
}

function segmentToClip(
  seg: TimelineSegment,
  projectId: string,
  projectDuration: number,
  sourceAsset: MediaAsset,
): Clip {
  const span = Math.max(0.01, seg.end_ts - seg.start_ts);
  if (seg.source === "generated") {
    return {
      id: crypto.randomUUID(),
      kind: "generated",
      url: seg.url,
      sourceStart: 0,
      sourceEnd: span,
      mediaDuration: span,
      volume: seg.audio ? 1 : 0,
      projectId,
      label: "ai edit",
    };
  }
  return {
    id: crypto.randomUUID(),
    kind: "source",
    url: seg.url,
    sourceStart: seg.start_ts,
    sourceEnd: seg.end_ts,
    mediaDuration: projectDuration,
    volume: seg.audio ? 1 : 0,
    projectId,
    sourceAssetId: sourceAsset.id,
    label: sourceAsset.label,
  };
}

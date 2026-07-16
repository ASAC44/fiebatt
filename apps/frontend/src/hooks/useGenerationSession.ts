/* eslint-disable react-hooks/exhaustive-deps, react-hooks/set-state-in-effect */
import { useEffect, useRef, useState } from "react";

import {
  accept,
  createEditPlan,
  generate,
  getHealth,
  pollJob,
  streamJobEvents,
  type AcceptResp,
  type EditPlanResp,
  type JobResp,
  type JobStreamEvent,
  type Variant,
  type BBox,
} from "@/lib/api";
import type { Clip } from "@/stores/edl";

type GenerationTarget = Pick<
  Clip,
  "id" | "projectId" | "sourceStart" | "sourceEnd" | "volume"
> & {
  sourceClipStart: number;
  sourceClipEnd: number;
};

type UseGenerationSessionArgs = {
  clip: Clip | null;
  sourceClip?: Clip | null;
  bbox: BBox | null;
  selectionId?: string | null;
  previewFrameTs: number | null;
  onAccepted?: (payload: AcceptedVariantPayload) => void | Promise<void>;
};

export type GenerationLogEntry = JobStreamEvent & { id: string };
export type AcceptedVariantPayload = {
  acceptResponse: AcceptResp;
  prompt: string;
  sourceVariantUrl: string;
  projectId: string;
};

export function useGenerationSession({
  clip,
  sourceClip,
  bbox,
  selectionId,
  previewFrameTs,
  onAccepted,
}: UseGenerationSessionArgs) {
  const [prompt, setPrompt] = useState("");
  const [plan, setPlan] = useState<EditPlanResp | null>(null);
  const [planning, setPlanning] = useState(false);
  const [fallbackNotice, setFallbackNotice] = useState<string | null>(null);
  const [adaptivePlanningEnabled, setAdaptivePlanningEnabled] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [variants, setVariants] = useState<Variant[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [acceptingIdx, setAcceptingIdx] = useState<number | null>(null);
  const [logs, setLogs] = useState<GenerationLogEntry[]>([]);
  const [result, setResult] = useState<JobResp | null>(null);
  const jobIdRef = useRef<string | null>(null);
  const generationTargetRef = useRef<GenerationTarget | null>(null);
  const streamCtlRef = useRef<AbortController | null>(null);

  const canGenerate =
    !!clip &&
    clip.kind === "source" &&
    !!clip.projectId &&
    !!prompt.trim() &&
    !planning &&
    !busy &&
    adaptivePlanningEnabled !== null;

  function updatePrompt(value: string) {
    setPrompt(value);
    setFallbackNotice(null);
    if (plan && plan.intent.raw_prompt !== value.trim()) setPlan(null);
  }

  function closeStream() {
    streamCtlRef.current?.abort();
    streamCtlRef.current = null;
  }

  function clearSession({ keepPrompt = true }: { keepPrompt?: boolean } = {}) {
    closeStream();
    setVariants([]);
    setStatus("");
    setErr(null);
    setAcceptingIdx(null);
    setLogs([]);
    setResult(null);
    jobIdRef.current = null;
    generationTargetRef.current = null;
    setPlan(null);
    setPlanning(false);
    setFallbackNotice(null);
    if (!keepPrompt) setPrompt("");
  }

  useEffect(() => {
    clearSession();
  }, [clip?.id, selectionId]);

  useEffect(() => {
    let cancelled = false;
    void getHealth()
      .then((health) => {
        if (!cancelled) {
          setAdaptivePlanningEnabled(
            health.features?.adaptive_edit_planning === true,
          );
        }
      })
      .catch(() => {
        // An older/unavailable backend must retain the known-safe legacy path.
        if (!cancelled) setAdaptivePlanningEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return () => closeStream();
  }, []);

  async function run(): Promise<boolean> {
    if (!canGenerate || !clip || !clip.projectId) return false;
    if (bbox && adaptivePlanningEnabled && !plan) {
      if (selectionId) {
        const planned = await preparePlan(undefined, true);
        if (planned) return true;
      } else {
        setFallbackNotice(
          "Precise selection unavailable. Rendering with legacy fixed window.",
        );
      }
    } else if (bbox && adaptivePlanningEnabled === false) {
      setFallbackNotice(
        "Adaptive planning is disabled by the backend. Rendering with the legacy fixed window.",
      );
    }
    const baseClip = sourceClip ?? clip;
    closeStream();
    setBusy(true);
    setErr(null);
    setStatus("queued");
    setVariants([]);
    setResult(null);
    setAcceptingIdx(null);
    setLogs([]);
    generationTargetRef.current = {
      id: baseClip.id,
      projectId: clip.projectId,
      sourceStart: clip.sourceStart,
      sourceEnd: clip.sourceEnd,
      volume: baseClip.volume,
      sourceClipStart: baseClip.sourceStart,
      sourceClipEnd: baseClip.sourceEnd,
    };
    try {
      const { job_id } = await generate({
        project_id: clip.projectId,
        target_clip_id: baseClip.id,
        plan_id: plan?.plan_id,
        start_ts: clip.sourceStart,
        end_ts: clip.sourceEnd,
        bbox: bbox ?? { x: 0, y: 0, w: 1, h: 1 },
        prompt: prompt.trim(),
        reference_frame_ts: previewFrameTs ?? (clip.sourceStart + clip.sourceEnd) / 2,
      });
      jobIdRef.current = job_id;

      // open the SSE console stream in parallel with the poll loop.
      // the stream carries thought-process events; the poll resolves when
      // the job flips to done/error so we can still drive variants state.
      streamCtlRef.current = streamJobEvents(job_id, {
        onEvent: (event) => {
          setLogs((prev) => [
            ...prev,
            { ...event, id: `${event.ts}-${prev.length}` },
          ]);
        },
        onError: (e) => {
          // stream failure is non-fatal — the poll loop still drives state.
          setLogs((prev) => [
            ...prev,
            {
              id: `err-${Date.now()}`,
              ts: Date.now() / 1000,
              stage: "stream_error",
              msg: `event stream dropped: ${String(e)}`,
            },
          ]);
        },
      });

      const final: JobResp = await pollJob(job_id, (job) => setStatus(job.status));
      if (final.status !== "done" || !final.variants.length) {
        throw new Error(final.error || "generation failed");
      }
      setResult(final);
      setVariants(final.variants);
      return true;
    } catch (e) {
      setErr(String(e));
      generationTargetRef.current = null;
      return false;
    } finally {
      setBusy(false);
      setStatus("");
    }
  }

  async function preparePlan(
    explicitRange?: { start: number; end: number },
    allowLegacyFallback = false,
  ): Promise<boolean> {
    if (!clip?.projectId || !selectionId || !prompt.trim()) return false;
    setPlanning(true);
    setErr(null);
    setStatus("planning");
    try {
      const next = await createEditPlan({
        project_id: clip.projectId,
        selection_id: selectionId,
        prompt: prompt.trim(),
        explicit_start_ts: explicitRange?.start,
        explicit_end_ts: explicitRange?.end,
      });
      if (!next.adaptive_generation_enabled) {
        setPlan(null);
        setAdaptivePlanningEnabled(false);
        if (allowLegacyFallback) {
          setFallbackNotice(
            "Adaptive planning is disabled by the backend. Rendering with the legacy fixed window.",
          );
        } else {
          setErr("Adaptive planning was disabled before this plan could be used.");
        }
        return false;
      }
      setPlan(next);
      setFallbackNotice(null);
      return true;
    } catch (error) {
      if (allowLegacyFallback) {
        setFallbackNotice(
          "Adaptive planning unavailable. Rendering with legacy fixed window.",
        );
      } else {
        setErr(String(error));
      }
      return false;
    } finally {
      setPlanning(false);
      setStatus("");
    }
  }

  async function acceptVariant(idx: number): Promise<boolean> {
    const target = generationTargetRef.current;
    if (!target || !target.projectId || !jobIdRef.current) return false;
    setAcceptingIdx(idx);
    try {
      const variant = variants[idx];
      if (!variant?.url) throw new Error("variant has no url");
      const accepted = await accept(jobIdRef.current, idx);
      const trimmedPrompt = prompt.trim();
      window.dispatchEvent(
        new CustomEvent("fiebatt:timeline-refresh", {
          detail: { tool: "accept_variant", timeline: accepted.timeline },
        }),
      );
      setPrompt("");
      clearSession({ keepPrompt: false });
      if (onAccepted) {
        void Promise.resolve(
          onAccepted({
            acceptResponse: accepted,
            prompt: trimmedPrompt,
            sourceVariantUrl: variant.url,
            projectId: target.projectId,
          }),
        ).catch(() => {});
      }
      return true;
    } catch (e) {
      setErr(String(e));
      setAcceptingIdx(null);
      return false;
    }
  }

  return {
    prompt,
    setPrompt: updatePrompt,
    plan,
    planning,
    fallbackNotice,
    adaptivePlanningEnabled,
    busy,
    status,
    variants,
    err,
    setErr,
    acceptingIdx,
    canGenerate,
    logs,
    result,
    run,
    adjustPlan: (start: number, end: number) => preparePlan({ start, end }),
    acceptVariant,
    clearSession,
  };
}

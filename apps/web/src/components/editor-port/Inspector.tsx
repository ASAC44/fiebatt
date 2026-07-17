/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useState, type ReactNode } from "react";
import type { EditPlanResp } from "@/lib/api";
import { clipAtTime, duration, sourceTimeFor, useEDL, type Clip } from "@/stores/edl";
import { Icon, type IconName } from "./Icon";
import { AgentChat } from "./AgentChat";
import { ContinuityPanel } from "@/features/continuity/ContinuityPanel";
import type { ContinuityDashboardController } from "@/features/continuity/useContinuityDashboard";
import { GenerationReveal } from "@/features/reveal/GenerationReveal";
import {
  buildEditWindow,
  MIN_EDIT_WINDOW_SECONDS,
} from "@/features/reveal/editWindow";
import { useGenerationSession } from "@/hooks/useGenerationSession";
import "./inspector.css";

type Tab = "ai" | "continuity" | "basic" | "info" | "agent";

export function Inspector({
  continuity,
  projectId,
}: {
  continuity: ContinuityDashboardController;
  projectId: string | null;
}) {
  const [tab, setTab] = useState<Tab>("ai");
  const { state } = useEDL();
  const selected = state.clips.find((c) => c.id === state.selectedId) ?? null;

  return (
    <div className="insp">
      <nav className="insp__tabs">
        <InspTab active={tab === "ai"} onClick={() => setTab("ai")} icon="sparkles" label="AI" />
        <InspTab active={tab === "continuity"} onClick={() => setTab("continuity")} icon="select" label="Flow" />
        <InspTab active={tab === "basic"} onClick={() => setTab("basic")} icon="sliders" label="Basic" />
        <InspTab active={tab === "info"} onClick={() => setTab("info")} icon="info" label="Info" />
        <InspTab active={tab === "agent"} onClick={() => setTab("agent")} icon="keyboard" label="Agent" />
      </nav>

      <div className="insp__body">
        {tab === "ai" && <AiTab continuity={continuity} />}
        {tab === "continuity" && <ContinuityTab continuity={continuity} />}
        {tab === "basic" && <BasicTab />}
        {tab === "info" && <InfoTab continuity={continuity} />}
        {tab === "agent" && <AgentChat projectId={projectId} />}
      </div>

      {selected && (
        <footer className="insp__foot mono">
          <span className={`chip chip--${selected.kind}`}>{selected.kind}</span>
          <span>{selected.label ?? "untitled"}</span>
          <span className="insp__foot-dur">{duration(selected).toFixed(2)}s</span>
        </footer>
      )}
    </div>
  );
}

function InspTab({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: IconName;
  label: string;
}) {
  return (
    <button className={`insp__tab ${active ? "insp__tab--on" : ""}`} onClick={onClick}>
      <Icon name={icon} size={14} />
      <span>{label}</span>
    </button>
  );
}

// ─── AI tab ──────────────────────────────────────────────────────────

function AiTab({
  continuity,
}: {
  continuity: ContinuityDashboardController;
}) {
  const { state, dispatch } = useEDL();
  const [lockedContext, setLockedContext] = useState<{
    clip: Clip;
    sourceClip: Clip;
    previewFrameTs: number | null;
    windowLabel: string;
  } | null>(null);
  const bbox = state.bbox;

  // Entity identification + SAM mask fetch lives in Preview — this tab just
  // reflects whatever's currently in the EDL.
  const entity = state.identified;
  const identifying = state.identifying;
  const activeHit = clipAtTime(state.clips, state.playhead);
  const sourceClip = activeHit?.clip ?? null;
  const previewFrameTs =
    activeHit && sourceClip
      ? sourceTimeFor(activeHit.clip, activeHit.offsetInClip)
      : sourceClip
        ? (sourceClip.sourceStart + sourceClip.sourceEnd) / 2
        : null;
  const editWindow = buildEditWindow(sourceClip, previewFrameTs);
  const activeClip = lockedContext?.clip ?? editWindow?.clip ?? null;
  const activeSourceClip = lockedContext?.sourceClip ?? sourceClip;
  const activePreviewFrameTs =
    lockedContext?.previewFrameTs ?? editWindow?.previewFrameTs ?? previewFrameTs;
  const activeWindowLabel = lockedContext?.windowLabel ?? editWindow?.label ?? null;
  const hasValidTarget = Boolean(lockedContext || editWindow?.valid);

  const {
    prompt,
    setPrompt,
    plan,
    planning,
    fallbackNotice,
    proLimit,
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
    adjustPlan,
    acceptVariant,
    clearSession,
  } = useGenerationSession({
    clip: activeClip,
    sourceClip: activeSourceClip,
    bbox,
    selectionId: state.mask?.selectionId,
    previewFrameTs: activePreviewFrameTs,
    onAccepted: async ({ acceptResponse, prompt, sourceVariantUrl }) => {
      await continuity.beginAcceptedEdit({
        prompt,
        sourceVariantUrl,
        segmentId: acceptResponse.segment_id,
        entityJobId: acceptResponse.entity_job_id,
      });
    },
  });
  const activeSession = busy || planning || !!plan || variants.length > 0 || acceptingIdx != null;
  const displayedWindowLabel = plan
    ? `${formatPlanTime(plan.edit_core.start_ts)}–${formatPlanTime(plan.edit_core.end_ts)} core`
    : bbox && adaptivePlanningEnabled
      ? "adaptive range pending"
      : activeWindowLabel;

  useEffect(() => {
    if (!activeSession) {
      setLockedContext(null);
    }
  }, [activeSession]);

  async function runReveal() {
    if (!sourceClip || sourceClip.kind !== "source" || !sourceClip.projectId || !editWindow?.valid) {
      return false;
    }
    setLockedContext({
      clip: editWindow.clip,
      sourceClip,
      previewFrameTs: editWindow.previewFrameTs,
      windowLabel: editWindow.label,
    });
    return run();
  }

  async function acceptReveal(idx: number) {
    return acceptVariant(idx);
  }

  function clearReveal() {
    setLockedContext(null);
    clearSession();
  }

  if (!sourceClip) {
    return <Hint>park the playhead on a source clip to aim the ai edit window.</Hint>;
  }
  if (sourceClip.kind !== "source") {
    return (
      <Hint>
        ai runs on source clips. move the playhead onto original footage before prompting.
      </Hint>
    );
  }
  if (!hasValidTarget) {
    return (
      <Hint>
        this source slice is shorter than {MIN_EDIT_WINDOW_SECONDS}s, so there is not enough footage to run the edit cleanly.
      </Hint>
    );
  }
  if (!activeClip) {
    return <Hint>move the playhead onto a valid source window before prompting.</Hint>;
  }

  return (
    <section className="pane">
      <div className="reveal-host reveal-host--panel">
        {plan && variants.length === 0 ? (
          <EditPlanPreview plan={plan} busy={planning || busy} onAdjust={adjustPlan} />
        ) : null}
        {proLimit && variants.length === 0 ? (
          <ProLimitCard
            limitSeconds={proLimit.limitSeconds}
            detectedSeconds={proLimit.detectedSeconds}
          />
        ) : null}
        <GenerationReveal
          clip={activeClip}
          bbox={bbox}
          entity={entity}
          identifying={identifying}
          layout="panel"
          windowLabel={displayedWindowLabel}
          session={{
            prompt,
            setPrompt,
            busy: busy || planning,
            status,
            variants,
            err,
            setErr,
            acceptingIdx,
            canGenerate,
            logs,
            result,
            runLabel: plan
              ? "generate planned edit"
              : bbox && adaptivePlanningEnabled
                ? "preview adaptive plan"
                : adaptivePlanningEnabled === null
                  ? "checking generation mode"
                  : "generate variants",
            notice: fallbackNotice,
            run: runReveal,
            acceptVariant: acceptReveal,
            clearSession: clearReveal,
          }}
          onClearRegion={() => dispatch({ type: "set_bbox", bbox: null })}
        />
      </div>

      <ContinuityPanel continuity={continuity} />
    </section>
  );
}

function ProLimitCard({
  limitSeconds,
  detectedSeconds,
}: {
  limitSeconds: number;
  detectedSeconds: number | null;
}) {
  const measured = detectedSeconds == null
    ? `more than ${limitSeconds.toFixed(0)} seconds`
    : `${detectedSeconds.toFixed(1)} seconds`;
  return (
    <aside className="pro-limit" aria-label="long edit limit">
      <div className="pro-limit__glow" aria-hidden="true" />
      <div className="pro-limit__head">
        <span className="pro-limit__badge mono">PRO · COMING SOON</span>
        <span className="pro-limit__safe mono">render safely paused</span>
      </div>
      <h3>This edit wants a bigger timeline.</h3>
      <p>
        We mapped a {measured} subject window, then stopped before starting any
        video render calls. This workspace currently supports up to {limitSeconds.toFixed(0)} seconds.
      </p>
      <ul>
        <li>edits with more than 30 seconds of motion context</li>
        <li>automatic continuation across long scenes</li>
        <li>larger quality-check and retry budgets</li>
      </ul>
      <p className="pro-limit__hint mono">
        For now, trim the source clip or ask for a shorter action.
      </p>
    </aside>
  );
}

function EditPlanPreview({
  plan,
  busy,
  onAdjust,
}: {
  plan: EditPlanResp;
  busy: boolean;
  onAdjust: (start: number, end: number) => Promise<boolean>;
}) {
  const [start, setStart] = useState(plan.edit_core.start_ts);
  const [end, setEnd] = useState(plan.edit_core.end_ts);

  useEffect(() => {
    setStart(plan.edit_core.start_ts);
    setEnd(plan.edit_core.end_ts);
  }, [plan.plan_id, plan.edit_core.start_ts, plan.edit_core.end_ts]);

  const valid = end > start && start >= plan.occurrence_start && end <= plan.occurrence_end;
  return (
    <section className="edit-plan" aria-label="adaptive edit plan">
      <div className="edit-plan__head">
        <span className="mono">planned before render</span>
        <strong>{Math.round(plan.confidence * 100)}% confidence</strong>
      </div>
      <div className="edit-plan__lane mono">
        <span>occurrence {formatPlanTime(plan.occurrence_start)}–{formatPlanTime(plan.occurrence_end)}</span>
        <span>context {formatPlanTime(plan.generation_context.start_ts)}–{formatPlanTime(plan.generation_context.end_ts)}</span>
      </div>
      <div className="edit-plan__range">
        <label>
          core in
          <input
            type="number"
            min={plan.occurrence_start}
            max={plan.occurrence_end}
            step={0.1}
            value={start}
            onChange={(event) => setStart(Number(event.target.value))}
          />
        </label>
        <label>
          core out
          <input
            type="number"
            min={plan.occurrence_start}
            max={plan.occurrence_end}
            step={0.1}
            value={end}
            onChange={(event) => setEnd(Number(event.target.value))}
          />
        </label>
        <button disabled={!valid || busy} onClick={() => void onAdjust(start, end)}>
          update
        </button>
      </div>
      <div className="edit-plan__meta mono">
        <span>{plan.provider}</span>
        <span>{plan.estimate.expected_generation_calls} {plan.estimate.expected_generation_calls === 1 ? "call" : "calls"}</span>
        <span>{plan.estimate.expected_generated_seconds.toFixed(1)} generated seconds</span>
        <span>{plan.estimate.analysis_duration_ms.toFixed(0)}ms planning</span>
        <span>{plan.estimate.frames_inspected} frames inspected</span>
      </div>
      {plan.warnings.map((warning) => (
        <p className="edit-plan__warning mono" key={warning}>{warning}</p>
      ))}
    </section>
  );
}

function formatPlanTime(value: number) {
  return `${value.toFixed(2)}s`;
}

function ContinuityTab({
  continuity,
}: {
  continuity: ContinuityDashboardController;
}) {
  return (
    <section className="pane">
      <ContinuityPanel continuity={continuity} />
    </section>
  );
}

// ─── Basic tab (volume etc.) ─────────────────────────────────────────

function BasicTab() {
  const { state, dispatch } = useEDL();
  const selected = state.clips.find((c) => c.id === state.selectedId) ?? null;
  if (!selected) return <Hint>Nothing selected.</Hint>;

  return (
    <section className="pane">
      <FieldHead label="Volume" hint="per-clip gain · 0–100" />
      <div className="slider-row">
        <Icon name={selected.volume === 0 ? "volume-mute" : "volume"} size={14} />
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={selected.volume}
          onChange={(e) =>
            dispatch({ type: "set_volume", id: selected.id, v: +e.target.value })
          }
        />
        <span className="mono slider-row__val">
          {Math.round(selected.volume * 100)}
        </span>
      </div>

      <FieldHead label="Shortcuts" hint="keyboard" />
      <dl className="keys">
        <dt className="mono">space</dt><dd>play / pause</dd>
        <dt className="mono">s</dt><dd>split at playhead</dd>
        <dt className="mono">⌫</dt><dd>delete selected</dd>
        <dt className="mono">drag edge</dt><dd>trim clip</dd>
        <dt className="mono">click clip</dt><dd>select</dd>
      </dl>
    </section>
  );
}

// ─── Info tab ────────────────────────────────────────────────────────

function InfoTab({
  continuity,
}: {
  continuity: ContinuityDashboardController;
}) {
  const { state } = useEDL();
  const selected = state.clips.find((c) => c.id === state.selectedId) ?? null;
  if (state.sources.length === 0) return <Hint>Import a clip to get started.</Hint>;
  return (
    <section className="pane">
      <FieldHead label="Library" />
      <div className="pane__meta">
        <Row k="sources"    v={String(state.sources.length)} />
        <Row k="clip count" v={String(state.clips.length)} />
        <Row k="entities"   v={String(continuity.projectEntityCount)} />
      </div>

      {selected && (
        <>
          <FieldHead label="Selected clip" />
          <div className="pane__meta">
            <Row k="kind"   v={selected.kind} />
            <Row k="label"  v={selected.label ?? ""} />
            <Row k="in"     v={`${selected.sourceStart.toFixed(3)}s`} />
            <Row k="out"    v={`${selected.sourceEnd.toFixed(3)}s`} />
            <Row k="dur"    v={`${duration(selected).toFixed(3)}s`} />
            <Row k="source" v={`${selected.mediaDuration.toFixed(2)}s (max)`} />
          </div>
        </>
      )}
    </section>
  );
}

// ─── helpers ────────────────────────────────────────────────────────

// ─── bits ────────────────────────────────────────────────────────────

function Hint({ children }: { children: ReactNode }) {
  return <p className="insp__hint">{children}</p>;
}

function FieldHead({ label, hint }: { label: string; hint?: string }) {
  return (
    <div className="fhead">
      <span className="label">{label}</span>
      {hint && <span className="fhead__hint">{hint}</span>}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="row">
      <span className="label row__k">{k}</span>
      <span className="mono row__v" title={v}>{v}</span>
    </div>
  );
}

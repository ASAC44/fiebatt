import { useEffect, useMemo, useRef, useState } from "react";

import {
  isEditorChecklistDismissed,
  setEditorChecklistDismissed,
} from "./storage";
import "./editor-checklist.css";

type ChecklistStep = {
  label: string;
  hint: string;
  done: boolean;
};

export function EditorChecklist({
  projectId,
  hasSources,
  hasSelection,
  hasBbox,
  hasAcceptedEdit,
  hasContinuityPack,
  continuityComplete,
  onImport,
}: {
  projectId?: string | null;
  hasSources: boolean;
  hasSelection: boolean;
  hasBbox: boolean;
  hasAcceptedEdit: boolean;
  hasContinuityPack: boolean;
  continuityComplete: boolean;
  onImport: () => void;
}) {
  const scope = projectId || "draft";
  // Lazy-init from storage so the first render already reflects a prior
  // dismiss — no brief flicker on reopen. Keyed on `scope` below to pull
  // fresh when the reel changes (tab swap, not same-project re-render).
  const [dismissed, setDismissed] = useState(() =>
    isEditorChecklistDismissed(scope),
  );
  // Track the last scope we synced for so we only re-read localStorage
  // when the user actually switches reels. Without this guard, `scope`
  // flips "draft" → realProjectId a tick after mount on fresh loads,
  // which would clobber a dismiss the user just made.
  const lastSyncedScopeRef = useRef(scope);
  useEffect(() => {
    if (lastSyncedScopeRef.current === scope) return;
    lastSyncedScopeRef.current = scope;
    setDismissed(isEditorChecklistDismissed(scope));
  }, [scope]);

  const steps = useMemo<ChecklistStep[]>(() => {
    const continuityHint = hasContinuityPack
      ? "your tracked subject showed up elsewhere. review the continuity pack before export."
      : "once a variant lands, iris can search for matching appearances and prep continuity edits.";

    return [
      {
        label: "bring in footage",
        hint: "upload a source reel before anything else happens.",
        done: hasSources,
      },
      {
        label: "pick the moment",
        hint: "scrub the timeline, grab the clip you want, and make the target explicit.",
        done: hasSelection,
      },
      {
        label: "box the subject",
        hint: "freeze on the frame that matters and drag a box around the thing you want to rewrite.",
        done: hasBbox,
      },
      {
        label: "generate and accept",
        hint: "write the edit like a fact change, compare variants, then commit one to the timeline.",
        done: hasAcceptedEdit,
      },
      {
        label: hasContinuityPack && !continuityComplete ? "review continuity pack" : "export the cut",
        hint: continuityHint,
        done: continuityComplete || (hasAcceptedEdit && !hasContinuityPack),
      },
    ];
  }, [continuityComplete, hasAcceptedEdit, hasBbox, hasContinuityPack, hasSelection, hasSources]);

  const completed = steps.filter((step) => step.done).length;
  const nextStep = steps.find((step) => !step.done);

  // Dismiss is user intent — always honor it, regardless of how many
  // steps are checked off. The old guard required `completed < total`
  // which meant finishing every step silently "un-dismissed" the panel.
  if (dismissed) return null;

  return (
    <section className="editor-checklist" aria-label="editor onboarding checklist">
      <div className="editor-checklist__header">
        <div>
          <div className="editor-checklist__eyebrow">guided edit flow</div>
          <h2 className="editor-checklist__title">don&apos;t boil the ocean all at once</h2>
          <p className="editor-checklist__copy">
            the core loop is still simple: choose a moment, tell iris what fact to rewrite,
            accept the best result, then either propagate it or export.
          </p>
        </div>
        <button
          type="button"
          className="editor-checklist__dismiss"
          aria-label="dismiss checklist"
          onClick={() => {
            setEditorChecklistDismissed(scope, true);
            setDismissed(true);
          }}
        >
          ×
        </button>
      </div>

      <div className="editor-checklist__progress">
        <div className="editor-checklist__progress-label">
          <span>activation</span>
          <span>{completed}/{steps.length}</span>
        </div>
        <div className="editor-checklist__bar">
          <div
            className="editor-checklist__bar-fill"
            style={{ width: `${(completed / steps.length) * 100}%` }}
          />
        </div>
      </div>

      <div className="editor-checklist__items">
        {steps.map((step, index) => {
          const isActive = !step.done && nextStep?.label === step.label;
          return (
            <article
              key={step.label}
              className={`editor-checklist__item ${step.done ? "is-done" : ""} ${isActive ? "is-active" : ""}`.trim()}
            >
              <div className="editor-checklist__badge">{step.done ? "✓" : index + 1}</div>
              <div>
                <div className="editor-checklist__label">{step.label}</div>
                <div className="editor-checklist__hint">{step.hint}</div>
              </div>
            </article>
          );
        })}
      </div>

      <div className="editor-checklist__footer">
        <div className="editor-checklist__next">
          {nextStep ? `next up: ${nextStep.label}` : "you’re clear to export or keep pushing the continuity story."}
        </div>
        {!hasSources && (
          <button type="button" className="editor-checklist__cta" onClick={onImport}>
            import footage
          </button>
        )}
      </div>
    </section>
  );
}

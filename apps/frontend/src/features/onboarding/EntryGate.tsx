import { useState } from "react";
import "./entry-gate.css";

type AuthGateViewProps = {
  scope: "library" | "editor";
  onBack: () => void;
  onContinue: () => Promise<void> | void;
};

type FirstRunOnboardingViewProps = {
  displayName: string;
  scope: "library" | "editor";
  onEnterStudio: () => void;
  onOpenLibrary: () => void;
};

export function AuthGateView({ scope, onBack, onContinue }: AuthGateViewProps) {
  const [busy, setBusy] = useState(false);

  return (
    <div className="entry-gate">
      <div className="entry-gate__shell">
        <div className="entry-gate__topbar">
          <button className="entry-gate__brand" onClick={onBack}>
            <span className="entry-gate__brand-mark" aria-hidden />
            <span>back to landing</span>
          </button>
          <div className="entry-gate__session">
            sign in required for {scope}
          </div>
        </div>

        <section className="entry-gate__hero">
          <div className="entry-gate__panel">
            <div className="entry-gate__eyebrow">auth gate</div>
            <h1 className="entry-gate__title">
              sign in
              <br />
              then open studio
            </h1>
            <p className="entry-gate__subtitle">
              no extra hub, no random detour. we just need identity before we touch project state,
              uploads, or your library.
            </p>
            <div className="entry-gate__actions">
              <button
                className="entry-gate__primary"
                onClick={async () => {
                  setBusy(true);
                  try {
                    await onContinue();
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {busy ? "opening google…" : "continue with google"}
              </button>
              <button className="entry-gate__ghost" onClick={onBack}>
                not right now
              </button>
            </div>
          </div>

          <aside className="entry-gate__sidepanel">
            <div className="entry-gate__side-title">what happens next</div>
            <div className="entry-gate__steps">
              {[
                ["library first", "open studio lands in your reel library by default."],
                ["new users get one setup pass", "first sign-in gets a short onboarding instead of a dead-end hub."],
                ["editor stays one click away", "new reel opens the editor directly from the library."],
              ].map(([label, copy], index) => (
                <div key={label} className="entry-gate__step">
                  <div className="entry-gate__step-index">{index + 1}</div>
                  <div>
                    <div className="entry-gate__step-label">{label}</div>
                    <div className="entry-gate__step-copy">{copy}</div>
                  </div>
                </div>
              ))}
            </div>
          </aside>
        </section>
      </div>
    </div>
  );
}

export function FirstRunOnboardingView({
  displayName,
  scope,
  onEnterStudio,
  onOpenLibrary,
}: FirstRunOnboardingViewProps) {
  return (
    <div className="entry-gate">
      <div className="entry-gate__shell">
        <div className="entry-gate__topbar">
          <div className="entry-gate__brand">
            <span className="entry-gate__brand-mark" aria-hidden />
            <span>iris onboarding</span>
          </div>
          <div className="entry-gate__session">
            first session for {String(displayName).toLowerCase()}
          </div>
        </div>

        <section className="entry-gate__hero">
          <div className="entry-gate__panel">
            <div className="entry-gate__eyebrow">first-time onboarding</div>
            <h1 className="entry-gate__title">
              alright,
              <br />
              here&apos;s the move
            </h1>
            <p className="entry-gate__subtitle">
              you don&apos;t need a fake start hub. you need one clear first action, then the real product.
              {scope === "editor"
                ? " finish this once, then you can jump straight back into the editor whenever you want."
                : " finish this once, then studio just opens to your library like a normal app."}
            </p>
            <div className="entry-gate__actions">
              <button className="entry-gate__primary" onClick={onEnterStudio}>
                start with a new reel
              </button>
              <button className="entry-gate__ghost" onClick={onOpenLibrary}>
                open library instead
              </button>
            </div>
          </div>

          <aside className="entry-gate__sidepanel">
            <div className="entry-gate__side-title">core loop</div>
            <div className="entry-gate__steps">
              {[
                ["upload a reel", "start with one source clip, not a pile of random assets."],
                ["scrub to the exact frame", "the edit should be anchored to the moment you actually care about."],
                ["box the subject", "tell iris what object or person the fact change belongs to."],
                ["prompt, compare, accept", "land one clean before/after, then worry about continuity and export."],
              ].map(([label, copy], index) => (
                <div key={label} className="entry-gate__step">
                  <div className="entry-gate__step-index">{index + 1}</div>
                  <div>
                    <div className="entry-gate__step-label">{label}</div>
                    <div className="entry-gate__step-copy">{copy}</div>
                  </div>
                </div>
              ))}
            </div>
          </aside>
        </section>
      </div>
    </div>
  );
}

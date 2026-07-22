/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { narrate, type JobResp, type Variant } from "@/lib/api";
import type { GenerationLogEntry } from "@/hooks/useGenerationSession";
import {
  duration,
  type BBox,
  type Clip,
  type IdentifiedEntity,
} from "@/stores/edl";
import "./reveal.css";

type RevealLayout = "panel" | "floating";

type GenerationRevealProps = {
  clip: Clip;
  bbox: BBox | null;
  entity: IdentifiedEntity | null;
  identifying: boolean;
  layout: RevealLayout;
  windowLabel?: string | null;
  session: RevealSession;
  onClearRegion?: () => void;
};

export type RevealSession = {
  prompt: string;
  setPrompt: (value: string) => void;
  busy: boolean;
  status: string;
  variants: Variant[];
  err: string | null;
  setErr: (value: string | null) => void;
  acceptingIdx: number | null;
  canGenerate: boolean;
  logs: GenerationLogEntry[];
  result?: JobResp | null;
  runLabel?: string;
  notice?: string | null;
  run: () => Promise<boolean>;
  acceptVariant: (idx: number) => Promise<boolean>;
  clearSession: () => void;
};

export function GenerationReveal({
  clip,
  bbox,
  entity,
  identifying,
  layout,
  windowLabel,
  session,
  onClearRegion,
}: GenerationRevealProps) {
  const [activeVariantIdx, setActiveVariantIdx] = useState<number | null>(null);
  const {
    prompt,
    setPrompt,
    busy,
    status,
    variants,
    err,
    setErr,
    acceptingIdx,
    canGenerate,
    logs,
    result,
    runLabel,
    notice,
    run,
    acceptVariant,
    clearSession,
  } = session;

  const hasVariants = variants.length > 0;
  const activeVariant =
    activeVariantIdx != null && activeVariantIdx < variants.length
      ? variants[activeVariantIdx]
      : null;
  const promptLocked = busy || hasVariants || acceptingIdx != null;
  const acceptanceBlocked = result?.generation_quality_state === "hard_fail";
  const sourcePreviewStart = result?.execution_window?.core_start ?? clip.sourceStart;
  const sourcePreviewEnd = result?.execution_window?.core_end ?? clip.sourceEnd;
  const generatedPreviewStart = result?.execution_window?.edit_start_offset ?? 0;
  const generatedPreviewEnd =
    result?.execution_window?.edit_end_offset ?? duration(clip);
  const regionSummary = describeRegion(bbox);
  const subjectSummary = identifying
    ? "identifying subject..."
    : entity
      ? `${entity.description} · ${entity.category}`
      : bbox
        ? "region locked, waiting on subject read"
        : "whole frame";
  const phaseLabel = hasVariants
    ? "review and compare"
    : busy
      ? "building variants"
      : "describe the transformation";

  // ─── narration ──────────────────────────────────────────────────────
  const [narrationUrl, setNarrationUrl] = useState<string | null>(null);
  const [narrationLoading, setNarrationLoading] = useState(false);
  const [narrationMuted, setNarrationMuted] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // request narration when a variant is selected
  useEffect(() => {
    if (!activeVariant?.id || !activeVariant.description) {
      setNarrationUrl(null);
      return;
    }
    let cancelled = false;
    setNarrationLoading(true);
    narrate(activeVariant.id, activeVariant.description)
      .then((res) => {
        if (!cancelled) setNarrationUrl(res.audio_url);
      })
      .catch(() => {
        // narration is non-critical — silently skip
      })
      .finally(() => {
        if (!cancelled) setNarrationLoading(false);
      });
    return () => { cancelled = true; };
  }, [activeVariant?.id, activeVariant?.description]);

  // play/pause narration audio
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !narrationUrl) return;
    audio.src = narrationUrl;
    if (!narrationMuted) {
      void audio.play().catch(() => {});
    }
  }, [narrationUrl, narrationMuted]);

  useEffect(() => {
    if (!hasVariants) {
      setActiveVariantIdx(null);
      setNarrationUrl(null);
      return;
    }
    setActiveVariantIdx((current) =>
      current == null || current >= variants.length ? 0 : current,
    );
  }, [hasVariants, variants.length]);

  async function handleRun(): Promise<boolean> {
    if (!canGenerate) return false;
    return run();
  }

  async function handleAccept(idx: number) {
    const accepted = await acceptVariant(idx);
    if (accepted) {
      setActiveVariantIdx(null);
      setNarrationUrl(null);
    }
  }

  function handleReset() {
    setActiveVariantIdx(null);
    setNarrationUrl(null);
    clearSession();
  }

  return (
    <div className={`reveal reveal--${layout}`}>
      {/* hidden audio element for narration */}
      <audio ref={audioRef} style={{ display: "none" }} />

      <div className="reveal__composer">
        <div className="reveal__heading">
          <div>
            <p className="reveal__eyebrow mono">{phaseLabel}</p>
            <h3 className="reveal__title">
              {hasVariants
                ? "pick the take that actually lands"
                : busy
                  ? "turning the note into something you can judge"
                  : "aim the edit before you spend the render"}
            </h3>
          </div>
          {busy && (
            <span className="reveal__status mono">{formatStatus(status)}</span>
          )}
        </div>

        <div className={`reveal__prompt-shell reveal__prompt-shell--${layout}`}>
          {layout === "panel" ? (
            <Textarea
              className="reveal__prompt-input reveal__prompt-input--area"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              rows={4}
              placeholder="e.g. make the jacket deep cherry red, keep the grade warm and cinematic"
              disabled={promptLocked}
            />
          ) : (
            <Input
              className="reveal__prompt-input reveal__prompt-input--line"
              type="text"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && canGenerate) {
                  void handleRun();
                }
              }}
              placeholder="describe the change..."
              disabled={promptLocked}
            />
          )}

          <Button
            className="reveal__generate"
            onClick={() => void handleRun()}
            disabled={!canGenerate}
          >
            {busy ? `${formatStatus(status)}…` : runLabel ?? "generate variants"}
          </Button>
        </div>

        <div className="reveal__context">
          <ContextPill k="target" v={clip.label ?? "selected range"} />
          {windowLabel ? <ContextPill k="window" v={windowLabel} /> : null}
          <ContextPill k="duration" v={`${duration(clip).toFixed(2)}s clip`} />
          <ContextPill k="scope" v={regionSummary} />
          <ContextPill k="subject" v={subjectSummary} />
          {bbox && onClearRegion && (
            <Button className="reveal__ghost reveal__ghost--small" onClick={onClearRegion} size="sm" variant="ghost">
              clear region
            </Button>
          )}
        </div>
        {notice ? <p className="reveal__notice mono">{notice}</p> : null}
      </div>

      {err && (
        <Alert className="reveal__error mono" variant="destructive">
          <span>{err}</span>
          <Button onClick={() => setErr(null)} aria-label="dismiss ai error" size="sm" variant="ghost">
            close
          </Button>
        </Alert>
      )}

      {(busy || (logs && logs.length > 0 && !hasVariants)) && (
        <div className="reveal__loading">
          <div className="reveal__loading-copy">
            <p className="reveal__loading-label mono">current prompt</p>
            <p className="reveal__loading-text">
              {prompt.trim() || "waiting for the edit note"}
            </p>
          </div>
          <div className="reveal__loading-steps">
            <LoadingStep active={!hasLogStage(logs, "plan_done")}>
              sampling candidate looks
            </LoadingStep>
            <LoadingStep active={hasLogStage(logs, "plan_done") && !hasLogStage(logs, "gen_done")}>
                rendering through the generation pipeline
            </LoadingStep>
            <LoadingStep active={hasLogStage(logs, "gen_done") && !hasLogStage(logs, "score_done")}>
              scoring variants for coherence and adherence
            </LoadingStep>
          </div>
          <ThoughtConsole logs={logs} />
        </div>
      )}

      {hasVariants && activeVariant && (
        <div className="reveal__review">
          {result ? <GenerationOutcome result={result} /> : null}
          <div className="reveal__review-head">
            <div>
              <p className="reveal__eyebrow mono">hero compare</p>
              <h4 className="reveal__review-title">
                {`variant ${variantLetter(activeVariantIdx ?? 0)}`}
              </h4>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              {/* narration controls */}
              {(narrationUrl || narrationLoading) && (
                <Button
                  className="reveal__narration-toggle mono"
                  onClick={() => {
                    const audio = audioRef.current;
                    if (narrationMuted) {
                      setNarrationMuted(false);
                      if (audio && narrationUrl) {
                        audio.currentTime = 0;
                        void audio.play().catch(() => {});
                      }
                    } else {
                      setNarrationMuted(true);
                      if (audio) audio.pause();
                    }
                  }}
                  title={narrationMuted ? "play narration" : "mute narration"}
                  style={{
                    padding: "6px 10px",
                    borderRadius: 999,
                    border: "1px solid rgba(255,255,255,0.12)",
                    background: narrationUrl && !narrationMuted
                      ? "rgba(126, 231, 135, 0.1)"
                      : "rgba(255,255,255,0.05)",
                    color: narrationUrl && !narrationMuted
                      ? "rgba(126, 231, 135, 0.85)"
                      : "rgba(255,255,255,0.5)",
                    fontSize: 10,
                    letterSpacing: "0.1em",
                    cursor: "pointer",
                    transition: "all 0.2s",
                  }}
                  size="sm"
                  variant="outline"
                >
                  {narrationLoading ? "loading voice..." : narrationMuted ? "play voice" : "narrating"}
                </Button>
              )}
              <div className="reveal__scores mono">
                <ScoreBadge label="prompt match" value={activeVariant.prompt_adherence} />
                <ScoreBadge label="visual quality" value={activeVariant.visual_coherence} />
                <ScoreBadge label="preservation" value={activeVariant.preservation_score ?? null} />
                <ScoreBadge
                  label="entry transition"
                  value={activeVariant.transition_review?.entry_applicable === false
                    ? null
                    : activeVariant.transition_review?.entry_continuity ?? null}
                />
                <ScoreBadge
                  label="exit transition"
                  value={activeVariant.transition_review?.exit_applicable === false
                    ? null
                    : activeVariant.transition_review?.exit_continuity ?? null}
                />
              </div>
            </div>
          </div>

          <div className={`reveal__compare reveal__compare--${layout}`}>
            <CompareCard
              tone="source"
              label="original"
              eyebrow="before"
              description="the untouched clip slice"
            >
              <SegmentVideo
                src={clip.url}
                start={sourcePreviewStart}
                end={sourcePreviewEnd}
                shouldPlay
              />
            </CompareCard>

            <CompareCard
              tone="variant"
              label={`variant ${variantLetter(activeVariantIdx ?? 0)}`}
              eyebrow="after"
              description={activeVariant.description || "generated option"}
            >
              {activeVariant.url ? (
                <SegmentVideo
                  src={activeVariant.url}
                  start={generatedPreviewStart}
                  end={generatedPreviewEnd}
                  shouldPlay
                />
              ) : (
                <div style={{
                  width: "100%",
                  height: "100%",
                  display: "grid",
                  placeItems: "center",
                  color: "rgba(255,255,255,0.3)",
                  fontSize: 12,
                  fontFamily: "var(--font-mono)",
                }}>
                  variant loading...
                </div>
              )}
            </CompareCard>
          </div>

          <p className="reveal__review-copy">
            {activeVariant.description || prompt.trim() || "generated variant"}
          </p>

          <div className="reveal__actions">
            <Button
              className="reveal__primary"
              onClick={() => void handleAccept(activeVariantIdx ?? 0)}
              disabled={acceptingIdx != null || !activeVariant.url || acceptanceBlocked}
            >
              {acceptanceBlocked
                ? "blocked: continuity validation failed"
                : acceptingIdx != null
                ? "applying variant..."
                : `apply variant ${variantLetter(activeVariantIdx ?? 0)}`}
            </Button>
            <Button className="reveal__ghost" onClick={handleReset} variant="ghost">
              different prompt
            </Button>
          </div>

          <div className="reveal__variant-grid">
            {variants.map((variant, index) => {
              const selected = index === activeVariantIdx;
              const disabled = acceptingIdx != null && acceptingIdx !== index;
              const bestScore = getBestVariantIndex(variants);
              return (
                <Button
                  key={`${variant.url ?? variant.id}-${index}`}
                  className={`reveal__variant-card ${selected ? "reveal__variant-card--active" : ""}`}
                  onClick={() => setActiveVariantIdx(index)}
                  disabled={acceptingIdx != null}
                  aria-pressed={selected}
                  style={{ opacity: disabled ? 0.5 : 1 }}
                  variant="ghost"
                >
                  <div className="reveal__variant-media">
                    <video
                      className="reveal__variant-video"
                      src={variant.url ?? undefined}
                      muted
                      loop
                      playsInline
                      autoPlay={selected}
                      onMouseEnter={(event) => {
                        const video = event.currentTarget;
                        void video.play().catch(() => {});
                      }}
                      onMouseLeave={(event) => {
                        const video = event.currentTarget;
                        video.pause();
                        video.currentTime = 0;
                      }}
                    />
                    {index === bestScore && (
                      <div
                        className="mono"
                        style={{
                          position: "absolute",
                          top: 6,
                          right: 6,
                          padding: "3px 7px",
                          borderRadius: 999,
                          background: "rgba(126, 231, 135, 0.2)",
                          border: "1px solid rgba(126, 231, 135, 0.3)",
                          color: "rgba(126, 231, 135, 0.9)",
                          fontSize: 9,
                          letterSpacing: "0.1em",
                          textTransform: "uppercase",
                        }}
                      >
                        best
                      </div>
                    )}
                  </div>

                  <div className="reveal__variant-meta">
                    <div className="reveal__variant-topline mono">
                      <span>{`variant ${variantLetter(index)}`}</span>
                      <span>{scoreSummary(variant)}</span>
                    </div>
                    <p className="reveal__variant-copy">
                      {variant.description || "generated option"}
                    </p>
                  </div>
                </Button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function ContextPill({ k, v }: { k: string; v: string }) {
  return (
    <div className="reveal__context-pill">
      <span className="reveal__context-k mono">{k}</span>
      <span className="reveal__context-v" title={v}>
        {v}
      </span>
    </div>
  );
}

function GenerationOutcome({ result }: { result: JobResp }) {
  const validation = result.continuity_validation;
  const seams = result.selected_seams;
  const composite = result.localized_compositing?.at(-1);
  const quality = result.generation_quality_state ?? "not reported";
  const providers = result.provider_attempts?.length
    ? result.provider_attempts.join(" → ")
    : result.provider ?? "unknown";
  const transition = result.transition_review;

  return (
    <section
      className={`reveal__outcome ${quality === "hard_fail" ? "reveal__outcome--blocked" : ""}`}
      aria-label="generation validation result"
    >
      <div className="reveal__outcome-pills mono">
        <span>quality {quality.replaceAll("_", " ")}</span>
        <span>seams {validation ? (validation.passed ? "passed" : "failed") : "unavailable"}</span>
        {result.preservation_score != null ? (
          <span>preservation {result.preservation_score}/10</span>
        ) : null}
        {transition ? (
          <span>entry {transition.entry_applicable === false ? "n/a" : `${transition.entry_continuity}/10`}</span>
        ) : null}
        {transition ? (
          <span>exit {transition.exit_applicable === false ? "n/a" : `${transition.exit_continuity}/10`}</span>
        ) : null}
        {seams?.entry ? <span>entry match {seams.entry.score.toFixed(3)}</span> : null}
        {seams?.exit ? <span>exit match {seams.exit.score.toFixed(3)}</span> : null}
        <span>{result.generation_attempts ?? 1} attempt(s)</span>
        {result.generated_seconds != null ? (
          <span>{result.generated_seconds.toFixed(1)} generated seconds</span>
        ) : null}
        <span>provider {providers}</span>
        {result.model ? (
          <span>model {result.model}</span>
        ) : null}
        {result.edit_mode ? (
          <span>mode {result.edit_mode.replaceAll("_", " ")}</span>
        ) : null}
        {composite ? (
          <span>local composite {composite.applied ? "applied" : "skipped"}</span>
        ) : null}
      </div>
      {result.generation_quality_evidence?.length ? (
        <p className="reveal__outcome-detail mono">
          {result.generation_quality_evidence.join(" · ")}
        </p>
      ) : null}
      {result.warnings?.map((warning) => (
        <p className="reveal__outcome-detail mono" key={warning}>{warning}</p>
      ))}
    </section>
  );
}

function ScoreBadge({
  label,
  value,
}: {
  label: string;
  value: number | null;
}) {
  if (value == null) return null;
  const tone = value >= 7
    ? "rgba(126, 231, 135, 0.15)"
    : value >= 4
      ? "rgba(255, 196, 87, 0.15)"
      : "rgba(255, 107, 107, 0.15)";
  const color = value >= 7
    ? "rgba(126, 231, 135, 0.9)"
    : value >= 4
      ? "rgba(255, 196, 87, 0.9)"
      : "rgba(255, 107, 107, 0.9)";
  return (
    <span
      className="reveal__score-badge"
      style={{ background: tone }}
    >
      <span>{label}</span>
      <strong style={{ color }}>{value}/10</strong>
    </span>
  );
}

function CompareCard({
  label,
  eyebrow,
  description,
  tone,
  children,
}: {
  label: string;
  eyebrow: string;
  description: string;
  tone: "source" | "variant";
  children: ReactNode;
}) {
  return (
    <article className={`reveal__compare-card reveal__compare-card--${tone}`}>
      <div className="reveal__compare-media">{children}</div>
      <div className="reveal__compare-copy">
        <div className="reveal__compare-head">
          <span className="reveal__compare-eyebrow mono">{eyebrow}</span>
          <h5>{label}</h5>
        </div>
        <p>{description}</p>
      </div>
    </article>
  );
}

function LoadingStep({
  active,
  children,
}: {
  active: boolean;
  children: ReactNode;
}) {
  return (
    <div className="reveal__loading-step" style={{ opacity: active ? 1 : 0.4 }}>
      <span
        className="reveal__loading-dot"
        style={{
          background: active ? "rgba(255,255,255,0.85)" : "rgba(255,255,255,0.25)",
          animation: active ? "pulse 1.5s ease-in-out infinite" : "none",
        }}
      />
      {children}
    </div>
  );
}

function SegmentVideo({
  src,
  start,
  end,
  shouldPlay,
}: {
  src: string;
  start: number;
  end: number;
  shouldPlay: boolean;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  const safeEnd = Math.max(start + 0.1, end);

  useEffect(() => {
    const video = ref.current;
    if (!video) return;

    let frameId: number | null = null;

    const syncPlayback = () => {
      // 10ms threshold matches the main preview rAF loop — plays as close
      // to the last frame as possible before looping back to start.
      if (video.currentTime < start || video.currentTime >= safeEnd - 0.01) {
        video.currentTime = start;
      }
      if (shouldPlay) {
        frameId = requestAnimationFrame(syncPlayback);
      }
    };

    const primeVideo = () => {
      video.currentTime = start;
      if (shouldPlay) {
        void video.play().catch(() => {});
        frameId = requestAnimationFrame(syncPlayback);
      } else {
        video.pause();
      }
    };

    if (video.readyState >= 1) {
      primeVideo();
    } else {
      video.addEventListener("loadedmetadata", primeVideo);
    }

    return () => {
      video.pause();
      video.removeEventListener("loadedmetadata", primeVideo);
      if (frameId != null) cancelAnimationFrame(frameId);
    };
  }, [src, start, safeEnd, shouldPlay]);

  return (
    <video
      ref={ref}
      className="reveal__compare-video"
      src={src}
      muted
      playsInline
    />
  );
}

function hasLogStage(logs: GenerationLogEntry[] | undefined, stage: string) {
  if (!logs) return false;
  return logs.some((entry) => entry.stage === stage);
}

const STAGE_BADGES: Record<string, { label: string; tone: string }> = {
  queued: { label: "queue", tone: "neutral" },
  bbox_missing: { label: "heads up", tone: "warn" },
  extract_clip: { label: "ffmpeg", tone: "neutral" },
  extract_frame: { label: "ffmpeg", tone: "neutral" },
  crop_bbox: { label: "ffmpeg", tone: "neutral" },
  crop_bbox_error: { label: "ffmpeg", tone: "warn" },
  extract_frame_error: { label: "ffmpeg", tone: "warn" },
  plan_start: { label: "planner", tone: "planner" },
  plan_done: { label: "planner", tone: "planner" },
  gen_start: { label: "wan", tone: "engine" },
  gen_submit: { label: "wan", tone: "engine" },
  gen_poll: { label: "wan", tone: "engine" },
  gen_done: { label: "wan", tone: "engine" },
  gen_echo: { label: "wan", tone: "warn" },
  gen_error: { label: "wan", tone: "error" },
  score_start: { label: "planner", tone: "planner" },
  score_done: { label: "planner", tone: "planner" },
  score_skipped: { label: "planner", tone: "neutral" },
  stream_error: { label: "stream", tone: "warn" },
  done: { label: "done", tone: "done" },
  error: { label: "error", tone: "error" },
};

function badgeFor(stage: string) {
  return (
    STAGE_BADGES[stage] ?? { label: stage.replace(/_/g, " "), tone: "neutral" }
  );
}

function ThoughtConsole({ logs }: { logs: GenerationLogEntry[] | undefined }) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [autoScroll, setAutoScroll] = useState(true);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el || !autoScroll) return;
    el.scrollTop = el.scrollHeight;
  }, [logs?.length, autoScroll]);

  if (!logs || logs.length === 0) {
    return (
      <div className="reveal__console reveal__console--empty">
        <span className="reveal__console-empty mono">
          waiting for the first signal from the pipeline...
        </span>
      </div>
    );
  }

  const start = logs[0].ts;
  return (
    <div className="reveal__console">
      <div className="reveal__console-head mono">
        <span className="reveal__console-title">thought process</span>
        <div className="reveal__console-controls">
          <button
            type="button"
            className={`reveal__console-toggle ${autoScroll ? "is-active" : ""}`}
            onClick={() => setAutoScroll((v) => !v)}
            title={autoScroll ? "pause auto-scroll" : "resume auto-scroll"}
          >
            {autoScroll ? "tail" : "paused"}
          </button>
          <span className="reveal__console-count">{logs.length} events</span>
        </div>
      </div>
      <div className="reveal__console-scroll" ref={scrollerRef}>
        {logs.map((entry) => {
          const badge = badgeFor(entry.stage);
          const delta = Math.max(0, entry.ts - start);
          const hasData = entry.data && Object.keys(entry.data).length > 0;
          const isOpen = expanded.has(entry.id);
          return (
            <div
              className={`reveal__console-row reveal__console-row--${badge.tone}`}
              key={entry.id}
            >
              <header className="reveal__console-meta mono">
                <span className="reveal__console-time">
                  +{delta.toFixed(1)}s
                </span>
                <span
                  className={`reveal__console-badge reveal__console-badge--${badge.tone}`}
                >
                  {badge.label}
                </span>
                <span className="reveal__console-stage">{entry.stage}</span>
              </header>
              <p className="reveal__console-msg">{entry.msg}</p>
              {hasData ? (
                <>
                  <button
                    type="button"
                    className="reveal__console-disclosure mono"
                    onClick={() =>
                      setExpanded((prev) => {
                        const next = new Set(prev);
                        if (next.has(entry.id)) next.delete(entry.id);
                        else next.add(entry.id);
                        return next;
                      })
                    }
                    aria-expanded={isOpen}
                  >
                    <span>{isOpen ? "hide details" : "show details"}</span>
                    <span className="reveal__console-disclosure-chev" aria-hidden>
                      {isOpen ? "−" : "+"}
                    </span>
                  </button>
                  {isOpen ? <ConsoleData data={entry.data!} /> : null}
                </>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ConsoleData({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(
    ([, v]) => v !== undefined && v !== null,
  );
  if (entries.length === 0) return null;
  return (
    <div className="reveal__console-data mono">
      {entries.map(([k, v]) => (
        <div className="reveal__console-kv" key={k}>
          <span className="reveal__console-key">{k}</span>
          <span className="reveal__console-val">{formatDatum(v)}</span>
        </div>
      ))}
    </div>
  );
}

function formatDatum(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number")
    return Number.isFinite(v) ? String(+v.toFixed(3)) : String(v);
  if (typeof v === "boolean") return v ? "yes" : "no";
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function formatStatus(status: string) {
  switch (status) {
    case "queued":
    case "pending":
      return "queued";
    case "processing":
      return "rendering";
    default:
      return status || "working";
  }
}

function variantLetter(index: number) {
  return String.fromCharCode(65 + index);
}

function scoreSummary(variant: Pick<
  Variant,
  | "visual_coherence"
  | "prompt_adherence"
  | "preservation_score"
  | "transition_review"
>) {
  const prompt = variant.prompt_adherence != null
    ? `prompt match ${variant.prompt_adherence}/10`
    : null;
  const visual = variant.visual_coherence != null
    ? `visual quality ${variant.visual_coherence}/10`
    : null;
  const preservation = variant.preservation_score != null
    ? `preservation ${variant.preservation_score}/10`
    : null;
  const transitions = variant.transition_review
    ? `entry ${variant.transition_review.entry_applicable === false ? "n/a" : `${variant.transition_review.entry_continuity}/10`} · exit ${variant.transition_review.exit_applicable === false ? "n/a" : `${variant.transition_review.exit_continuity}/10`}`
    : null;
  return [prompt, visual, preservation, transitions].filter(Boolean).join(" · ") || "no scores";
}

function describeRegion(bbox: BBox | null) {
  if (!bbox) return "full frame";
  const wPct = Math.round(bbox.w * 100);
  const hPct = Math.round(bbox.h * 100);
  const cx = bbox.x + bbox.w / 2;
  const cy = bbox.y + bbox.h / 2;
  const vertical = cy < 0.33 ? "top" : cy > 0.66 ? "bottom" : "center";
  const horizontal = cx < 0.33 ? "left" : cx > 0.66 ? "right" : "center";
  const anchor =
    vertical === "center" && horizontal === "center"
      ? "center"
      : `${vertical} ${horizontal}`.trim();
  return `${anchor} · ${wPct}×${hPct}%`;
}

/** Match the backend's correctness-first attempt ordering. */
function getBestVariantIndex(variants: Variant[]): number {
  let bestIdx = 0;
  let bestRank: number[] | null = null;
  for (let i = 0; i < variants.length; i++) {
    const v = variants[i];
    const transition = v.transition_review;
    const visual = v.visual_coherence ?? 0;
    const prompt = v.prompt_adherence ?? 0;
    const preservation = v.preservation_score ?? visual;
    const entry = transition?.entry_applicable === false
      ? 10
      : transition?.entry_continuity ?? 0;
    const exit = transition?.exit_applicable === false
      ? 10
      : transition?.exit_continuity ?? 0;
    const rank = [
      Number(prompt >= 6 && visual >= 5 && preservation >= 6),
      Number(v.continuity_validation?.passed === true),
      Number(transition != null && entry >= 7 && exit >= 7),
      Math.min(visual, prompt),
      Math.min(entry, exit),
      prompt,
      Math.min(visual, preservation),
    ];
    if (bestRank == null || outranks(rank, bestRank)) {
      bestRank = rank;
      bestIdx = i;
    }
  }
  return bestRank?.some((value) => value > 0) ? bestIdx : -1;
}

function outranks(candidate: number[], current: number[]): boolean {
  for (let index = 0; index < candidate.length; index++) {
    if (candidate[index] !== current[index]) {
      return candidate[index] > current[index];
    }
  }
  return false;
}

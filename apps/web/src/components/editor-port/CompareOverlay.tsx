import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Pause, Play, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  clipAtTime,
  sourceTimeFor,
  totalDuration,
  useEDL,
  type Clip,
  type MediaAsset,
} from "@/stores/edl";
import { useAgent, type AgentMessage } from "@/stores/agent";

export function CompareOverlay({ onClose }: { onClose: () => void }) {
  const { state } = useEDL();
  const { state: agentState } = useAgent();
  const original = state.sources.find((asset) => asset.kind === "source") ?? state.sources[0] ?? null;
  const editedDuration = totalDuration(state.clips);
  const comparisonDuration = Math.max(original?.duration ?? 0, editedDuration);
  const [playhead, setPlayhead] = useState(() =>
    Math.min(state.playhead, comparisonDuration || state.playhead),
  );
  const [playing, setPlaying] = useState(false);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const [singleView, setSingleView] = useState<"original" | "edited">("original");
  const [sideBySide, setSideBySide] = useState(true);
  const originalRef = useRef<HTMLVideoElement>(null);
  const editedRef = useRef<HTMLVideoElement>(null);
  const editedFreezeRef = useRef<HTMLCanvasElement>(null);
  const editedClipIdRef = useRef<string | null>(null);
  const editedLoadIdRef = useRef(0);
  const editedLoadingRef = useRef(false);
  const desiredEditedTimeRef = useRef(0);
  const playingRef = useRef(false);
  const rafRef = useRef<number | null>(null);
  const lastTickRef = useRef<number | null>(null);

  const generatedPreview = useMemo(
    () => findLatestPreview(agentState.messages),
    [agentState.messages],
  );
  const previewClip = useMemo(() => {
    if (!generatedPreview) return null;
    const { message, variant, timelineStart, timelineEnd } = generatedPreview;
    const timelineDuration = timelineEnd - timelineStart;
    const mediaStart = message.mediaStart ?? 0;
    const mediaEnd = message.mediaEnd ?? mediaStart + timelineDuration;
    return {
      id: `preview-${message.jobId}-${variant.id}`,
      kind: "generated" as const,
      url: variant.url!,
      sourceStart: mediaStart,
      sourceEnd: mediaEnd,
      mediaDuration: Math.max(mediaEnd, message.mediaEnd ?? mediaEnd),
      volume: 0,
      label: "Latest generated preview",
    } satisfies Clip;
  }, [generatedPreview]);
  const editedHitAt = useCallback((targetPlayhead: number) => {
    if (
      generatedPreview &&
      previewClip &&
      targetPlayhead >= generatedPreview.timelineStart &&
      targetPlayhead < generatedPreview.timelineEnd
    ) {
      return {
        clip: previewClip,
        offsetInClip: targetPlayhead - generatedPreview.timelineStart,
      };
    }
    return clipAtTime(state.clips, targetPlayhead);
  }, [generatedPreview, previewClip, state.clips]);
  const active = useMemo(() => editedHitAt(playhead), [editedHitAt, playhead]);
  const activeClip = active?.clip ?? null;
  const originalActive = sideBySide || singleView === "original";
  const editedActive = sideBySide || singleView === "edited";

  useEffect(() => {
    playingRef.current = playing;
  }, [playing]);

  const captureEditedFrame = useCallback(() => {
    const video = editedRef.current;
    const canvas = editedFreezeRef.current;
    if (!video || !canvas || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
    canvas.width = video.videoWidth || 1920;
    canvas.height = video.videoHeight || 1080;
    canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.style.transition = "none";
    canvas.style.opacity = "1";
  }, []);

  const revealEditedFrame = useCallback(() => {
    const canvas = editedFreezeRef.current;
    if (!canvas) return;
    canvas.style.transition = "opacity 0.1s ease-out";
    canvas.style.opacity = "0";
  }, []);

  const syncVideos = useCallback((targetPlayhead: number, force = false) => {
    const originalVideo = originalRef.current;
    const editedVideo = editedRef.current;
    const hit = editedHitAt(targetPlayhead);
    const clip = hit?.clip ?? null;

    if (originalVideo && original) {
      const originalTime = Math.min(targetPlayhead, Math.max(0, original.duration - 0.001));
      if (force || Math.abs(originalVideo.currentTime - originalTime) > 0.12) {
        originalVideo.currentTime = originalTime;
      }
    }

    if (editedVideo) {
      if (clip && hit) {
        const wantedTime = sourceTimeFor(clip, hit.offsetInClip);
        desiredEditedTimeRef.current = wantedTime;
        if (
          editedClipIdRef.current !== clip.id ||
          editedVideo.getAttribute("src") !== clip.url
        ) {
          const loadId = ++editedLoadIdRef.current;
          editedClipIdRef.current = clip.id;
          editedLoadingRef.current = true;
          setPlaybackError(null);
          captureEditedFrame();
          originalVideo?.pause();
          editedVideo.pause();

          const isCurrentLoad = () => editedLoadIdRef.current === loadId;
          const finishWhenDecoded = () => {
            if (!isCurrentLoad() || editedVideo.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
            const target = desiredEditedTimeRef.current;
            if (Math.abs(editedVideo.currentTime - target) > 0.08) {
              editedVideo.currentTime = target;
              return;
            }
            editedLoadingRef.current = false;
            setPlaybackError(null);
            revealEditedFrame();
            if (playingRef.current) {
              void Promise.allSettled([
                originalVideo?.play() ?? Promise.resolve(),
                editedVideo.play(),
              ]);
            }
          };
          const seekWhenReady = () => {
            if (!isCurrentLoad()) return;
            const target = Math.min(
              desiredEditedTimeRef.current,
              Math.max(
                0,
                Number.isFinite(editedVideo.duration)
                  ? editedVideo.duration - 0.001
                  : desiredEditedTimeRef.current,
              ),
            );
            if (Math.abs(editedVideo.currentTime - target) <= 0.02) finishWhenDecoded();
            else editedVideo.currentTime = target;
          };
          const stopOnError = () => {
            if (!isCurrentLoad()) return;
            editedLoadingRef.current = false;
            playingRef.current = false;
            originalVideo?.pause();
            editedVideo.pause();
            setPlaying(false);
            setPlaybackError("This comparison clip could not be loaded.");
          };
          editedVideo.addEventListener("loadedmetadata", seekWhenReady, { once: true });
          editedVideo.addEventListener("loadeddata", finishWhenDecoded, { once: true });
          editedVideo.addEventListener("seeked", finishWhenDecoded, { once: true });
          editedVideo.addEventListener("error", stopOnError, { once: true });
          editedVideo.src = clip.url;
          editedVideo.load();
        } else if (
          !editedLoadingRef.current &&
          (force || Math.abs(editedVideo.currentTime - wantedTime) > 0.12)
        ) {
          editedVideo.currentTime = wantedTime;
        }
      } else {
        editedVideo.pause();
      }
    }
  }, [captureEditedFrame, editedHitAt, original, revealEditedFrame]);

  useEffect(() => {
    syncVideos(playhead);
  }, [playhead, syncVideos]);

  useEffect(() => {
    const originalVideo = originalRef.current;
    const editedVideo = editedRef.current;
    if (!originalVideo || !editedVideo) return;

    if (playing && !editedLoadingRef.current) {
      originalVideo.play().catch(() => {});
      editedVideo.play().catch(() => {});
    } else {
      originalVideo.pause();
      editedVideo.pause();
    }
  }, [playing, activeClip?.id]);

  const playCompare = useCallback(() => {
    const originalVideo = originalRef.current;
    const editedVideo = editedRef.current;
    if (!originalVideo || !editedVideo) return;

    if (playing) {
      playingRef.current = false;
      setPlaying(false);
      originalVideo.pause();
      editedVideo.pause();
      return;
    }

    const restart = comparisonDuration > 0 && playhead >= comparisonDuration - 0.05;
    const nextPlayhead = restart ? 0 : playhead;
    playingRef.current = true;
    lastTickRef.current = null;
    setPlayhead(nextPlayhead);
    syncVideos(nextPlayhead, true);

    window.requestAnimationFrame(() => {
      void Promise.allSettled([originalVideo.play(), editedVideo.play()]);
      setPlaying(true);
    });
  }, [comparisonDuration, playhead, playing, syncVideos]);

  useEffect(() => {
    if (!playing) {
      lastTickRef.current = null;
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      return;
    }

    const tick = (now: number) => {
      if (lastTickRef.current == null) {
        lastTickRef.current = now;
      }
      const delta = (now - lastTickRef.current) / 1000;
      lastTickRef.current = now;
      if (editedLoadingRef.current) {
        rafRef.current = requestAnimationFrame(tick);
        return;
      }
      setPlayhead((current) => {
        const next = Math.min(comparisonDuration, current + delta);
        if (next >= comparisonDuration) {
          setPlaying(false);
          return comparisonDuration;
        }
        return next;
      });
      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [comparisonDuration, playing]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === " ") {
        event.preventDefault();
        setPlaying((value) => !value);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!original || state.clips.length === 0) {
    return (
      <div className="fixed inset-0 z-[10000] grid place-items-center bg-background/95 px-6">
        <div className="max-w-sm text-center">
          <p className="text-sm text-muted-foreground">Add a source video and timeline clips before comparing.</p>
          <Button className="mt-4" onClick={onClose} variant="outline">Close</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-[10000] flex flex-col bg-background text-foreground">
      <header className="flex h-12 items-center justify-between border-b border-border px-4">
        <div>
          <div className="text-sm font-medium">Compare</div>
          <div className="text-xs text-muted-foreground">Original video and full edited timeline play together.</div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setSingleView((value) => (value === "original" ? "edited" : "original"))}
          >
            {singleView === "original" ? "Show edited" : "Show original"}
          </Button>
          <Button
            size="sm"
            variant={sideBySide ? "default" : "outline"}
            onClick={() => setSideBySide((value) => !value)}
          >
            {sideBySide ? "Single view" : "Side by side"}
          </Button>
          <span className="font-mono text-xs text-muted-foreground">
            {formatTime(playhead)} / {formatTime(comparisonDuration)}
          </span>
          <Button size="icon-sm" variant="ghost" onClick={onClose} aria-label="Close compare">
            <X />
          </Button>
        </div>
      </header>

      <main className={`relative grid min-h-0 flex-1 ${sideBySide ? "grid-cols-2 gap-px bg-border" : "grid-cols-1 bg-background"}`}>
        <ComparePane
          label="Original"
          asset={original}
          overlay={!sideBySide}
          active={originalActive}
        >
          <video
            ref={originalRef}
            src={original.url}
            muted
            playsInline
            preload="auto"
            aria-label="Original video"
            className="h-full w-full bg-black object-contain"
          />
        </ComparePane>
        <ComparePane
          label="Edited timeline"
          clip={activeClip}
          overlay={!sideBySide}
          active={editedActive}
        >
          <div className="relative h-full w-full bg-black">
            <video
              ref={editedRef}
              muted
              playsInline
              preload="auto"
              aria-label="Edited timeline preview"
              className="h-full w-full bg-black object-contain"
            />
            <canvas
              ref={editedFreezeRef}
              aria-hidden="true"
              data-testid="compare-freeze-frame"
              className="pointer-events-none absolute top-1/2 left-1/2 h-auto w-auto max-h-full max-w-full -translate-x-1/2 -translate-y-1/2 bg-black opacity-0"
            />
            {playbackError && (
              <div className="absolute inset-0 z-20 grid place-items-center bg-black/75 px-6 text-center text-xs text-white">
                {playbackError} Close Compare and retry when the media is available.
              </div>
            )}
          </div>
        </ComparePane>
      </main>

      <footer className="flex h-16 items-center gap-4 border-t border-border px-4">
        <Button
          onClick={playCompare}
        >
          {playing ? <Pause /> : <Play />}
          {playing ? "Pause" : "Play"}
        </Button>
        <input
          aria-label="Compare playhead"
          className="h-1 flex-1 accent-primary"
          min={0}
          max={Math.max(comparisonDuration, 0.001)}
          step={0.01}
          type="range"
          value={playhead}
          onChange={(event) => {
            setPlaying(false);
            const nextPlayhead = Number(event.currentTarget.value);
            setPlayhead(nextPlayhead);
            syncVideos(nextPlayhead, true);
          }}
        />
      </footer>
    </div>
  );
}

function ComparePane({
  label,
  children,
  asset,
  clip,
  overlay,
  active,
}: {
  label: string;
  children: ReactNode;
  asset?: MediaAsset | null;
  clip?: Clip | null;
  overlay: boolean;
  active: boolean;
}) {
  return (
    <section
      aria-hidden={overlay && !active}
      data-active={active ? "true" : "false"}
      data-testid={`compare-pane-${label === "Original" ? "original" : "edited"}`}
      className={`min-h-0 bg-background transition-opacity duration-75 ${
        overlay ? "absolute inset-0" : "relative"
      } ${overlay && !active ? "pointer-events-none z-0 opacity-0" : "z-10 opacity-100"}`}
    >
      <div className="absolute top-3 left-3 z-10 rounded-md border border-border bg-background/85 px-3 py-1.5 backdrop-blur">
        <div className="text-xs font-medium">{label}</div>
        <div className="max-w-56 truncate text-xs text-muted-foreground">
          {asset?.label ?? clip?.label ?? "timeline"}
        </div>
      </div>
      {children}
    </section>
  );
}

function formatTime(seconds: number) {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const rest = Math.floor(safe % 60);
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

type PreviewMessage = Extract<AgentMessage, { type: "variant_preview" }>;
type SuggestionMessage = Extract<AgentMessage, { type: "suggestion" }>;

function findLatestPreview(messages: AgentMessage[]) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.type !== "variant_preview") continue;
    const variant =
      message.variants.find(
        (item) => item.id === message.recommendedVariantId && item.url,
      ) ?? message.variants.find((item) => item.url);
    if (!variant?.url) continue;

    const suggestion = messages.findLast(
      (item): item is SuggestionMessage =>
        item.type === "suggestion" && item.edit.job_id === message.jobId,
    );
    const timelineStart = message.timelineStart ?? suggestion?.edit.start_ts;
    const timelineEnd = message.timelineEnd ?? suggestion?.edit.end_ts;
    if (
      timelineStart == null ||
      timelineEnd == null ||
      timelineEnd <= timelineStart
    ) continue;
    return {
      message: message as PreviewMessage,
      variant,
      timelineStart,
      timelineEnd,
    };
  }
  return null;
}

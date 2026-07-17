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

export function CompareOverlay({ onClose }: { onClose: () => void }) {
  const { state } = useEDL();
  const original = state.sources.find((asset) => asset.kind === "source") ?? state.sources[0] ?? null;
  const editedDuration = totalDuration(state.clips);
  const comparisonDuration = Math.max(original?.duration ?? 0, editedDuration);
  const [playhead, setPlayhead] = useState(() =>
    Math.min(state.playhead, comparisonDuration || state.playhead),
  );
  const [playing, setPlaying] = useState(false);
  const [singleView, setSingleView] = useState<"original" | "edited">("original");
  const [sideBySide, setSideBySide] = useState(true);
  const originalRef = useRef<HTMLVideoElement>(null);
  const editedRef = useRef<HTMLVideoElement>(null);
  const editedClipIdRef = useRef<string | null>(null);
  const rafRef = useRef<number | null>(null);
  const lastTickRef = useRef<number | null>(null);

  const active = useMemo(() => clipAtTime(state.clips, playhead), [state.clips, playhead]);
  const activeClip = active?.clip ?? null;
  const showOriginal = sideBySide || singleView === "original";
  const showEdited = sideBySide || singleView === "edited";

  const syncVideos = useCallback((targetPlayhead: number, force = false) => {
    const originalVideo = originalRef.current;
    const editedVideo = editedRef.current;
    const hit = clipAtTime(state.clips, targetPlayhead);
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
        if (editedClipIdRef.current !== clip.id) {
          editedClipIdRef.current = clip.id;
          editedVideo.src = clip.url;
          editedVideo.load();
          editedVideo.currentTime = wantedTime;
        } else if (force || Math.abs(editedVideo.currentTime - wantedTime) > 0.12) {
          editedVideo.currentTime = wantedTime;
        }
      } else {
        editedVideo.pause();
      }
    }
  }, [original, state.clips]);

  useEffect(() => {
    syncVideos(playhead);
  }, [playhead, syncVideos]);

  useEffect(() => {
    const originalVideo = originalRef.current;
    const editedVideo = editedRef.current;
    if (!originalVideo || !editedVideo) return;

    if (playing) {
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
      setPlaying(false);
      originalVideo.pause();
      editedVideo.pause();
      return;
    }

    const restart = comparisonDuration > 0 && playhead >= comparisonDuration - 0.05;
    const nextPlayhead = restart ? 0 : playhead;
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

      <main className={`grid min-h-0 flex-1 ${sideBySide ? "grid-cols-2 gap-px bg-border" : "grid-cols-1 bg-background"}`}>
        <ComparePane label="Original" asset={original} hidden={!showOriginal}>
          <video
            ref={originalRef}
            src={original.url}
            muted
            playsInline
            aria-label="Original video"
            className="h-full w-full bg-black object-contain"
          />
        </ComparePane>
        <ComparePane label="Edited timeline" clip={activeClip} hidden={!showEdited}>
          <video
            ref={editedRef}
            muted
            playsInline
            aria-label="Edited timeline preview"
            className="h-full w-full bg-black object-contain"
          />
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
  hidden,
}: {
  label: string;
  children: ReactNode;
  asset?: MediaAsset | null;
  clip?: Clip | null;
  hidden?: boolean;
}) {
  return (
    <section className={`relative min-h-0 bg-background ${hidden ? "hidden" : ""}`}>
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

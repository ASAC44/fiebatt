/* eslint-disable react-hooks/exhaustive-deps */

import { useCallback, useRef, useState, type MouseEvent } from "react";

// ─── types ────────────────────────────────────────────────────────────

interface Segment {
  start_ts: number;
  end_ts: number;
  source: "original" | "generated";
}

interface VideoScrubberProps {
  duration: number;
  playhead: number;
  onSeek: (ts: number) => void;
  segments?: Segment[];
}

// ─── helpers ─────────────────────────────────────────────────────────

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

// ─── component ────────────────────────────────────────────────────────

export function VideoScrubber({
  duration,
  playhead,
  onSeek,
  onScrubStart,
  onScrubEnd,
  segments = [],
}: VideoScrubberProps & {
  onScrubStart?: () => void;
  onScrubEnd?: () => void;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const [hoverTs, setHoverTs] = useState<number | null>(null);

  const progress = duration > 0 ? clamp(playhead / duration, 0, 1) : 0;

  const tsFromEvent = useCallback(
    (e: MouseEvent | globalThis.MouseEvent): number => {
      const track = trackRef.current;
      if (!track || duration <= 0) return 0;
      const rect = track.getBoundingClientRect();
      const fraction = clamp((e.clientX - rect.left) / rect.width, 0, 1);
      return fraction * duration;
    },
    [duration],
  );

  const handleMouseDown = useCallback(
    (e: MouseEvent) => {
      e.preventDefault();
      setDragging(true);
      onScrubStart?.();
      onSeek(tsFromEvent(e));

      const handleMove = (me: globalThis.MouseEvent) => {
        onSeek(tsFromEvent(me));
      };

      const handleUp = () => {
        setDragging(false);
        onScrubEnd?.();
        window.removeEventListener("mousemove", handleMove);
        window.removeEventListener("mouseup", handleUp);
      };

      window.addEventListener("mousemove", handleMove);
      window.addEventListener("mouseup", handleUp);
    },
    [onSeek, tsFromEvent],
  );

  const handleMouseMove = useCallback(
    (e: MouseEvent) => {
      if (!dragging) {
        setHoverTs(tsFromEvent(e));
      }
    },
    [dragging, tsFromEvent],
  );

  const handleMouseLeave = useCallback(() => {
    if (!dragging) setHoverTs(null);
  }, [dragging]);

  return (
    <>
      <div className="flex select-none flex-col justify-center gap-1 p-0">
        <div
          className="relative cursor-pointer py-1.5"
          ref={trackRef}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
          role="slider"
          aria-label="Video timeline"
          aria-valuenow={playhead}
          aria-valuemin={0}
          aria-valuemax={duration}
          tabIndex={0}
        >
          {/* hover timestamp */}
          {hoverTs != null && !dragging && duration > 0 && (
            <span
              className="pointer-events-none absolute top-[-18px] z-3 -translate-x-1/2 whitespace-nowrap text-[9px] tracking-[0.05em] text-[var(--ink-fade)]"
              style={{ left: `${(hoverTs / duration) * 100}%` }}
            >
              {formatTime(hoverTs)}
            </span>
          )}

          <div className="relative h-1 overflow-hidden rounded bg-[var(--panel-3)]">
            {/* progress fill */}
            <div
              className="pointer-events-none absolute top-0 left-0 h-full rounded bg-[var(--ink-fade)] transition-[width] duration-75"
              style={{ width: `${progress * 100}%` }}
            />

            {/* generated segments */}
            {segments
              .filter((s) => s.source === "generated")
              .map((seg, i) => {
                if (duration <= 0) return null;
                const left = (seg.start_ts / duration) * 100;
                const width = ((seg.end_ts - seg.start_ts) / duration) * 100;
                return (
                  <div
                    key={`seg-${i}`}
                    className="pointer-events-none absolute top-0 h-full rounded"
                    style={{
                      left: `${left}%`,
                      width: `${width}%`,
                      background: "color-mix(in oklab, var(--primary) 32%, transparent)",
                    }}
                  />
                );
              })}
          </div>

          {/* playhead */}
          <div
            className={`pointer-events-none absolute top-1/2 z-2 h-3.5 w-2 -translate-x-1/2 -translate-y-1/2 rounded border border-[var(--bg)] bg-[var(--ink)] ${
              dragging ? "" : "transition-[left] duration-75"
            }`}
            style={{ left: `${progress * 100}%` }}
          />
        </div>

        <div className="flex justify-between text-[9px] tracking-[0.05em] text-[var(--ink-fade)]">
          <span>0:00</span>
          <span className="text-[var(--ink-dim)]">{formatTime(playhead)}</span>
          <span>{formatTime(duration)}</span>
        </div>
      </div>
    </>
  );
}

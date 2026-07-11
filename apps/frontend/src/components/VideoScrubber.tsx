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
      <style>{`
        .scrubber {
          display: flex;
          flex-direction: column;
          gap: 4px;
          padding: 8px 12px 10px;
          user-select: none;
          height: 48px;
          justify-content: center;
        }
        .scrubber__track-wrap {
          position: relative;
          cursor: pointer;
          padding: 6px 0;
        }
        .scrubber__track {
          position: relative;
          height: 5px;
          border-radius: 3px;
          background: var(--panel-3);
          overflow: hidden;
        }
        .scrubber__fill {
          position: absolute;
          top: 0;
          left: 0;
          height: 100%;
          background: rgba(255, 255, 255, 0.18);
          border-radius: 3px;
          transition: width 0.05s linear;
          pointer-events: none;
        }
        .scrubber__segment {
          position: absolute;
          top: 0;
          height: 100%;
          border-radius: 3px;
          pointer-events: none;
        }
        .scrubber__playhead {
          position: absolute;
          top: 50%;
          width: 11px;
          height: 11px;
          border-radius: 50%;
          background: var(--ink);
          border: 2px solid var(--bg);
          box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.2);
          transform: translate(-50%, -50%);
          pointer-events: none;
          z-index: 2;
          transition: left 0.05s linear;
        }
        .scrubber__playhead--dragging {
          transition: none;
          box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.4),
                      0 0 6px rgba(255, 255, 255, 0.15);
        }
        .scrubber__hover-time {
          position: absolute;
          top: -18px;
          transform: translateX(-50%);
          font-family: var(--f-mono);
          font-size: 9px;
          color: var(--ink-fade);
          letter-spacing: 0.05em;
          pointer-events: none;
          white-space: nowrap;
          z-index: 3;
        }
        .scrubber__labels {
          display: flex;
          justify-content: space-between;
          font-family: var(--f-mono);
          font-size: 9px;
          color: var(--ink-fade);
          letter-spacing: 0.05em;
        }
        .scrubber__current {
          color: var(--ink-dim);
        }
      `}</style>

      <div className="scrubber">
        <div
          className="scrubber__track-wrap"
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
              className="scrubber__hover-time"
              style={{ left: `${(hoverTs / duration) * 100}%` }}
            >
              {formatTime(hoverTs)}
            </span>
          )}

          <div className="scrubber__track">
            {/* progress fill */}
            <div
              className="scrubber__fill"
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
                    className="scrubber__segment"
                    style={{
                      left: `${left}%`,
                      width: `${width}%`,
                      background: "rgba(126, 231, 135, 0.25)",
                    }}
                  />
                );
              })}
          </div>

          {/* playhead */}
          <div
            className={`scrubber__playhead ${dragging ? "scrubber__playhead--dragging" : ""}`}
            style={{ left: `${progress * 100}%` }}
          />
        </div>

        <div className="scrubber__labels">
          <span>0:00</span>
          <span className="scrubber__current">{formatTime(playhead)}</span>
          <span>{formatTime(duration)}</span>
        </div>
      </div>
    </>
  );
}

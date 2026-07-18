import type { ReactNode } from "react";

import { totalDuration, useEDL } from "@/stores/edl";
import { Icon } from "./Icon";
import { VideoScrubber } from "./VideoScrubber";

export function PreviewControls({
  duration,
  videoWidth,
  videoHeight,
  segments,
}: {
  duration: number;
  videoWidth: number;
  videoHeight: number;
  segments: Array<{
    start_ts: number;
    end_ts: number;
    source: "original" | "generated";
  }>;
}) {
  const { state, dispatch } = useEDL();

  return (
    <div className="mx-3.5 mt-3 mb-0 flex flex-col gap-1.5 rounded-lg border border-[var(--edge)] px-2.5 pt-2 pb-2.5">
      <VideoScrubber
        duration={duration}
        playhead={state.playhead}
        onSeek={(ts) => dispatch({ type: "set_playhead", t: ts })}
        onScrubStart={() => dispatch({ type: "set_playing", playing: false })}
        segments={segments}
      />

      <div className="grid grid-cols-[1fr_auto_1fr] items-center bg-transparent p-0">
        <span className="text-xs tracking-[0.04em] text-[var(--ink)]">
          {fmt(state.playhead)}
          <span className="text-[var(--ink-ghost)]"> / </span>
          <span className="text-[var(--ink-fade)]">{fmt(duration)}</span>
        </span>

        <Transport />

        <div className="justify-self-end rounded border border-[var(--edge)] px-2 py-0.5 text-[10px] tracking-[0.2em] text-[var(--ink-fade)]">
          {formatAspectRatio(videoWidth, videoHeight)}
        </div>
      </div>
    </div>
  );
}

function Transport() {
  const { state, dispatch } = useEDL();
  const total = totalDuration(state.clips);
  const step = 1 / 24;

  return (
    <div className="inline-flex items-center gap-1">
      <IconBtn title="jump to start" onClick={() => dispatch({ type: "set_playhead", t: 0 })}>
        <Icon name="skip-back" size={14} />
      </IconBtn>
      <IconBtn title="back 1 frame" onClick={() => dispatch({ type: "set_playhead", t: state.playhead - step })}>
        <Icon name="step-back" size={14} />
      </IconBtn>
      <button
        className="mx-0.5 grid h-[26px] w-8 place-items-center rounded border border-primary bg-primary text-primary-foreground transition-colors hover:bg-primary/90 active:bg-primary/80 focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-[-1px] focus-visible:outline-[var(--chrome)]"
        onClick={() => dispatch({ type: "set_playing", playing: !state.playing })}
        title={state.playing ? "pause (space)" : "play (space)"}
      >
        <Icon name={state.playing ? "pause" : "play"} size={16} />
      </button>
      <IconBtn title="forward 1 frame" onClick={() => dispatch({ type: "set_playhead", t: state.playhead + step })}>
        <Icon name="step-fwd" size={14} />
      </IconBtn>
      <IconBtn title="jump to end" onClick={() => dispatch({ type: "set_playhead", t: total })}>
        <Icon name="skip-fwd" size={14} />
      </IconBtn>
    </div>
  );
}

function IconBtn({
  title,
  onClick,
  children,
}: {
  title: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      className="grid size-[26px] place-items-center rounded text-[var(--ink-dim)] transition-colors hover:text-[var(--ink)] focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-[-1px] focus-visible:outline-[var(--chrome)]"
      title={title}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function fmt(t: number) {
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  const f = Math.floor((t % 1) * 100);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}:${String(f).padStart(2, "0")}`;
}

function formatAspectRatio(width: number, height: number) {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return "—";
  }
  const gcd = (a: number, b: number): number => (b === 0 ? a : gcd(b, a % b));
  const divisor = gcd(Math.round(width), Math.round(height));
  return `${Math.round(width) / divisor} : ${Math.round(height) / divisor}`;
}

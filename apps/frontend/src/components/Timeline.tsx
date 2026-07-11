import { useCallback, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { type Clip, clipAtTime, duration, sourceTimeFor, totalDuration, useEDL } from "../stores/edl";
import { Icon, type IconName } from "./Icon";
import "./timeline.css";

/**
 * Timeline layout (top → bottom):
 *   [toolbar]   icon row: select · split · trim · delete · zoom
 *   [body]      left: track headers (V1, A1) with lock/eye/mute
 *                right: scrollable strip with ruler + tracks + playhead
 */
const HEADER_WIDTH = 92;

export function Timeline() {
  const { state, dispatch } = useEDL();
  const [pps, setPps] = useState(80);
  const stripRef = useRef<HTMLDivElement>(null);
  const selected = state.clips.find((c) => c.id === state.selectedId) ?? null;
  const total = totalDuration(state.clips);

  const onStripDown = (e: ReactMouseEvent) => {
    const rect = stripRef.current!.getBoundingClientRect();
    const setT = (clientX: number) => {
      const t = Math.max(0, Math.min(total, (clientX - rect.left + stripRef.current!.scrollLeft) / pps));
      dispatch({ type: "set_playhead", t });
    };
    setT(e.clientX);
    const onMove = (ev: MouseEvent) => setT(ev.clientX);
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  return (
    <div className="tl">
      <Toolbar pps={pps} setPps={setPps} selected={selected} />

      <div className="tl__body">
        <div className="tl__headers">
          <TrackHeader label="V1" kind="video" />
          <TrackHeader label="A1" kind="audio" />
        </div>

        <div
          ref={stripRef}
          className="tl__strip"
          style={{ width: `calc(100% - ${HEADER_WIDTH}px)` }}
        >
          <div
            className="tl__scroll"
            style={{ minWidth: Math.max(total * pps + 240, 600) }}
            onMouseDown={onStripDown}
          >
            <Ruler pps={pps} total={total} />
            <ClipsRow pps={pps} />
            <AudioRow pps={pps} />
            <Playhead pps={pps} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── toolbar ─────────────────────────────────────────────────────────

function Toolbar({
  pps,
  setPps,
  selected,
}: {
  pps: number;
  setPps: (n: number) => void;
  selected: Clip | null;
}) {
  const { state, dispatch } = useEDL();
  return (
    <div className="tbar">
      <ToolBtn icon="select" title="select" active />
      <Sep />
      <ToolBtn
        icon={state.playing ? "pause" : "play"}
        title={state.playing ? "pause" : "play (space)"}
        onClick={() => dispatch({ type: "set_playing", playing: !state.playing })}
        disabled={state.clips.length === 0}
      />
      <ToolBtn
        icon="undo"
        title="undo (⌘Z)"
        onClick={() => dispatch({ type: "undo" })}
        disabled={!state._history || state._history.length === 0}
      />
      <ToolBtn
        icon="redo"
        title="redo (⌘⇧Z)"
        onClick={() => dispatch({ type: "redo" })}
        disabled={!state._future || state._future.length === 0}
      />
      <Sep />
      <ToolBtn
        icon="split"
        title="split at playhead (s)"
        onClick={() => dispatch({ type: "split_at_playhead" })}
        disabled={state.clips.length === 0}
      />
      <ToolBtn
        icon="trim-in"
        title="trim in to playhead"
        onClick={() => {
          if (!selected) return;
          const hit = clipAtTime(state.clips, state.playhead);
          if (!hit || hit.clip.id !== selected.id) return;
          const sourceTs = sourceTimeFor(hit.clip, hit.offsetInClip);
          dispatch({ type: "trim", id: selected.id, side: "in", sourceTs });
        }}
        disabled={!selected}
      />
      <ToolBtn
        icon="trim-out"
        title="trim out to playhead"
        onClick={() => {
          if (!selected) return;
          const hit = clipAtTime(state.clips, state.playhead);
          if (!hit || hit.clip.id !== selected.id) return;
          const sourceTs = sourceTimeFor(hit.clip, hit.offsetInClip);
          dispatch({ type: "trim", id: selected.id, side: "out", sourceTs });
        }}
        disabled={!selected}
      />
      <ToolBtn
        icon="trash"
        title="delete selected (⌫)"
        onClick={() => selected && dispatch({ type: "remove", id: selected.id })}
        disabled={!selected}
        tone="danger"
      />

      <div className="tbar__spacer" />

      <div className="tbar__zoom">
        <Icon
          name="zoom-out"
          size={12}
          style={{ cursor: 'pointer' }}
          onClick={() => setPps(Math.max(20, pps - 20))}
        />
        <input
          type="range"
          min={20}
          max={340}
          step={20}
          value={pps}
          onChange={(e) => {
            const v = +e.target.value;
            requestAnimationFrame(() => setPps(v));
          }}
        />
        <Icon
          name="zoom-in"
          size={12}
          style={{ cursor: 'pointer' }}
          onClick={() => setPps(Math.min(340, pps + 20))}
        />
      </div>
    </div>
  );
}

function ToolBtn({
  icon,
  title,
  onClick,
  disabled,
  active,
  tone,
}: {
  icon: IconName;
  title: string;
  onClick?: () => void;
  disabled?: boolean;
  active?: boolean;
  tone?: "danger";
}) {
  return (
    <button
      className={`tbn ${active ? "tbn--on" : ""} ${tone ? `tbn--${tone}` : ""}`}
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      <Icon name={icon} size={14} />
    </button>
  );
}

function Sep() {
  return <span className="tbar__sep" />;
}

// ─── track headers ──────────────────────────────────────────────────

function TrackHeader({ label, kind }: { label: string; kind: "video" | "audio" }) {
  const [locked, setLocked] = useState(false);
  const [vis, setVis] = useState(true);
  const [mute, setMute] = useState(false);

  return (
    <div className="th">
      <span className="th__label mono">{label}</span>
      <div className="th__ctrls">
        <HBtn
          icon={locked ? "lock" : "unlock"}
          on={locked}
          onClick={() => setLocked((x) => !x)}
          title="lock"
        />
        {kind === "video" ? (
          <HBtn
            icon={vis ? "eye" : "eye-off"}
            on={vis}
            onClick={() => setVis((x) => !x)}
            title="visibility"
          />
        ) : (
          <HBtn
            icon={mute ? "volume-mute" : "volume"}
            on={!mute}
            onClick={() => setMute((x) => !x)}
            title="mute"
          />
        )}
      </div>
    </div>
  );
}

function HBtn({
  icon,
  on,
  onClick,
  title,
}: {
  icon: IconName;
  on: boolean;
  onClick: () => void;
  title: string;
}) {
  return (
    <button className={`hbn ${on ? "hbn--on" : ""}`} onClick={onClick} title={title}>
      <Icon name={icon} size={12} />
    </button>
  );
}

// ─── ruler ──────────────────────────────────────────────────────────

function Ruler({ pps, total }: { pps: number; total: number }) {
  const dur = Math.max(total, 10);
  const step = pps > 200 ? 0.5 : pps > 100 ? 1 : pps > 50 ? 2 : 5;
  const ticks = useMemo(() => {
    const arr: number[] = [];
    for (let t = 0; t <= dur + step; t += step) arr.push(+t.toFixed(2));
    return arr;
  }, [dur, step]);

  return (
    <div className="rlr" style={{ width: dur * pps + 240 }}>
      {ticks.map((t, i) => (
        <div key={t} className="rlr__t" style={{ left: t * pps }}>
          {i % 2 === 0 && <span className="mono rlr__lbl">{fmtTime(t)}</span>}
          <span className={`rlr__mk ${i % 2 === 0 ? "rlr__mk--major" : ""}`} />
        </div>
      ))}
    </div>
  );
}

// ─── clips row ──────────────────────────────────────────────────────

function ClipsRow({ pps }: { pps: number }) {
  const { state, dispatch } = useEDL();
  const positions = useMemo(() => {
    const arr: { startInTl: number }[] = [];
    let acc = 0;
    for (const c of state.clips) {
      arr.push({ startInTl: acc });
      acc += duration(c);
    }
    return arr;
  }, [state.clips]);

  return (
    <div className="tl__clips">
      {state.clips.map((c, i) => (
        <ClipTile
          key={c.id}
          clip={c}
          startInTl={positions[i].startInTl}
          pps={pps}
          selected={state.selectedId === c.id}
          onSelect={() => dispatch({ type: "select", id: c.id })}
        />
      ))}
    </div>
  );
}

function ClipTile({
  clip,
  startInTl,
  pps,
  selected,
  onSelect,
}: {
  clip: Clip;
  startInTl: number;
  pps: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const { dispatch } = useEDL();
  const d = duration(clip);
  const width = d * pps;

  const trim = useCallback(
    (side: "in" | "out") => (e: ReactMouseEvent) => {
      e.stopPropagation();
      e.preventDefault();
      const startX = e.clientX;
      const startSource = side === "in" ? clip.sourceStart : clip.sourceEnd;
      const onMove = (ev: MouseEvent) => {
        const dx = ev.clientX - startX;
        const newSourceTs = startSource + dx / pps;
        dispatch({ type: "trim", id: clip.id, side, sourceTs: newSourceTs });
      };
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [clip.id, clip.sourceStart, clip.sourceEnd, pps, dispatch],
  );

  return (
    <div
      className={`cl cl--${clip.kind} ${selected ? "cl--sel" : ""}`}
      style={{ left: startInTl * pps, width }}
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
    >
      <div className="cl__strip" />
      <div className="cl__content">
        <Icon
          name={clip.kind === "generated" ? "sparkles" : "media"}
          size={10}
          className="cl__icon"
        />
        <span className="cl__label">{clip.label ?? "untitled"}</span>
      </div>
      <div className="cl__handle cl__handle--in"  onMouseDown={trim("in")} />
      <div className="cl__handle cl__handle--out" onMouseDown={trim("out")} />
    </div>
  );
}

// ─── audio row ──────────────────────────────────────────────────────

function AudioRow({ pps }: { pps: number }) {
  const { state } = useEDL();
  const positions = useMemo(() => {
    const arr: { startInTl: number }[] = [];
    let acc = 0;
    for (const c of state.clips) {
      arr.push({ startInTl: acc });
      acc += duration(c);
    }
    return arr;
  }, [state.clips]);

  return (
    <div className="tl__audio">
      {state.clips.map((c, i) => (
        <div
          key={c.id}
          className={`aud ${state.selectedId === c.id ? "aud--sel" : ""}`}
          style={{
            left: positions[i].startInTl * pps,
            width: duration(c) * pps,
          }}
        >
          {/* fake waveform — a set of vertical bars whose height
              modulates pseudo-randomly but scaled by clip volume */}
          <Wave volume={c.volume} seed={c.id} width={duration(c) * pps} />
        </div>
      ))}
    </div>
  );
}

function Wave({ volume, seed, width }: { volume: number; seed: string; width: number }) {
  const barCount = Math.max(8, Math.floor(width / 4));
  const bars = useMemo(() => {
    const h = hash(seed);
    return Array.from({ length: barCount }, (_, i) => {
      const n = ((h ^ (i * 2654435761)) >>> 0) / 0xffffffff;
      return 0.15 + n * 0.85;
    });
  }, [seed, barCount]);
  return (
    <div className="wv">
      {bars.map((v, i) => (
        <span
          key={i}
          className="wv__b"
          style={{ height: `${Math.min(100, v * volume * 100)}%` }}
        />
      ))}
    </div>
  );
}

function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

// ─── playhead ───────────────────────────────────────────────────────

function Playhead({ pps }: { pps: number }) {
  const { state } = useEDL();
  return (
    <div className="phd" style={{ transform: `translateX(${state.playhead * pps}px)` }}>
      <div className="phd__head" />
      <div className="phd__line" />
    </div>
  );
}

// ─── utilities ──────────────────────────────────────────────────────

function fmtTime(t: number) {
  const s = Math.floor(t);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

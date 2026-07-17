/**
 * EDL — edit decision list. The timeline is an ordered list of clips.
 * Each clip references a source URL plus a sub-range (sourceStart..sourceEnd).
 *
 * timeline-time of clip[i] starts at sum(duration of 0..i-1). duration(clip)
 * = sourceEnd - sourceStart.
 *
 * The store is pure — operations take state and return new state. No side
 * effects. Mutations happen via a reducer, exposed through a context hook.
 *
 * ┌─────────────────────────┬────────────────────────────────────────────┐
 * │ state.sources (library) │ uploaded/generated media that sits in the  │
 * │                         │ side panel until the user adds it.         │
 * ├─────────────────────────┼────────────────────────────────────────────┤
 * │ state.clips (timeline)  │ ordered list of clip segments that play.   │
 * │                         │ each clip points back at a source via url. │
 * └─────────────────────────┴────────────────────────────────────────────┘
 */
import {
  createContext,
  useContext,
  useMemo,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";

// ─── types ────────────────────────────────────────────────────────────

/** A piece of media sitting in the library waiting to be used. */
export type MediaAsset = {
  id: string;
  url: string;
  /** full duration of the underlying file (seconds) */
  duration: number;
  fps: number;
  /** backend project id for /api/generate calls */
  projectId: string;
  label: string;
  kind: "source" | "generated";
};

export type Clip = {
  id: string;
  kind: "source" | "generated";
  url: string;
  /** seconds into the source URL where this clip begins */
  sourceStart: number;
  /** seconds into the source URL where this clip ends */
  sourceEnd: number;
  /**
   * Full duration of the underlying source file. Upper bound for sourceEnd
   * when trimming — you can't stretch a clip past what the file contains.
   */
  mediaDuration: number;
  /** 0..1 */
  volume: number;
  label?: string;
  /** optional project/job trail for generated clips */
  projectId?: string;
  generatedFromClipId?: string;
  /** library asset this clip was cut from (so removing all clips doesn't
   *  purge the source from the library) */
  sourceAssetId?: string;
};

export type BBox = { x: number; y: number; w: number; h: number };

/** SAM-refined outline of the subject inside the bbox. Points are normalized 0-1. */
export type Mask = {
  contour: [number, number][];
  contours?: [number, number][][];
  selectionId?: string | null;
  seedTs?: number | null;
  maskUrl?: string | null;
  subjectReferenceUrl?: string | null;
  score?: number | null;
};

/** Gemini's description of whatever's inside the current bbox. */
export type IdentifiedEntity = {
  description: string;
  category: string;
  attributes: Record<string, string>;
};

export type State = {
  /** library pool — imported + generated media, not on the timeline yet */
  sources: MediaAsset[];
  /** ordered sequence on the timeline */
  clips: Clip[];
  selectedId: string | null;
  /** timeline-time in seconds (read/written by preview + timeline) */
  playhead: number;
  playing: boolean;
  /** bounding box selection for AI generation (normalized 0-1) */
  bbox: BBox | null;
  /** SAM contour that snaps to the subject inside `bbox`. Cleared whenever bbox changes. */
  mask: Mask | null;
  /** what Gemini thinks the bbox contains. Cleared whenever bbox changes. */
  identified: IdentifiedEntity | null;
  /** true while the /api/identify request is in flight. */
  identifying: boolean;
  /** past states for undo */
  _history: State[];
  /** future states for redo */
  _future: State[];
};

export const initialState: State = {
  sources: [],
  clips: [],
  selectedId: null,
  playhead: 0,
  playing: false,
  bbox: null,
  mask: null,
  identified: null,
  identifying: false,
  _history: [],
  _future: [],
};

// ─── helpers ──────────────────────────────────────────────────────────

export const duration = (c: Clip) => Math.max(0, c.sourceEnd - c.sourceStart);

export const totalDuration = (clips: Clip[]) =>
  clips.reduce((s, c) => s + duration(c), 0);

/** returns { clip, offsetInClip, index, startInTimeline } or null */
export function clipAtTime(clips: Clip[], t: number) {
  let acc = 0;
  for (let i = 0; i < clips.length; i++) {
    const d = duration(clips[i]);
    if (t < acc + d || i === clips.length - 1) {
      // clamp offset so it never reaches exactly `d` (prevents boundary confusion)
      const offset = Math.max(0, Math.min(d - 0.001, t - acc));
      return {
        clip: clips[i],
        index: i,
        offsetInClip: offset,
        startInTimeline: acc,
      };
    }
    acc += d;
  }
  return null;
}

/** the absolute source-time for a timeline-time, if playing a specific clip */
export const sourceTimeFor = (c: Clip, offsetInClip: number) =>
  c.sourceStart + Math.max(0, Math.min(duration(c), offsetInClip));

export function timelineSpans(clips: Clip[]) {
  let acc = 0;
  return clips.map((clip) => {
    const start = acc;
    const end = start + duration(clip);
    acc = end;
    return { clip, start, end };
  });
}

// ─── actions ──────────────────────────────────────────────────────────

export type Action =
  | { type: "add_source"; asset: MediaAsset }
  | { type: "remove_source"; assetId: string }
  | { type: "add_to_timeline"; assetId: string }
  | { type: "select"; id: string | null }
  | { type: "set_playhead"; t: number }
  | { type: "set_playing"; playing: boolean }
  | { type: "trim"; id: string; side: "in" | "out"; sourceTs: number }
  | { type: "split_at_playhead" }
  | { type: "remove"; id: string }
  | { type: "set_volume"; id: string; v: number }
  | { type: "reorder"; from: number; to: number }
  | { type: "replace"; id: string; with: Clip }
  | { type: "replace_range"; id: string; start: number; end: number; with: Clip }
  | { type: "set_bbox"; bbox: BBox | null }
  | { type: "set_mask"; mask: Mask | null }
  | { type: "set_identified"; entity: IdentifiedEntity | null; loading: boolean }
  | { type: "hydrate"; sources: MediaAsset[]; clips: Clip[] }
  | { type: "undo" }
  | { type: "redo" };

/** Actions that don't mutate the timeline and shouldn't create undo entries */
const SKIP_HISTORY = new Set<string>([
  "undo",
  "redo",
  "set_playhead",
  "set_playing",
  "set_mask",
  "set_identified",
]);

const MAX_HISTORY = 50;

/** Strip history arrays from a snapshot so we don't nest them recursively */
function snap(s: State): State {
  return { ...s, _history: [], _future: [] };
}

function clearEditState(state: State): State {
  return {
    ...state,
    bbox: null,
    mask: null,
    identified: null,
    identifying: false,
  };
}

function undoableReducer(state: State, a: Action): State {
  if (a.type === "undo") {
    if (state._history.length === 0) return state;
    const prev = state._history[state._history.length - 1];
    return {
      ...prev,
      _history: state._history.slice(0, -1),
      _future: [...state._future, snap(state)],
    };
  }
  if (a.type === "redo") {
    if (state._future.length === 0) return state;
    const next = state._future[state._future.length - 1];
    return {
      ...next,
      _history: [...state._history, snap(state)],
      _future: state._future.slice(0, -1),
    };
  }

  const newState = coreReducer(state, a);
  if (newState === state) return state; // no-op, skip history

  if (SKIP_HISTORY.has(a.type)) {
    return { ...newState, _history: state._history, _future: state._future };
  }

  const history = [...state._history, snap(state)].slice(-MAX_HISTORY);
  return { ...newState, _history: history, _future: [] };
}

function coreReducer(state: State, a: Action): State {
  switch (a.type) {
    case "hydrate": {
      // wholesale replace library + timeline. used when reopening a saved
      // reel so we rebuild the EDL from the backend's segment rows
      // instead of starting from a blank canvas.
      return {
        ...state,
        sources: a.sources,
        clips: a.clips,
        selectedId: null,
        playhead: 0,
        playing: false,
        bbox: null,
        mask: null,
        identified: null,
        identifying: false,
      };
    }

    case "add_source": {
      if (state.sources.some((s) => s.id === a.asset.id)) return state;
      return { ...state, sources: [...state.sources, a.asset] };
    }

    case "remove_source": {
      return {
        ...state,
        sources: state.sources.filter((s) => s.id !== a.assetId),
      };
    }

    case "add_to_timeline": {
      const asset = state.sources.find((s) => s.id === a.assetId);
      if (!asset) return state;
      const clip = clipFromAsset(asset);
      const clips = [...state.clips, clip];
      return { ...state, clips, selectedId: clip.id };
    }

    case "select":
      if (state.selectedId === a.id) return state;
      return { ...clearEditState(state), selectedId: a.id };

    case "set_playhead":
      {
        const playhead = Math.max(0, Math.min(totalDuration(state.clips), a.t));
        const prevClipId = clipAtTime(state.clips, state.playhead)?.clip.id ?? null;
        const nextClipId = clipAtTime(state.clips, playhead)?.clip.id ?? null;
        // A box describes one decoded frame. Keeping it while seeking inside
        // the same clip can silently target a different object.
        if (prevClipId !== nextClipId || Math.abs(playhead - state.playhead) > 0.08) {
          return { ...clearEditState(state), playhead };
        }
        return { ...state, playhead };
      }

    case "set_playing":
      return { ...state, playing: a.playing };

    case "trim": {
      const clips = state.clips.map((c) => {
        if (c.id !== a.id) return c;
        // keep at least 0.1s and stay inside the source file span — users
        // can't stretch a clip past what the file actually contains.
        const MIN = 0.1;
        const maxEnd = c.mediaDuration > 0 ? c.mediaDuration : c.sourceEnd;
        if (a.side === "in") {
          const newStart = Math.max(0, Math.min(c.sourceEnd - MIN, a.sourceTs));
          return { ...c, sourceStart: newStart };
        }
        const newEnd = Math.max(
          c.sourceStart + MIN,
          Math.min(maxEnd, a.sourceTs),
        );
        return { ...c, sourceEnd: newEnd };
      });
      return { ...state, clips };
    }

    case "split_at_playhead": {
      const hit = clipAtTime(state.clips, state.playhead);
      if (!hit) return state;
      const { clip, index, offsetInClip } = hit;
      // don't split at the very edges
      if (offsetInClip < 0.05 || offsetInClip > duration(clip) - 0.05) return state;

      const splitAtSourceTs = sourceTimeFor(clip, offsetInClip);
      const left: Clip = { ...clip, sourceEnd: splitAtSourceTs };
      const right: Clip = {
        ...clip,
        id: cryptoUid(),
        sourceStart: splitAtSourceTs,
      };
      const clips = [
        ...state.clips.slice(0, index),
        left,
        right,
        ...state.clips.slice(index + 1),
      ];
      return { ...state, clips, selectedId: right.id };
    }

    case "remove": {
      const idx = state.clips.findIndex((c) => c.id === a.id);
      if (idx < 0) return state;
      const clips = state.clips.filter((c) => c.id !== a.id);
      return {
        ...state,
        clips,
        selectedId:
          clips.length === 0
            ? null
            : clips[Math.min(idx, clips.length - 1)]?.id ?? null,
      };
    }

    case "set_volume": {
      const clips = state.clips.map((c) =>
        c.id === a.id ? { ...c, volume: Math.max(0, Math.min(1, a.v)) } : c,
      );
      return { ...state, clips };
    }

    case "reorder": {
      if (a.from === a.to) return state;
      const clips = [...state.clips];
      const [moved] = clips.splice(a.from, 1);
      clips.splice(a.to, 0, moved);
      return { ...state, clips };
    }

    case "replace": {
      const clips = state.clips.map((c) => (c.id === a.id ? a.with : c));
      return {
        ...state,
        clips,
        selectedId: a.with.id,
        bbox: null,
        mask: null,
        identified: null,
        identifying: false,
      };
    }
    case "replace_range": {
      const idx = state.clips.findIndex((c) => c.id === a.id);
      if (idx < 0) return state;
      const current = state.clips[idx];
      if (a.start < current.sourceStart || a.start >= a.end) {
        return state;
      }

      // Clamp the split point to the clip's actual end. When the AI overlap
      // window extends past the original clip's sourceEnd, we absorb the
      // tail instead of silently no-oping (the previous guard `a.end >
      // current.sourceEnd → return state` caused the whole dispatch to
      // be dropped, leaving the timeline unchanged).
      const splitEnd = Math.min(a.end, current.sourceEnd);

      const replacementClips: Clip[] = [];
      if (a.start - current.sourceStart > 1e-3) {
        replacementClips.push({
          ...current,
          id: cryptoUid(),
          sourceEnd: a.start,
        });
      }
      replacementClips.push(a.with);
      if (current.sourceEnd - splitEnd > 1e-3) {
        replacementClips.push({
          ...current,
          id: cryptoUid(),
          sourceStart: splitEnd,
        });
      }

      return {
        ...state,
        clips: [
          ...state.clips.slice(0, idx),
          ...replacementClips,
          ...state.clips.slice(idx + 1),
        ],
        selectedId: a.with.id,
        bbox: null,
        mask: null,
        identified: null,
        identifying: false,
      };
    }
    case "set_bbox":
      // any bbox change invalidates the SAM mask + identified entity —
      // fresh ones will be fetched by whichever effect owns that pipeline.
      return {
        ...state,
        bbox: a.bbox,
        mask: null,
        identified: null,
        identifying: false,
      };
    case "set_mask":
      return { ...state, mask: a.mask };
    case "set_identified":
      return { ...state, identified: a.entity, identifying: a.loading };
    default:
      return state;
  }
}

function cryptoUid() {
  return (crypto as Crypto & { randomUUID: () => string }).randomUUID();
}

// ─── context ──────────────────────────────────────────────────────────

const StoreCtx = createContext<{
  state: State;
  dispatch: Dispatch<Action>;
} | null>(null);

export function EDLProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(undoableReducer, initialState);
  const value = useMemo(() => ({ state, dispatch }), [state]);
  return <StoreCtx.Provider value={value}>{children}</StoreCtx.Provider>;
}

export function useEDL() {
  const ctx = useContext(StoreCtx);
  if (!ctx) throw new Error("useEDL must be inside EDLProvider");
  return ctx;
}

// ─── factories ────────────────────────────────────────────────────────

export function newMediaAsset(partial: Omit<MediaAsset, "id">): MediaAsset {
  return { id: cryptoUid(), ...partial };
}

/** Build a timeline clip that plays a library asset in full. */
export function clipFromAsset(asset: MediaAsset): Clip {
  return {
    id: cryptoUid(),
    kind: asset.kind,
    url: asset.url,
    sourceStart: 0,
    sourceEnd: asset.duration,
    mediaDuration: asset.duration,
    volume: 1,
    label: asset.label,
    projectId: asset.projectId,
    sourceAssetId: asset.id,
  };
}

/** Backwards-compat factory for callers that build a clip directly. */
export function newClip(
  partial: Partial<Clip> &
    Pick<Clip, "url" | "sourceStart" | "sourceEnd"> & {
      mediaDuration?: number;
    },
): Clip {
  return {
    id: cryptoUid(),
    kind: "source",
    volume: 1,
    mediaDuration: partial.mediaDuration ?? partial.sourceEnd,
    ...partial,
  };
}

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";
import {
  EDLProvider,
  newMediaAsset,
  timelineSpans,
  totalDuration,
  useEDL,
  type Clip,
  type MediaAsset,
} from "../stores/edl";
import { Preview } from "../components/Preview";
import { Inspector } from "../components/Inspector";
import { Timeline } from "../components/Timeline";
import { Library } from "../components/Library";
import { UploadDrop } from "../components/UploadDrop";
import { VideoScrubber } from "../components/VideoScrubber";
import { AgentChat } from "../components/AgentChat";
import { AgentProvider } from "../stores/agent";
import {
  exportVideo,
  getTimeline,
  pollExport,
  saveTimeline,
  upload,
  type PersistedAsset,
  type PersistedClip,
  type TimelineSegment,
} from "../api/client";
import { Icon } from "../components/Icon";
import { ContinuityStatusBadge } from "../features/continuity/ContinuityStatusBadge";
import {
  useContinuityDashboard,
  type ContinuityDashboardController,
} from "../features/continuity/useContinuityDashboard";
import { EditorChecklist } from "../features/onboarding/EditorChecklist";
import { useAgentEdlBridge } from "../hooks/useAgentEdlBridge";
import "../styles/global.css";
import "./studio.css";

export type StudioInitialProject = {
  projectId: string;
  videoUrl: string;
  duration: number;
  fps: number;
  label?: string;
};

function buildSourceAsset(
  project: StudioInitialProject,
  sourceUrl = project.videoUrl,
): MediaAsset {
  return newMediaAsset({
    url: sourceUrl,
    duration: project.duration,
    fps: project.fps,
    projectId: project.projectId,
    label: project.label || project.projectId.slice(0, 8),
    kind: "source",
  });
}

function buildTimelineClip(
  segment: TimelineSegment,
  project: StudioInitialProject,
  sourceAsset: MediaAsset,
): Clip {
  const span = Math.max(0.01, segment.end_ts - segment.start_ts);
  if (segment.source === "generated") {
    return {
      id: crypto.randomUUID(),
      kind: "generated",
      url: segment.url,
      sourceStart: 0,
      sourceEnd: span,
      mediaDuration: span,
      volume: segment.audio ? 1 : 0,
      projectId: project.projectId,
      label: "ai edit",
    };
  }
  return {
    id: crypto.randomUUID(),
    kind: "source",
    url: segment.url,
    sourceStart: segment.start_ts,
    sourceEnd: segment.end_ts,
    mediaDuration: project.duration,
    volume: segment.audio ? 1 : 0,
    projectId: project.projectId,
    sourceAssetId: sourceAsset.id,
    label: sourceAsset.label,
  };
}

// ─── EDL round-trip helpers ──────────────────────────────────────────
//
// The persisted EDL on the backend uses snake_case fields; the frontend
// store uses camelCase. These two helpers are the *only* places where
// translation happens — one on hydrate (server → store) and one on save
// (store → server). Keep them mirror images.

function clipFromPersisted(c: PersistedClip): Clip {
  return {
    id: c.id,
    kind: c.kind,
    url: c.url,
    sourceStart: c.source_start,
    sourceEnd: c.source_end,
    mediaDuration: c.media_duration,
    volume: c.volume,
    label: c.label ?? undefined,
    projectId: c.project_id ?? undefined,
    sourceAssetId: c.source_asset_id ?? undefined,
    generatedFromClipId: c.generated_from_clip_id ?? undefined,
  };
}

function assetFromPersisted(a: PersistedAsset): MediaAsset {
  return {
    id: a.id,
    kind: a.kind,
    url: a.url,
    duration: a.duration,
    fps: a.fps,
    projectId: a.project_id,
    label: a.label,
  };
}

function clipToPersisted(c: Clip): PersistedClip {
  return {
    id: c.id,
    kind: c.kind,
    url: c.url,
    source_start: c.sourceStart,
    source_end: c.sourceEnd,
    media_duration: c.mediaDuration,
    volume: c.volume,
    label: c.label ?? null,
    project_id: c.projectId ?? null,
    source_asset_id: c.sourceAssetId ?? null,
    generated_from_clip_id: c.generatedFromClipId ?? null,
  };
}

function assetToPersisted(a: MediaAsset): PersistedAsset {
  return {
    id: a.id,
    kind: a.kind,
    url: a.url,
    duration: a.duration,
    fps: a.fps,
    project_id: a.projectId,
    label: a.label,
  };
}

/** Compact content-hash of everything that affects playback. Used by the
 * auto-save effect to skip no-op PUTs (selection changes, playhead moves,
 * bbox draws — all of those leave this signature unchanged). */
function edlSignature(clips: Clip[], sources: MediaAsset[]): string {
  const c = clips
    .map((x) =>
      `${x.id}|${x.url}|${x.sourceStart.toFixed(3)}|${x.sourceEnd.toFixed(3)}|${x.volume.toFixed(3)}`,
    )
    .join(";");
  const s = sources.map((x) => `${x.id}|${x.url}|${x.duration.toFixed(3)}`).join(";");
  return `${c}#${s}`;
}

function buildGeneratedAssets(project: StudioInitialProject, segments: TimelineSegment[]) {
  const assets: MediaAsset[] = [];
  const seen = new Set<string>();
  for (const segment of segments) {
    if (segment.source !== "generated" || seen.has(segment.url)) continue;
    seen.add(segment.url);
    const span = Math.max(0.01, segment.end_ts - segment.start_ts);
    assets.push(newMediaAsset({
      url: segment.url,
      duration: span,
      fps: project.fps,
      projectId: project.projectId,
      label: `ai edit ${assets.length + 1}`,
      kind: "generated",
    }));
  }
  return assets;
}

export function Studio({
  onExit,
  onLibrary,
  initialProject,
}: {
  onExit: () => void;
  onLibrary?: () => void;
  initialProject?: StudioInitialProject;
}) {
  return (
    <EDLProvider>
      <AgentProvider>
        <StudioInner
          onExit={onExit}
          onLibrary={onLibrary}
          initialProject={initialProject}
        />
      </AgentProvider>
    </EDLProvider>
  );
}

function StudioInner({
  onExit,
  onLibrary,
  initialProject,
}: {
  onExit: () => void;
  onLibrary?: () => void;
  initialProject?: StudioInitialProject;
}) {
  const { state, dispatch } = useEDL();
  const [uploading, setUploading] = useState(false);
  const [mode, setMode] = useState<'vibe' | 'pro'>('vibe');
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [hydratingProject, setHydratingProject] = useState(Boolean(initialProject));
  const rootRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // lock body scroll when studio is mounted
  useEffect(() => {
    const orig = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.documentElement.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = orig;
      document.documentElement.style.overflow = '';
    };
  }, []);

  // hydrate an existing project once, if one was passed in. we fetch the
  // saved segment rows from the backend and rebuild the EDL so resuming
  // gives you back your splits and accepted AI variants exactly as you
  // left them. falls back to a single full-length clip if the timeline
  // fetch fails (e.g. offline, brand-new project with no segments yet).
  //
  // IMPORTANT: this effect keys ONLY on `projectId`. parent routes routinely
  // build a fresh `initialProject` literal on each render and may even flip
  // sibling fields (e.g. label) a tick late once `listProjects()` resolves.
  // if we included those in deps, the effect would cancel its own in-flight
  // fetch, the replacement run would early-return via the ref guard, and
  // state would end up empty (sources=0, clips=0). by snapshotting the
  // rest into a ref that we read at dispatch-time, we get a stable, single
  // fetch per projectId change.
  const projectId = initialProject?.projectId;
  const initialProjectRef = useRef(initialProject);
  initialProjectRef.current = initialProject;

  // Always-current snapshot of the EDL so event-driven effects (like
  // ``iris:timeline-refresh``) can read the *live* source list without
  // closing over stale values or forcing a re-run of their useEffect on
  // every tiny state change.
  const stateRef = useRef(state);
  stateRef.current = state;

  // The "effective" project id is the one AgentChat actually sends to the
  // backend — it falls back to whatever source the user just uploaded in
  // vibe mode so the chat keeps working even before we've persisted a
  // reopen-able reel. The timeline-refresh listener MUST key on this same
  // fallback chain, otherwise uploading-in-vibe-mode silently skips the
  // listener attach and ``accept_variant`` lands on the server but the
  // preview never updates. (This was the "pressing apply does nothing"
  // bug — initialProject was undefined, so projectId was undefined, so
  // the useEffect early-returned before registering the listener.)
  const effectiveProjectId =
    projectId
    ?? state.sources.find((asset) => asset.kind === "source")?.projectId
    ?? state.sources[0]?.projectId
    ?? null;

  useEffect(() => {
    if (!projectId) return;
    setHydratingProject(true);

    let cancelled = false;
    (async () => {
      try {
        const tl = await getTimeline(projectId);
        if (cancelled) return;
        // re-read the latest snapshot in case duration/fps/videoUrl settled
        // after we kicked off the fetch. projectId has not changed (effect
        // is keyed on it) so the snapshot still refers to the right reel.
        const snap = initialProjectRef.current;
        if (!snap || snap.projectId !== projectId) return;

        // prefer the saved EDL if one exists — it encodes splits/trims/
        // reorders/volume that the flat segment view can't express. this
        // is the happy path for any reel the user has edited and reopened.
        if (tl.edl && tl.edl.clips.length > 0) {
          const clips = tl.edl.clips.map(clipFromPersisted);
          const sources = tl.edl.sources.map(assetFromPersisted);
          // stamp lastSavedAt so we don't immediately auto-save on hydrate.
          lastSavedAtRef.current = tl.edl.updated_at ?? Date.now() / 1000;
          lastSavedSigRef.current = edlSignature(clips, sources);
          dispatch({ type: "hydrate", sources, clips });
          return;
        }

        const project: StudioInitialProject = {
          projectId,
          videoUrl: snap.videoUrl,
          duration: snap.duration,
          fps: snap.fps,
          label: snap.label,
        };
        const sourceUrl =
          tl.segments.find((seg) => seg.source === "original")?.url
          ?? project.videoUrl;
        const sourceAsset = buildSourceAsset(project, sourceUrl);
        const clips: Clip[] = tl.segments.length > 0
          ? tl.segments.map((seg) => buildTimelineClip(seg, project, sourceAsset))
          : [{
              id: crypto.randomUUID(),
              kind: "source",
              url: sourceAsset.url,
              sourceStart: 0,
              sourceEnd: sourceAsset.duration,
              mediaDuration: sourceAsset.duration,
              volume: 1,
              projectId: sourceAsset.projectId,
              sourceAssetId: sourceAsset.id,
              label: sourceAsset.label,
            }];
        const sources = [sourceAsset, ...buildGeneratedAssets(project, tl.segments)];
        // treat the reconstructed-from-segments state as the baseline so
        // the very first auto-save only fires after a real user edit.
        lastSavedSigRef.current = edlSignature(clips, sources);
        dispatch({ type: "hydrate", sources, clips });
      } catch (err) {
        if (cancelled) return;
        console.warn("[studio] timeline fetch failed, falling back to single-clip:", err);
        const snap = initialProjectRef.current;
        if (!snap || snap.projectId !== projectId) return;
        const sourceAsset = buildSourceAsset({
          projectId,
          videoUrl: snap.videoUrl,
          duration: snap.duration,
          fps: snap.fps,
          label: snap.label,
        });
        const fallback: Clip = {
          id: crypto.randomUUID(),
          kind: "source",
          url: sourceAsset.url,
          sourceStart: 0,
          sourceEnd: sourceAsset.duration,
          mediaDuration: sourceAsset.duration,
          volume: 1,
          projectId: sourceAsset.projectId,
          sourceAssetId: sourceAsset.id,
          label: sourceAsset.label,
        };
        lastSavedSigRef.current = edlSignature([fallback], [sourceAsset]);
        dispatch({ type: "hydrate", sources: [sourceAsset], clips: [fallback] });
      } finally {
        if (!cancelled) setHydratingProject(false);
      }
    })();

    return () => { cancelled = true; };
  }, [projectId, dispatch]);

  // ─── silent re-hydrate on agent-driven timeline mutations ─────────
  //
  // When the agent accepts a variant (or splits / deletes / reverts)
  // the DB gets a new Segment row but the saved EDL doesn't — so
  // preferring tl.edl here would hand us back the stale pre-accept
  // timeline. Rebuild from `tl.segments` instead, which timeline_builder
  // already splits around generated regions for us, then let the normal
  // auto-save persist the new EDL on top.
  useEffect(() => {
    if (!effectiveProjectId) {
      // eslint-disable-next-line no-console
      console.warn("[studio] timeline-refresh listener NOT attached: no projectId");
      return;
    }
    // eslint-disable-next-line no-console
    console.log("[studio] timeline-refresh listener attached for project", effectiveProjectId);

    const pid = effectiveProjectId;
    const handler = async (ev: Event) => {
      // eslint-disable-next-line no-console
      console.log(
        "[studio] iris:timeline-refresh fired for project",
        pid,
        "detail=",
        (ev as CustomEvent).detail,
      );
      try {
        const tl = await getTimeline(pid);

        // Prefer the saved initialProject snapshot (from ?reopen=), but
        // fall back to constructing one from the live EDL source asset —
        // this is the vibe-mode upload path where the parent route never
        // provided an initialProject prop. Without this fallback the
        // handler aborted via the ref mismatch and the preview stayed
        // frozen on the original clip.
        const snap = initialProjectRef.current;
        const liveSource = stateRef.current.sources.find(
          (a) => a.kind === "source" && a.projectId === pid,
        );
        let project: StudioInitialProject | null = null;
        if (snap && snap.projectId === pid) {
          project = {
            projectId: pid,
            videoUrl: snap.videoUrl,
            duration: snap.duration,
            fps: snap.fps,
            label: snap.label,
          };
        } else if (liveSource) {
          project = {
            projectId: pid,
            videoUrl: liveSource.url,
            duration: liveSource.duration,
            fps: liveSource.fps,
            label: liveSource.label,
          };
        } else {
          // eslint-disable-next-line no-console
          console.warn(
            "[studio] refresh aborted: no project snapshot and no live source for",
            pid,
          );
          return;
        }
        const sourceUrl =
          tl.segments.find((seg) => seg.source === "original")?.url
          ?? project.videoUrl;
        const sourceAsset = buildSourceAsset(project, sourceUrl);
        const clips: Clip[] =
          tl.segments.length > 0
            ? tl.segments.map((seg) => buildTimelineClip(seg, project!, sourceAsset))
            : [];
        // eslint-disable-next-line no-console
        console.log(
          `[studio] refresh rebuilt ${clips.length} clip(s) from ${tl.segments.length} segment(s)`,
          tl.segments.map((s) => ({ source: s.source, start: s.start_ts, end: s.end_ts })),
        );
        if (clips.length === 0) return;
        const sources = [sourceAsset, ...buildGeneratedAssets(project, tl.segments)];
        // Deliberately do NOT stamp lastSavedSigRef here — we want the
        // auto-save effect to pick up the new state and persist it, so
        // the next full reload sees the generated clip in the saved EDL.
        dispatch({ type: "hydrate", sources, clips });
        // eslint-disable-next-line no-console
        console.log("[studio] hydrate dispatched with", clips.length, "clip(s)");
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("[studio] timeline refresh failed:", err);
      }
    };

    window.addEventListener("iris:timeline-refresh", handler);
    return () => {
      // eslint-disable-next-line no-console
      console.log("[studio] timeline-refresh listener DETACHED from project", pid);
      window.removeEventListener("iris:timeline-refresh", handler);
    };
  }, [effectiveProjectId, dispatch]);

  // ─── auto-save ────────────────────────────────────────────────────
  //
  // Debounced PUT to /api/timeline whenever the user's EDL actually
  // changes. Guards:
  //   - never fire while we're still hydrating (the effect above)
  //   - never fire for no-op state (signature comparison)
  //   - never fire until we've seen at least one clip (blank canvas
  //     should not overwrite a saved EDL with garbage)
  //
  // Signature covers only the fields that would affect playback — clip
  // order, timing, volume, url; library membership, etc. Transient state
  // like playhead/selection/bbox is ignored.
  const [saveStatus, setSaveStatus] = useState<
    "idle" | "pending" | "saving" | "saved" | "error"
  >("idle");
  const lastSavedSigRef = useRef<string>("");
  const lastSavedAtRef = useRef<number>(0);
  const saveTimerRef = useRef<number | null>(null);

  const currentSig = edlSignature(state.clips, state.sources);
  useEffect(() => {
    if (!projectId) return;
    if (hydratingProject) return;
    if (state.clips.length === 0 && state.sources.length === 0) return;
    if (currentSig === lastSavedSigRef.current) return;

    setSaveStatus("pending");
    if (saveTimerRef.current != null) {
      window.clearTimeout(saveTimerRef.current);
    }
    // capture the state snapshot for the closure — we deliberately do NOT
    // add state.clips/state.sources to deps because currentSig already
    // summarizes them and re-deriving means adding every intermediate
    // keystroke to the debounce chain would burn timers needlessly.
    const clipsSnapshot = state.clips;
    const sourcesSnapshot = state.sources;
    saveTimerRef.current = window.setTimeout(async () => {
      setSaveStatus("saving");
      try {
        const clips = clipsSnapshot.map(clipToPersisted);
        const sources = sourcesSnapshot.map(assetToPersisted);
        const resp = await saveTimeline(projectId, clips, sources);
        lastSavedSigRef.current = currentSig;
        lastSavedAtRef.current = resp.updated_at;
        setSaveStatus("saved");
      } catch (err) {
        console.warn("[studio] timeline save failed:", err);
        setSaveStatus("error");
      }
    }, 600);

    return () => {
      if (saveTimerRef.current != null) {
        window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, hydratingProject, currentSig]);

  // on unmount, flush any pending save synchronously-ish — last chance to
  // get the user's final state to the server before the route unmounts.
  useEffect(() => {
    return () => {
      if (saveTimerRef.current != null) {
        window.clearTimeout(saveTimerRef.current);
      }
    };
  }, []);

  async function handleFile(file: File) {
    setUploading(true);
    try {
      const res = await upload(file);
      // Drop imported media into the library pool. The user chooses when
      // it lands on the timeline via the plus button in the Library.
      const asset = newMediaAsset({
        url: res.video_url,
        duration: res.duration,
        fps: res.fps,
        projectId: res.project_id,
        label: file.name.replace(/\.[^.]+$/, ""),
        kind: "source",
      });
      dispatch({ type: "add_source", asset });
      // Vibe mode hides the Library sidebar entirely, so the usual plus-
      // button affordance is gone. Auto-drop new imports straight onto
      // the timeline there — otherwise the clip would just vanish into
      // a pool the user can't see.
      if (mode === "vibe") {
        dispatch({ type: "add_to_timeline", assetId: asset.id });
      }
    } catch (e) {
      alert(`upload failed: ${e}`);
    } finally {
      setUploading(false);
    }
  }

  // keyboard shortcuts
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setShowShortcuts(false);
        return;
      }
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

      switch (e.key) {
        case ' ':
          e.preventDefault();
          dispatch({ type: 'set_playing', playing: !state.playing });
          break;
        case 's':
        case 'b':
          if (!e.metaKey && !e.ctrlKey) {
            dispatch({ type: 'split_at_playhead' });
          }
          break;
        case 'Backspace':
        case 'Delete':
          if (state.selectedId) dispatch({ type: 'remove', id: state.selectedId });
          break;
        case 'z':
          if (e.metaKey || e.ctrlKey) {
            e.preventDefault();
            if (e.shiftKey) {
              dispatch({ type: 'redo' });
            } else {
              dispatch({ type: 'undo' });
            }
          }
          break;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state.playing, state.selectedId, dispatch]);

  const [exporting, setExporting] = useState(false);
  const [exportStatus, setExportStatus] = useState("");
  const [exportUrl, setExportUrl] = useState<string | null>(null);
  const [exportDownloadUrl, setExportDownloadUrl] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportBytes, setExportBytes] = useState<number | null>(null);

  const handleExport = useCallback(async () => {
    const projectId =
      initialProject?.projectId
      ?? state.sources.find((asset) => asset.kind === "source")?.projectId
      ?? state.sources[0]?.projectId;
    if (!projectId || state.clips.length === 0) return;
    setExporting(true);
    setExportStatus("queued");
    setExportUrl(null);
    setExportDownloadUrl(null);
    setExportError(null);
    setExportBytes(null);
    try {
      const { export_job_id } = await exportVideo(projectId);
      const res = await pollExport(export_job_id, (job) => {
        setExportStatus(job.status);
      });
      if (res.status !== "done" || !res.export_url) {
        throw new Error(res.error || "export finished without a download url");
      }
      setExportUrl(res.export_url);
      setExportDownloadUrl(res.download_url ?? res.export_url);
      setExportStatus("done");
      // Best-effort HEAD for file size in the stats row. S3 presigned
      // GETs allow HEAD via the same signature. If the bucket lacks CORS
      // or the request fails for any other reason we silently hide the
      // size chip — it's purely cosmetic.
      try {
        const head = await fetch(res.export_url, { method: "HEAD" });
        const len = head.headers.get("content-length");
        if (head.ok && len) setExportBytes(Number(len));
      } catch {
        // ignore.
      }
    } catch (err) {
      setExportError(String(err));
      setExportStatus("error");
    } finally {
      setExporting(false);
    }
  }, [initialProject?.projectId, state.sources, state.clips.length]);

  const dismissExport = useCallback(() => {
    setExportUrl(null);
    setExportDownloadUrl(null);
    setExportError(null);
    setExportStatus("");
    setExportBytes(null);
  }, []);

  const hasSources = state.sources.length > 0;
  const continuityProjectId = effectiveProjectId;
  const continuity = useContinuityDashboard(continuityProjectId);

  // bridge agent tool calls → EDL timeline refresh
  useAgentEdlBridge(
    continuityProjectId,
    initialProject ? {
      videoUrl: initialProject.videoUrl,
      duration: initialProject.duration,
      fps: initialProject.fps,
      label: initialProject.label,
    } : undefined,
  );

  const projectLabel =
    initialProject?.label
    ?? initialProject?.projectId.slice(0, 8)
    ?? state.sources[0]?.projectId.slice(0, 8);
  const hasAcceptedEdit = state.clips.some((clip) => clip.kind === "generated");
  const continuityComplete =
    continuity.propagationCounts.total > 0 &&
    continuity.propagationCounts.applied === continuity.propagationCounts.total;
  const exportLabel =
    exporting
      ? exportStatus === "processing"
        ? "Rendering…"
        : "Queueing…"
      : "Export";

  return (
    <main className={`studio ${mode === 'vibe' ? 'studio--vibe' : ''}`} ref={rootRef}>
      <input
        ref={fileInputRef}
        type="file"
        accept="video/*"
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) handleFile(f);
          e.target.value = '';
        }}
      />
      <TopBar
        onExit={onExit}
        onLibrary={onLibrary}
        projectLabel={projectLabel}
        mode={mode}
        onToggleMode={() => setMode(m => m === 'vibe' ? 'pro' : 'vibe')}
        onImport={() => fileInputRef.current?.click()}
        onShowShortcuts={() => setShowShortcuts(true)}
        onExport={handleExport}
        exporting={exporting}
        exportLabel={exportLabel}
        canExport={state.clips.length > 0}
        continuity={continuity}
        saveStatus={saveStatus}
        lastSavedAt={lastSavedAtRef.current}
      />

      {showShortcuts && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 9999,
            background: 'rgba(0,0,0,0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
          onClick={() => setShowShortcuts(false)}
        >
          <div
            style={{
              background: '#1a1a1a',
              border: '1px solid rgba(255,255,255,0.12)',
              borderRadius: 12,
              padding: '28px 36px',
              minWidth: 320,
              maxWidth: 420,
              color: 'rgba(255,255,255,0.85)',
              fontFamily: 'var(--font-mono, monospace)',
              fontSize: 13,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ margin: '0 0 18px', fontSize: 15, letterSpacing: '0.06em', color: '#fff' }}>
              Keyboard Shortcuts
            </h3>
            {([
              ['Space', 'Play / Pause'],
              ['S', 'Split at playhead'],
              ['Delete / Backspace', 'Remove selected clip'],
              ['\u2318 Z', 'Undo'],
              ['\u2318 Shift Z', 'Redo'],
            ] as const).map(([key, desc]) => (
              <div key={key} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                <span style={{ color: 'rgba(255,255,255,0.5)' }}>{desc}</span>
                <kbd style={{ background: 'rgba(255,255,255,0.08)', padding: '2px 8px', borderRadius: 4, fontSize: 11 }}>{key}</kbd>
              </div>
            ))}
            <p style={{ marginTop: 16, fontSize: 11, color: 'rgba(255,255,255,0.3)', textAlign: 'center' }}>
              Press Escape or click outside to close
            </p>
          </div>
        </div>
      )}

      {(exportStatus === "done" || exportStatus === "error" || (exporting && exportStatus)) && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 9998,
            background: 'rgba(0,0,0,0.7)',
            backdropFilter: 'blur(6px)',
            WebkitBackdropFilter: 'blur(6px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 24,
          }}
          onClick={!exporting ? dismissExport : undefined}
        >
          <div
            style={{
              background: '#141414',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 14,
              padding: exportUrl ? 24 : '28px 36px',
              minWidth: 340,
              maxWidth: exportUrl ? 640 : 440,
              width: exportUrl ? '100%' : 'auto',
              color: 'rgba(255,255,255,0.85)',
              fontFamily: 'var(--font-mono, monospace)',
              textAlign: 'center',
              boxShadow: '0 30px 80px rgba(0,0,0,0.6)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {exporting && (
              <>
                <div style={{ fontSize: 11, letterSpacing: '0.14em', color: 'rgba(255,255,255,0.4)', marginBottom: 12, textTransform: 'uppercase' }}>
                  exporting
                </div>
                <div style={{ fontSize: 15, marginBottom: 16 }}>
                  {exportStatus === "queued" ? "queueing render job..." : "rendering your reel..."}
                </div>
                <div style={{
                  height: 3,
                  borderRadius: 2,
                  background: 'rgba(255,255,255,0.08)',
                  overflow: 'hidden',
                }}>
                  <div style={{
                    height: '100%',
                    borderRadius: 2,
                    background: 'rgba(255,255,255,0.4)',
                    width: exportStatus === "processing" ? '65%' : '20%',
                    transition: 'width 1.5s ease',
                  }} />
                </div>
              </>
            )}

            {exportUrl && !exporting && (
              <ExportComplete
                url={exportUrl}
                downloadUrl={exportDownloadUrl ?? exportUrl}
                downloadFilename={`iris-${(initialProject?.projectId ?? state.sources[0]?.projectId ?? 'reel').slice(0, 8)}.mp4`}
                fps={initialProject?.fps ?? state.sources[0]?.fps ?? null}
                duration={totalDuration(state.clips)}
                bytes={exportBytes}
                error={exportError}
                onDismiss={dismissExport}
              />
            )}

            {exportError && !exportUrl && (
              <>
                <div style={{ fontSize: 11, letterSpacing: '0.14em', color: 'rgba(255,107,107,0.8)', marginBottom: 12, textTransform: 'uppercase' }}>
                  export failed
                </div>
                <div style={{ fontSize: 13, marginBottom: 16, color: 'rgba(255,255,255,0.6)' }}>
                  {exportError}
                </div>
                <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
                  <button
                    onClick={() => { dismissExport(); void handleExport(); }}
                    style={{
                      padding: '8px 20px',
                      borderRadius: 999,
                      background: 'rgba(255,255,255,0.12)',
                      color: '#fff',
                      fontSize: 12,
                      border: '1px solid rgba(255,255,255,0.2)',
                      cursor: 'pointer',
                    }}
                  >
                    retry
                  </button>
                  <button
                    onClick={dismissExport}
                    style={{
                      padding: '8px 16px',
                      borderRadius: 999,
                      background: 'transparent',
                      color: 'rgba(255,255,255,0.5)',
                      fontSize: 12,
                      border: '1px solid rgba(255,255,255,0.08)',
                      cursor: 'pointer',
                    }}
                  >
                    dismiss
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      <section className="studio__body">
        <aside className="studio__left">
          <Library onUpload={handleFile} uploading={uploading} />
        </aside>

        <Splitter
          orientation="vertical"
          cssVar="--left-w"
          rootRef={rootRef}
          min={180}
          max={480}
          anchor="left"
        />

        <section className="studio__center">
          {hasSources ? (
            <>
              <Preview />
              {mode === 'vibe' && (
                <VideoScrubber
                  duration={initialProject?.duration ?? state.clips.reduce((sum, c) => sum + (c.sourceEnd - c.sourceStart), 0)}
                  playhead={state.playhead}
                  onSeek={(ts) => dispatch({ type: 'set_playhead', t: ts })}
                  onScrubStart={() => dispatch({ type: 'set_playing', playing: false })}
                  segments={timelineSpans(state.clips).map(({ clip, start, end }) => ({
                    start_ts: start,
                    end_ts: end,
                    source: clip.kind === 'generated' ? 'generated' as const : 'original' as const,
                  }))}
                />
              )}
            </>
          ) : hydratingProject ? (
            <div
              style={{
                display: 'grid',
                placeItems: 'center',
                height: '100%',
                color: 'rgba(255,255,255,0.55)',
                fontFamily: 'var(--font-mono, monospace)',
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                fontSize: 11,
              }}
            >
              reopening reel…
            </div>
          ) : (
            <UploadDrop onFile={handleFile} busy={uploading} />
          )}
        </section>

        <Splitter
          orientation="vertical"
          cssVar="--right-w"
          rootRef={rootRef}
          min={220}
          max={520}
          anchor="right"
        />

        <aside className="studio__right">
          {mode === 'vibe' ? (
            <AgentChat projectId={continuityProjectId} />
          ) : (
            <>
              <div style={{ padding: "12px 12px 0" }}>
                <EditorChecklist
                  projectId={continuityProjectId}
                  hasSources={hasSources}
                  hasSelection={Boolean(state.selectedId)}
                  hasBbox={Boolean(state.bbox)}
                  hasAcceptedEdit={hasAcceptedEdit}
                  hasContinuityPack={continuity.hasPropagatableAppearances}
                  continuityComplete={continuityComplete}
                  onImport={() => fileInputRef.current?.click()}
                />
              </div>
              <Inspector
                mode={mode}
                continuity={continuity}
                projectId={continuityProjectId}
                showAiTab={mode === 'pro'}
              />
            </>
          )}
        </aside>
      </section>

      <Splitter
        orientation="horizontal"
        cssVar="--tl-h"
        rootRef={rootRef}
        min={140}
        max={520}
        anchor="bottom"
      />

      <section className="studio__bottom">
        <Timeline />
      </section>
    </main>
  );
}

// ─── splitter ─────────────────────────────────────────────────────────
//
// a thin draggable divider that writes a CSS variable on the studio root.
// `anchor` tells us which direction grows when the cursor moves toward the
// origin:
//   - "left"   → dragging right makes the left panel bigger
//   - "right"  → dragging left  makes the right panel bigger
//   - "bottom" → dragging up    makes the bottom panel (timeline) bigger

function Splitter({
  orientation,
  cssVar,
  rootRef,
  min,
  max,
  anchor,
}: {
  orientation: "vertical" | "horizontal";
  cssVar: string;
  rootRef: RefObject<HTMLDivElement | null>;
  min: number;
  max: number;
  anchor: "left" | "right" | "bottom";
}) {
  const onDown = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      const root = rootRef.current;
      if (!root) return;
      e.preventDefault();
      (e.target as HTMLElement).setPointerCapture(e.pointerId);

      const cs = getComputedStyle(root);
      const startValue = parseFloat(cs.getPropertyValue(cssVar)) || 0;
      const startPos = orientation === "vertical" ? e.clientX : e.clientY;

      document.body.style.userSelect = "none";
      document.body.style.cursor =
        orientation === "vertical" ? "col-resize" : "row-resize";

      const onMove = (ev: PointerEvent) => {
        const pos = orientation === "vertical" ? ev.clientX : ev.clientY;
        let delta = pos - startPos;
        // flip delta when the panel grows in the opposite direction of the drag
        if (anchor === "right" || anchor === "bottom") delta = -delta;
        const next = Math.max(min, Math.min(max, startValue + delta));
        root.style.setProperty(cssVar, `${next}px`);
      };

      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        document.body.style.userSelect = "";
        document.body.style.cursor = "";
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [orientation, cssVar, rootRef, min, max, anchor],
  );

  return (
    <div
      className={`splitter splitter--${orientation}`}
      onPointerDown={onDown}
      role="separator"
      aria-orientation={orientation}
    />
  );
}

// ─── top bar ──────────────────────────────────────────────────────────

function TopBar({
  onExit,
  onLibrary,
  projectLabel,
  mode,
  onToggleMode,
  onImport,
  onShowShortcuts,
  onExport,
  exporting,
  exportLabel,
  canExport,
  continuity,
  saveStatus,
  lastSavedAt,
}: {
  onExit: () => void;
  onLibrary?: () => void;
  projectLabel?: string;
  mode: 'vibe' | 'pro';
  onToggleMode: () => void;
  onImport?: () => void;
  onShowShortcuts?: () => void;
  onExport?: () => void;
  exporting?: boolean;
  exportLabel?: string;
  canExport?: boolean;
  continuity: ContinuityDashboardController;
  saveStatus?: "idle" | "pending" | "saving" | "saved" | "error";
  lastSavedAt?: number;
}) {
  return (
    <header className="topbar">
      <div className="topbar__left">
        <button className="topbar__brand" onClick={onExit} title="back to landing">
          <span className="topbar__mark" aria-hidden />
          <span className="topbar__word">iris</span>
        </button>
        <span className="topbar__divider" />
        {onLibrary && (
          <>
            <button
              className="topbar__menu"
              onClick={onLibrary}
              title="back to my reels"
              style={{ letterSpacing: '0.08em' }}
            >
              ← my reels
            </button>
            <span className="topbar__divider" />
          </>
        )}
        <TopMenuItem label="File" onClick={onImport} title="import clip" />
        <TopMenuItem label="Edit" />
        <TopMenuItem label="View" onClick={onToggleMode} title={`switch to ${mode === 'vibe' ? 'pro' : 'vibe'} mode`} />
        <TopMenuItem label="Help" onClick={onShowShortcuts} title="keyboard shortcuts" />
      </div>

      <div className="topbar__center">
        <span className="mono topbar__proj">
          {projectLabel ? `reel · ${projectLabel}` : "untitled reel"}
        </span>
      </div>

      <div className="topbar__right">
        <SaveChip status={saveStatus} lastSavedAt={lastSavedAt} />
        <ContinuityStatusBadge continuity={continuity} />
        <button
          onClick={onToggleMode}
          className={`topbar__vibe ${mode === 'vibe' ? 'topbar__vibe--on' : ''}`}
          title={`switch to ${mode === 'vibe' ? 'pro' : 'vibe'} mode`}
        >
          {mode}
        </button>
        <button className="topbar__ghost" title="keyboard shortcuts" onClick={onShowShortcuts}>
          <Icon name="keyboard" size={14} />
          <span>Shortcuts</span>
        </button>
        <button
          className="cta topbar__export"
          disabled={!canExport || exporting}
          onClick={onExport}
        >
          {exportLabel || (exporting ? "Exporting…" : "Export")}
        </button>
      </div>
    </header>
  );
}

// ─── save-status chip ───────────────────────────────────────────────
//
// Lives on the right side of the top bar, to the left of the continuity
// badge. Shows one of four states:
//   • pending / saving → animated dot, "saving…"
//   • saved            → quiet "saved · <relative time>"
//   • error            → "save failed" in the error hue
//   • idle + no last save → nothing (blank canvas)
//
// The relative-time label re-computes every 15s so it drifts naturally
// ("saved · now" → "saved · 15s ago" → "saved · 1m ago") without us
// having to keep a timer alive on every dispatch.

function relativeTime(epochSeconds: number): string {
  if (!epochSeconds) return "";
  const delta = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (delta < 5) return "just now";
  if (delta < 60) return `${Math.floor(delta)}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  return `${Math.floor(delta / 3600)}h ago`;
}

function SaveChip({
  status,
  lastSavedAt,
}: {
  status?: "idle" | "pending" | "saving" | "saved" | "error";
  lastSavedAt?: number;
}) {
  const [, rerender] = useState(0);
  useEffect(() => {
    if (status !== "saved") return;
    const id = window.setInterval(() => rerender((n) => n + 1), 15_000);
    return () => window.clearInterval(id);
  }, [status, lastSavedAt]);

  if (!status || status === "idle") return null;

  const busy = status === "pending" || status === "saving";
  const errored = status === "error";
  const label = errored
    ? "save failed"
    : busy
      ? "saving…"
      : `saved · ${relativeTime(lastSavedAt ?? 0)}`;

  return (
    <span
      title={errored ? "last save failed — will retry on next edit" : "timeline auto-saved to iris"}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 7,
        padding: "4px 11px 4px 10px",
        borderRadius: 9999,
        fontFamily: "var(--f-mono)",
        fontSize: 10,
        letterSpacing: "0.14em",
        textTransform: "uppercase",
        color: errored ? "var(--err)" : "var(--ink-fade)",
        border: `1px solid ${errored ? "rgba(255,106,106,0.35)" : "var(--edge)"}`,
        background: "rgba(12,12,12,0.6)",
        transition: "color 140ms var(--ease), border-color 140ms var(--ease)",
        userSelect: "none",
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: errored
            ? "var(--err)"
            : busy
              ? "rgba(232,232,232,0.85)"
              : "linear-gradient(135deg, #f4f4f4, #9a9a9a)",
          boxShadow: errored
            ? "0 0 6px rgba(255,106,106,0.55)"
            : busy
              ? "0 0 8px rgba(232,232,232,0.4)"
              : "inset 0 -1px 0 rgba(0,0,0,0.4), 0 0 4px rgba(232,232,232,0.25)",
          animation: busy ? "pulse 1.4s ease-in-out infinite" : undefined,
        }}
      />
      {label}
    </span>
  );
}

function TopMenuItem({ label, onClick, title }: { label: string; onClick?: () => void; title?: string }) {
  return <button className="topbar__menu" onClick={onClick} title={title}>{label}</button>;
}

// ─── export-complete modal body ──────────────────────────────────────
//
// Shows a preview of the rendered reel, a compact stats strip, and a
// single download action. Resolution is read directly off the <video>
// element once its metadata loads, so we don't need to plumb width/height
// through the studio props.

function ExportComplete({
  url,
  downloadUrl,
  downloadFilename,
  fps,
  duration,
  bytes,
  error,
  onDismiss,
}: {
  url: string;
  downloadUrl: string;
  downloadFilename: string;
  fps: number | null;
  duration: number;
  bytes: number | null;
  error: string | null;
  onDismiss: () => void;
}) {
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null);

  return (
    <>
      <div
        style={{
          fontSize: 10,
          letterSpacing: '0.18em',
          color: 'rgba(126,231,135,0.75)',
          marginBottom: 14,
          textTransform: 'uppercase',
        }}
      >
        ● export complete
      </div>

      <div
        style={{
          position: 'relative',
          width: '100%',
          aspectRatio: dims ? `${dims.w} / ${dims.h}` : '16 / 9',
          background: '#000',
          borderRadius: 8,
          overflow: 'hidden',
          border: '1px solid rgba(255,255,255,0.06)',
          marginBottom: 14,
        }}
      >
        <video
          src={url}
          controls
          playsInline
          preload="metadata"
          onLoadedMetadata={(e) => {
            const v = e.currentTarget;
            if (v.videoWidth && v.videoHeight) {
              setDims({ w: v.videoWidth, h: v.videoHeight });
            }
          }}
          style={{
            width: '100%',
            height: '100%',
            display: 'block',
            background: '#000',
          }}
        />
      </div>

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '6px 14px',
          justifyContent: 'center',
          fontSize: 11,
          color: 'rgba(255,255,255,0.55)',
          letterSpacing: '0.06em',
          marginBottom: 18,
        }}
      >
        {dims && <StatChip label="res" value={`${dims.w}×${dims.h}`} />}
        <StatChip label="dur" value={`${duration.toFixed(2)}s`} />
        {fps !== null && <StatChip label="fps" value={String(Math.round(fps))} />}
        {bytes !== null && <StatChip label="size" value={fmtBytes(bytes)} />}
        <StatChip label="fmt" value="mp4 · h.264" />
      </div>

      {error && (
        <div
          style={{
            fontSize: 11,
            color: 'rgba(255,107,107,0.75)',
            marginBottom: 12,
          }}
        >
          {error}
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
        {/* Plain anchor with `download` + `rel=noopener`. The href already
            signs Content-Disposition: attachment server-side, so the
            browser triggers a save-to-disk in place — no new tab, no CORS
            fetch, no inline playback hijack. */}
        <a
          href={downloadUrl}
          download={downloadFilename}
          rel="noopener"
          style={{
            padding: '9px 22px',
            borderRadius: 999,
            background: '#fff',
            color: '#000',
            fontSize: 11,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            fontFamily: 'inherit',
            border: '1px solid rgba(255,255,255,0.14)',
            textDecoration: 'none',
            cursor: 'pointer',
            transition: 'opacity 0.18s',
          }}
        >
          download mp4
        </a>
        <button
          onClick={onDismiss}
          style={{
            padding: '9px 16px',
            borderRadius: 999,
            background: 'transparent',
            color: 'rgba(255,255,255,0.5)',
            fontSize: 11,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            fontFamily: 'inherit',
            border: '1px solid rgba(255,255,255,0.08)',
            cursor: 'pointer',
          }}
        >
          close
        </button>
      </div>
    </>
  );
}

function StatChip({ label, value }: { label: string; value: string }) {
  return (
    <span>
      <span style={{ color: 'rgba(255,255,255,0.3)' }}>{label} </span>
      <span style={{ color: 'rgba(255,255,255,0.78)' }}>{value}</span>
    </span>
  );
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)}MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)}GB`;
}

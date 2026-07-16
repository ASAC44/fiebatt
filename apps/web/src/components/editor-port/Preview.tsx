/* eslint-disable react-hooks/exhaustive-deps, react-hooks/set-state-in-effect */
import { useEffect, useRef, useState, useCallback } from "react";
import { clipAtTime, duration, sourceTimeFor, totalDuration, timelineSpans, useEDL } from "@/stores/edl";
import { getMask, identifyRegion } from "@/lib/api";
import BoundingBox from "./BoundingBox";
import { PreviewControls } from "./PreviewControls";

/**
 * Preview monitor. Single <video> element; swaps src + seeks whenever the
 * playhead crosses a clip boundary. Transport controls below. The stage
 * letterboxes the video within a 16:9 frame so the aspect feels stable
 * across clip swaps.
 *
 * Flicker suppression: A <canvas> sits on top of the video and captures
 * the last decoded frame before each src swap. The canvas stays visible
 * until the new source has seeked to the correct position, then fades out.
 * This eliminates the black-flash that occurs while the browser decodes
 * the first frame of the incoming clip.
 */
export function Preview() {
  const { state, dispatch } = useEDL();
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const currentClipIdRef = useRef<string | null>(null);
  const transitioningRef = useRef(false);
  const rafRef = useRef<number | null>(null);
  const [videoSize, setVideoSize] = useState({ width: 1920, height: 1080 });

  const hit = clipAtTime(state.clips, state.playhead);
  const activeClip = hit?.clip ?? null;
  const total = totalDuration(state.clips);
  const frameTs = hit ? sourceTimeFor(hit.clip, hit.offsetInClip) : null;

  // Drawing a bounding box kicks off Gemini identification and SAM mask
  // refinement independently. This has to live here (not inside the Inspector's AiTab)
  // because the bbox overlay itself lives on the preview and the user
  // might not be looking at the AI tab when they draw one. The effect
  // keys off bbox + projectId so it fires once per region, not per frame.
  const bbox = state.bbox;
  const projectId = activeClip?.kind === "source" ? (activeClip.projectId ?? null) : null;
  useEffect(() => {
    if (!bbox || !projectId || frameTs == null || state.playing) return;
    const controller = new AbortController();
    let cancelled = false;
    dispatch({ type: "set_identified", entity: null, loading: true });
    identifyRegion(projectId, frameTs, bbox, controller.signal)
      .then((resp) => {
        if (cancelled) return;
        dispatch({
          type: "set_identified",
          entity: {
            description: resp.description,
            category: resp.category,
            attributes: resp.attributes,
          },
          loading: false,
        });
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        dispatch({ type: "set_identified", entity: null, loading: false });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [bbox, projectId, activeClip?.id, frameTs, state.playing, dispatch]);

  // Do not make the fast SAM result wait for the much slower vision-language
  // identification request. Keeping separate abort controllers also means a
  // slow entity description can never suppress a valid mask contour.
  useEffect(() => {
    if (!bbox || !projectId || frameTs == null || state.playing) return;
    const controller = new AbortController();
    let cancelled = false;
    dispatch({ type: "set_mask", mask: null });
    getMask(projectId, frameTs, bbox, controller.signal)
      .then((resp) => {
        if (cancelled) return;
        dispatch({
          type: "set_mask",
          mask: resp.contour.length
            ? { contour: resp.contour, contours: resp.contours }
            : null,
        });
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        dispatch({ type: "set_mask", mask: null });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [bbox, projectId, activeClip?.id, frameTs, state.playing, dispatch]);

  // Synchronously paint the current video frame to the canvas and make it
  // visible BEFORE changing v.src — this must be synchronous (direct DOM)
  // because React state updates are batched/async and the video goes black
  // in the same microtask as the src swap.
  const captureFreeze = useCallback(() => {
    const v = videoRef.current;
    const c = canvasRef.current;
    if (!v || !c || v.readyState < 2) return;
    c.width = v.videoWidth || 1920;
    c.height = v.videoHeight || 1080;
    const ctx = c.getContext("2d");
    if (ctx) ctx.drawImage(v, 0, 0, c.width, c.height);
    // show synchronously — no React state, no batching delay
    c.style.transition = 'none';
    c.style.opacity = '1';
  }, []);

  // Release the freeze canvas once the new clip's first frame is decoded.
  const releaseFreeze = useCallback(() => {
    const c = canvasRef.current;
    if (!c) return;
    c.style.transition = 'opacity 0.1s ease-out';
    c.style.opacity = '0';
  }, []);

  // mirror active clip into <video>
  useEffect(() => {
    const v = videoRef.current;
    if (!v || !activeClip) return;

    const want = sourceTimeFor(activeClip, hit!.offsetInClip);
    const clipChanged = currentClipIdRef.current !== activeClip.id;

    if (clipChanged) {
      transitioningRef.current = false;
      currentClipIdRef.current = activeClip.id;
      // Capture last frame before src swap — shown as a freeze overlay to
      // prevent the black-flash flicker during browser decode.
      captureFreeze();
      // pause before source swap to prevent audio bleed
      const wasPlaying = !v.paused;
      v.pause();
      v.volume = activeClip.volume;
      v.src = activeClip.url;

      // Wait for both metadata (to know duration) and seeked (first frame
      // decoded at the target position) before releasing the freeze overlay.
      const onLoaded = () => {
        v.currentTime = want;
        v.volume = activeClip.volume;
      };
      const onSeeked = () => {
        // First frame of new clip is decoded — drop the freeze canvas.
        releaseFreeze();
        if (wasPlaying || state.playing) v.play().catch(() => {});
      };
      v.addEventListener("loadedmetadata", onLoaded, { once: true });
      v.addEventListener("seeked", onSeeked, { once: true });
      return () => {
        v.removeEventListener("loadedmetadata", onLoaded);
        v.removeEventListener("seeked", onSeeked);
        // safety: if effect re-runs before seeked fires, release the freeze
        releaseFreeze();
      };
    }

    v.volume = activeClip.volume;
    if (Math.abs(v.currentTime - want) > 0.15) {
      v.currentTime = want;
    }
  }, [activeClip?.id, activeClip?.url, activeClip?.volume, hit?.offsetInClip, state.playing, activeClip, captureFreeze, releaseFreeze]);

  useEffect(() => {
    if (!activeClip) {
      currentClipIdRef.current = null;
      setVideoSize({ width: 1920, height: 1080 });
    }
  }, [activeClip?.id]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (state.playing) v.play().catch(() => dispatch({ type: "set_playing", playing: false }));
    else v.pause();
  }, [state.playing, dispatch]);

  // Advance by clip id rather than by the playhead. At a media boundary the
  // playhead can already round into the following clip, which made an `ended`
  // handler occasionally identify the wrong item and stop. The guard also
  // makes the native event and the rAF boundary check safe to use together.
  const advanceFromClip = useCallback((clipId: string) => {
    if (transitioningRef.current) return;

    const endedIdx = state.clips.findIndex((clip) => clip.id === clipId);
    if (endedIdx < 0) {
      dispatch({ type: "set_playing", playing: false });
      return;
    }
    transitioningRef.current = true;

    const nextIdx = endedIdx + 1;
    if (nextIdx < state.clips.length) {
      const nextStart = state.clips
        .slice(0, nextIdx)
        .reduce((sum, clip) => sum + duration(clip), 0);
      dispatch({ type: "set_playhead", t: nextStart });
      dispatch({ type: "select", id: state.clips[nextIdx].id });
      return;
    }

    dispatch({ type: "set_playing", playing: false });
    dispatch({ type: "set_playhead", t: totalDuration(state.clips) });
  }, [state.clips, dispatch]);

  const handleEnded = useCallback(() => {
    const clipId = currentClipIdRef.current;
    if (clipId) advanceFromClip(clipId);
  }, [advanceFromClip]);

  useEffect(() => {
    if (!state.playing) transitioningRef.current = false;
  }, [state.playing]);

  // rAF loop while playing — advance timeline playhead, jump clips at boundaries
  useEffect(() => {
    function tick() {
      rafRef.current = requestAnimationFrame(tick);
      const v = videoRef.current;
      if (!v || !state.playing) return;

      // find which clip the playhead is currently in
      const hit2 = clipAtTime(state.clips, state.playhead);
      if (!hit2) return;
      const { clip, startInTimeline } = hit2;

      // `ended` implies `paused`, so process it before the regular paused
      // guard. This is a fallback for browsers that deliver the native ended
      // event after the animation frame in which playback halted.
      if (v.ended) {
        advanceFromClip(clip.id);
        return;
      }
      if (v.paused) return;

      // compute offset within the clip using video element's current time
      // relative to the clip's source range (not timeline position)
      const clipDur = duration(clip);
      const sourceOffset = v.currentTime - clip.sourceStart;
      const clampedOffset = Math.max(0, Math.min(sourceOffset, clipDur));

      // check if we've reached the end of this clip
      // Use a tighter 10ms early-exit threshold so we display as close to
      // the clip's last frame as possible before switching — reducing the
      // visual frame gap at cuts (was 40ms / ~1 frame at 25fps).
      if (clampedOffset >= clipDur - 0.01) {
        const nextIdx = hit2.index + 1;
        if (nextIdx < state.clips.length) {
          // advance to the next clip — pause first, let the source-swap effect handle it
          v.pause();
          advanceFromClip(clip.id);
        } else {
          // end of timeline
          dispatch({ type: "set_playing", playing: false });
          dispatch({ type: "set_playhead", t: totalDuration(state.clips) });
        }
        return;
      }

      // only dispatch if playhead actually changed (avoid dispatch spam)
      const newTimeline = startInTimeline + clampedOffset;
      if (Math.abs(newTimeline - state.playhead) > 0.01) {
        dispatch({ type: "set_playhead", t: newTimeline });
      }
    }
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [state.playing, state.clips, state.playhead, dispatch, advanceFromClip]);

  return (
    <div className="grid h-full min-h-0 min-w-0 flex-1 grid-rows-[minmax(0,1fr)_auto] overflow-hidden pb-2">
      <div
        className="relative grid min-h-0 place-items-center overflow-hidden bg-[var(--recessed)]"
        ref={stageRef}
      >
        {activeClip ? (
          <>
            <video
              ref={videoRef}
              className="block max-h-full max-w-full bg-black object-contain"
              playsInline
              onLoadedMetadata={(e) => {
                const { videoWidth, videoHeight } = e.currentTarget;
                setVideoSize({
                  width: videoWidth || 1920,
                  height: videoHeight || 1080,
                });
              }}
              onEnded={handleEnded}
            />
            {/* Freeze-frame canvas: synchronously painted before src swap to
                suppress the black-flash. Starts hidden (opacity:0); captureFreeze
                sets it to 1 directly via the ref, releaseFreeze fades it back. */}
            <canvas
              ref={canvasRef}
              className="pointer-events-none absolute inset-0 block h-full w-full max-w-full bg-black object-contain opacity-0"
              style={{
                maxHeight: "100%",
              }}
            />
            <BoundingBox
              videoWidth={videoSize.width}
              videoHeight={videoSize.height}
              containerRef={stageRef}
              disabled={state.playing}
              onBoxDrawn={(bbox) => dispatch({ type: "set_bbox", bbox })}
              onClear={() => dispatch({ type: "set_bbox", bbox: null })}
              bbox={state.bbox}
              mask={state.playing ? null : state.mask}
            />
          </>
        ) : (
          <div className="text-[10px] tracking-[0.25em] text-[var(--ink-fade)]">no clip</div>
        )}
      </div>

      <PreviewControls
        duration={total}
        segments={timelineSpans(state.clips).map(({ clip, start, end }) => ({
          start_ts: start,
          end_ts: end,
          source: clip.kind === "generated" ? "generated" : "original",
        }))}
      />
    </div>
  );
}

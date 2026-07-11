import { useEffect, useRef, useState, useCallback, type ReactNode } from "react";
import { clipAtTime, duration, sourceTimeFor, totalDuration, useEDL } from "../stores/edl";
import { identifyRegion } from "../api/client";
import { Icon } from "./Icon";
import BoundingBox from "./BoundingBox";
import "./preview.css";

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
  const rafRef = useRef<number | null>(null);
  const [videoSize, setVideoSize] = useState({ width: 1920, height: 1080 });

  const hit = clipAtTime(state.clips, state.playhead);
  const activeClip = hit?.clip ?? null;
  const total = totalDuration(state.clips);
  const frameTs = hit ? sourceTimeFor(hit.clip, hit.offsetInClip) : null;

  // Drawing a bounding box kicks off Gemini identification + SAM mask
  // refinement. This has to live here (not inside the Inspector's AiTab)
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
        dispatch({
          type: "set_mask",
          mask: resp.mask?.contour?.length ? { contour: resp.mask.contour } : null,
        });
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        dispatch({ type: "set_identified", entity: null, loading: false });
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

  // rAF loop while playing — advance timeline playhead, jump clips at boundaries
  useEffect(() => {
    function tick() {
      rafRef.current = requestAnimationFrame(tick);
      const v = videoRef.current;
      if (!v || !state.playing || v.paused) return;

      // find which clip the playhead is currently in
      const hit2 = clipAtTime(state.clips, state.playhead);
      if (!hit2) return;
      const { clip, startInTimeline } = hit2;

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
          const nextStart = startInTimeline + clipDur;
          v.pause();
          dispatch({ type: "set_playhead", t: nextStart });
          // selection follows playback
          dispatch({ type: "select", id: state.clips[nextIdx].id });
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
  }, [state.playing, state.clips, state.playhead, dispatch]);

  return (
    <div className="pv">
      <div className="pv__stage" ref={stageRef} style={{ position: 'relative' }}>
        {activeClip ? (
          <>
            <video
              ref={videoRef}
              className="pv__video"
              playsInline
              onLoadedMetadata={(e) => {
                const { videoWidth, videoHeight } = e.currentTarget;
                setVideoSize({
                  width: videoWidth || 1920,
                  height: videoHeight || 1080,
                });
              }}
            />
            {/* Freeze-frame canvas: synchronously painted before src swap to
                suppress the black-flash. Starts hidden (opacity:0); captureFreeze
                sets it to 1 directly via the ref, releaseFreeze fades it back. */}
            <canvas
              ref={canvasRef}
              className="pv__video"
              style={{
                position: 'absolute',
                inset: 0,
                width: '100%',
                height: '100%',
                pointerEvents: 'none',
                opacity: 0,
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
              mask={state.mask}
            />
          </>
        ) : (
          <div className="pv__placeholder mono">no clip</div>
        )}
      </div>

      <div className="pv__bar">
        <span className="mono pv__tc">
          {fmt(state.playhead)}
          <span className="pv__sep"> / </span>
          <span className="pv__total">{fmt(total)}</span>
        </span>

        <Transport />

        <div className="pv__aspect mono">16 : 9</div>
      </div>
    </div>
  );
}

function Transport() {
  const { state, dispatch } = useEDL();
  const total = totalDuration(state.clips);
  const step = 1 / 24;

  return (
    <div className="tp">
      <IconBtn title="jump to start" onClick={() => dispatch({ type: "set_playhead", t: 0 })}>
        <Icon name="skip-back" size={14} />
      </IconBtn>
      <IconBtn title="back 1 frame" onClick={() => dispatch({ type: "set_playhead", t: state.playhead - step })}>
        <Icon name="step-back" size={14} />
      </IconBtn>
      <button
        className="tp__play"
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
    <button className="tp__btn" title={title} onClick={onClick}>
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

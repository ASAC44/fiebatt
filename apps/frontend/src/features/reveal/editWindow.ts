import { duration, type Clip } from "../../stores/edl";

export const MIN_EDIT_WINDOW_SECONDS = 2;
export const MAX_EDIT_WINDOW_SECONDS = 5;
export const DEFAULT_EDIT_WINDOW_SECONDS = 3;

export type EditWindow = {
  clip: Clip;
  valid: boolean;
  mode: "full" | "window";
  label: string;
  start: number;
  end: number;
  previewFrameTs: number;
};

export function buildEditWindow(
  clip: Clip | null,
  previewFrameTs: number | null,
): EditWindow | null {
  if (!clip) return null;

  const clipDuration = duration(clip);
  const midpoint = clip.sourceStart + clipDuration / 2;
  const requestedTs = clamp(
    previewFrameTs ?? midpoint,
    clip.sourceStart,
    clip.sourceEnd,
  );

  if (clipDuration < MIN_EDIT_WINDOW_SECONDS) {
    return {
      clip,
      valid: false,
      mode: "full",
      label: formatWindowLabel(clip.sourceStart, clip.sourceEnd),
      start: clip.sourceStart,
      end: clip.sourceEnd,
      previewFrameTs: requestedTs,
    };
  }

  if (clipDuration <= MAX_EDIT_WINDOW_SECONDS) {
    return {
      clip,
      valid: true,
      mode: "full",
      label: formatWindowLabel(clip.sourceStart, clip.sourceEnd),
      start: clip.sourceStart,
      end: clip.sourceEnd,
      previewFrameTs: requestedTs,
    };
  }

  const halfWindow = DEFAULT_EDIT_WINDOW_SECONDS / 2;
  const center = clamp(
    requestedTs,
    clip.sourceStart + halfWindow,
    clip.sourceEnd - halfWindow,
  );
  const start = center - halfWindow;
  const end = center + halfWindow;

  return {
    clip: {
      ...clip,
      sourceStart: start,
      sourceEnd: end,
    },
    valid: true,
    mode: "window",
    label: formatWindowLabel(start, end),
    start,
    end,
    previewFrameTs: clamp(requestedTs, start, end),
  };
}

function formatWindowLabel(start: number, end: number) {
  return `${formatTimestamp(start)}-${formatTimestamp(end)}`;
}

function formatTimestamp(value: number) {
  const totalSeconds = Math.max(0, value);
  const mins = Math.floor(totalSeconds / 60);
  const secs = totalSeconds % 60;
  return `${mins}:${secs.toFixed(1).padStart(4, "0")}`;
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

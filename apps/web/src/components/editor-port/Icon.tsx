/**
 * Tiny line icon set. 1.5px stroke, currentColor, 24px viewBox.
 * Keeps the whole UI visually consistent — one pen weight everywhere.
 */
import type * as React from "react";
import type { SVGProps } from "react";

export type IconName =
  | "media"
  | "audio"
  | "effects"
  | "adjust"
  | "play"
  | "pause"
  | "skip-back"
  | "skip-fwd"
  | "step-back"
  | "step-fwd"
  | "split"
  | "trash"
  | "select"
  | "undo"
  | "redo"
  | "lock"
  | "unlock"
  | "eye"
  | "eye-off"
  | "volume"
  | "volume-mute"
  | "plus"
  | "keyboard"
  | "sparkles"
  | "sliders"
  | "info"
  | "close"
  | "trim-in"
  | "trim-out"
  | "zoom-in"
  | "zoom-out";

type Props = SVGProps<SVGSVGElement> & { name: IconName; size?: number };

export function Icon({ name, size = 16, ...rest }: Props) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {paths[name]}
    </svg>
  );
}

const paths: Record<IconName, React.ReactElement> = {
  media: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="1" />
      <path d="M3 9h18M9 5v14" />
    </>
  ),
  audio: (
    <>
      <path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2" />
    </>
  ),
  effects: (
    <>
      <path d="M5 3v4M3 5h4M18 15v4M16 17h4" />
      <path d="M13 4l3 7 5 2-5 2-3 7-3-7-5-2 5-2 3-7z" />
    </>
  ),
  adjust: (
    <>
      <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h14M18 18h2" />
      <circle cx="16" cy="6" r="2" />
      <circle cx="10" cy="12" r="2" />
      <circle cx="16" cy="18" r="2" />
    </>
  ),
  play: <path d="M6 4l14 8-14 8V4z" />,
  pause: <path d="M7 4v16M17 4v16" />,
  "skip-back": <path d="M6 4v16M20 4L8 12l12 8V4z" />,
  "skip-fwd": <path d="M18 4v16M4 4l12 8-12 8V4z" />,
  "step-back": <path d="M15 4l-8 8 8 8V4z" />,
  "step-fwd": <path d="M9 4l8 8-8 8V4z" />,
  split: (
    <>
      <path d="M12 2v20" />
      <path d="M5 7l3 3M19 7l-3 3M5 17l3-3M19 17l-3-3" />
    </>
  ),
  trash: (
    <>
      <path d="M4 7h16M10 7V4h4v3M6 7l1 13h10l1-13M10 11v6M14 11v6" />
    </>
  ),
  select: <path d="M5 3l14 8-6 2-2 6L5 3z" />,
  undo: <path d="M9 14l-5-5 5-5M4 9h10a6 6 0 010 12h-4" />,
  redo: <path d="M15 14l5-5-5-5M20 9H10a6 6 0 000 12h4" />,
  lock: (
    <>
      <rect x="5" y="11" width="14" height="10" rx="1" />
      <path d="M8 11V7a4 4 0 018 0v4" />
    </>
  ),
  unlock: (
    <>
      <rect x="5" y="11" width="14" height="10" rx="1" />
      <path d="M8 11V7a4 4 0 017-2.6" />
    </>
  ),
  eye: (
    <>
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  "eye-off": (
    <>
      <path d="M3 3l18 18" />
      <path d="M10.7 6.2A11 11 0 0112 6c7 0 11 6 11 6a18 18 0 01-3.2 3.8M6 6.6A18 18 0 001 12s4 7 11 7a11 11 0 004.3-.9" />
      <path d="M9.5 9.5a3 3 0 004.2 4.2" />
    </>
  ),
  volume: (
    <>
      <path d="M4 9h3l5-4v14l-5-4H4V9z" />
      <path d="M15 8a5 5 0 010 8M18 5a9 9 0 010 14" />
    </>
  ),
  "volume-mute": (
    <>
      <path d="M4 9h3l5-4v14l-5-4H4V9z" />
      <path d="M16 9l5 6M21 9l-5 6" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  keyboard: (
    <>
      <rect x="2" y="6" width="20" height="12" rx="1" />
      <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10" />
    </>
  ),
  sparkles: (
    <>
      <path d="M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2 2-5zM19 14l1 2 2 1-2 1-1 2-1-2-2-1 2-1 1-2z" />
    </>
  ),
  sliders: (
    <>
      <path d="M4 6h16M4 12h16M4 18h16" />
      <circle cx="10" cy="6" r="2" />
      <circle cx="16" cy="12" r="2" />
      <circle cx="8" cy="18" r="2" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v.01M11 12h1v5h1" />
    </>
  ),
  close: <path d="M5 5l14 14M19 5L5 19" />,
  "trim-in": <path d="M8 4v16M14 8l-4 4 4 4" />,
  "trim-out": <path d="M16 4v16M10 8l4 4-4 4" />,
  "zoom-in": (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="M20 20l-4-4M8 11h6M11 8v6" />
    </>
  ),
  "zoom-out": (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="M20 20l-4-4M8 11h6" />
    </>
  ),
};

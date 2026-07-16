/* eslint-disable react-hooks/set-state-in-effect */
import { useCallback, useEffect, useRef, useState } from "react";

interface BoundingBoxProps {
  videoWidth: number;
  videoHeight: number;
  containerRef: React.RefObject<HTMLDivElement | null>;
  onBoxDrawn: (bbox: { x: number; y: number; w: number; h: number }) => void;
  onClear: () => void;
  disabled?: boolean;
  bbox?: { x: number; y: number; w: number; h: number } | null;
  /** SAM-refined contour that snaps to the subject. Points are normalized 0-1. */
  mask?: { contour: [number, number][]; contours?: [number, number][][] } | null;
}

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

function BoundingBox({
  videoWidth,
  videoHeight,
  containerRef,
  onBoxDrawn,
  onClear,
  disabled = false,
  bbox = null,
  mask = null,
}: BoundingBoxProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawingRef = useRef(false);
  const startRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  const activeBoxRef = useRef<Box | null>(null);
  const [box, setBox] = useState<Box | null>(null);
  const [activeBox, setActiveBox] = useState<Box | null>(null);

  const updateActiveBox = useCallback((next: Box | null) => {
    activeBoxRef.current = next;
    setActiveBox(next);
  }, []);

  /** Resize canvas to match the actual displayed video rect inside the stage. */
  const syncSize = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const { width, height } = container.getBoundingClientRect();
    if (width <= 0 || height <= 0) return;

    const safeVideoWidth = videoWidth > 0 ? videoWidth : 1920;
    const safeVideoHeight = videoHeight > 0 ? videoHeight : 1080;
    const videoAspect = safeVideoWidth / safeVideoHeight;
    const containerAspect = width / height;

    let displayWidth = width;
    let displayHeight = height;
    if (videoAspect > containerAspect) {
      displayHeight = width / videoAspect;
    } else {
      displayWidth = height * videoAspect;
    }

    const left = (width - displayWidth) / 2;
    const top = (height - displayHeight) / 2;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = displayWidth * dpr;
    canvas.height = displayHeight * dpr;
    canvas.style.width = `${displayWidth}px`;
    canvas.style.height = `${displayHeight}px`;
    canvas.style.left = `${left}px`;
    canvas.style.top = `${top}px`;
  }, [containerRef, videoWidth, videoHeight]);

  /** Convert a mouse event to normalized 0-1 coords relative to video dimensions. */
  const toNormalized = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement> | MouseEvent): { x: number; y: number } => {
      const canvas = canvasRef.current;
      if (!canvas) return { x: 0, y: 0 };

      const rect = canvas.getBoundingClientRect();
      const px = (e.clientX - rect.left) / rect.width;
      const py = (e.clientY - rect.top) / rect.height;

      return {
        x: Math.max(0, Math.min(1, px)),
        y: Math.max(0, Math.min(1, py)),
      };
    },
    [],
  );

  /** Draw the current state onto the canvas. */
  const paint = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const cw = canvas.width;
    const ch = canvas.height;
    ctx.clearRect(0, 0, cw, ch);

    // While the user is still dragging, show the raw rectangle. Once a SAM
    // mask arrives, the rectangle fades back and the contour becomes primary.
    const renderBox = activeBox ?? box;
    const maskContours = mask?.contours?.length
      ? mask.contours
      : mask?.contour?.length
        ? [mask.contour]
        : [];
    const hasMask = maskContours.some((contour) => contour.length > 2);
    const isDragging = activeBox !== null;

    if (renderBox) {
      const bx = renderBox.x * cw;
      const by = renderBox.y * ch;
      const bw = renderBox.w * cw;
      const bh = renderBox.h * ch;

      if (!hasMask || isDragging) {
        ctx.fillStyle = "rgba(225, 29, 72, 0.18)";
        ctx.fillRect(bx, by, bw, bh);
      }

      ctx.save();
      ctx.shadowColor = "rgba(225, 29, 72, 0.75)";
      ctx.shadowBlur = hasMask && !isDragging ? 3 : 7;
      ctx.strokeStyle = hasMask && !isDragging ? "rgba(225, 29, 72, 0.65)" : "#fb7185";
      ctx.lineWidth = hasMask && !isDragging ? 1.5 : 2.5;
      ctx.setLineDash([7, 4]);
      ctx.strokeRect(bx, by, bw, bh);
      ctx.setLineDash([]);
      ctx.restore();
    }

    // SAM contour — solid glowing outline that snaps to the subject.
    if (hasMask) {
      ctx.beginPath();
      for (const contour of maskContours) {
        if (contour.length < 3) continue;
        const [firstX, firstY] = contour[0];
        ctx.moveTo(firstX * cw, firstY * ch);
        for (let i = 1; i < contour.length; i++) {
          const [mx, my] = contour[i];
          ctx.lineTo(mx * cw, my * ch);
        }
        ctx.closePath();
      }

      ctx.fillStyle = "rgba(225, 29, 72, 0.22)";
      ctx.fill();

      ctx.save();
      ctx.shadowColor = "rgba(225, 29, 72, 0.95)";
      ctx.shadowBlur = 10;
      ctx.strokeStyle = "#fb7185";
      ctx.lineWidth = 2.5;
      ctx.stroke();
      ctx.restore();
    }
  }, [box, activeBox, mask]);

  /** Sync canvas size on mount and whenever the container resizes. */
  useEffect(() => {
    syncSize();

    const observer = new ResizeObserver(() => {
      syncSize();
      paint();
    });

    const container = containerRef.current;
    if (container) observer.observe(container);

    return () => observer.disconnect();
  }, [syncSize, paint, containerRef]);

  /** Repaint whenever the box, active drawing, or mask changes. */
  useEffect(() => {
    paint();
  }, [paint]);

  useEffect(() => {
    setBox(bbox);
    if (!bbox) updateActiveBox(null);
  }, [bbox, updateActiveBox]);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      console.log('[BoundingBox] mousedown', { disabled, pos: toNormalized(e) });
      if (disabled) return;

      const pos = toNormalized(e);
      drawingRef.current = true;
      startRef.current = pos;
      updateActiveBox({ x: pos.x, y: pos.y, w: 0, h: 0 });
    },
    [disabled, toNormalized, updateActiveBox],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (!drawingRef.current || disabled) return;

      const pos = toNormalized(e);
      const sx = startRef.current.x;
      const sy = startRef.current.y;

      // Normalize so x,y is always the top-left corner
      const x = Math.min(sx, pos.x);
      const y = Math.min(sy, pos.y);
      const w = Math.abs(pos.x - sx);
      const h = Math.abs(pos.y - sy);

      updateActiveBox({ x, y, w, h });
    },
    [disabled, toNormalized, updateActiveBox],
  );

  const handleMouseUp = useCallback(() => {
    if (!drawingRef.current || disabled) return;
    drawingRef.current = false;

    const completedBox = activeBoxRef.current;
    if (completedBox && completedBox.w > 0.005 && completedBox.h > 0.005) {
      console.log('[BoundingBox] box drawn', completedBox);
      setBox(completedBox);
      onBoxDrawn(completedBox);
    } else {
      console.log('[BoundingBox] box too small, ignored', completedBox);
    }

    updateActiveBox(null);
  }, [disabled, onBoxDrawn, updateActiveBox]);

  const handleDoubleClick = useCallback(() => {
    if (disabled) return;
    setBox(null);
    updateActiveBox(null);
    onClear();
  }, [disabled, onClear, updateActiveBox]);

  /** Catch mouseup outside the canvas so a drag isn't stuck. */
  useEffect(() => {
    const onGlobalMouseUp = () => {
      if (drawingRef.current) {
        drawingRef.current = false;
        const completedBox = activeBoxRef.current;
        updateActiveBox(null);

        if (completedBox && completedBox.w > 0.005 && completedBox.h > 0.005) {
          setBox(completedBox);
          onBoxDrawn(completedBox);
        }
      }
    };

    window.addEventListener("mouseup", onGlobalMouseUp);
    return () => window.removeEventListener("mouseup", onGlobalMouseUp);
  }, [onBoxDrawn, updateActiveBox]);

  return (
    <canvas
      ref={canvasRef}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onDoubleClick={handleDoubleClick}
      style={{
        position: "absolute",
        zIndex: 10,
        pointerEvents: disabled ? "none" : "auto",
        cursor: disabled ? "default" : "crosshair",
      }}
    />
  );
}

export default BoundingBox;

import { useCallback, useEffect, useRef, useState } from "react";

interface BoundingBoxProps {
  videoWidth: number;
  videoHeight: number;
  containerRef: React.RefObject<HTMLDivElement>;
  onBoxDrawn: (bbox: { x: number; y: number; w: number; h: number }) => void;
  onClear: () => void;
  disabled?: boolean;
  bbox?: { x: number; y: number; w: number; h: number } | null;
  /** SAM-refined contour that snaps to the subject. Points are normalized 0-1. */
  mask?: { contour: [number, number][] } | null;
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
  const [box, setBox] = useState<Box | null>(null);
  const [activeBox, setActiveBox] = useState<Box | null>(null);

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
    const hasMask = !!(mask && mask.contour.length > 2);
    const isDragging = activeBox !== null;

    if (renderBox) {
      const bx = renderBox.x * cw;
      const by = renderBox.y * ch;
      const bw = renderBox.w * cw;
      const bh = renderBox.h * ch;

      if (!hasMask || isDragging) {
        ctx.fillStyle = "rgba(255, 255, 255, 0.08)";
        ctx.fillRect(bx, by, bw, bh);
      }

      ctx.strokeStyle = hasMask && !isDragging ? "rgba(255, 255, 255, 0.35)" : "#ffffff";
      ctx.lineWidth = hasMask && !isDragging ? 1 : 2;
      ctx.setLineDash([6, 4]);
      ctx.strokeRect(bx, by, bw, bh);
      ctx.setLineDash([]);
    }

    // SAM contour — solid glowing outline that snaps to the subject.
    if (hasMask) {
      ctx.beginPath();
      const [firstX, firstY] = mask!.contour[0];
      ctx.moveTo(firstX * cw, firstY * ch);
      for (let i = 1; i < mask!.contour.length; i++) {
        const [mx, my] = mask!.contour[i];
        ctx.lineTo(mx * cw, my * ch);
      }
      ctx.closePath();

      ctx.fillStyle = "rgba(120, 200, 255, 0.18)";
      ctx.fill();

      ctx.save();
      ctx.shadowColor = "rgba(120, 200, 255, 0.9)";
      ctx.shadowBlur = 8;
      ctx.strokeStyle = "#9ad7ff";
      ctx.lineWidth = 2;
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
    if (!bbox) setActiveBox(null);
  }, [bbox]);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      console.log('[BoundingBox] mousedown', { disabled, pos: toNormalized(e) });
      if (disabled) return;

      const pos = toNormalized(e);
      drawingRef.current = true;
      startRef.current = pos;
      setActiveBox({ x: pos.x, y: pos.y, w: 0, h: 0 });
    },
    [disabled, toNormalized],
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

      setActiveBox({ x, y, w, h });
    },
    [disabled, toNormalized],
  );

  const handleMouseUp = useCallback(() => {
    if (!drawingRef.current || disabled) return;
    drawingRef.current = false;

    if (activeBox && activeBox.w > 0.005 && activeBox.h > 0.005) {
      console.log('[BoundingBox] box drawn', activeBox);
      setBox(activeBox);
      onBoxDrawn(activeBox);
    } else {
      console.log('[BoundingBox] box too small, ignored', activeBox);
    }

    setActiveBox(null);
  }, [disabled, activeBox, onBoxDrawn]);

  const handleDoubleClick = useCallback(() => {
    if (disabled) return;
    setBox(null);
    setActiveBox(null);
    onClear();
  }, [disabled, onClear]);

  /** Catch mouseup outside the canvas so a drag isn't stuck. */
  useEffect(() => {
    const onGlobalMouseUp = () => {
      if (drawingRef.current) {
        drawingRef.current = false;
        setActiveBox((prev) => {
          if (prev && prev.w > 0.005 && prev.h > 0.005) {
            setBox(prev);
            onBoxDrawn(prev);
          }
          return null;
        });
      }
    };

    window.addEventListener("mouseup", onGlobalMouseUp);
    return () => window.removeEventListener("mouseup", onGlobalMouseUp);
  }, [onBoxDrawn]);

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

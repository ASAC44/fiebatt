"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Excalidraw } from "@excalidraw/excalidraw";
import type {
  ExcalidrawImperativeAPI,
  ExcalidrawInitialDataState,
} from "@excalidraw/excalidraw/types";
import { Maximize2, Minimize2 } from "lucide-react";
import "@excalidraw/excalidraw/index.css";

export function TechnicalFlowExcalidraw() {
  const [scene, setScene] = useState<ExcalidrawInitialDataState | null>(null);
  const [api, setApi] = useState<ExcalidrawImperativeAPI | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadScene() {
      const response = await fetch("/fiebatt-technical-flow.excalidraw", {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error("Failed to load fiebatt technical flow");
      }
      const data = (await response.json()) as ExcalidrawInitialDataState;
      if (!cancelled) {
        setScene(data);
      }
    }

    void loadScene();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!api || !scene?.elements?.length) return;

    const frame = window.requestAnimationFrame(() => {
      api.scrollToContent(scene.elements ?? undefined, {
        animate: false,
        fitToViewport: true,
        maxZoom: 1,
        minZoom: 0.25,
        viewportZoomFactor: 0.9,
      });
    });

    return () => window.cancelAnimationFrame(frame);
  }, [api, scene]);

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === containerRef.current);
      window.requestAnimationFrame(() => {
        api?.refresh();
        api?.scrollToContent(scene?.elements ?? undefined, {
          animate: false,
          fitToViewport: true,
          maxZoom: 1,
          minZoom: 0.25,
          viewportZoomFactor: 0.9,
        });
      });
    };

    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, [api, scene?.elements]);

  const toggleFullscreen = useCallback(async () => {
    const node = containerRef.current;
    if (!node) return;

    if (document.fullscreenElement === node) {
      await document.exitFullscreen();
      return;
    }

    await node.requestFullscreen();
  }, []);

  return (
    <div
      ref={containerRef}
      className="relative h-[620px] overflow-hidden rounded-[2rem] border border-black/10 bg-white md:h-[760px] [&:fullscreen]:h-screen [&:fullscreen]:rounded-none [&:fullscreen]:border-0"
    >
      <button
        aria-label={isFullscreen ? "Exit fullscreen diagram" : "Fullscreen diagram"}
        className="absolute right-4 top-4 z-10 inline-flex h-10 items-center gap-2 rounded-full border border-black/10 bg-white/85 px-4 text-sm font-medium text-neutral-900 backdrop-blur transition-colors hover:bg-white"
        onClick={toggleFullscreen}
        type="button"
      >
        {isFullscreen ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
        {isFullscreen ? "Exit" : "Fullscreen"}
      </button>
      {scene ? (
        <Excalidraw
          excalidrawAPI={setApi}
          initialData={{
            elements: scene.elements,
            appState: {
              ...scene.appState,
              currentItemFontFamily: 1,
              viewBackgroundColor: "#FFFFFF",
              viewModeEnabled: true,
              zenModeEnabled: false,
            },
            files: scene.files,
          }}
          theme="light"
          UIOptions={{
            canvasActions: {
              changeViewBackgroundColor: false,
              clearCanvas: false,
              export: false,
              loadScene: false,
              saveAsImage: false,
              saveToActiveFile: false,
              toggleTheme: false,
            },
          }}
          viewModeEnabled
          zenModeEnabled={false}
        />
      ) : (
        <div className="flex h-full items-center justify-center text-sm text-neutral-500">
          Loading flow chart...
        </div>
      )}
    </div>
  );
}

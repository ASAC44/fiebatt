"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import type { ExcalidrawInitialDataState } from "@excalidraw/excalidraw/types";
import "@excalidraw/excalidraw/index.css";

const Excalidraw = dynamic(
  () => import("@excalidraw/excalidraw").then((module) => module.Excalidraw),
  { ssr: false },
);

export function TechnicalFlowDiagram() {
  const [scene, setScene] = useState<ExcalidrawInitialDataState | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    fetch("/fiebatt-technical-flow.excalidraw")
      .then((response) => {
        if (!response.ok) throw new Error("Could not load the Excalidraw scene.");
        return response.json() as Promise<ExcalidrawInitialDataState>;
      })
      .then((data) => {
        if (!cancelled) setScene(data);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <div className="grid h-full min-h-96 place-items-center rounded-2xl bg-white p-8 text-sm text-neutral-500">
        The technical diagram could not be loaded.
      </div>
    );
  }

  if (!scene) {
    return (
      <div className="grid h-full min-h-96 place-items-center rounded-2xl bg-white p-8 text-sm text-neutral-500">
        Loading the technical diagram…
      </div>
    );
  }

  return (
    <div className="h-[min(58rem,75vh)] min-h-[28rem] w-full overflow-hidden rounded-2xl bg-white">
      <Excalidraw
        initialData={{
          ...scene,
          appState: {
            ...scene.appState,
            viewModeEnabled: true,
          },
        }}
        viewModeEnabled
        UIOptions={{
          canvasActions: {
            changeViewBackgroundColor: false,
            clearCanvas: false,
            export: false,
            loadScene: false,
            saveToActiveFile: false,
            toggleTheme: false,
          },
        }}
      />
    </div>
  );
}

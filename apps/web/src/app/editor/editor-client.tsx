"use client";

/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { getProject, listProjects, type ProjectDetail } from "@/lib/api";
import { Studio, type StudioInitialProject } from "./Studio";

function toInitialProject(project: ProjectDetail): StudioInitialProject {
  return {
    projectId: project.project_id,
    videoUrl: project.video_url,
    duration: project.duration,
    fps: project.fps,
    label: project.project_id.slice(0, 8),
  };
}

export function EditorClient({ initialProjectId }: { initialProjectId?: string }) {
  const router = useRouter();
  const [project, setProject] = useState<StudioInitialProject | undefined>();
  const [loading, setLoading] = useState(Boolean(initialProjectId));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!initialProjectId) {
      setProject(undefined);
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      getProject(initialProjectId),
      listProjects().catch(() => null),
    ])
      .then(([detail, items]) => {
        if (cancelled) return;
        const listMatch = items?.find((item) => item.project_id === initialProjectId);
        setProject({
          ...toInitialProject(detail),
          videoUrl: listMatch?.video_url ?? detail.video_url,
          duration: listMatch?.duration ?? detail.duration,
          fps: listMatch?.fps ?? detail.fps,
        });
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [initialProjectId]);

  if (loading) {
    return (
      <main className="grid h-screen place-items-center bg-background text-sm text-muted-foreground">
        Reopening video
      </main>
    );
  }

  if (error) {
    return (
      <main className="grid h-screen place-items-center bg-background px-6 text-center">
        <div className="max-w-md">
          <h1 className="text-lg font-semibold">Could not reopen this video</h1>
          <p className="mt-2 text-sm text-muted-foreground">{error}</p>
        </div>
      </main>
    );
  }

  return (
    <Studio
      initialProject={project}
      onExit={() => router.push("/projects")}
      onLibrary={() => router.push("/projects")}
    />
  );
}

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, Loader2 } from "lucide-react";

import { getProject, type ProjectDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme-toggle";

export function ProjectDetailClient({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;

    getProject(projectId)
      .then((value) => {
        if (alive) setProject(value);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      });

    return () => {
      alive = false;
    };
  }, [projectId]);

  return (
    <main className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4">
          <Button asChild variant="outline">
            <Link href="/projects">
              <ArrowLeft />
              Projects
            </Link>
          </Button>
          <ThemeToggle />
        </div>
      </header>

      <section className="mx-auto max-w-7xl px-5 py-8">
        {error ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
            {error}
          </div>
        ) : null}

        {!project && !error ? (
          <div className="flex min-h-64 items-center justify-center rounded-lg border border-border bg-card text-sm text-muted-foreground">
            <Loader2 className="mr-2 animate-spin" />
            Loading project
          </div>
        ) : null}

        {project ? (
          <div className="grid gap-6 lg:grid-cols-[1.4fr_0.6fr]">
            <div className="overflow-hidden rounded-lg border border-border bg-card">
              <video
                className="aspect-video w-full bg-black object-contain"
                controls
                playsInline
                src={project.video_url}
              />
            </div>

            <aside className="rounded-lg border border-border bg-card p-5">
              <p className="text-sm font-medium text-primary">Project</p>
              <h1 className="mt-2 break-all text-2xl font-semibold">{project.project_id}</h1>
              <dl className="mt-6 grid gap-4 text-sm">
                <Metric label="Duration" value={formatDuration(project.duration)} />
                <Metric label="Resolution" value={`${project.width}x${project.height}`} />
                <Metric label="FPS" value={String(Math.round(project.fps))} />
                <Metric label="Segments" value={String(project.segments.length)} />
                <Metric label="Entities" value={String(project.entities.length)} />
              </dl>
            </aside>
          </div>
        ) : null}
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border pb-3 last:border-b-0 last:pb-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}

function formatDuration(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const minutes = Math.floor(sec / 60);
  const seconds = Math.floor(sec % 60);
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

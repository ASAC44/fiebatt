"use client";

import Link from "next/link";
import Image from "next/image";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Plus } from "lucide-react";

import {
  deleteProject,
  listProjects,
  logout,
  me,
  updateProject,
  type Me,
  type ProjectListItem,
} from "@/lib/api";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { ProjectCard } from "@/components/project-card";

export function ProjectsClient() {
  const router = useRouter();
  const [items, setItems] = useState<ProjectListItem[] | null>(null);
  const [profile, setProfile] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    Promise.all([me(), listProjects()])
      .then(([currentUser, projects]) => {
        if (!currentUser.signed_in) {
          router.replace("/login?next=/projects");
          return;
        }
        if (alive) setProfile(currentUser);
        if (alive) setItems(projects);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      });

    return () => {
      alive = false;
    };
  }, [router, loadAttempt]);

  async function handleLogout() {
    await logout().catch(() => undefined);
    router.replace("/login");
  }

  const displayName = profile?.email?.split("@")[0] || "there";
  const avatarLetter = (profile?.email?.[0] || "V").toUpperCase();

  async function handleDelete(projectId: string) {
    const project = items?.find((item) => item.project_id === projectId);
    if (!window.confirm(`Delete ${project?.name || "this project"}? This cannot be undone.`)) {
      return;
    }
    const previous = items;
    setItems((current) =>
      current ? current.filter((item) => item.project_id !== projectId) : current,
    );
    try {
      await deleteProject(projectId);
    } catch (err) {
      setItems(previous);
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleRename(projectId: string, name: string) {
    try {
      const updated = await updateProject(projectId, name);
      setItems((current) =>
        current?.map((item) =>
          item.project_id === projectId ? { ...item, name: updated.name } : item,
        ) ?? current,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      throw err;
    }
  }

  return (
    <main className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-20 bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4">
          <Link className="flex items-center gap-3 text-2xl font-semibold" href="/">
            <span className="flex size-8 items-center justify-center overflow-hidden rounded-md bg-background">
              <Image
                alt=""
                aria-hidden
                className="size-8"
                height={32}
                priority
                src="/logo.png"
                width={32}
              />
            </span>
            fiebatt
          </Link>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Button asChild className="h-10 px-4 text-sm" variant="ghost">
              <Link href="/settings">Settings</Link>
            </Button>
            <Button className="h-10 px-4 text-sm" onClick={handleLogout} variant="ghost">
              Log out
            </Button>
            <span className="flex size-10 items-center justify-center rounded-full bg-card text-base font-semibold ring-1 ring-border">
              {avatarLetter}
            </span>
          </div>
        </div>
      </header>

      <section className="mx-auto flex max-w-7xl flex-col gap-8 px-5 py-8">
        <div>
          <h1 className="text-4xl font-semibold tracking-normal">
            Welcome back, {displayName}
          </h1>
        </div>

        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="text-sm font-medium text-primary">Projects</p>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">
              Open a reel, continue editing, or upload a new source video.
            </p>
          </div>
          <Button asChild>
            <Link href="/editor">
              <Plus />
              New video
            </Link>
          </Button>
        </div>

        {error ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-5 text-sm text-destructive">
            <p>{error}</p>
            <Button
              className="mt-4"
              onClick={() => {
                setError(null);
                setItems(null);
                setLoadAttempt((value) => value + 1);
              }}
              variant="outline"
            >
              Try again
            </Button>
          </div>
        ) : items === null ? (
          <div className="flex min-h-64 items-center justify-center rounded-lg border border-border bg-card text-sm text-muted-foreground">
            <Loader2 className="mr-2 animate-spin" />
            Loading projects
          </div>
        ) : items.length === 0 ? (
          <div className="flex min-h-64 flex-col items-center justify-center rounded-lg border border-dashed border-border bg-card/40 px-6 text-center">
            <h2 className="text-lg font-medium">No projects yet</h2>
            <p className="mt-2 max-w-md text-sm text-muted-foreground">
              Upload a video to create your first project.
            </p>
            <Button asChild className="mt-5">
              <Link href="/editor"><Plus />New video</Link>
            </Button>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {items.map((project) => (
              <ProjectCard
                href={`/editor?projectId=${project.project_id}`}
                id={project.project_id}
                key={project.project_id}
                durationLabel={formatDuration(project.duration)}
                aspectRatioLabel={formatAspectRatio(project.width, project.height)}
                lastEdited={formatRelative(project.created_at)}
                onDelete={() => handleDelete(project.project_id)}
                onRename={(name) => handleRename(project.project_id, name)}
                title={project.name}
                videoUrl={project.video_url}
              />
            ))}

          </div>
        )}
      </section>
    </main>
  );
}

function formatDuration(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const minutes = Math.floor(sec / 60);
  const seconds = Math.floor(sec % 60);
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function formatAspectRatio(width: number, height: number): string {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return "—";
  }

  const gcd = (a: number, b: number): number => (b === 0 ? a : gcd(b, a % b));
  const divisor = gcd(Math.round(width), Math.round(height));
  const w = Math.round(width) / divisor;
  const h = Math.round(height) / divisor;
  return `${w}:${h}`;
}

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diff = Date.now() - then;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "2-digit",
  });
}

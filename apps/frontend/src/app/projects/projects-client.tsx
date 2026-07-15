"use client";

import Link from "next/link";
import Image from "next/image";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Plus } from "lucide-react";

import {
  deleteProject,
  listProjects,
  me,
  type Me,
  type ProjectListItem,
} from "@/lib/api";
import { clearAuthToken, hasAuthToken } from "@/lib/auth";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { ProjectCard } from "@/components/project-card";

const MOCK_PROJECTS = [
  {
    id: "mock-001",
    title: "court energy",
    meta: "0:18 · draft",
    lastEdited: "2h ago",
    imageUrl: "/mock-project-1.jpg",
  },
  {
    id: "mock-002",
    title: "halftime cut",
    meta: "0:42 · concept",
    lastEdited: "yesterday",
    imageUrl: "/mock-project-2.jpg",
  },
  {
    id: "mock-003",
    title: "final buzzer",
    meta: "1:06 · placeholder",
    lastEdited: "3d ago",
    imageUrl: "/mock-project-3.jpg",
  },
];

export function ProjectsClient() {
  const router = useRouter();
  const [items, setItems] = useState<ProjectListItem[] | null>(null);
  const [profile, setProfile] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    if (!hasAuthToken()) {
      router.replace("/login?next=/projects");
      return () => {
        alive = false;
      };
    }

    Promise.all([me(), listProjects()])
      .then(([currentUser, projects]) => {
        if (alive) setProfile(currentUser);
        if (alive) setItems(projects);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      });

    return () => {
      alive = false;
    };
  }, [router]);

  function handleLogout() {
    clearAuthToken();
    router.replace("/login");
  }

  const displayName = profile?.email?.split("@")[0] || "there";
  const avatarLetter = (profile?.email?.[0] || "V").toUpperCase();

  async function handleDelete(projectId: string) {
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
            <Button className="h-10 px-4 text-sm">Go premium</Button>
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
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
            {error}
          </div>
        ) : null}

        {items === null ? (
          <div className="flex min-h-64 items-center justify-center rounded-lg border border-border bg-card text-sm text-muted-foreground">
            <Loader2 className="mr-2 animate-spin" />
            Loading projects
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {(items.length ? items : []).map((project) => (
              <ProjectCard
                href={`/editor?projectId=${project.project_id}`}
                id={project.project_id}
                key={project.project_id}
                lastEdited={formatRelative(project.created_at)}
                meta={`${formatDuration(project.duration)} · ${project.width}x${project.height}`}
                onDelete={() => handleDelete(project.project_id)}
                title={project.project_id.slice(0, 8)}
                videoUrl={project.video_url}
              />
            ))}

            {MOCK_PROJECTS.map((project) => (
              <ProjectCard
                href="/editor"
                id={project.id}
                imageUrl={project.imageUrl}
                key={project.id}
                lastEdited={project.lastEdited}
                meta={project.meta}
                title={project.title}
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

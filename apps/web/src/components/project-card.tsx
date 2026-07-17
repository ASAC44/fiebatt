"use client";

import Link from "next/link";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, Pencil, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type ProjectCardProps = {
  id: string;
  title: string;
  lastEdited: string;
  href: string;
  durationLabel?: string;
  aspectRatioLabel?: string;
  meta?: string;
  videoUrl?: string;
  imageUrl?: string;
  onDelete?: () => void;
  onRename?: (name: string) => void | Promise<void>;
  placeholderClassName?: string;
};

export function ProjectCard({
  id,
  title,
  lastEdited,
  href,
  durationLabel,
  aspectRatioLabel,
  meta,
  videoUrl,
  imageUrl,
  onDelete,
  onRename,
  placeholderClassName,
}: ProjectCardProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const [saving, setSaving] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  async function saveRename() {
    const name = draft.trim();
    if (!onRename || saving) return;
    if (!name) {
      setDraft(title);
      setRenameError("Name cannot be empty.");
      return;
    }
    if (name === title) {
      setEditing(false);
      setRenameError(null);
      return;
    }
    setSaving(true);
    setRenameError(null);
    try {
      await onRename(name);
      setEditing(false);
    } catch {
      setRenameError("Could not rename project.");
      inputRef.current?.focus();
    } finally {
      setSaving(false);
    }
  }

  function cancelRename() {
    setDraft(title);
    setRenameError(null);
    setEditing(false);
  }

  return (
    <article className="group overflow-hidden rounded-lg border border-border bg-background p-2 transition-colors hover:border-primary/25 hover:bg-muted/45">
      <Link className="block" href={href}>
        <div
          className={cn(
            "aspect-video overflow-hidden rounded-sm border border-border/60 bg-muted",
            placeholderClassName,
          )}
        >
          {videoUrl ? (
            <video
              className="h-full w-full object-cover"
              muted
              playsInline
              preload="metadata"
              src={videoUrl}
            />
          ) : imageUrl ? (
            <Image
              alt=""
              aria-hidden
              className="h-full w-full object-cover"
              height={360}
              src={imageUrl}
              width={640}
            />
          ) : (
            <div className="h-full w-full bg-[linear-gradient(135deg,var(--muted),var(--background))]" />
          )}
        </div>
      </Link>

      <div className="flex items-center justify-between gap-3 p-2 pt-4">
        <div className="min-w-0">
          {editing ? (
            <div className="flex items-center gap-1">
              <input
                ref={inputRef}
                aria-label={`Rename ${title}`}
                className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-sm font-medium outline-none focus:border-primary"
                disabled={saving}
                maxLength={120}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void saveRename();
                  if (event.key === "Escape") cancelRename();
                }}
                value={draft}
              />
              <Button
                aria-label="Save project name"
                disabled={saving}
                onClick={() => void saveRename()}
                size="icon-sm"
                type="button"
                variant="ghost"
              >
                <Check />
              </Button>
              <Button
                aria-label="Cancel rename"
                disabled={saving}
                onClick={cancelRename}
                size="icon-sm"
                type="button"
                variant="ghost"
              >
                <X />
              </Button>
            </div>
          ) : (
            <p className="truncate font-medium">{title}</p>
          )}
          {renameError ? (
            <p className="text-xs text-destructive" role="alert">{renameError}</p>
          ) : null}
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground transition-opacity group-hover:opacity-0">
            {durationLabel ? (
              <span className="inline-flex items-center rounded-md border border-border/70 bg-muted/35 px-2 py-0.5 text-xs">
                {durationLabel}
              </span>
            ) : null}
            {aspectRatioLabel ? (
              <span className="inline-flex items-center rounded-md border border-border/70 bg-muted/35 px-2 py-0.5 text-xs">
                {aspectRatioLabel}
              </span>
            ) : null}
            {!durationLabel && !aspectRatioLabel ? <span>{meta}</span> : null}
          </div>
          <p className="-mt-5 text-sm text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
            Last edited {lastEdited}
          </p>
        </div>
        <div className="flex gap-2">
          {onRename && !editing ? (
            <Button
              aria-label={`Rename ${title}`}
              className="opacity-0 transition-opacity group-hover:opacity-100"
              onClick={() => setEditing(true)}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              <Pencil />
            </Button>
          ) : null}
          <Link
            aria-label={`Open ${title}`}
            className="flex size-8 items-center justify-center text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:text-foreground"
            href={href}
          >
            <ArrowRight className="size-4" />
          </Link>
          {onDelete ? (
            <Button
              aria-label={`Delete ${title}`}
              onClick={onDelete}
              size="icon"
              type="button"
              variant="destructive"
            >
              <Trash2 />
            </Button>
          ) : null}
        </div>
      </div>
      <span className="sr-only">{id}</span>
    </article>
  );
}

import Link from "next/link";
import Image from "next/image";
import { ArrowRight, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type ProjectCardProps = {
  id: string;
  title: string;
  meta: string;
  lastEdited: string;
  href: string;
  videoUrl?: string;
  imageUrl?: string;
  onDelete?: () => void;
  placeholderClassName?: string;
};

export function ProjectCard({
  id,
  title,
  meta,
  lastEdited,
  href,
  videoUrl,
  imageUrl,
  onDelete,
  placeholderClassName,
}: ProjectCardProps) {
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
          <p className="truncate font-medium">{title}</p>
          <p className="text-sm text-muted-foreground transition-opacity group-hover:opacity-0">
            {meta}
          </p>
          <p className="-mt-5 text-sm text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
            Last edited {lastEdited}
          </p>
        </div>
        <div className="flex gap-2">
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

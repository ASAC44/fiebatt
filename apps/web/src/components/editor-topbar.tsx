"use client";

import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowLeft, Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ProjectNameEditor } from "@/components/project-name-editor";
import { EditorGuideButton } from "@/components/editor-guide";

export function EditorTopbar({
  projectId,
  projectName,
  onImport,
  onCompare,
  onShowShortcuts,
  onShowGuide,
  onExport,
  exporting = false,
  exportLabel,
  canExport = true,
  statusSlot,
}: {
  projectId: string | null;
  projectName: string;
  onImport?: () => void;
  onCompare?: () => void;
  onShowShortcuts?: () => void;
  onShowGuide?: () => void;
  onExport?: () => void;
  exporting?: boolean;
  exportLabel?: string;
  canExport?: boolean;
  statusSlot?: ReactNode;
}) {
  return (
    <header className="relative grid h-12 grid-cols-[minmax(0,1fr)_minmax(180px,22rem)_minmax(0,1fr)] items-center gap-3 border-b border-border/80 bg-background px-3 shadow-[inset_0_-1px_0_rgba(255,255,255,0.04)] md:px-4 after:pointer-events-none after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-border/90">
      <div className="flex min-w-0 items-center gap-2 md:gap-3">
        <Link className="flex h-8 shrink-0 items-center gap-2 text-lg font-semibold" href="/projects">
          <Image
            alt=""
            aria-hidden
            className="size-7"
            height={28}
            priority
            src="/logo.png"
            width={28}
          />
          fiebatt
        </Link>
        <div className="hidden h-5 w-px bg-border sm:block" />
        <Button asChild aria-label="Back to projects" size="icon-sm" variant="ghost">
          <Link href="/projects">
            <ArrowLeft />
          </Link>
        </Button>
        <div className="flex min-w-0 items-center gap-1 overflow-x-auto">
          {onImport ? (
            <Button className="h-8 shrink-0 px-2 text-xs md:px-2.5" onClick={onImport} variant="ghost">
              Upload
            </Button>
          ) : null}
          <Button className="h-8 shrink-0 px-2 text-xs md:px-2.5" onClick={onCompare} variant="ghost">
            Compare
          </Button>
          {onShowGuide ? <EditorGuideButton onClick={onShowGuide} /> : null}
          <Button asChild className="hidden h-8 shrink-0 px-2 text-xs md:inline-flex md:px-2.5" variant="ghost">
            <Link href="/settings">Settings</Link>
          </Button>
        </div>
      </div>

      <div className="flex min-w-0 justify-center px-2">
        <ProjectNameEditor initialName={projectName} key={projectId ?? "new"} projectId={projectId} />
      </div>

      <div className="flex min-w-0 items-center justify-end gap-1.5">
        <div className="hidden min-w-0 items-center gap-1.5 lg:flex">
          {statusSlot}
        </div>
        <Button className="hidden h-8 shrink-0 px-3 text-sm md:inline-flex" onClick={onShowShortcuts} variant="outline">
          Shortcuts
        </Button>
        <Button
          className="h-8 shrink-0 px-3 text-sm"
          disabled={!canExport || exporting}
          onClick={onExport}
        >
          {exporting ? exportLabel || "Exporting..." : (
            <>
              <Download />
              {exportLabel || "Export"}
            </>
          )}
        </Button>
      </div>
    </header>
  );
}

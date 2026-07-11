"use client";

import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowLeft, Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ProjectNameEditor } from "@/components/project-name-editor";

export function EditorTopbar({
  projectName,
  mode = "vibe",
  onModeChange,
  onImport,
  onCompare,
  onShowShortcuts,
  onExport,
  exporting = false,
  exportLabel,
  canExport = true,
  statusSlot,
}: {
  projectName: string;
  mode?: "vibe" | "pro";
  onModeChange?: (mode: "vibe" | "pro") => void;
  onImport?: () => void;
  onCompare?: () => void;
  onShowShortcuts?: () => void;
  onExport?: () => void;
  exporting?: boolean;
  exportLabel?: string;
  canExport?: boolean;
  statusSlot?: ReactNode;
}) {
  return (
    <header className="grid h-10 grid-cols-[1fr_auto_1fr] items-center border-b border-border bg-background px-3">
      <div className="flex items-center gap-3">
        <Link className="flex h-8 items-center gap-2 text-lg font-semibold" href="/projects">
          <Image
            alt=""
            aria-hidden
            className="size-7"
            height={28}
            priority
            src="/logo.png"
            width={28}
          />
          feibatt
        </Link>
        <div className="h-5 w-px bg-border" />
        <Button asChild aria-label="Back to projects" size="icon-sm" variant="ghost">
          <Link href="/projects">
            <ArrowLeft />
          </Link>
        </Button>
        <div className="flex items-center gap-1">
          <Button className="h-8 px-2.5 text-xs" onClick={onImport} variant="ghost">
            Files
          </Button>
          <Button className="h-8 px-2.5 text-xs" variant="ghost">
            Edit
          </Button>
          <Button
            className="h-8 px-2.5 text-xs"
            onClick={() => onModeChange?.(mode === "vibe" ? "pro" : "vibe")}
            variant="ghost"
          >
            View
          </Button>
          <Button className="h-8 px-2.5 text-xs" onClick={onShowShortcuts} variant="ghost">
            Help
          </Button>
          <Button className="h-8 px-2.5 text-xs" onClick={onCompare} variant="ghost">
            Compare
          </Button>
          <Button asChild className="h-8 px-2.5 text-xs" variant="ghost">
            <Link href="/settings">Settings</Link>
          </Button>
        </div>
      </div>

      <ProjectNameEditor initialName={projectName} />

      <div className="flex items-center justify-end gap-1.5">
        {statusSlot}
        <Tabs value={mode} onValueChange={(value) => onModeChange?.(value as "vibe" | "pro")}>
          <TabsList className="h-8">
            <TabsTrigger className="px-3 text-sm" value="vibe">
              Vibe
            </TabsTrigger>
            <TabsTrigger className="px-3 text-sm" value="pro">
              Editor
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <Button className="h-8 px-3 text-sm" onClick={onShowShortcuts} variant="outline">
          Shortcuts
        </Button>
        <Button
          className="h-8 px-3 text-sm"
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

"use client";

import { useState } from "react";

import { updateProject } from "@/lib/api";

export function ProjectNameEditor({
  initialName,
  projectId,
}: {
  initialName: string;
  projectId: string | null;
}) {
  const [name, setName] = useState(initialName);
  const [savedName, setSavedName] = useState(initialName);

  async function save() {
    const nextName = name.trim();
    if (!projectId || !nextName || nextName === savedName) {
      if (!nextName) setName(savedName);
      return;
    }
    try {
      const project = await updateProject(projectId, nextName);
      setName(project.name);
      setSavedName(project.name);
    } catch {
      setName(savedName);
    }
  }

  return (
    <input
      aria-label="Project name"
      className="h-8 w-56 rounded-md border border-transparent bg-transparent px-2 text-center text-sm font-medium outline-none transition focus:border-border focus:bg-card"
      disabled={!projectId}
      maxLength={120}
      onChange={(event) => setName(event.target.value)}
      onBlur={() => void save()}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
        if (event.key === "Escape") {
          setName(savedName);
          event.currentTarget.blur();
        }
      }}
      value={name}
    />
  );
}

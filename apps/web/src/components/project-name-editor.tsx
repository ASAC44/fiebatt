"use client";

import { useState } from "react";

export function ProjectNameEditor({ initialName }: { initialName: string }) {
  const [name, setName] = useState(initialName);

  return (
    <input
      aria-label="Project name"
      className="h-8 w-56 rounded-md border border-transparent bg-transparent px-2 text-center text-sm font-medium outline-none transition focus:border-border focus:bg-card"
      onChange={(event) => setName(event.target.value)}
      value={name}
    />
  );
}

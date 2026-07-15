import { EditorClient } from "./editor-client";

export default async function EditorPage({
  searchParams,
}: {
  searchParams: Promise<{ projectId?: string }>;
}) {
  const { projectId } = await searchParams;
  return <EditorClient initialProjectId={projectId} />;
}

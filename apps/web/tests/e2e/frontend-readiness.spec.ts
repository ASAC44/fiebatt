import { expect, test, type Page, type Route } from "@playwright/test";

const project = {
  project_id: "project-123",
  name: "clip",
  video_url: "/test-video.mp4",
  duration: 4,
  fps: 24,
  width: 1280,
  height: 720,
  created_at: "2026-07-17T00:00:00Z",
};

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    body: JSON.stringify(body),
    contentType: "application/json",
    status,
  });
}

async function installApi(page: Page, projects: typeof project[] = []) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (path === "/api/me") {
      return json(route, {
        session_id: "user:1",
        user_id: "1",
        email: "editor@example.com",
        signed_in: true,
      });
    }
    if (path === "/api/projects" && request.method() === "GET") {
      return json(route, projects);
    }
    if (path === "/api/health") return json(route, { ok: true, features: {} });
    if (path === "/api/upload") return json(route, project);
    if (path === `/api/projects/${project.project_id}`) {
      return json(route, { ...project, segments: [], entities: [] });
    }
    if (path === `/api/timeline/${project.project_id}`) {
      return json(route, {
        project_id: project.project_id,
        segments: [{
          start_ts: 0,
          end_ts: 4,
          source: "original",
          url: project.video_url,
          audio: true,
        }],
        edl: null,
      });
    }
    if (path.endsWith("/conversations")) return json(route, []);
    return json(route, {});
  });
}

test("project library shows only saved projects", async ({ page }) => {
  await installApi(page);
  await page.goto("/projects");

  await expect(page.getByRole("heading", { name: "No projects yet" })).toBeVisible();
  await expect(page.getByRole("link", { name: "New video" }).first()).toBeVisible();
  await expect(page.locator("article")).toHaveCount(0);
});

test("settings use automatic platform routing", async ({ page }) => {
  await installApi(page);
  await page.goto("/settings");

  await expect(page.getByText("Generation is configured and routed automatically by Fiebatt.")).toBeVisible();
  await expect(page.getByText("editor@example.com")).toBeVisible();
  await expect(page.getByLabel("Video provider")).toHaveCount(0);
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
});

test("upload opens the authoritative project", async ({ page }) => {
  await installApi(page, [project]);
  await page.goto("/editor");

  await page.locator('input[type="file"]').first().setInputFiles({
    name: "clip.mp4",
    mimeType: "video/mp4",
    buffer: Buffer.from("video"),
  });

  await expect(page).toHaveURL(/\/editor\?projectId=project-123$/);
  await expect(page.getByLabel("Project name")).toHaveValue("clip");
});

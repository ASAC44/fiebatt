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

async function installApi(
  page: Page,
  projects: typeof project[] = [],
  timeline?: unknown,
  jobs?: {
    agentSse: string;
    jobSse?: string;
    current: () => unknown | null;
    onAgentChat?: (body: unknown) => void;
  },
  history?: {
    conversations: Array<Record<string, unknown>>;
    messages: Array<Record<string, unknown>>;
  },
) {
  await page.addInitScript(() => {
    window.localStorage.setItem("fiebatt.editor.guide.seen", "1");
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (path === "/api/agent/chat" && jobs) {
      jobs.onAgentChat?.(request.postDataJSON());
      return route.fulfill({
        body: jobs.agentSse,
        contentType: "text/event-stream",
        status: 200,
      });
    }
    if (path === "/api/jobs/job-navigation/stream" && jobs) {
      return route.fulfill({
        body: jobs.jobSse ?? "",
        contentType: "text/event-stream",
        status: 200,
      });
    }
    if (path === "/api/jobs/job-navigation" && jobs) {
      return json(route, jobs.current());
    }
    if (path === `/api/projects/${project.project_id}/generation-jobs` && jobs) {
      const current = jobs.current();
      return json(route, current ? [current] : []);
    }

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
      if (request.method() === "PATCH") {
        const body = request.postDataJSON() as { name: string };
        return json(route, { ...project, name: body.name });
      }
      return json(route, { ...project, segments: [], entities: [] });
    }
    if (path === `/api/timeline/${project.project_id}`) {
      return json(route, timeline ? { revision: 0, ...(timeline as object) } : {
        project_id: project.project_id,
        duration: project.duration,
        revision: 0,
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
    if (path === "/api/conversations/conversation-history/messages") {
      return json(route, history?.messages ?? []);
    }
    if (path.endsWith("/conversations")) return json(route, history?.conversations ?? []);
    return json(route, {});
  });
}

test("saved edit conversation is restored after reopening", async ({ page }) => {
  const createdAt = "2026-07-22T21:36:59Z";
  await installApi(page, [project], undefined, undefined, {
    conversations: [{
      id: "conversation-history",
      project_id: project.project_id,
      title: null,
      created_at: createdAt,
      updated_at: createdAt,
      message_count: 2,
    }],
    messages: [
      {
        id: "history-user",
        conversation_id: "conversation-history",
        role: "user",
        content: { text: "make this man jump once" },
        created_at: createdAt,
      },
      {
        id: "history-agent",
        conversation_id: "conversation-history",
        role: "agent",
        content: {
          tool_calls: [
            { id: "plan-1", tool: "create_edit_plan", args: {}, status: "done" },
            { id: "generate-1", tool: "generate_edit", args: {}, status: "done" },
          ],
          prompt_plan: {
            job_id: "job-history",
            user_prompt: "make this man jump once",
            vendor: "wan",
            plan: {
              description: "The selected man jumps once.",
              intent: "transform",
              conditioning_strategy: "source_video",
              tone: "original",
              color_grading: "original",
              region_emphasis: "selected man",
              prompt: "Continue walking, jump once, land, and resume walking.",
              prompt_for_veo: null,
            },
          },
        },
        created_at: createdAt,
      },
    ],
  });

  await page.goto(`/editor?projectId=${project.project_id}`);

  await expect(page.getByText("make this man jump once", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("planning edit window", { exact: true })).toBeVisible();
  await expect(page.getByText("starting video render", { exact: true })).toBeVisible();
  await expect(page.getByText(/Continue walking, jump once/)).toBeVisible();
});

test("project library shows only saved projects", async ({ page }) => {
  await installApi(page);
  await page.goto("/projects");

  await expect(page.getByRole("heading", { name: "No projects yet" })).toBeVisible();
  await expect(page.getByRole("link", { name: "New video" }).first()).toBeVisible();
  await expect(page.locator("article")).toHaveCount(0);
});

test("project can be renamed from the library", async ({ page }) => {
  await installApi(page, [project]);
  await page.goto("/projects");

  await page.getByRole("button", { name: "Rename clip" }).click();
  await page.getByLabel("Rename clip").fill("Launch cut");
  await page.getByRole("button", { name: "Save project name" }).click();

  await expect(page.getByText("Launch cut")).toBeVisible();
  await expect(page.getByRole("button", { name: "Rename Launch cut" })).toBeVisible();
});

test("project can be renamed from the editor", async ({ page }) => {
  await installApi(page, [project]);
  await page.goto(`/editor?projectId=${project.project_id}`);

  const name = page.getByLabel("Project name");
  await name.fill("Final reel");
  await name.press("Enter");

  await expect(name).toHaveValue("Final reel");
  await expect(name).toHaveAttribute("aria-invalid", "false");
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
  await expect(page.getByPlaceholder("describe an edit...")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Vibe" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Editor" })).toHaveCount(0);
});

test("login rejects recursive return paths", async ({ page }) => {
  await installApi(page);
  await page.goto("/login?next=%2Flogin%3Fnext%3D%252Fprojects");

  await page.getByLabel("Email").fill("editor@example.com");
  await page.getByLabel("Password").fill("password123");
  await page.getByRole("button", { name: "Log in" }).click();

  await expect(page).toHaveURL(/\/projects$/);
});

test("compare opens original and complete edited timeline side by side", async ({ page }) => {
  await installApi(page, [project], {
    project_id: project.project_id,
    duration: 4,
    segments: [],
    edl: {
      updated_at: 1,
      sources: [
        {
          id: "source-1",
          kind: "source",
          url: "/test-video.mp4",
          duration: 4,
          fps: 24,
          project_id: project.project_id,
          label: "clip",
        },
        {
          id: "generated-1",
          kind: "generated",
          url: "/generated.mp4",
          duration: 2,
          fps: 24,
          project_id: project.project_id,
          label: "applied edit",
        },
      ],
      clips: [
        {
          id: "clip-original",
          kind: "source",
          url: "/test-video.mp4",
          source_start: 0,
          source_end: 2,
          media_duration: 4,
          volume: 1,
          label: "clip",
          project_id: project.project_id,
          source_asset_id: "source-1",
        },
        {
          id: "clip-generated",
          kind: "generated",
          url: "/generated.mp4",
          source_start: 0,
          source_end: 2,
          media_duration: 2,
          volume: 1,
          label: "applied edit",
          project_id: project.project_id,
          source_asset_id: "generated-1",
        },
      ],
    },
  });
  await page.goto(`/editor?projectId=${project.project_id}`);
  await expect(page.getByLabel("Project name")).toHaveValue("clip");

  await page.getByRole("button", { name: "Compare" }).click();

  await expect(page.getByLabel("Original video")).toBeVisible();
  await expect(page.getByLabel("Edited timeline preview")).toBeVisible();
  await expect(page.getByRole("button", { name: "Single view" })).toBeVisible();

  await page.getByRole("button", { name: "Single view" }).click();
  const originalPane = page.getByTestId("compare-pane-original");
  const editedPane = page.getByTestId("compare-pane-edited");
  await expect(originalPane).toHaveCSS("display", "block");
  await expect(editedPane).toHaveCSS("display", "block");
  await expect(originalPane).toHaveAttribute("data-active", "true");
  await expect(editedPane).toHaveAttribute("data-active", "false");

  await page.getByRole("button", { name: "Show edited" }).click();
  await expect(originalPane).toHaveCSS("display", "block");
  await expect(editedPane).toHaveCSS("display", "block");
  await expect(originalPane).toHaveAttribute("data-active", "false");
  await expect(editedPane).toHaveAttribute("data-active", "true");

  await page.getByLabel("Compare playhead").fill("3");
  await expect(page.getByLabel("Edited timeline preview")).toHaveAttribute(
    "src",
    "/generated.mp4",
  );
});

test("portrait freeze frames stay contained in preview and compare", async ({ page }) => {
  await installApi(page, [{ ...project, width: 720, height: 1280 }]);
  await page.goto(`/editor?projectId=${project.project_id}`);

  const previewFreeze = page.getByTestId("preview-freeze-frame");
  await previewFreeze.evaluate((canvas: HTMLCanvasElement) => {
    canvas.width = 720;
    canvas.height = 1280;
    canvas.style.opacity = "1";
  });
  const previewBox = await previewFreeze.boundingBox();
  expect(previewBox).not.toBeNull();
  expect(previewBox!.width / previewBox!.height).toBeCloseTo(720 / 1280, 2);

  await page.getByRole("button", { name: "Compare" }).click();
  const compareFreeze = page.getByTestId("compare-freeze-frame");
  await compareFreeze.evaluate((canvas: HTMLCanvasElement) => {
    canvas.width = 720;
    canvas.height = 1280;
    canvas.style.opacity = "1";
  });
  const compareBox = await compareFreeze.boundingBox();
  expect(compareBox).not.toBeNull();
  expect(compareBox!.width / compareBox!.height).toBeCloseTo(720 / 1280, 2);
});

test("agent receives active clip media time after a trim", async ({ page }) => {
  let requestBody: Record<string, unknown> | null = null;
  await installApi(page, [project], {
    project_id: project.project_id,
    duration: 5,
    segments: [],
    edl: {
      updated_at: 1,
      sources: [],
      clips: [{
        id: "trimmed-source",
        kind: "source",
        url: "/test-video.mp4",
        source_start: 8,
        source_end: 13,
        media_duration: 20,
        volume: 1,
        project_id: project.project_id,
      }],
    },
  }, {
    agentSse: "event: done\ndata: {}\n\n",
    current: () => null,
    onAgentChat: (body) => { requestBody = body as Record<string, unknown>; },
  });
  await page.goto(`/editor?projectId=${project.project_id}`);

  await page.getByPlaceholder("describe an edit...").fill("make this car green");
  await page.getByRole("button", { name: "send message" }).click();

  await expect.poll(() => requestBody).not.toBeNull();
  const capturedBody = requestBody as Record<string, unknown> | null;
  expect(capturedBody?.playhead_ts).toBe(0);
  expect(capturedBody?.source_frame_ts).toBe(8);
  expect(capturedBody?.target_clip_id).toBe("trimmed-source");
});

test("generation preview returns after leaving and reopening editor", async ({ page }) => {
  let started = false;
  let finished = false;
  const currentJob = () => started ? ({
    job_id: "job-navigation",
    kind: "generate",
    status: finished ? "done" : "processing",
    error: null,
    created_at: new Date().toISOString(),
    accepted: false,
    start_ts: 0,
    end_ts: 3,
    variants: finished
      ? [{
          id: "variant-navigation",
          index: 0,
          status: "done",
          url: "/generated-navigation.mp4",
          description: "finished while away",
          visual_coherence: 8,
          prompt_adherence: 10,
          preservation_score: 9,
          transition_review: {
            entry_continuity: 4,
            exit_continuity: 9,
            evidence: ["entry pose jumps"],
          },
          quality_state: "pass",
          quality_evidence: [],
          continuity_validation: {
            passed: true,
            metrics: {},
            issues: [],
            sampled_frames: 12,
          },
          selected_seams: null,
          error: null,
        }]
      : [],
  }) : null;
  const agentSse = [
    "event: suggestion",
    `data: ${JSON.stringify({ edit: {
      job_id: "job-navigation",
      start_ts: 0,
      end_ts: 3,
      suggestion: "make the car green",
    } })}`,
    "",
    "event: done",
    "data: {}",
    "",
    "",
  ].join("\n");

  await installApi(page, [project], undefined, {
    agentSse,
    current: currentJob,
    onAgentChat: () => { started = true; },
  });
  await page.goto(`/editor?projectId=${project.project_id}`);
  await page.getByPlaceholder("describe an edit...").fill("make the car green");
  await page.getByRole("button", { name: "send message" }).click();
  await expect(page.getByText(/rendering|queued/).first()).toBeVisible();
  await expect.poll(() => page.evaluate(() =>
    localStorage.getItem("fiebatt.pending-agent-turn.project-123"),
  )).not.toBeNull();

  await page.goto("/projects");
  finished = true;
  await page.goto(`/editor?projectId=${project.project_id}`);

  await expect(page.getByText("finished while away")).toBeVisible();
  await expect(page.getByText(
    "Prompt match 10/10 · Visual quality 8/10 · Preservation 9/10 · Entry 4/10 · Exit 9/10",
  )).toBeVisible();
  await expect(page.getByRole("button", { name: "apply" })).toBeVisible();
});

test("failed render restores as a safe status instead of a raw red error", async ({ page }) => {
  const currentJob = () => ({
    job_id: "job-navigation",
    kind: "generate",
    status: "error",
    error: "The video model returned an incomplete clip, so it was not added to your timeline.",
    failure_state: {
      code: "invalid_provider_duration",
      user_message: "The video model returned an incomplete clip, so it was not added to your timeline.",
      retryable: true,
    },
    progress_state: {
      stage: "failed",
      message: "The video model returned an incomplete clip, so it was not added to your timeline.",
      status: "failed",
      updated_at: Date.now() / 1000,
    },
    created_at: new Date().toISOString(),
    accepted: false,
    start_ts: 0,
    end_ts: 4,
    variants: [],
  });

  await installApi(page, [project], undefined, {
    agentSse: "event: done\ndata: {}\n\n",
    current: currentJob,
  });
  await page.goto(`/editor?projectId=${project.project_id}`);

  await expect(page.getByText("render stopped safely")).toBeVisible();
  await expect(page.getByText(/not added to your timeline/i)).toBeVisible();
  await expect(page.getByText(/3\.88s|4\.04s|ffmpeg|exception/i)).toHaveCount(0);
  await expect(page.locator(".msg--error")).toHaveCount(0);
});

test("corrective retry keeps one stable corrected-pass status", async ({ page }) => {
  const currentJob = () => ({
    job_id: "job-navigation",
    kind: "generate",
    status: "processing",
    error: null,
    created_at: new Date().toISOString(),
    accepted: false,
    start_ts: 0,
    end_ts: 5,
    retry_state: {
      status: "dispatched",
      retry_at: Date.now() / 1000,
      evidence: ["target text was incomplete"],
    },
    variants: [{
      id: "first-pass",
      index: 0,
      status: "done",
      url: "/first-pass.mp4",
      description: "first pass",
      quality_state: "review_warning",
      quality_evidence: ["target text was incomplete"],
      error: null,
    }],
  });
  const jobSse = [
    `data: ${JSON.stringify({ stage: "gen_retry", msg: "starting corrected pass", ts: 1 })}`,
    "",
    `data: ${JSON.stringify({ stage: "gen_poll", msg: "Wan still rendering", ts: 2 })}`,
    "",
    `data: ${JSON.stringify({ stage: "score_start", msg: "reviewing result", ts: 3 })}`,
    "",
    // A delayed poll event must not move the UI backwards to rendering.
    `data: ${JSON.stringify({ stage: "gen_poll", msg: "stale rendering heartbeat", ts: 4 })}`,
    "",
    "",
  ].join("\n");

  await installApi(page, [project], undefined, {
    agentSse: "event: done\ndata: {}\n\n",
    jobSse,
    current: currentJob,
  });
  await page.goto(`/editor?projectId=${project.project_id}`);

  await expect(page.getByText("reviewing the corrected pass…")).toBeVisible();
  await expect(page.getByText(/reviewing first pass/i)).toHaveCount(0);
});

test("retry keeps the first preview mounted and compare uses the chosen pass", async ({ page }) => {
  let reads = 0;
  const currentJob = () => {
    reads += 1;
    const correctedReady = reads >= 3;
    return {
      job_id: "job-navigation",
      kind: "generate",
      status: correctedReady ? "done" : "processing",
      error: null,
      created_at: new Date().toISOString(),
      accepted: false,
      start_ts: 0,
      end_ts: 3,
      recommended_variant_id: correctedReady ? "corrected-pass" : null,
      selected_seams: correctedReady ? {
        passed: true,
        media_start: 0,
        media_end: 3,
        timeline_start: 0,
        timeline_end: 3,
      } : null,
      variants: [
        {
          id: "first-pass",
          index: 0,
          status: "done",
          url: `/first-pass.mp4?X-Amz-Signature=first-${reads}`,
          description: "first pass",
          quality_state: "review_warning",
          quality_evidence: ["action incomplete"],
          error: null,
        },
        ...(correctedReady ? [{
          id: "corrected-pass",
          index: 1,
          status: "done",
          url: `/corrected-pass.mp4?X-Amz-Signature=corrected-${reads}`,
          description: "corrected pass",
          quality_state: "pass",
          quality_evidence: [],
          error: null,
        }] : []),
      ],
    };
  };

  await installApi(page, [project], undefined, {
    agentSse: "event: done\ndata: {}\n\n",
    current: currentJob,
  });
  await page.goto(`/editor?projectId=${project.project_id}`);

  const firstVideo = page.locator('video[src^="/first-pass.mp4?X-Amz-Signature="]');
  await expect(firstVideo).toHaveCount(1);
  await firstVideo.evaluate((element) => element.setAttribute("data-stable-player", "yes"));

  await expect(
    page.locator(".variant-preview__attempt").getByText("Corrected pass", { exact: true }),
  ).toBeVisible({ timeout: 10_000 });
  await expect(firstVideo).toHaveAttribute("data-stable-player", "yes");

  await page.getByRole("button", { name: "Compare" }).click();
  await expect(page.getByLabel("Edited timeline preview")).toHaveAttribute(
    "src",
    /^\/corrected-pass\.mp4\?X-Amz-Signature=/,
  );
});

test("prompt card separates user request from refined prompt", async ({ page }) => {
  const agentSse = [
    "event: prompt_plan_started",
    `data: ${JSON.stringify({
      job_id: "job-prompt",
      user_prompt: "A polished emerald vehicle with exact color consistency.",
    })}`,
    "",
    "event: prompt_plan",
    `data: ${JSON.stringify({
      job_id: "job-prompt",
      plan: {
        prompt: "A polished emerald vehicle with exact color consistency.",
        intent: "appearance change",
      },
    })}`,
    "",
    "event: done",
    "data: {}",
    "",
    "",
  ].join("\n");
  await installApi(page, [project], undefined, {
    agentSse,
    current: () => ({}),
  });
  await page.goto(`/editor?projectId=${project.project_id}`);
  await page.getByPlaceholder("describe an edit...").fill("make the car green");
  await page.getByRole("button", { name: "send message" }).click();

  await expect(page.locator(".prompt-plan__user")).toHaveText("make the car green");
  await expect(page.locator(".prompt-plan__content")).toHaveText(
    "A polished emerald vehicle with exact color consistency.",
  );
  await expect(page.locator(".prompt-plan__lane-k")).toHaveText(["you", "refined"]);
});

test("agent reply never shows raw tool markup", async ({ page }) => {
  const leaked = `Working on it.
<｜DSML｜function_calls>
<｜DSML｜invoke name="analyze_video">
<｜DSML｜parameter name="project_id" string="true">project-123</｜DSML｜parameter>`;
  const agentSse = [
    "event: token",
    `data: ${JSON.stringify({ text: leaked })}`,
    "",
    "event: done",
    "data: {}",
    "",
    "",
  ].join("\n");
  await installApi(page, [project], undefined, {
    agentSse,
    current: () => ({}),
  });
  await page.goto(`/editor?projectId=${project.project_id}`);
  await page.getByPlaceholder("describe an edit...").fill("inspect this video");
  await page.getByRole("button", { name: "send message" }).click();

  await expect(page.getByText("Working on it.")).toBeVisible();
  await expect(page.getByText(/DSML|function_calls|functioncall/i)).toHaveCount(0);
});

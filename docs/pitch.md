# fiebatt pitch

## Run of show

1. Landing hero: introduce the problem and product.
2. Landing platform/CLI section: explain why this is more than a prompt UI.
3. Editor: send the live prompt.
4. Landing technical diagram: explain the full pipeline while processing runs.
5. Editor chat: show the generated response and variant.
6. Compare view: switch original, edited, and side-by-side.
7. Editor timeline/export: accept the result and close.

## Script

[Screen: landing page hero. Keep the fiebatt headline and main navigation visible.]

Hey everyone, this is **fiebatt**.

The easiest way to explain it is this:

Most AI video tools are like, “Give me a prompt, I’ll make you a new video.”

That’s cool, but it breaks down fast when you already have footage and you only
want to change one specific thing.

fiebatt is built for that moment.

It’s a **surgical AI video editor**.

Not “generate a random new clip.”
Not “replace my whole scene.”
But: change this person, at this moment, in this region, inside this real
timeline.

So you can take an existing reel and say:

> Make the man jump three times, then keep walking normally.

And fiebatt treats that as an edit, not a brand new video.

[Screen: landing page, scroll just enough to hint at the platform/CLI section.]

Now the fun part: fiebatt is not just a UI. It’s a platform.

The same editing system can be used from the web app, from the CLI, or by agents
like Claude and Codex. If an AI agent can run terminal commands, it can use
fiebatt. It can inspect the project, understand the timeline, request edits,
review variants, and export the result.

So this is not just AI video generation. This is agent-native video editing.

Under the hood, we’re combining the prompt with the actual editor state:
playhead, timeline, selected region, source clip, and conversation history.

Then we use vision and localization. SAM-style masking helps lock onto the
subject, so the edit stays focused instead of messing with the whole frame.

[Screen: open Projects, then open the editor project. Keep the video preview and
chat input visible.]

Let me show it.

I’m going into the editor, and I’ll type:

> Make the man jump three times, then he walks normally.

I hit send, and now fiebatt starts working.

[Screen: editor chat. Send the prompt. As soon as the working states appear,
switch back to the landing page.]

While that runs, let’s jump back to the landing page and look at what’s actually
happening.

[Screen: landing page technical diagram. Fullscreen it if the room needs larger
text.]

This diagram is the full pipeline, and this is where the system gets really
interesting.

The user can come in from two places: the web editor or the terminal. The web
editor has the visual workflow, but the CLI exposes the same backend, so an
agent like Claude or Codex can drive the product from shell commands. That means
the product is not locked inside one UI. It is a real editing platform.

[Screen: diagram input/context lane.]

When I send a prompt, fiebatt does not just forward that prompt to a video
model. First it builds context.

It knows the active project ID, the source video, the current playhead, the
selected region, the timeline duration, the EDL state, the current clip window,
the conversation history, and the last accepted edits. So when I say “make the
man jump,” the system understands which man, which moment, and where that edit
belongs in the timeline.

[Screen: diagram agent planning and tool-calling blocks.]

Then the agent layer starts.

The chat request goes into the backend as a streaming SSE loop. The agent can
call tools like `get_timeline`, `preview_frame`, `preview_strip`,
`identify_region`, `generate_edit`, `wait_for_job`, `score_variant`,
`accept_variant`, `propagate`, and `export_video`.

Each tool has a specific job.

`get_timeline` tells the agent what clips are currently on the timeline.
`preview_frame` grabs the exact frame under the playhead, so the agent can see
the visual moment the user is talking about. `preview_strip` samples a short
range of frames, so the agent understands motion before and after the current
frame instead of making a decision from one still image.

`identify_region` takes the selected box and asks: what is inside this region?
Is it a person, a shoe, a ball, a face, a car, a background object? That gives
the edit a concrete target.

`generate_edit` starts the actual edit job. `wait_for_job` keeps watching until
the render finishes. `score_variant` checks whether the output actually follows
the prompt and still looks coherent. `accept_variant` writes the chosen result
back into the timeline. `propagate` searches for the same subject elsewhere in
the reel. `export_video` renders the final MP4.

So instead of one giant black-box model call, this is a tool-using editing
agent. It can inspect, decide, call a tool, observe the result, and continue.

[Screen: diagram model routing block. Optionally open Settings briefly to show
provider choices.]

For model routing, we support normal provider paths and Mesh API.

Mesh API is used like a model gateway. In our flow, that means the agent can
send the project-aware prompt to a model such as `deepseek/deepseek-v3.2` and
use it for reasoning: break the request into steps, decide which tools to call,
rewrite a messy human prompt into a cleaner edit brief, and keep the answer in a
structured format the backend can execute.

For video generation, the settings page can switch between Wan, HappyHorse,
Veo, and Mesh API Veo. Those are the engines that can actually create the edited
video variant. fiebatt wraps them behind one adapter shape, so the rest of the
editor does not care which provider made the clip.

That means we can flip the generation backend without rebuilding the editor.

[Screen: diagram edit brief block.]

Now after the prompt enters the system, we rewrite it into a structured edit
brief.

That brief is not just “make him jump.”

It becomes:

- what action should change
- which subject should change
- what time range the edit belongs to
- whether the edit should use the whole frame or only a selected region
- what should stay untouched
- how the movement should start and end
- what style or color should be preserved

So the generation model receives a much more careful instruction than the raw
prompt.

[Screen: diagram vision/localization lane. Point at frame inspector, CLIP, and
SAM-style masking.]

Then the vision stack kicks in.

First, we sample frames from the video. That gives the system a short visual
memory of what is happening over time: where the subject enters, how they move,
what the camera is doing, and what the background looks like.

Then we inspect the current frame. If the user is parked at second five, that
frame becomes the anchor for the edit.

If the user draws a box, we crop that box and analyze only that area. This is
how fiebatt figures out that the user is pointing at the walking person, not the
building behind them or the entire scene.

CLIP-style retrieval is used for matching language to visuals. In simple terms:
we turn the user phrase, like “the man walking” or “the ball,” into a search
query over frames and crops. That helps us find the frames where the requested
thing appears, even if the user did not manually mark every occurrence.

SAM-style masking is the next step. A bounding box is rough. It includes extra
background. SAM turns that rough box into a cleaner object mask, closer to the
actual outline of the subject. So instead of editing a rectangle, we can lock
onto the person or object inside the rectangle.

That part matters a lot.

Without localization, AI video edits tend to drift. They change the background,
the camera, the clothes, the whole scene. With region locking, we can say:
focus on this subject, preserve the plaza, preserve the buildings, preserve the
lighting, preserve the camera, and only change the motion we asked for.

[Screen: diagram generation job and provider blocks.]

Then we create a generation job.

The backend slices the right source window with ffmpeg. If the edit is from
second three to second eight, we extract that exact clip range instead of
sending the full video around blindly.

Then we grab a reference frame from the edit window. That frame gives the video
model visual grounding: what the person looks like, what the environment looks
like, and what the camera framing should stay close to.

We store the clip and frame through the storage layer, create a job row in the
database, create variant rows, and start the worker. The worker emits progress
events the whole time, so the UI can show what is happening instead of freezing.

The generation provider then creates variants.

Wan is useful when we want source-video-style editing where the original clip
helps preserve temporal context. HappyHorse is a fallback path that can create
image-conditioned variants from a reference frame. Veo is a high-quality video
generation route. Mesh API Veo is the same idea routed through Mesh, so the
video model can be swapped from settings without rewriting the product.

Each provider has different response shapes, task IDs, polling behavior, and
output URLs. fiebatt normalizes all of that into one internal shape: job,
variant, status, URL, description, score, and error.

[Screen: diagram scoring/review blocks.]

After generation, we score.

The scoring layer checks a few practical things:

Did the output actually follow the prompt?
Does the subject still look like the same subject?
Did the background stay stable?
Does the motion look temporally coherent frame to frame?
Does the edit still match the source lighting and framing?

Bad outputs can be rejected or retried. Good outputs become variant previews.

[Screen: return to editor chat and show the completed variant preview.]

Then we come back to the user.

And this is the important product decision: fiebatt does not auto-commit the AI
result. It shows variants. The human compares. The human accepts.

When a variant is accepted, the backend writes a generated segment into the
timeline. That generated segment has a start time, end time, source URL, order
index, and link back to the variant that created it.

So the result is not just a loose file in a downloads folder. It becomes part of
the edit decision list. The editor refreshes, the timeline updates, and future
exports use that accepted segment.

[Screen: editor timeline. If the variant is ready, click Apply/Accept so the
timeline refresh is visible.]

From there, we can do continuity propagation. If this subject appears again
later, the system can search for matching appearances and prepare follow-up
edits. That search uses the subject identity and keyframes from the project, so
we can find similar appearances without asking the user to manually hunt through
the whole video.

So one localized change can become part of a coherent authored sequence, not
just a one-off trick.

Finally, export takes the current timeline and renders it into a final MP4. It
pulls together original source clips and generated clips, keeps timing aligned,
normalizes fps, handles audio behavior, stitches everything together, and gives
back an output URL.

[Screen: landing technical diagram zoomed out.]

So the diagram is basically the whole idea:

prompt in,
context attached,
agent tools,
Mesh or Qwen-style planning,
CLIP retrieval,
SAM localization,
generation providers,
scoring,
variant review,
compare,
accept,
timeline update,
continuity,
export.

That is why we call it surgical video editing. It is not just a model. It is the
editing system around the model.

[Screen: editor page. Show the finished chat state.]

Now let’s go back to the editor.

The result is ready. I open Compare.

[Screen: compare view. First show original, then edited, then side-by-side.]

Now we can switch between the original and the edited version, or view them side
by side. That makes it easy to demo, easy to review, and easy to decide whether
the edit actually worked.

[Screen: editor timeline/export controls.]

Once we like it, we accept it, and fiebatt updates the timeline. From there, it
can be exported like a normal edited reel.

So the big idea is:

**fiebatt turns AI video generation into a real editing workflow.**

Precise edits.
Real timelines.
SAM-based localization.
Agent access through the terminal.
Compare before commit.
Export when it’s ready.

That’s fiebatt.

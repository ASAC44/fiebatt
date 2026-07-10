# Fix: duration mismatch + over-generation

## Bug 1: Agent picks 3s window instead of full clip

**File**: `backend/app/api/routes/agent.py`

**Current (lines 123-125)**:
```
timestamps that you can derive from the playhead, duration, or their
natural-language request (e.g. "the first 3 seconds" = start_ts 0.0,
end_ts 3.0). Only ask for clarification if the request is genuinely
```

**Replace with**:
```
timestamps that you can derive from the playhead, duration, or their
natural-language request. Default start_ts to 0.0 and end_ts to
timeline_duration (the full project length). Only use a shorter window
when the user explicitly specifies a time range. Only ask for
clarification if the request is genuinely
```

## Bug 2: HappyHorse over-generates on subtle edits

**File**: `ai/prompts/edit_plan.txt`

**Current (lines 73-75)**:
```
- If conditioning_strategy is "first_frame", you can reference the
  subject directly ("the same woman", "the same car") — Veo will see
  the opening frame and preserve it.
```

**Replace with**:
```
- If conditioning_strategy is "first_frame" AND the intent is a subtle
  transform (color grade, texture change, eye color, etc.), the
  prompt_for_veo MUST explicitly preserve the original composition.
  Use language like: "Exact same framing, same camera position, same
  subject position and pose as the reference frame. Only <change>."
  Do NOT re-describe the scene generically — that causes the model to
  change camera angle, subject position, and composition.
```

**Current (line 76-77)**:
```
- Keep prompt_for_veo self-contained: one paragraph, 40-80 words,
  concrete visual nouns and adjectives, no instructions to the model.
```

**Add after**:
```
- For "transform" intents with first_frame conditioning: describe ONLY
  the change being made, not the full scene. Over-describing the scene
  causes the model to regenerate everything, changing framing and
  composition. A short focused prompt like "Exact same framing and
  subject. Only change: the woman's eyes are now vivid bright blue
  instead of brown." produces more predictable results.
```

## Verification

1. Backend is running with `--reload` — changes will be picked up automatically
2. Open the editor, upload an 8-second clip, type "edit her eyes to black"
3. Agent should generate a full 8-second edit, and the resulting video should preserve the original composition with only eye color changed

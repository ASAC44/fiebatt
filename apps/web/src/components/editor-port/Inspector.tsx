import { type ReactNode } from "react";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldSet,
} from "@/components/ui/field";
import { Kbd } from "@/components/ui/kbd";
import { Slider } from "@/components/ui/slider";
import { useEDL } from "@/stores/edl";

export function Inspector() {
  return (
    <div className="h-full min-h-0 overflow-auto p-4">
      <BasicTab />
    </div>
  );
}

// ─── Basic tab (volume etc.) ─────────────────────────────────────────

function BasicTab() {
  const { state, dispatch } = useEDL();
  const selected = state.clips.find((c) => c.id === state.selectedId) ?? null;
  if (!selected) return <Hint>Nothing selected.</Hint>;

  return (
    <FieldSet className="gap-6">
      <FieldGroup className="gap-6">
        <Field className="gap-3">
          <FieldContent>
            <FieldLabel>Volume</FieldLabel>
            <FieldDescription>Per-clip gain, 0 to 100.</FieldDescription>
          </FieldContent>
          <div className="flex items-center gap-3 py-2">
            <Slider
              aria-label="Clip volume"
              className="min-w-0 flex-1"
              min={0}
              max={1}
              step={0.01}
              value={[selected.volume]}
              onValueChange={([value]) =>
                dispatch({ type: "set_volume", id: selected.id, v: value ?? selected.volume })
              }
            />
            <span className="w-9 shrink-0 text-right font-mono text-xs text-muted-foreground tabular-nums">
              {Math.round(selected.volume * 100)}
            </span>
          </div>
        </Field>

        <Field className="gap-3">
          <FieldContent>
            <FieldLabel>Shortcuts</FieldLabel>
            <FieldDescription>Common timeline controls.</FieldDescription>
          </FieldContent>
          <div className="divide-y divide-border/50 border-y border-border/50">
            <Shortcut keys={["Space"]} label="Play / pause" />
            <Shortcut keys={["S"]} label="Split at playhead" />
            <Shortcut keys={["Delete"]} label="Delete selected" />
            <Shortcut keys={["Drag edge"]} label="Trim clip" />
            <Shortcut keys={["Click clip"]} label="Select" />
          </div>
        </Field>
      </FieldGroup>
    </FieldSet>
  );
}

function Shortcut({ keys, label }: { keys: string[]; label: string }) {
  return (
    <div className="flex min-h-9 items-center justify-between gap-3 py-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {keys.map((key) => (
          <Kbd key={key} className="h-6 min-w-6 rounded-md border border-border/70 bg-muted/40 px-2 text-[11px] text-foreground">
            {key}
          </Kbd>
        ))}
      </div>
      <span className="text-right text-xs text-muted-foreground">{label}</span>
    </div>
  );
}

// ─── helpers ────────────────────────────────────────────────────────

function Hint({ children }: { children: ReactNode }) {
  return (
    <p className="flex h-full min-h-32 items-center justify-center text-center text-sm text-muted-foreground">
      {children}
    </p>
  );
}

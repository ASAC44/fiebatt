import { useRef, useState, type ReactNode } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useEDL, type MediaAsset } from "@/stores/edl";
import { Icon } from "./Icon";
import "./library.css";

/**
 * Left sidebar — tabbed header (Media / Audio / Effects / Adjust), body
 * is tab content. Only "Media" is active for now; the other tabs exist to
 * establish the layout (and act as hints for what the studio will grow).
 *
 * The Media tab now mirrors CapCut: uploads land in the library and sit
 * there. Tap the plus button on a tile to drop it onto the timeline.
 */
type Tab = "media" | "audio" | "effects" | "adjust";

export function Library({
  onUpload,
  uploading,
}: {
  onUpload: (f: File) => void;
  uploading: boolean;
}) {
  const [tab, setTab] = useState<Tab>("media");

  return (
    <div className="lib">
      <div className="lib__tabs">
        <div className="flex items-center justify-center gap-1">
          <LibraryTab active={tab === "media"} onClick={() => setTab("media")}>Media</LibraryTab>
          <LibraryTab active={tab === "audio"} onClick={() => setTab("audio")}>Audio</LibraryTab>
          <LibraryTab active={tab === "effects"} onClick={() => setTab("effects")}>Effects</LibraryTab>
          <LibraryTab active={tab === "adjust"} onClick={() => setTab("adjust")}>Adjust</LibraryTab>
        </div>
      </div>

      <div className="lib__body">
        {tab === "media" ? (
          <MediaTab onUpload={onUpload} uploading={uploading} />
        ) : (
          <ComingSoon tab={tab} />
        )}
      </div>
    </div>
  );
}

function LibraryTab({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <Button
      className={active ? "bg-muted text-foreground" : "text-muted-foreground"}
      size="sm"
      variant="ghost"
      onClick={onClick}
    >
      {children}
    </Button>
  );
}

function ComingSoon({ tab }: { tab: Tab }) {
  return (
    <div
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        color: "var(--ink-fade)",
        textAlign: "center",
        padding: 40,
      }}
    >
      {tab} coming soon
    </div>
  );
}

function MediaTab({
  onUpload,
  uploading,
}: {
  onUpload: (f: File) => void;
  uploading: boolean;
}) {
  const { state, dispatch } = useEDL();
  const fileRef = useRef<HTMLInputElement>(null);
  const assets: MediaAsset[] = state.sources;

  return (
    <>
      <div className="lib__head">
        <span className="label">Library</span>
        <Button
          variant="default"
          size="xs"
          className="lib__add"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          title="import media"
        >
          <Plus data-icon="inline-start" />
          Add
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept="video/*"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onUpload(f);
            e.target.value = "";
          }}
        />
      </div>

      {assets.length === 0 ? (
        <div className="lib__empty">
          <p className="lib__empty-text">
            drop a clip or tap <Icon name="plus" size={11} /> to import
          </p>
          <p className="lib__empty-sub mono">
            imports sit here — plus ⊕ drops them on the timeline
          </p>
        </div>
      ) : (
        <ul className="lib__grid">
          {assets.map((a) => (
            <AssetTile
              key={a.id}
              asset={a}
              onAdd={() => dispatch({ type: "add_to_timeline", assetId: a.id })}
              onRemove={() => dispatch({ type: "remove_source", assetId: a.id })}
            />
          ))}
        </ul>
      )}

      {uploading && <div className="lib__busy mono">importing…</div>}
    </>
  );
}

function AssetTile({
  asset,
  onAdd,
  onRemove,
}: {
  asset: MediaAsset;
  onAdd: () => void;
  onRemove: () => void;
}) {
  return (
    <li className="asset">
      <div className="asset__thumb">
        <video
          src={asset.url}
          muted
          preload="metadata"
          className="asset__video"
        />
        <span className="asset__dur mono">{fmtTime(asset.duration)}</span>
        <Button
          variant="default"
          size="xs"
          className="asset__add"
          onClick={onAdd}
          onDoubleClick={onAdd}
          title="add to timeline"
        >
          +add
        </Button>
        <button
          className="asset__del"
          onClick={onRemove}
          title="remove from library"
        >
          <Icon name="close" size={10} />
        </button>
      </div>
      <span className="asset__name" title={asset.label}>
        {asset.label || "untitled"}
      </span>
    </li>
  );
}

function fmtTime(t: number) {
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

import { useRef, useState } from "react";
import { useEDL, type MediaAsset } from "../stores/edl";
import { Icon, type IconName } from "./Icon";
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
      <nav className="lib__tabs">
        <TabBtn active={tab === "media"} onClick={() => setTab("media")} icon="media" label="Media" />
        <TabBtn active={tab === "audio"} onClick={() => setTab("audio")} icon="audio" label="Audio" />
        <TabBtn active={tab === "effects"} onClick={() => setTab("effects")} icon="effects" label="Effects" />
        <TabBtn active={tab === "adjust"} onClick={() => setTab("adjust")} icon="adjust" label="Adjust" />
      </nav>

      <div className="lib__body">
        {tab === "media" && <MediaTab onUpload={onUpload} uploading={uploading} />}
        {tab !== "media" && <ComingSoon tab={tab} />}
      </div>
    </div>
  );
}

function TabBtn({
  active,
  onClick,
  icon,
  label,
}: {
  active?: boolean;
  onClick?: () => void;
  icon: IconName;
  label: string;
}) {
  return (
    <button
      className={`lib__tab ${active ? "lib__tab--on" : ""}`}
      onClick={onClick}
      title={label}
    >
      <Icon name={icon} size={15} />
      <span>{label}</span>
    </button>
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
        <button
          className="lib__add"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          title="import media"
        >
          <Icon name="plus" size={13} />
        </button>
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
        <button
          className="asset__add"
          onClick={onAdd}
          onDoubleClick={onAdd}
          title="add to timeline"
        >
          <Icon name="plus" size={14} />
        </button>
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

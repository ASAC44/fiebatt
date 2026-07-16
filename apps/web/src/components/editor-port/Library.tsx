import { Button } from "@/components/ui/button";
import { useEDL, type MediaAsset } from "@/stores/edl";
import { Icon } from "./Icon";
import "./library.css";

/**
 * Left sidebar for source and generated media attached to this project.
 */
export function Library() {
  return (
    <div className="lib">
      <div className="lib__tabs">
        <span className="text-sm font-medium">Media</span>
      </div>

      <div className="lib__body">
        <MediaTab />
      </div>
    </div>
  );
}

function MediaTab() {
  const { state, dispatch } = useEDL();
  const assets: MediaAsset[] = state.sources;

  return (
    <>
      <div className="lib__head">
        <span className="label">Library</span>
      </div>

      {assets.length === 0 ? (
        <div className="lib__empty">
          <p className="lib__empty-text">
            Upload a video to start a project.
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

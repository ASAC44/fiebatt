import { useRef, useState } from "react";
import { Icon } from "./Icon";
import "./upload-drop.css";

export function UploadDrop({
  onFile,
  busy,
}: {
  onFile: (f: File) => void;
  busy?: boolean;
}) {
  const inRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  return (
    <div
      className={`drop ${dragging ? "drop--hot" : ""} ${busy ? "drop--busy" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const f = e.dataTransfer.files[0];
        if (f) onFile(f);
      }}
      onClick={() => !busy && inRef.current?.click()}
    >
      <div className="drop__plate">
        <div className="drop__ring">
          <Icon name="plus" size={22} />
        </div>
        <div className="drop__text">
          <strong className="drop__title">
            {busy ? "Importing…" : "Drop a clip"}
          </strong>
          <span className="drop__sub mono">
            mp4 · mov · ≤ 2 min · drag or click
          </span>
        </div>
      </div>
      <input
        ref={inRef}
        type="file"
        accept="video/*"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
        }}
      />
    </div>
  );
}

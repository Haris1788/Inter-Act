// Canvas toolbar - top-left tool clusters (select, pan, edit, spatial, fit).
// Mirrors .canvas-toolbar / .tool-cluster from styles-v2.css.

import { useEditorMode } from "@/store/selectors";
import { useStore } from "@/store";

interface CanvasToolbarProps {
  onFit?: () => void;
}

export function CanvasToolbar({ onFit }: CanvasToolbarProps) {
  const mode = useEditorMode();
  const setMode = useStore((s) => s.setMode);

  return (
    <div className="absolute left-[14px] top-[14px] z-[5] flex gap-2">
      {/* Primary tools cluster */}
      <div className="flex items-center gap-[1px] rounded-md border border-line bg-white p-[3px] shadow-e1">
        <ToolBtn
          active={mode === "select"}
          title="Select"
          onClick={() => setMode("select")}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
            <path d="M5 3l14 6-6 2-2 6z" />
          </svg>
        </ToolBtn>
        <ToolBtn
          active={mode === "pan"}
          title="Pan"
          onClick={() => setMode("pan")}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
          >
            <path d="M7 11V5a1.5 1.5 0 1 1 3 0v5" />
            <path d="M10 9V4a1.5 1.5 0 1 1 3 0v6" />
            <path d="M13 9V5a1.5 1.5 0 1 1 3 0v7" />
            <path d="M16 8a1.5 1.5 0 1 1 3 0v7a5 5 0 0 1-5 5h-2a5 5 0 0 1-5-5v-1" />
          </svg>
        </ToolBtn>
        <ToolBtn
          active={mode === "edit"}
          title="Edit"
          onClick={() => setMode("edit")}
        >
          {/* Node/edit glyph: pencil touching a node circle */}
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
          >
            <circle cx="8" cy="8" r="3" />
            <path d="M14 10l6-6" />
            <path d="M17 4l3 3" />
            <path d="M10 14l4-4" />
          </svg>
        </ToolBtn>
        <div className="mx-[2px] my-1 w-px self-stretch bg-line-soft" />
      </div>

      {/* Secondary tools cluster */}
      <div className="flex items-center gap-[1px] rounded-md border border-line bg-white p-[3px] shadow-e1">
        <ToolBtn title="Spatial overlay">
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
          >
            <circle cx="12" cy="12" r="9" />
            <circle cx="12" cy="12" r="4" />
          </svg>
        </ToolBtn>
        <ToolBtn title="Fit to view" onClick={onFit}>
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
          >
            <polyline points="4 9 4 4 9 4" />
            <polyline points="15 4 20 4 20 9" />
            <polyline points="20 15 20 20 15 20" />
            <polyline points="9 20 4 20 4 15" />
          </svg>
        </ToolBtn>
      </div>
    </div>
  );
}

function ToolBtn({
  children,
  active,
  title,
  onClick,
}: {
  children: React.ReactNode;
  active?: boolean;
  title?: string;
  onClick?: () => void;
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      className={
        active
          ? "inline-flex h-[26px] w-[26px] items-center justify-center rounded-[5px] bg-cta text-white"
          : "inline-flex h-[26px] w-[26px] items-center justify-center rounded-[5px] text-ink-3 hover:bg-bg-hover hover:text-ink"
      }
    >
      {children}
    </button>
  );
}

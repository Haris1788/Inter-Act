// Zoom cluster — bottom-left: zoom out, zoom level readout, zoom in, fit.
// Mirrors .zoom-cluster from styles-v2.css.

import { useReactFlow, useViewport } from "@xyflow/react";

interface ZoomClusterProps {
  onFit?: () => void;
}

export function ZoomCluster({ onFit }: ZoomClusterProps) {
  const rf = useReactFlow();
  const { zoom } = useViewport();
  const pct = Math.round(zoom * 100);

  return (
    <div className="absolute bottom-[14px] left-[14px] z-[5] inline-flex items-center gap-[1px] rounded-md border border-line bg-white p-[3px] shadow-e1">
      <ZBtn title="Zoom out" onClick={() => rf.zoomOut({ duration: 200 })}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
      </ZBtn>
      <span className="px-2 font-mono text-11 text-ink-2">{pct}%</span>
      <ZBtn title="Zoom in" onClick={() => rf.zoomIn({ duration: 200 })}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="5" y1="12" x2="19" y2="12" />
          <line x1="12" y1="5" x2="12" y2="19" />
        </svg>
      </ZBtn>
      <ZBtn title="Fit" onClick={onFit}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M9 4H5a1 1 0 0 0-1 1v4" />
          <path d="M15 4h4a1 1 0 0 1 1 1v4" />
          <path d="M9 20H5a1 1 0 0 1-1-1v-4" />
          <path d="M15 20h4a1 1 0 0 0 1-1v-4" />
        </svg>
      </ZBtn>
    </div>
  );
}

function ZBtn({
  children,
  title,
  onClick,
}: {
  children: React.ReactNode;
  title?: string;
  onClick?: () => void;
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      className="inline-flex h-[26px] w-[26px] items-center justify-center rounded-[5px] text-ink-3 hover:bg-bg-hover hover:text-ink"
    >
      {children}
    </button>
  );
}

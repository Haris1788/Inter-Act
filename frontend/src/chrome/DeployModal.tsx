import { useEffect, useRef, useState, useCallback } from "react";
import { buildDeployPayload, deployTopology } from "@/runtime/deploy";
import type { DeployPayload, DeployResult } from "@/runtime/deploy";
import { useStore } from "@/store";

export function DeployModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);

  // Compute payload + result once per open; memoize while open
  const [cached, setCached] = useState<{ payload: DeployPayload; result: DeployResult } | null>(null);

  useEffect(() => {
    if (open) {
      const state = useStore.getState();
      const payload = buildDeployPayload(state);
      const result = deployTopology(payload);
      setCached({ payload, result });
      setCopied(false);
      // Focus the panel for keyboard accessibility
      setTimeout(() => panelRef.current?.focus(), 0);
    } else {
      setCached(null);
    }
  }, [open]);

  // Escape key to close
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  const handleCopy = useCallback(() => {
    if (!cached) return;
    navigator.clipboard.writeText(JSON.stringify(cached.payload, null, 2)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  }, [cached]);

  const handleDownload = useCallback(() => {
    if (!cached) return;
    const blob = new Blob([JSON.stringify(cached.payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "orchestration-deploy.json";
    a.click();
    URL.revokeObjectURL(url);
  }, [cached]);

  if (!open || !cached) return null;

  const { payload, result } = cached;
  const armCount = payload.actors.filter((a) => a.kind === "arm").length;
  const agvCount = payload.actors.filter((a) => a.kind === "agv").length;
  const jsonText = JSON.stringify(payload, null, 2);

  return (
    <div
      className="fixed inset-0 z-[70] grid place-items-center bg-black/20"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        className="w-[540px] max-w-[calc(100vw-32px)] rounded-lg border border-line bg-bg-panel shadow-e3 outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 pt-5 pb-4 border-b border-line">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="m-0 text-14 font-semibold text-ink leading-snug">Build &amp; Deploy</h2>
              <p className="mt-[3px] text-12 text-ink-3 leading-snug">{result.note}</p>
            </div>
            <button
              onClick={onClose}
              className="flex-shrink-0 w-[26px] h-[26px] rounded-sm inline-flex items-center justify-center text-ink-3 hover:bg-bg-hover hover:text-ink"
              aria-label="Close"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* Summary chips */}
          <div className="mt-3 flex flex-wrap gap-[6px]">
            {armCount > 0 && (
              <span className="inline-flex items-center gap-[5px] h-[22px] px-2 rounded-[5px] text-11 font-medium bg-[#EFF4FF] text-[#1B47A5] border border-[#C3D3F7]">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="3" y="9" width="18" height="7" rx="1.5" />
                  <path d="M5 9V7h14v2" />
                </svg>
                {armCount} arm{armCount !== 1 ? "s" : ""}
              </span>
            )}
            {agvCount > 0 && (
              <span className="inline-flex items-center gap-[5px] h-[22px] px-2 rounded-[5px] text-11 font-medium bg-[#E7F4EC] text-[#1D6B38] border border-[#B8DEC5]">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="2" y="11" width="20" height="8" rx="2" />
                  <circle cx="7" cy="19" r="2" />
                  <circle cx="17" cy="19" r="2" />
                  <path d="M7 11V7l5-3 5 3v4" />
                </svg>
                {agvCount} AGV{agvCount !== 1 ? "s" : ""}
              </span>
            )}
            <span className="inline-flex items-center gap-[5px] h-[22px] px-2 rounded-[5px] text-11 font-medium bg-[#E7E6E2] text-ink-2 border border-line">
              {payload.zones.length} zone{payload.zones.length !== 1 ? "s" : ""}
            </span>
            {payload.goal?.text && (
              <span
                className="inline-flex items-center h-[22px] px-2 rounded-[5px] text-11 font-medium bg-[#E7E6E2] text-ink-2 border border-line truncate max-w-[200px]"
                title={payload.goal.text}
              >
                {payload.goal.text}
              </span>
            )}
          </div>
        </div>

        {/* JSON preview */}
        <div className="px-5 pt-4 pb-3">
          <div className="text-11 text-ink-3 mb-[6px] font-medium uppercase tracking-wide">
            Payload preview
          </div>
          <pre
            className="font-mono text-12 text-ink leading-relaxed bg-[#FAF8F2] border border-line rounded-md px-3 py-3 overflow-auto max-h-[280px] m-0 whitespace-pre"
          >
            {jsonText}
          </pre>
        </div>

        {/* Footer */}
        <div className="px-5 pb-5 pt-1 flex items-center gap-2">
          <button
            onClick={handleCopy}
            className="h-[30px] px-3 rounded-[5px] text-12 font-medium text-ink-2 border border-line bg-bg-panel hover:bg-bg-hover transition-colors"
          >
            {copied ? "Copied!" : "Copy JSON"}
          </button>
          <button
            onClick={handleDownload}
            className="h-[30px] px-3 rounded-[5px] text-12 font-medium text-ink-2 border border-line bg-bg-panel hover:bg-bg-hover transition-colors"
          >
            Download
          </button>
          <div className="flex-1" />
          <span className="text-11 text-ink-3 mr-2">Backend deploy is wired in a later phase.</span>
          <button
            onClick={onClose}
            className="h-[30px] px-4 rounded-[5px] text-12 font-medium bg-cta text-[#FAFAF7] hover:opacity-90 transition-opacity"
          >
            Looks good
          </button>
        </div>
      </div>
    </div>
  );
}

import { useState } from "react";
import { cn } from "@/lib/cn";
import { useActors, useDirty } from "@/store/selectors";
import { useStore } from "@/store";
import { ConnectionChip } from "./ConnectionChip";
import { DeployModal } from "./DeployModal";
import { serializeTopology, docToState } from "@/lib/topologySchema";
import { exportTopologyFile, importTopologyFile } from "@/lib/persistence";
import { useTopologyValidation, useLifecycle } from "@/store/derived";
import type { Tone } from "@/lib/lifecycle";

function toneDot(tone: Tone): string {
  switch (tone) {
    case "ok":   return "bg-ok";
    case "warn":  return "bg-warn";
    case "err":   return "bg-err";
    case "info":  return "bg-info";
    case "idle":
    default:      return "bg-ink-5";
  }
}

export function Topbar() {
  const actors = useActors();
  const actorCount = Object.keys(actors).length;
  const dirty = useDirty();
  const [deployOpen, setDeployOpen] = useState(false);
  const { deployable, warnings } = useTopologyValidation();
  const lifecycle = useLifecycle();

  const onExport = () => exportTopologyFile(serializeTopology(useStore.getState()));
  const onImport = async () => {
    try {
      const doc = await importTopologyFile();
      const s = useStore.getState();
      s.replaceTopology(docToState(doc));
      s.markDirty();
    } catch {
      /* user cancelled / invalid file — non-fatal */
    }
  };
  return (
    <header
      className={cn(
        "flex items-center gap-3 border-b border-line bg-bg-panel px-3.5",
        "h-12 shrink-0",
      )}
    >
      {/* LEFT */}
      <div className="flex shrink-0 items-center gap-2.5">
        {/* Brand */}
        <div className="flex items-center gap-2 font-semibold">
          <span className="text-14 tracking-tight">
            Inter<em className="not-italic text-ink-4">·</em>Act
          </span>
        </div>
        {/* Divider */}
        <span className="inline-block h-5 w-px bg-line" />
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 rounded-sm px-2.5 py-1">
          <span className="text-12 text-ink-3">in3-group</span>
          <span className="text-12 text-ink-5">/</span>
          <span className="text-12 font-semibold text-ink">atlas-floor-3</span>
          <span className="text-12 text-ink-5">/</span>
          <span className="font-mono text-12 text-ink-2">orchestration.yaml</span>
          <ConnectionChip />
          {/* Lifecycle status chip */}
          <span
            className={cn(
              "font-mono text-10.5 tracking-wider",
              "inline-flex items-center gap-1.5",
              "rounded-full border border-line bg-bg-sunken px-[7px] py-px",
            )}
          >
            <span
              className={cn(
                "inline-block h-1.5 w-1.5 rounded-full",
                toneDot(lifecycle.tone),
                lifecycle.tone === "info" && "animate-pulse-dot",
              )}
            />
            {lifecycle.label}
          </span>
        </div>
      </div>

      {/* CENTER */}
      <div className="mx-auto flex min-w-0 shrink-0 items-center gap-1.5 text-12.5 text-ink-3">
        {/* Save */}
        <button
          className={cn(
            "inline-flex h-[30px] items-center gap-1.5 rounded-md border border-transparent",
            "px-2.5 text-12.5 text-ink-2 transition-colors hover:bg-bg-hover",
          )}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
            <polyline points="17 21 17 13 7 13 7 21" />
            <polyline points="7 3 7 8 15 8" />
          </svg>
          Save
          <span
            className={cn(
              "font-mono text-10.5 text-ink-4",
              "rounded border border-line bg-bg-surface px-1",
            )}
          >
            ⌘S
          </span>
        </button>

        {/* Undo */}
        <button
          title="Undo"
          className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-md text-ink-3 hover:bg-bg-hover hover:text-ink"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 7v6h6" />
            <path d="M21 17a9 9 0 0 0-15-6.7L3 13" />
          </svg>
        </button>

        {/* Redo */}
        <button
          title="Redo"
          className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-md text-ink-3 hover:bg-bg-hover hover:text-ink"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 7v6h-6" />
            <path d="M3 17a9 9 0 0 1 15-6.7L21 13" />
          </svg>
        </button>

        {/* Divider */}
        <span className="mx-1 inline-block h-[18px] w-px bg-line" />

        <button
          onClick={onImport}
          className="inline-flex h-[30px] items-center gap-1.5 rounded-md border border-transparent px-2.5 text-12.5 text-ink-2 hover:bg-bg-hover"
        >
          Import
        </button>
        <button
          onClick={onExport}
          className="inline-flex h-[30px] items-center gap-1.5 rounded-md border border-transparent px-2.5 text-12.5 text-ink-2 hover:bg-bg-hover"
        >
          Export
        </button>
      </div>

      {/* RIGHT */}
      <div className="flex shrink-0 items-center gap-2">
        {/* Validate */}
        <button
          className={cn(
            "inline-flex h-[30px] items-center gap-1.5 rounded-md",
            "border border-line bg-bg-surface px-3 text-12.5 text-ink-2",
          )}
        >
          <span
            className={cn(
              "inline-block h-1.5 w-1.5 rounded-full",
              deployable && warnings === 0 ? "bg-ok" : !deployable ? "bg-err" : "bg-warn",
            )}
            style={{
              boxShadow: deployable && warnings === 0
                ? "0 0 0 2px #E6EBE0"
                : !deployable
                  ? "0 0 0 2px #F5E0E0"
                  : "0 0 0 2px #F0E6D0",
            }}
          />
          {deployable && warnings === 0
            ? "Coherent"
            : !deployable
              ? "Not deployable"
              : `${warnings} hint${warnings !== 1 ? "s" : ""}`}
          {" · "}
          <span className="font-mono text-11 text-ink-4">{actorCount} actors registered</span>
        </button>

        {/* Deploy */}
        <button
          onClick={() => setDeployOpen(true)}
          className={cn(
            "inline-flex h-8 items-center gap-2 rounded-md border border-cta",
            "bg-cta px-3.5 text-12.5 font-semibold text-[#FAFAF7]",
            "shadow-[0_1px_0_rgba(24,24,27,0.04),inset_0_1px_0_rgba(255,255,255,0.06)]",
            "hover:bg-cta-2",
          )}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
          </svg>
          Build &amp; Deploy
        </button>

        {/* Notifications */}
        <button
          title="Notifications"
          className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-md text-ink-3 hover:bg-bg-hover hover:text-ink"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
            <path d="M13.7 21a2 2 0 0 1-3.4 0" />
          </svg>
        </button>
      </div>
      {dirty && (
        <span
          title="Unsaved changes"
          className="pointer-events-none fixed left-1/2 top-[40px] -translate-x-1/2 font-mono text-10.5 text-ink-4"
        >
          ● unsaved
        </span>
      )}
      <DeployModal open={deployOpen} onClose={() => setDeployOpen(false)} />
    </header>
  );
}

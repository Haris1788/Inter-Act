import { useEffect, useRef, useState, useCallback } from "react";
import { buildDeployPayload, runDeploy } from "@/runtime/deploy";
import type { DeployPayload } from "@/runtime/deploy";
import { useStore } from "@/store";
import {
  useDeployPhase,
  useDeployMessage,
  useDataSource,
  useLinkStatus,
} from "@/store/selectors";
import { useTopologyValidation } from "@/store/derived";
import type { DeployPhase } from "@/store/types";

// ── Step model ───────────────────────────────────────────────────────────────

type StepState = "pending" | "active" | "done" | "failed";

interface StepDef {
  id: string;
  label: string;
  liveOnly?: boolean;
}

const ALL_STEPS: StepDef[] = [
  { id: "validate", label: "Validate" },
  { id: "prepare", label: "Prepare" },
  { id: "restart", label: "Restart runtime", liveOnly: true },
  { id: "reconcile", label: "Reconcile", liveOnly: true },
  { id: "done", label: "Done" },
];

// ── Step state resolver using local failedAt ─────────────────────────────────

function resolveStepState(
  stepId: string,
  phase: DeployPhase,
  isLive: boolean,
  failedAtStep: string | null,
): StepState {
  const order: string[] = isLive
    ? ["validate", "prepare", "restart", "reconcile", "done"]
    : ["validate", "prepare", "done"];

  const si = order.indexOf(stepId);
  if (si === -1) return "pending";

  if (phase === "idle") return "pending";

  if (phase === "failed") {
    if (!failedAtStep) return "pending";
    const fi = order.indexOf(failedAtStep);
    if (si < fi) return "done";
    if (si === fi) return "failed";
    return "pending";
  }

  // active phase → map to step
  const activeByPhase: Partial<Record<DeployPhase, string>> = {
    validating: "validate",
    preparing: "prepare",
    restarting: "restart",
    reconciling: "reconcile",
  };
  const terminalDone: DeployPhase[] = ["prepared", "ready"];

  if (terminalDone.includes(phase)) {
    return "done"; // all steps done
  }

  const current = activeByPhase[phase] ?? "";
  const ci = order.indexOf(current);
  if (ci === -1) return "pending";
  if (si < ci) return "done";
  if (si === ci) return "active";
  return "pending";
}

// ── Dot component ────────────────────────────────────────────────────────────

function StepDot({ state }: { state: StepState }) {
  if (state === "done") {
    return (
      <span className="w-[18px] h-[18px] rounded-full bg-[#1D6B38] flex items-center justify-center flex-shrink-0">
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#FAFAF7" strokeWidth="3">
          <path d="M5 13l4 4L19 7" />
        </svg>
      </span>
    );
  }
  if (state === "failed") {
    return (
      <span className="w-[18px] h-[18px] rounded-full bg-[#B91C1C] flex items-center justify-center flex-shrink-0">
        <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="#FAFAF7" strokeWidth="3">
          <path d="M18 6 6 18M6 6l12 12" />
        </svg>
      </span>
    );
  }
  if (state === "active") {
    return (
      <span
        className="w-[18px] h-[18px] rounded-full border-2 border-cta flex-shrink-0"
        style={{ animation: "pulseDot 1.4s ease-in-out infinite" }}
      />
    );
  }
  // pending
  return (
    <span className="w-[18px] h-[18px] rounded-full border border-line bg-bg-panel flex-shrink-0" />
  );
}

// ── Validation gate ───────────────────────────────────────────────────────────

interface ValidationGateProps {
  deployable: boolean;
  errors: number;
  warnings: number;
}

function ValidationGate({ deployable, errors, warnings }: ValidationGateProps) {
  if (deployable && warnings === 0) return null;

  if (!deployable) {
    return (
      <div className="mx-5 mt-4 px-3 py-2 rounded-md border border-[#FECACA] bg-[#FEF2F2] text-12 text-[#B91C1C]">
        <span className="font-medium">Cannot deploy — </span>
        {errors > 0 ? `${errors} error${errors !== 1 ? "s" : ""} must be resolved` : "topology is not deployable"}
        {warnings > 0 ? `, ${warnings} warning${warnings !== 1 ? "s" : ""}` : ""}
      </div>
    );
  }

  return (
    <div className="mx-5 mt-4 px-3 py-2 rounded-md border border-[#FEF3C7] bg-[#FFFBEB] text-12 text-[#92400E]">
      <span className="font-medium">{warnings} warning{warnings !== 1 ? "s" : ""} — </span>
      topology is deployable
    </div>
  );
}

// ── Main modal ────────────────────────────────────────────────────────────────

export function DeployModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);
  const [running, setRunning] = useState(false);
  const [failedAtStep, setFailedAtStep] = useState<string | null>(null);

  const phase = useDeployPhase();
  const deployMessage = useDeployMessage();
  const dataSource = useDataSource();
  const linkStatus = useLinkStatus();
  const validation = useTopologyValidation();

  // isLive: only when live data source + connected
  const isLive = dataSource === "live" && linkStatus === "connected";

  // Build payload live on each render (cheap; derived from store state)
  const payload: DeployPayload = buildDeployPayload(useStore.getState());
  const jsonText = JSON.stringify(payload, null, 2);

  // Track the step at which a failure occurred
  const phaseToStep: Partial<Record<DeployPhase, string>> = {
    validating: "validate",
    preparing: "prepare",
    restarting: "restart",
    reconciling: "reconcile",
  };

  // When phase transitions to failed, record the last active step
  const prevPhaseRef = useRef<DeployPhase>(phase);
  useEffect(() => {
    if (phase === "failed" && prevPhaseRef.current !== "failed") {
      const lastStep = phaseToStep[prevPhaseRef.current] ?? "validate";
      setFailedAtStep(lastStep);
    }
    if (phase === "idle") {
      setFailedAtStep(null);
    }
    prevPhaseRef.current = phase;
  });

  // Reset on open/close
  useEffect(() => {
    if (open) {
      setCopied(false);
      setRunning(false);
      setFailedAtStep(null);
      setTimeout(() => panelRef.current?.focus(), 0);
    }
  }, [open]);

  // Escape key
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(jsonText).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  }, [jsonText]);

  const handleDownload = useCallback(() => {
    const blob = new Blob([jsonText], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "orchestration-deploy.json";
    a.click();
    URL.revokeObjectURL(url);
  }, [jsonText]);

  const handleDeploy = useCallback(async () => {
    if (running) return;
    setRunning(true);
    setFailedAtStep(null);
    await runDeploy({ applyRestart: isLive });
    setRunning(false);
  }, [running, isLive]);

  if (!open) return null;

  const armCount = payload.actors.filter((a) => a.kind === "arm").length;
  const agvCount = payload.actors.filter((a) => a.kind === "agv").length;

  // Steps to render
  const visibleSteps = ALL_STEPS.filter((s) => !s.liveOnly || isLive);

  // Active phases — button disabled
  const activePhases: DeployPhase[] = ["validating", "preparing", "restarting", "reconciling"];
  const isInFlight = activePhases.includes(phase) || running;

  // Button label
  let buttonLabel = isLive ? "Deploy & restart" : "Prepare deploy";
  if (isInFlight) buttonLabel = "Running…";
  else if (phase === "ready") buttonLabel = "Deploy again";
  else if (phase === "prepared") buttonLabel = "Prepare again";
  else if (phase === "failed") buttonLabel = isLive ? "Retry deploy" : "Retry prepare";

  // Status line under stepper
  const showStatusLine = phase !== "idle";

  // Terminal state notes (honest)
  const isPrepared = phase === "prepared";
  const isReady = phase === "ready";
  const isFailed = phase === "failed";

  return (
    <>
      {/* Pulse animation keyframes injected inline */}
      <style>{`
        @keyframes pulseDot {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.35; }
        }
      `}</style>

      <div
        className="fixed inset-0 z-[70] grid place-items-center bg-black/20"
        onClick={onClose}
      >
        <div
          ref={panelRef}
          tabIndex={-1}
          className="w-[560px] max-w-[calc(100vw-32px)] rounded-lg border border-line bg-bg-panel shadow-e3 outline-none"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="px-5 pt-5 pb-4 border-b border-line">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="m-0 text-14 font-semibold text-ink leading-snug">Build &amp; Deploy</h2>
                <p className="mt-[3px] text-12 text-ink-3 leading-snug">
                  {isLive
                    ? "Topology will be prepared and the backend pipeline restarted."
                    : "Authored topology prepared for the offline pipeline.yaml → generate_compose flow."}
                </p>
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
              {/* Data source indicator */}
              <span className={`inline-flex items-center gap-[5px] h-[22px] px-2 rounded-[5px] text-11 font-medium border ${isLive ? "bg-[#E7F4EC] text-[#1D6B38] border-[#B8DEC5]" : "bg-[#E7E6E2] text-ink-2 border-line"}`}>
                <span className={`w-[6px] h-[6px] rounded-full ${isLive ? "bg-[#1D6B38]" : "bg-ink-3"}`} />
                {isLive ? "live" : "mock"}
              </span>
            </div>
          </div>

          {/* Validation gate */}
          <ValidationGate
            deployable={validation.deployable}
            errors={validation.errors}
            warnings={validation.warnings}
          />

          {/* Lifecycle stepper */}
          <div className="px-5 pt-4 pb-3">
            <div className="text-11 text-ink-3 mb-[10px] font-medium uppercase tracking-wide">
              Deployment lifecycle
            </div>
            <div className="flex items-center gap-0">
              {visibleSteps.map((step, i) => {
                const state = resolveStepState(step.id, phase, isLive, failedAtStep);
                const isLast = i === visibleSteps.length - 1;
                return (
                  <div key={step.id} className="flex items-center flex-1 min-w-0">
                    <div className="flex flex-col items-center gap-[5px] flex-shrink-0">
                      <StepDot state={state} />
                      <span
                        className={`text-10 font-medium leading-none whitespace-nowrap ${
                          state === "active"
                            ? "text-ink"
                            : state === "done"
                            ? "text-[#1D6B38]"
                            : state === "failed"
                            ? "text-[#B91C1C]"
                            : "text-ink-3"
                        }`}
                      >
                        {step.label}
                      </span>
                    </div>
                    {!isLast && (
                      <div
                        className={`h-[1px] flex-1 mx-1 mb-[14px] ${
                          state === "done" ? "bg-[#1D6B38]/40" : "bg-line"
                        }`}
                      />
                    )}
                  </div>
                );
              })}
            </div>

            {/* Status sub-line */}
            {showStatusLine && (
              <div
                className={`mt-3 px-3 py-[7px] rounded-md border text-12 font-mono ${
                  isFailed
                    ? "border-[#FECACA] bg-[#FEF2F2] text-[#B91C1C]"
                    : isReady
                    ? "border-[#B8DEC5] bg-[#E7F4EC] text-[#1D6B38]"
                    : isPrepared
                    ? "border-[#C3D3F7] bg-[#EFF4FF] text-[#1B47A5]"
                    : "border-line bg-[#FAF8F2] text-ink-2"
                }`}
              >
                {deployMessage || " "}
              </div>
            )}

            {/* Honest terminal notes */}
            {isPrepared && (
              <p className="mt-2 text-11 text-ink-3 leading-snug">
                Payload recorded. To apply: edit <span className="font-mono">pipeline.yaml</span> and run{" "}
                <span className="font-mono">generate_compose.py</span> → <span className="font-mono">docker compose up --build</span>.
              </p>
            )}
            {isReady && (
              <p className="mt-2 text-11 text-[#1D6B38] leading-snug">
                Backend pipeline restarted and reconciled. Runtime is live.
              </p>
            )}
          </div>

          {/* JSON preview */}
          <div className="px-5 pt-1 pb-3">
            <div className="text-11 text-ink-3 mb-[6px] font-medium uppercase tracking-wide">
              Payload preview
            </div>
            <pre className="font-mono text-12 text-ink leading-relaxed bg-[#FAF8F2] border border-line rounded-md px-3 py-3 overflow-auto max-h-[200px] m-0 whitespace-pre">
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
            <button
              onClick={handleDeploy}
              disabled={isInFlight || !validation.deployable}
              className="h-[30px] px-4 rounded-[5px] text-12 font-medium bg-cta text-[#FAFAF7] hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {buttonLabel}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

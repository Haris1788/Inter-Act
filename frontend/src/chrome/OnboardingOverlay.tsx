import { useEffect, useRef, useState } from "react";

const STORAGE_KEY = "interact.onboarded";

const REGIONS = [
  {
    label: "Left panel",
    desc: "Actor and capability registry. Drag actors onto the canvas to build a topology.",
  },
  {
    label: "Center canvas",
    desc: "Orchestration canvas - React Flow topology editor with runtime state overlay.",
  },
  {
    label: "Right panel",
    desc: "Actor inspector: identity, capabilities, motion parameters, diagnostics.",
  },
  {
    label: "Bottom strip",
    desc: "Coordination log, event stream, and topology validation output.",
  },
] as const;

export function OnboardingOverlay() {
  const [visible, setVisible] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // localStorage can throw in private/sandboxed contexts - never crash the app.
    try {
      if (!localStorage.getItem(STORAGE_KEY)) setVisible(true);
    } catch {
      setVisible(true);
    }
  }, []);

  useEffect(() => {
    if (!visible) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismiss();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [visible]);

  useEffect(() => {
    if (!visible) return;
    const t = setTimeout(() => cardRef.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [visible]);

  function dismiss() {
    try {
      localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      /* storage unavailable - overlay simply re-shows next load */
    }
    setVisible(false);
  }

  if (!visible) return null;

  return (
    <div
      className="fixed inset-0 z-[80] grid place-items-center bg-black/15"
      onClick={dismiss}
      aria-modal="true"
      role="dialog"
      aria-labelledby="onboarding-title"
    >
      <div
        ref={cardRef}
        tabIndex={-1}
        className="w-[480px] max-w-[calc(100vw-32px)] rounded-lg border border-line bg-bg-panel shadow-e3 outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 pt-6 pb-4 border-b border-line">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2
                id="onboarding-title"
                className="m-0 text-14 font-semibold text-ink leading-snug"
              >
                INTER.act - orchestration workspace
              </h2>
              <p className="mt-1 text-12 text-ink-3 leading-relaxed">
                Author a topology of arms and AGVs, visualize runtime
                coordination, and prepare deployment payloads.
              </p>
            </div>
            <button
              onClick={dismiss}
              className="flex-shrink-0 w-[26px] h-[26px] rounded-sm inline-flex items-center justify-center text-ink-3 hover:bg-bg-hover hover:text-ink"
              aria-label="Close orientation"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Region legend */}
        <div className="px-6 py-4">
          <p className="text-11 text-ink-4 font-medium uppercase tracking-wide mb-3">
            Workspace regions
          </p>
          <ul className="m-0 p-0 list-none space-y-3">
            {REGIONS.map(({ label, desc }) => (
              <li key={label} className="flex gap-3">
                <span className="mt-px flex-shrink-0 w-[72px] text-12 font-medium text-ink-2 leading-snug">
                  {label}
                </span>
                <span className="text-12 text-ink-3 leading-snug">{desc}</span>
              </li>
            ))}
          </ul>
        </div>

        {/* Footer note + action */}
        <div className="px-6 pb-5 pt-1">
          <p className="text-11 text-ink-4 leading-snug mb-4 pl-2 border-l-2 border-line">
            Standalone mode runs on mock fixtures - see the mock/live chip in
            the toolbar. Live mode binds the real Flask/Socket.IO backend.
          </p>
          <div className="flex items-center justify-between gap-3">
            <span className="text-11 text-ink-5">
              Press Esc or click outside to dismiss.
            </span>
            <button
              onClick={dismiss}
              className="h-[30px] px-4 rounded-[5px] text-12 font-medium bg-cta text-[#FAFAF7] hover:opacity-90 transition-opacity"
            >
              Got it
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

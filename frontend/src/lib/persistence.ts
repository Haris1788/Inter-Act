// Local topology persistence + file import/export. Frontend only.
import { type TopologyDoc, parseTopology } from "./topologySchema";

const KEY = "interact.topology.v1";
const WORKSPACE_KEY = "interact.workspace";

export const DEFAULT_WORKSPACE_NAME = "Untitled workspace";

export function loadWorkspaceName(): string {
  try {
    return localStorage.getItem(WORKSPACE_KEY) || DEFAULT_WORKSPACE_NAME;
  } catch {
    return DEFAULT_WORKSPACE_NAME;
  }
}

export function saveWorkspaceName(name: string): void {
  try {
    localStorage.setItem(WORKSPACE_KEY, name || DEFAULT_WORKSPACE_NAME);
  } catch {
    /* storage unavailable - name persists for this session only */
  }
}

export function saveLocal(doc: TopologyDoc): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(doc));
  } catch {
    // storage full / unavailable - non-fatal for an authoring autosave
  }
}

// Legacy single-letter veh IDs from pre-Phase 3 sessions: veh-a, veh-b, etc.
// These appear when old pipeline.yaml `assets:` blocks were loaded.
const LEGACY_ID_RE = /^veh-[a-z]$/;

export function loadLocal(): TopologyDoc | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const r = parseTopology(raw);
    if (!r.ok) return null;
    // Auto-purge stale legacy actor IDs (e.g. veh-a/b/c/d from old sessions).
    const purged = r.doc.actors.filter((a) => !LEGACY_ID_RE.test(a.id));
    return { ...r.doc, actors: purged };
  } catch {
    return null;
  }
}

export function clearLocal(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* noop */
  }
}

export function exportTopologyFile(doc: TopologyDoc): void {
  const blob = new Blob([JSON.stringify(doc, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `orchestration-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export function importTopologyFile(): Promise<TopologyDoc> {
  return new Promise((resolve, reject) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "application/json,.json";
    input.onchange = () => {
      const file = input.files?.[0];
      if (!file) return reject(new Error("No file selected"));
      const reader = new FileReader();
      reader.onload = () => {
        const r = parseTopology(String(reader.result ?? ""));
        if (r.ok) resolve(r.doc);
        else reject(new Error(r.error));
      };
      reader.onerror = () => reject(new Error("Read failed"));
      reader.readAsText(file);
    };
    input.click();
  });
}

// Local topology persistence + file import/export. Frontend only.
import { type TopologyDoc, parseTopology } from "./topologySchema";

const KEY = "interact.topology.v1";

export function saveLocal(doc: TopologyDoc): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(doc));
  } catch {
    // storage full / unavailable - non-fatal for an authoring autosave
  }
}

export function loadLocal(): TopologyDoc | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const r = parseTopology(raw);
    return r.ok ? r.doc : null;
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

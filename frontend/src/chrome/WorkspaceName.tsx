import { useEffect, useRef, useState } from "react";

// Editable workspace name — replaces hardcoded prototype org/project naming.
// Generic default, localStorage-persisted, double-click to rename.
const KEY = "interact.workspace";
const DEFAULT = "Untitled workspace";

function load(): string {
  try {
    return localStorage.getItem(KEY) || DEFAULT;
  } catch {
    return DEFAULT;
  }
}

export function WorkspaceName() {
  const [name, setName] = useState(DEFAULT);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => setName(load()), []);
  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  function commit() {
    const next = draft.trim() || DEFAULT;
    setName(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* storage unavailable — name stays for this session only */
    }
    setEditing(false);
  }

  return (
    <div className="flex items-center gap-2 rounded-sm px-2.5 py-1">
      {editing ? (
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") setEditing(false);
          }}
          className="w-[180px] rounded-sm border border-line bg-bg-surface px-1.5 py-0.5 text-12 font-semibold text-ink outline-none"
        />
      ) : (
        <button
          onDoubleClick={() => {
            setDraft(name);
            setEditing(true);
          }}
          title="Double-click to rename workspace"
          className="text-12 font-semibold text-ink hover:text-ink-2"
        >
          {name}
        </button>
      )}
    </div>
  );
}

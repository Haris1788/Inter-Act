import { useEffect, useRef, useState } from "react";
import { useStore } from "@/store";

// Editable workspace name. Single authoritative source = the store
// (persisted by save/autosave, not here). Single click to rename;
// Enter / blur commits, Escape cancels.
export function WorkspaceName() {
  const name = useStore((s) => s.workspaceName);
  const setWorkspaceName = useStore((s) => s.setWorkspaceName);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  function start() {
    setDraft(name);
    setEditing(true);
  }
  function commit() {
    const next = draft.trim();
    if (next) setWorkspaceName(next);
    setEditing(false);
  }

  return (
    <div className="flex items-center gap-2 rounded-sm px-2.5 py-1">
      {editing ? (
        <input
          ref={inputRef}
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") setEditing(false);
          }}
          className="w-[180px] rounded-sm border border-line bg-bg-surface px-1.5 py-0.5 text-12 font-semibold text-ink outline-none focus:border-cta"
        />
      ) : (
        <button
          onClick={start}
          title="Click to rename workspace"
          className="text-12 font-semibold text-ink hover:text-ink-2"
        >
          {name}
        </button>
      )}
    </div>
  );
}

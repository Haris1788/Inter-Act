// Right-click context menu for actor nodes. Authoring actions only.
import { useEffect } from "react";
import { useStore } from "@/store";
import { useContextMenu } from "@/store/selectors";

export function ContextMenu() {
  const menu = useContextMenu();
  const close = useStore((s) => s.closeContextMenu);

  useEffect(() => {
    if (!menu) return;
    const onDoc = () => close();
    window.addEventListener("click", onDoc);
    window.addEventListener("blur", onDoc);
    return () => {
      window.removeEventListener("click", onDoc);
      window.removeEventListener("blur", onDoc);
    };
  }, [menu, close]);

  if (!menu || !menu.nodeId) return null;
  const id = menu.nodeId;

  const item =
    "flex w-full items-center justify-between gap-6 rounded-[5px] px-2.5 py-1.5 text-12 text-ink-2 hover:bg-bg-hover";
  const kbd = "font-mono text-10.5 text-ink-4";

  return (
    <div
      className="fixed z-[60] min-w-[180px] rounded-md border border-line bg-bg-panel p-1 shadow-e3"
      style={{ left: menu.x, top: menu.y }}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        className={item}
        onClick={() => {
          const s = useStore.getState();
          const n = s.duplicateActor(id);
          if (n) {
            s.setSelection([n]);
            s.setSelectedActor(n);
            s.markDirty();
          }
          close();
        }}
      >
        Duplicate <span className={kbd}>⌘D</span>
      </button>
      <button
        className={item}
        onClick={() => {
          const s = useStore.getState();
          s.setSelectedActor(id);
          s.setSelection([id]);
          close();
        }}
      >
        Inspect
      </button>
      <div className="my-1 h-px bg-line-soft" />
      <button
        className="flex w-full items-center justify-between gap-6 rounded-[5px] px-2.5 py-1.5 text-12 text-[#8B3621] hover:bg-[#F6E8DF]"
        onClick={() => {
          const s = useStore.getState();
          s.removeActor(id);
          s.clearSelection();
          s.markDirty();
          close();
        }}
      >
        Delete <span className={kbd}>⌫</span>
      </button>
    </div>
  );
}

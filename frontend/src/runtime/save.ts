// One save path used by the Save button, ⌘S and the 1s autosave so the
// authored topology AND the workspace name are always persisted together
// and the dirty flag is cleared in exactly one place.
import { useStore } from "@/store";
import { serializeTopology } from "@/lib/topologySchema";
import { saveLocal, saveWorkspaceName } from "@/lib/persistence";

export function saveWorkspace(): void {
  const s = useStore.getState();
  saveLocal(serializeTopology(s));
  saveWorkspaceName(s.workspaceName);
  s.markClean();
}

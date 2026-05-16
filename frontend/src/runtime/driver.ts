import type { useStore } from "@/store";

// The single seam between the UI and any data source. The mock driver
// implements this now; a socket driver will implement the identical interface
// next phase (targeting the existing, unchanged nginx `/socket.io/` proxy).
export interface RuntimeDriver {
  start(store: typeof useStore): void;
  stop(): void;
}

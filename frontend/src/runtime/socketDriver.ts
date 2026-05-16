import type { RuntimeDriver } from "./driver";

// STUB — next phase. The Flask backend + nginx `/socket.io/` proxy already
// exist and are unchanged. This driver will subscribe to the existing event
// stream (state_update, object_update, arm_health, asset_health, log_message)
// and write into the runtime + spatial slices using the SAME slice actions the
// mock driver uses, so no component changes are required to go live.
export function createSocketDriver(): RuntimeDriver {
  return {
    start() {
      // import { io } from "socket.io-client";
      // const sock = io({ path: "/socket.io" });
      // sock.on("state_update", ...) -> store.getState().setActorRuntime(...)
      throw new Error("socketDriver not wired yet — UI-only phase uses mockDriver");
    },
    stop() {},
  };
}

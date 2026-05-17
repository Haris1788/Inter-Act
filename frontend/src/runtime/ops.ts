// Operational-control seam. Components invoke runtime actions through these
// thin delegates instead of holding a driver reference. App registers the
// active driver (mock or socket) once it is created; on teardown it clears
// it. Keeps the UI ignorant of which driver is live (the Phase 1 seam).

import type { RuntimeDriver, ActorOpResult } from "./driver";

let active: RuntimeDriver | null = null;

export function setActiveDriver(d: RuntimeDriver | null): void {
  active = d;
}

const NO_DRIVER: ActorOpResult = {
  ok: false,
  note: "runtime driver not ready",
};

export function opStopActor(actorId: string): Promise<ActorOpResult> {
  return active ? active.stopActor(actorId) : Promise.resolve(NO_DRIVER);
}

export function opRestartActor(actorId: string): Promise<ActorOpResult> {
  return active ? active.restartActor(actorId) : Promise.resolve(NO_DRIVER);
}

export function opRefreshRuntime(): Promise<void> {
  return active ? active.refreshRuntime() : Promise.resolve();
}

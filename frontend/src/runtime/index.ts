import type { RuntimeDriver } from "./driver";
import { createMockDriver } from "./mockDriver";
import { createSocketDriver } from "./socketDriver";

// Single, isolated swap point between mock and live runtime (the seam the
// Phase 1 architecture promised). Mode:
//   VITE_RUNTIME=live  → always socket driver
//   VITE_RUNTIME=mock  → always prototype mock
//   (default / auto)   → probe /config; live if backend reachable, else mock
//                        so the UI still demos standalone.
const MODE = (import.meta.env.VITE_RUNTIME as string | undefined) ?? "auto";

async function backendReachable(): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 1500);
    const r = await fetch("/config", { signal: ctrl.signal });
    clearTimeout(t);
    return r.ok;
  } catch {
    return false;
  }
}

export async function createRuntimeDriver(): Promise<RuntimeDriver> {
  if (MODE === "mock") return createMockDriver();
  if (MODE === "live") return createSocketDriver();
  return (await backendReachable()) ? createSocketDriver() : createMockDriver();
}

export type { RuntimeDriver };

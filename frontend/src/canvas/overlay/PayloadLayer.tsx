// PayloadLayer (Sneh-port adapted to our backend).
//
// One Konva layer renders every active package. Each payload's draw position
// comes from its current `carrier`:
//   carrier = "agv-N"            → live AGV kinematics pos (interpolated)
//   carrier = "arm-N"            → tip of that arm (from station + angle)
//   carrier = "zone:input_arm"   → input arm's TABLE centre
//   carrier = "zone:output_arm"  → output arm's TABLE centre
//   carrier = null               → don't render
//
// Why this exists: with three separate slices (tablePkgs / armCargo / agvCargo)
// any state-update lag duplicates the package across renders. One unified
// layer + one carrier means the package can only ever be in ONE place at a
// time — which fixes the "package on table AND on AGV" smoothness bug.

import { useEffect, useState } from "react";
import { Layer, Rect } from "react-konva";

import type { ViewportTransform as Transform } from "./useViewportTransform";
import {
  useAgvKinematics,
  useArmAngles,
  usePayloads,
} from "@/store/selectors";

interface StationGeo {
  name: string;            // "input_arm" | "output_arm"
  armKey: string;          // "arm-1" | "arm-2"
  pivotX: number;
  pivotY: number;
  reach: number;
  tableCX: number;
  tableCY: number;
}

export function PayloadLayer({ tx }: { tx: Transform }) {
  const payloads = usePayloads();
  const armAngles = useArmAngles();
  const kin = useAgvKinematics();
  const [stations, setStations] = useState<StationGeo[]>([]);
  const [cellPx, setCellPx] = useState<number>(40);

  useEffect(() => {
    fetch("/config")
      .then((r) => r.json())
      .then((cfg) => {
        const wh = cfg.warehouse;
        if (!wh?.stations) return;
        const cp = wh.grid?.cell_px ?? 40;
        setCellPx(cp);
        const result: StationGeo[] = [];
        for (const [name, raw] of Object.entries(wh.stations)) {
          const s = raw as {
            px: { x: number; y: number };
            dock_px?: { x: number; y: number };
            served_by?: string;
          };
          if (!s.px) continue;
          const dock = s.dock_px ?? s.px;
          const dx = dock.x - s.px.x;
          const dy = dock.y - s.px.y;
          const reach = Math.hypot(dx, dy) || cp;
          // Table is the mirror of dock across the arm pivot.
          const tableCX = s.px.x - dx;
          const tableCY = s.px.y - dy;
          result.push({
            name,
            armKey: s.served_by ?? "",
            pivotX: s.px.x,
            pivotY: s.px.y,
            reach,
            tableCX,
            tableCY,
          });
        }
        setStations(result);
      })
      .catch(() => {});
  }, []);

  const PKG = cellPx * 0.26;  // small enough to sit visibly on top of the AGV

  const items: { id: string; x: number; y: number; color: string }[] = [];

  for (const [id, p] of Object.entries(payloads)) {
    if (!p.carrier) continue;
    let x = 0, y = 0, found = false;

    if (p.carrier.startsWith("zone:")) {
      const stationName = p.carrier.slice(5);
      const stn = stations.find((s) => s.name === stationName);
      if (stn) {
        x = stn.tableCX;
        y = stn.tableCY;
        found = true;
      }
    } else if (p.carrier.startsWith("arm-")) {
      const stn = stations.find((s) => s.armKey === p.carrier);
      if (stn) {
        const angle = armAngles[p.carrier] ?? 270;
        const rad = (angle * Math.PI) / 180;
        x = stn.pivotX + Math.cos(rad) * stn.reach;
        y = stn.pivotY + Math.sin(rad) * stn.reach;
        found = true;
      }
    } else if (p.carrier.startsWith("agv-")) {
      const k = kin[p.carrier];
      if (k) {
        x = k.pos.x;
        y = k.pos.y;
        found = true;
      }
    }

    if (found) items.push({ id, x, y, color: p.color });
  }

  if (items.length === 0) return null;

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {items.map((it) => (
        <Rect
          key={it.id}
          x={it.x - PKG / 2}
          y={it.y - PKG / 2}
          width={PKG}
          height={PKG}
          cornerRadius={3}
          fill={it.color}
          stroke="rgba(0,0,0,0.65)"
          strokeWidth={1.5}
          shadowColor="rgba(0,0,0,0.5)"
          shadowBlur={6}
          shadowOffset={{ x: 0, y: 2 }}
          shadowOpacity={0.6}
        />
      ))}
    </Layer>
  );
}

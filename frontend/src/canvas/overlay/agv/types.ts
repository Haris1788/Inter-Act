// Shared prop type for all three AGV silhouette components.

export interface AgvSilhouetteProps {
  /** Canvas px, already transformed by the Konva stage. */
  x: number;
  y: number;
  /** Heading in radians. 0 = facing right (+x). */
  theta: number;
  /** Body fill colour. */
  color: string;
  isSelected: boolean;
  /** True while the AGV carries a package — shows cargo indicator on body. */
  carried: boolean;
  /** Raw backend phase/status string — used for status dot colour. */
  status: string;
}

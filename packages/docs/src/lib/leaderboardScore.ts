/**
 * Speed, the one leaderboard number the exporter does not blend: a word on
 * fixed anchors and a gauge fill. The blended scores (extraction, chat,
 * overall) come from scripts/benchmark/export_leaderboard.py in the data file.
 */

/** Speed tiers on fixed anchors (median seconds per probe), so a label never moves when the field changes. */
export const SPEED_TIERS: readonly { label: string; maxSeconds: number }[] = [
  { label: "Fast", maxSeconds: 15 },
  { label: "Moderate", maxSeconds: 45 },
  { label: "Slow", maxSeconds: 120 },
  { label: "Very slow", maxSeconds: Infinity },
];

export function speedLabel(ms: number): string {
  const s = ms / 1000;
  return SPEED_TIERS.find((t) => s < t.maxSeconds)!.label;
}

/** Gauge fill 0..1 on a log scale: 4 s per probe fills the bar, 250 s empties it. */
export function speedFill(ms: number): number {
  const s = Math.min(Math.max(ms / 1000, 4), 250);
  return 1 - Math.log(s / 4) / Math.log(250 / 4);
}

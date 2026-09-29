import type { Mover } from "./models";
const proofs = (globalThis.showcaseProofs ??= new Map<string, Mover>());
export function rememberProofs(rows: Mover[]) {
  for (const row of rows) {
    const key = `${row.ranking_build}:${row.song_key}`;
    if (!proofs.has(key) && proofs.size >= 500)
      proofs.delete(proofs.keys().next().value!);
    proofs.set(key, row);
  }
}
export function capturedProof(ranking: string, song: string) {
  return proofs.get(`${ranking}:${song}`);
}
import type { Build } from "../components/number";
const histories = (globalThis.showcaseHistoryProofs ??= new Map<
  string,
  { build: Build; days: Set<string> }
>());
export function rememberHistory(
  song: string,
  build: Build,
  days: { day: string }[],
) {
  const key = `${build.built_at}:${song}`;
  if (!histories.has(key) && histories.size >= 100)
    histories.delete(histories.keys().next().value!);
  histories.set(key, { build, days: new Set(days.map((day) => day.day)) });
}
export function capturedHistory(song: string, stamp: string, day: string) {
  const history = histories.get(`${stamp}:${song}`);
  return history?.days.has(day) ? history.build : null;
}

declare global {
  var showcaseProofs: Map<string, Mover> | undefined;
  var showcaseHistoryProofs:
    Map<string, { build: Build; days: Set<string> }> | undefined;
}

import type { Build } from "../components/number";

// A call records one closed read, even when its inputs come from separate caches.
export function consistentCallBuilds(
  builds: readonly Build[],
  closeNo = builds[0]?.close_no,
) {
  const card = builds[0];
  return (
    !!card &&
    card.cycle_id !== null &&
    card.close_no !== null &&
    card.close_no === closeNo &&
    builds.every(
      (build) =>
        build.stamped &&
        build.built_at !== null &&
        build.cycle_id === card.cycle_id &&
        build.close_no === card.close_no,
    )
  );
}

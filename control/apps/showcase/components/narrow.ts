"use client";
import { useSyncExternalStore } from "react";
// One picture per width. A hidden copy would still count toward the phone word budget.
const query = "(max-width: 899px)";
export function useNarrow() {
  return useSyncExternalStore(
    (notify) => {
      const media = window.matchMedia(query);
      media.addEventListener("change", notify);
      return () => media.removeEventListener("change", notify);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

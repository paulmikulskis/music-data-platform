"use client";
import { useEffect } from "react";

export function ClearLink({
  reason,
}: {
  reason: "expired" | "timeout" | "key-refused" | "ended" | null;
}) {
  useEffect(() => {
    // Keep only recovery guidance, so reloads never need a token in browser storage.
    const path = reason ? `/sign-in?reason=${reason}` : "/sign-in";
    window.history.replaceState(null, "", path);
  }, [reason]);
  return null;
}

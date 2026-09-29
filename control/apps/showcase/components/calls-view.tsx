"use client";
import { useEffect } from "react";
export function CallsView({ csrf, week }: { csrf: string; week: string }) {
  useEffect(() => {
    void fetch("/calls/view", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-csrf-token": csrf },
      body: JSON.stringify({ week }),
    }).catch(() => {});
  }, [csrf, week]);
  return null;
}

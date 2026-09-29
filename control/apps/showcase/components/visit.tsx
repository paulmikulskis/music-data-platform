"use client";
import { useEffect, useState } from "react";
export function Visit({ csrf, changed }: { csrf: string; changed: boolean }) {
  const [shown, setShown] = useState(changed);
  useEffect(() => {
    void fetch("/visit", { method: "POST", headers: { "x-csrf-token": csrf } });
  }, [csrf]);
  return shown ? (
    <button
      className="visit-toast"
      onClick={() => setShown(false)}
      aria-label="Dismiss visit update"
    >
      Since your last visit · Dismiss
    </button>
  ) : null;
}

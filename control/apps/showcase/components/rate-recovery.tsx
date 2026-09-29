"use client";
import { useEffect } from "react";

export function RateRecovery() {
  useEffect(() => {
    try {
      sessionStorage.removeItem("showcase-rate-retry");
    } catch {}
  }, []);
  return null;
}

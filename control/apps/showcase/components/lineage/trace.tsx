"use client";
import { useMemo } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
const Viewer = dynamic(() => import("./viewer"), { ssr: false });
export function Trace() {
  const query = useSearchParams();
  const entry = query.get("trace");
  const song = query.get("song") ?? undefined;
  const ranking = query.get("ranking") ?? undefined;
  const source = query.get("source") ?? undefined;
  const selection = useMemo(
    () => ({ entry: entry ?? "", song, ranking, source }),
    [entry, song, ranking, source],
  );
  if (!entry) return null;
  return <Viewer key={JSON.stringify(selection)} selection={selection} />;
}

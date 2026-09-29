"use client";
import { useEffect, useState } from "react";
import { animate, useReducedMotion } from "motion/react";
import { Number, type Provenance } from "./number";
import { CopySQL } from "./copy-sql";
import { Sheet } from "./sheet";
import { Hover } from "./hover";
import { HowCard, HowSheet } from "./how-we-know";
// Every number says how we know it: a hover card, and on tap a sheet with the trail.
export function Metric({
  value,
  label,
  provenance,
  inline = false,
  onOpen,
}: {
  value: string | null;
  label: string;
  provenance?: Provenance;
  inline?: boolean;
  onOpen?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [animated, setAnimated] = useState({ source: value, display: value });
  const reduced = useReducedMotion();
  useEffect(() => {
    const key = `number:${label}:${provenance?.build?.relation ?? "control"}`;
    const numeric =
      value === null ? NaN : parseFloat(value.replace(/[$,]/g, ""));
    if (
      reduced ||
      !globalThis.Number.isFinite(numeric) ||
      !globalThis.Number.isSafeInteger(Math.round(numeric)) ||
      sessionStorage.getItem(key)
    )
      return;
    sessionStorage.setItem(key, "1");
    const animation = animate(0, numeric, {
      duration: 0.22,
      onComplete: () => setAnimated({ source: value, display: value }),
      onUpdate: (n) =>
        setAnimated({
          source: value,
          display: value?.startsWith("$")
            ? `$${n.toFixed(2)}`
            : Math.round(n).toLocaleString("en-US"),
        }),
    });
    return () => animation.stop();
  }, [value, label, provenance?.build?.relation, reduced]);
  return (
    <>
      <Hover label="How we know" card={<HowCard provenance={provenance} />}>
        <button
          className={inline ? "metric-inline" : "metric-button"}
          onClick={() => (onOpen ? onOpen() : setOpen(true))}
          aria-label={`${label}: ${value ?? "unknown"}. How we know`}
        >
          <Number
            value={animated.source === value ? animated.display : value}
            label={label}
            provenance={provenance}
            compact
          />
        </button>
      </Hover>
      {open && (
        <Sheet title="How we know" close={() => setOpen(false)}>
          <HowSheet label={label} provenance={provenance} />
          {provenance && (
            <details>
              <summary>For analysts</summary>
              <p>
                {provenance.cadence ?? provenance.provenance ?? "live query"}
                {provenance.build?.close_no
                  ? ` #${provenance.build.close_no}`
                  : ""}{" "}
                · {provenance.scope}
              </p>
              <CopySQL sql={provenance.sql} kind={provenance.copyKind} />
              <pre>{provenance.sql}</pre>
            </details>
          )}
        </Sheet>
      )}
    </>
  );
}

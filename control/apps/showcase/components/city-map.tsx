"use client";
import { motion, useReducedMotion } from "motion/react";
import type { Place } from "./number";
import { landColumns, landNorth, landRows, landStep } from "../lib/world-land";
// Land drawn once as round dots, one per 3-degree cell.
let land: string | undefined;
function landPath() {
  return (land ??= landRows
    .flatMap((hex, row) =>
      [...hex].flatMap((digit, i) => {
        const bits = parseInt(digit, 16);
        return [0, 1, 2, 3]
          .filter((bit) => bits & (8 >> bit))
          .map((bit) => `M${i * 4 + bit + 0.5} ${row + 0.5}h0`);
      }),
    )
    .join(""));
}
const x = (lon: number) => (lon + 180) / landStep;
const y = (lat: number) => (landNorth - lat) / landStep;
// A small world with a lit dot for every place, lit in the order it arrives.
// caption={null} leaves the names to the surrounding sheet.
export function CityMap({
  places,
  caption,
}: {
  places: Place[];
  caption?: null;
}) {
  const reduced = useReducedMotion();
  return (
    <figure className="city-map">
      <svg
        viewBox={`0 0 ${landColumns} ${landRows.length}`}
        role="img"
        aria-label={`Map of ${places.map((place) => place.name).join(", ")}`}
      >
        <path className="land" d={landPath()} />
        {places.map((place, i) => (
          <motion.circle
            key={`${place.name}:${i}`}
            cx={x(place.lon)}
            cy={y(place.lat)}
            r="1.1"
            className="place"
            initial={reduced ? false : { opacity: 0, scale: 0.4 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.2, delay: reduced ? 0 : i * 0.06 }}
          >
            <title>{place.name}</title>
          </motion.circle>
        ))}
      </svg>
      {caption !== null && (
        <figcaption>
          {places
            .slice(0, 4)
            .map((place) => place.name)
            .join(" · ")}
          {places.length > 4 ? ` · ${places.length - 4} more` : ""}
        </figcaption>
      )}
    </figure>
  );
}

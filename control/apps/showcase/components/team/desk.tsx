"use client";
import { linkHref, type LinkPreview } from "../../lib/links";
import { markFor } from "@mdp/contracts/flows";
import { useNarrow } from "../narrow";
// The analyst's desk: SQL, R and Python on a laptop, one line to the ready tables, and the
// reviewed change that puts a new number on a screen like this one. Hand-drawn, static.
const tools = ["postgresql", "r", "python", "dbt"];
function Tool({ name, x, y }: { name: string; x: number; y: number }) {
  const mark = markFor(name);
  if (!mark) return null;
  return (
    <a
      href={mark.href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`Open ${mark.label}`}
    >
      {name === "postgresql" && (
        <rect
          x={x - 8}
          y={y - 8}
          width="44"
          height="44"
          rx="4"
          className="tile"
        />
      )}
      {mark.src ? (
        <image href={mark.src} x={x} y={y} width="28" height="28" />
      ) : (
        <text x={x} y={y + 20}>
          {mark.label}
        </text>
      )}
    </a>
  );
}
function Cylinder({
  x,
  y,
  w,
  h,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
}) {
  const ry = 9;
  return (
    <g className="cylinder">
      <path
        d={`M${x} ${y + ry} v${h - 2 * ry} a${w / 2} ${ry} 0 0 0 ${w} 0 v-${h - 2 * ry}`}
      />
      <ellipse cx={x + w / 2} cy={y + ry} rx={w / 2} ry={ry} />
      <path d={`M${x} ${y + h / 2} a${w / 2} ${ry} 0 0 0 ${w} 0`} />
    </g>
  );
}
type DeskLinks = Record<"cylinder" | "laptop" | "screen", LinkPreview | null>;
export function TeamDesk({ links }: { links: DeskLinks }) {
  const narrow = useNarrow();
  return (
    <figure className="team-desk" aria-label="Analyst onboarding">
      {narrow ? <TallDesk links={links} /> : <WideDesk links={links} />}
    </figure>
  );
}
function WideDesk({ links }: { links: DeskLinks }) {
  return (
    <svg
      className="desk-wide"
      viewBox="0 0 1000 300"
      role="img"
      aria-label="A laptop with SQL, R, Python and dbt connects to the ready tables; a reviewed change puts a new number on this screen."
    >
      <text x="20" y="28" className="eyebrow-text">
        An analyst’s desk
      </text>
      <text x="470" y="28" className="eyebrow-text">
        Ready to use
      </text>
      <text x="760" y="28" className="eyebrow-text">
        On your screen
      </text>
      <g className="draw">
        <rect
          x="40"
          y="60"
          width="300"
          height="170"
          rx="6"
          className="laptop"
        />
        <path d="M20 232h340l14 22H6z" />
        <path d="M340 145 H 470" />
        <path d="M600 145 H 760" />
        <path d="M840 240 C 840 285, 190 285, 190 254" strokeDasharray="4 4" />
      </g>
      <g className="node">
        <rect x="760" y="70" width="200" height="150" rx="6" />
        <rect
          x="778"
          y="92"
          width="80"
          height="6"
          rx="3"
          className="screen-line"
        />
        <rect
          x="778"
          y="110"
          width="150"
          height="4"
          rx="2"
          className="screen-line"
        />
        <rect
          x="778"
          y="124"
          width="120"
          height="4"
          rx="2"
          className="screen-line"
        />
        <rect
          x="778"
          y="160"
          width="40"
          height="40"
          rx="3"
          className="screen-line"
        />
        <rect
          x="826"
          y="160"
          width="40"
          height="40"
          rx="3"
          className="screen-line"
        />
        <text x="880" y="188" className="accent-text">
          new number
        </text>
      </g>
      <Cylinder x={470} y={80} w={130} h={130} />
      <text x="535" y="236" textAnchor="middle" className="node-name">
        the same numbers
      </text>
      {tools.map((name, i) => (
        <Tool key={name} name={name} x={72 + i * 66} y={92} />
      ))}
      <text x="64" y="160" className="node-name">
        SQL · R · Python · dbt
      </text>
      <text x="64" y="182" className="node-kind">
        DuckDB, RStudio, DBeaver or Jupyter
      </text>
      <text x="64" y="204" className="node-kind">
        A practice copy runs on the laptop.
      </text>
      <text x="380" y="135" className="node-kind">
        personal login
      </text>
      <text x="640" y="135" className="node-kind">
        every night
      </text>
      <text x="380" y="282" className="node-kind">
        a reviewed change puts a new number on screen
      </text>
      <a
        href={links.laptop ? (linkHref(links.laptop) ?? undefined) : undefined}
        aria-label="Open practice steps"
      >
        <rect className="desk-hit" x="60" y="123" width="260" height="70" />
      </a>
      <a
        href={
          links.cylinder ? (linkHref(links.cylinder) ?? undefined) : undefined
        }
        aria-label="Open warehouse on Stack"
      >
        <rect className="desk-hit" x="465" y="70" width="150" height="180" />
      </a>
      <a
        href={links.screen ? (linkHref(links.screen) ?? undefined) : undefined}
        aria-label="Open proposal steps"
      >
        <rect className="desk-hit" x="760" y="70" width="200" height="150" />
      </a>
    </svg>
  );
}
function TallDesk({ links }: { links: DeskLinks }) {
  return (
    <svg
      className="desk-tall"
      viewBox="0 0 390 560"
      role="img"
      aria-label="A laptop with SQL, R, Python and dbt connects to the ready tables; a reviewed change puts a new number on this screen."
    >
      <text x="20" y="24" className="eyebrow-text">
        An analyst’s desk
      </text>
      <g className="draw">
        <rect
          x="60"
          y="44"
          width="270"
          height="130"
          rx="6"
          className="laptop"
        />
        <path d="M44 176h302l12 18H32z" />
        <path d="M195 194 V 250" />
        <path d="M195 402 V 420" />
        <path d="M60 480 C 20 480, 20 210, 44 210" strokeDasharray="4 4" />
      </g>
      {tools.map((name, i) => (
        <Tool key={name} name={name} x={90 + i * 60} y={70} />
      ))}
      <Cylinder x={130} y={250} w={130} h={120} />
      <text x="195" y="394" textAnchor="middle" className="node-name">
        same numbers
      </text>
      <g className="node">
        <rect x="60" y="420" width="270" height="110" rx="6" />
        <rect
          x="78"
          y="440"
          width="80"
          height="6"
          rx="3"
          className="screen-line"
        />
        <rect
          x="78"
          y="458"
          width="200"
          height="4"
          rx="2"
          className="screen-line"
        />
        <rect
          x="78"
          y="486"
          width="34"
          height="34"
          rx="3"
          className="screen-line"
        />
        <rect
          x="120"
          y="486"
          width="34"
          height="34"
          rx="3"
          className="screen-line"
        />
        <text x="170" y="510" className="accent-text">
          new number
        </text>
      </g>
      <text x="72" y="552" className="node-kind">
        a reviewed change, then on screen
      </text>
      <a
        href={links.laptop ? (linkHref(links.laptop) ?? undefined) : undefined}
        aria-label="Open practice steps"
      >
        <rect className="desk-hit" x="60" y="123" width="260" height="70" />
      </a>
      <a
        href={
          links.cylinder ? (linkHref(links.cylinder) ?? undefined) : undefined
        }
        aria-label="Open warehouse on Stack"
      >
        <rect className="desk-hit" x="125" y="245" width="140" height="155" />
      </a>
      <a
        href={links.screen ? (linkHref(links.screen) ?? undefined) : undefined}
        aria-label="Open proposal steps"
      >
        <rect className="desk-hit" x="60" y="420" width="270" height="110" />
      </a>
    </svg>
  );
}

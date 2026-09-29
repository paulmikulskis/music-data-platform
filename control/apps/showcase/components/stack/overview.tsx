"use client";
import { useEffect, useRef } from "react";
import { markFor } from "@mdp/contracts/flows";
import { useNarrow } from "../narrow";
// One hand-drawn picture: outside sources flow into the platform's servers and databases, and the
// ready numbers flow out to the platform's apps. Hairlines, one accent, no chart library. It draws on
// once when scrolled into view; reduced motion shows the finished picture.
const sources = [
  "apple_music",
  "spotify",
  "shazam",
  "billboard",
  "deezer",
];
type Kind = "server" | "database" | "timer";
type Node = {
  id: string;
  name: string;
  kind: Kind;
  x: number;
  y: number;
  w: number;
  h: number;
  // A name split over two lines where one line would run past the box.
  lines?: [string, string];
};
function Glyph({ kind, x, y }: { kind: Kind; x: number; y: number }) {
  if (kind === "database")
    return (
      <g transform={`translate(${x} ${y})`} className="glyph">
        <ellipse cx="9" cy="3" rx="8" ry="3" />
        <path d="M1 3v10c0 1.7 3.6 3 8 3s8-1.3 8-3V3" />
        <path d="M1 8c0 1.7 3.6 3 8 3s8-1.3 8-3" />
      </g>
    );
  if (kind === "timer")
    return (
      <g transform={`translate(${x} ${y})`} className="glyph">
        <circle cx="9" cy="9" r="8" />
        <path d="M9 4v5l3 2" />
      </g>
    );
  return (
    <g transform={`translate(${x} ${y})`} className="glyph">
      <rect x="1" y="1" width="16" height="6" rx="1.5" />
      <rect x="1" y="10" width="16" height="6" rx="1.5" />
      <circle cx="4" cy="4" r="0.9" fill="currentColor" />
      <circle cx="4" cy="13" r="0.9" fill="currentColor" />
    </g>
  );
}
function Box({
  node,
  kindLabel,
  accent = false,
  dashed = false,
  sub,
}: {
  node: Node;
  kindLabel: boolean;
  accent?: boolean;
  dashed?: boolean;
  sub?: string;
}) {
  const label = {
    server: "Server",
    database: "Database",
    timer: "On a timer",
  }[node.kind];
  return (
    <a href={`#${node.id}`} className="overview-node">
      <g className={`node ${accent ? "accent" : ""}`}>
        <rect
          x={node.x}
          y={node.y}
          width={node.w}
          height={node.h}
          rx="4"
          strokeDasharray={dashed ? "4 4" : undefined}
        />
        <Glyph kind={node.kind} x={node.x + 12} y={node.y + 12} />
        {node.lines ? (
          <text x={node.x + 38} y={node.y + 19} className="node-name">
            <tspan x={node.x + 38}>{node.lines[0]}</tspan>
            <tspan x={node.x + 38} dy="15">
              {node.lines[1]}
            </tspan>
          </text>
        ) : (
          <text x={node.x + 38} y={node.y + 25} className="node-name">
            {node.name}
          </text>
        )}
        {kindLabel && (
          <text x={node.x + 38} y={node.y + 41} className="node-kind">
            {sub ?? label}
          </text>
        )}
      </g>
    </a>
  );
}
function useDrawOn(picture: boolean) {
  const ref = useRef<SVGSVGElement>(null);
  const drawn = useRef(false);
  // The server renders the finished picture. On mount the lines hide through the DOM attribute
  // alone, and the first time the picture scrolls into view they draw on once. A picture that has
  // drawn stays drawn through any later render, width change or scroll. Reduced motion keeps the
  // finished picture from the start.
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof IntersectionObserver === "undefined") return;
    if (
      drawn.current ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      drawn.current = true;
      element.dataset.state = "drawn";
      return;
    }
    element.dataset.state = "armed";
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          drawn.current = true;
          element.dataset.state = "drawn";
          observer.disconnect();
        }
      },
      { threshold: 0.15 },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [picture]);
  return ref;
}

function SourceMark({
  name,
  x,
  y,
  withName,
}: {
  name: string;
  x: number;
  y: number;
  withName: boolean;
}) {
  const mark = markFor(name);
  if (!mark) return null;
  return (
    <a
      href={mark.href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`Open ${mark.label}`}
      className="overview-mark"
    >
      {mark.src ? (
        <>
          {mark.plate && (
            <rect
              x={x - 3}
              y={y - 15}
              width="30"
              height="30"
              rx="5"
              className="plate"
            />
          )}
          <image href={mark.src} x={x} y={y - 12} width="24" height="24" />
        </>
      ) : (
        <text x={x} y={y + 4} className="node-name">
          {mark.label}
        </text>
      )}
      {withName && (
        <text x={x + 34} y={y + 4} className="node-name">
          {mark.label}
        </text>
      )}
    </a>
  );
}
// The hosting mark links to its provider like every other mark on the page.
function FlyMark({
  x,
  y,
  size,
  text,
}: {
  x: number;
  y: number;
  size: number;
  text: string;
}) {
  const mark = markFor("fly");
  if (!mark?.src) return null;
  return (
    <a
      href={mark.href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`Open ${mark.label}`}
      className="overview-mark caption"
    >
      <image href={mark.src} x={x} y={y} width={size} height={size} />
      <text x={x + size + 8} y={y + size - 4} className="node-kind">
        {text}
      </text>
    </a>
  );
}
export function StackOverview() {
  const narrow = useNarrow();
  const svgRef = useDrawOn(narrow);
  return (
    <figure className="stack-overview" aria-label="How the servers connect">
      {narrow ? (
        <TallOverview svgRef={svgRef} />
      ) : (
        <WideOverview svgRef={svgRef} />
      )}
      <figcaption>
        Solid lines run today. A dashed line is planned. Tap a box for its card.
      </figcaption>
    </figure>
  );
}
type OverviewProps = { svgRef: React.RefObject<SVGSVGElement | null> };
function WideOverview({ svgRef }: OverviewProps) {
  const clock: Node = {
    id: "clock",
    name: "Clock",
    kind: "timer",
    x: 330,
    y: 168,
    w: 120,
    h: 54,
  };
  const readers: Node = {
    id: "readers",
    name: "Readers",
    kind: "server",
    x: 476,
    y: 168,
    w: 120,
    h: 54,
  };
  const warehouse: Node = {
    id: "warehouse",
    name: "Warehouse",
    kind: "database",
    x: 622,
    y: 168,
    w: 120,
    h: 54,
  };
  const api: Node = {
    id: "data-api",
    name: "Data API",
    kind: "server",
    x: 768,
    y: 168,
    w: 110,
    h: 54,
  };
  const brainz: Node = {
    id: "musicbrainz",
    name: "MusicBrainz copy",
    kind: "database",
    x: 600,
    y: 286,
    w: 164,
    h: 54,
  };
  // Wide enough for their sub-labels; the browser walk measures every label against its box.
  const app: Node = {
    id: "this-app",
    name: "This app",
    kind: "server",
    x: 990,
    y: 60,
    w: 200,
    h: 54,
  };
  const rows = [60, 112, 164, 216, 268, 320];
  return (
    <svg
      ref={svgRef}
      className="overview-wide"
      viewBox="0 0 1200 420"
      role="img"
      aria-label="Outside sources flow into the platform's data platform, and its numbers flow out to the platform's apps."
      data-state="idle"
    >
      <text x="20" y="34" className="eyebrow-text">
        Outside
      </text>
      <text x="300" y="34" className="eyebrow-text">
        Music Data Platform
      </text>
      <text x="990" y="34" className="eyebrow-text">
        the platform's apps
      </text>
      <g className="draw">
        <rect
          x="300"
          y="46"
          width="612"
          height="344"
          rx="8"
          className="frame"
          pathLength={1}
        />
        {rows.map((y) => (
          <path
            key={y}
            d={`M176 ${y} C 240 ${y}, 240 195, 300 195`}
            pathLength={1}
          />
        ))}
        <path d="M450 195h26" pathLength={1} />
        <path d="M596 195h26" pathLength={1} />
        <path d="M742 195h26" pathLength={1} />
        <path d="M682 286v-64" pathLength={1} />
        <path d="M878 195 C 934 195, 934 87, 990 87" pathLength={1} />
        <path
          d="M878 195 C 934 195, 934 217, 990 217"
          strokeDasharray="4 4"
          pathLength={1}
        />
        <path d="M1090 244v78" pathLength={1} />
      </g>
      {sources.map((name, i) => (
        <SourceMark key={name} name={name} x={20} y={rows[i] ?? 60} withName />
      ))}
      <Box node={clock} kindLabel />
      <Box node={readers} kindLabel />
      <Box node={warehouse} kindLabel accent />
      <Box node={api} kindLabel />
      <Box node={brainz} kindLabel />
      <Box node={app} kindLabel sub="Server · what you are reading" />
      <FlyMark
        x={320}
        y={360}
        size={18}
        text="Fly.io · New Jersey · private network"
      />
    </svg>
  );
}
function TallOverview({ svgRef }: OverviewProps) {
  const clock: Node = {
    id: "clock",
    name: "Clock",
    kind: "timer",
    x: 60,
    y: 250,
    w: 270,
    h: 48,
  };
  const readers: Node = {
    id: "readers",
    name: "Readers",
    kind: "server",
    x: 60,
    y: 320,
    w: 270,
    h: 48,
  };
  const warehouse: Node = {
    id: "warehouse",
    name: "Warehouse",
    kind: "database",
    x: 60,
    y: 390,
    w: 124,
    h: 48,
  };
  const brainz: Node = {
    id: "musicbrainz",
    name: "MusicBrainz copy",
    kind: "database",
    x: 196,
    y: 390,
    w: 134,
    h: 48,
    lines: ["MusicBrainz", "copy"],
  };
  const api: Node = {
    id: "data-api",
    name: "Data API",
    kind: "server",
    x: 60,
    y: 460,
    w: 270,
    h: 48,
  };
  const app: Node = {
    id: "this-app",
    name: "This app",
    kind: "server",
    x: 60,
    y: 600,
    w: 270,
    h: 48,
  };
  const columns = [40, 100, 160, 220, 280, 340];
  return (
    <svg
      ref={svgRef}
      className="overview-tall"
      viewBox="0 0 390 860"
      role="img"
      aria-label="Outside sources flow into the platform's data platform, and its numbers flow out to the platform's apps."
      data-state="idle"
    >
      <text x="20" y="26" className="eyebrow-text">
        Outside
      </text>
      <text x="40" y="188" className="eyebrow-text">
        Music Data Platform
      </text>
      <text x="40" y="576" className="eyebrow-text">
        the platform's apps
      </text>
      <g className="draw">
        <rect
          x="40"
          y="200"
          width="310"
          height="340"
          rx="8"
          className="frame"
          pathLength={1}
        />
        {columns.map((x) => (
          <path
            key={x}
            d={`M${x + 12} 76 C ${x + 12} 130, 195 130, 195 200`}
            pathLength={1}
          />
        ))}
        <path d="M195 298v22" pathLength={1} />
        <path d="M122 368v22" pathLength={1} />
        <path d="M263 368v22" pathLength={1} />
        <path d="M184 414h12" pathLength={1} />
        <path d="M122 438v22" pathLength={1} />
        <path d="M195 508v92" pathLength={1} />
        <path d="M195 648v42" strokeDasharray="4 4" pathLength={1} />
        <path d="M195 738v42" pathLength={1} />
      </g>
      {sources.map((name, i) => (
        <SourceMark
          key={name}
          name={name}
          x={columns[i] ?? 40}
          y={56}
          withName={false}
        />
      ))}
      <Box node={clock} kindLabel={false} />
      <Box node={readers} kindLabel={false} />
      <Box node={warehouse} kindLabel={false} accent />
      <Box node={brainz} kindLabel={false} />
      <Box node={api} kindLabel={false} />
      <Box node={app} kindLabel={false} />
      <FlyMark x={60} y={512} size={16} text="Fly.io · New Jersey" />
    </svg>
  );
}

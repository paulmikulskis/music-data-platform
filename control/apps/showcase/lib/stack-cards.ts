// Viewer copy for each deployed service. Aliases match ops/showcase/stack/apps.json.
// Counts here are dated hand facts; a measured count from the deploy artifact replaces them.
export type StackGroup = "platform";
export type StackCard = {
  alias: string;
  id: string;
  group: StackGroup;
  kind: "Server" | "Database" | "On a timer";
  name: string;
  tagline: string;
  count: { text: string; as_of: string } | null;
  marks: string[];
  more: string[];
  tie: string | null;
};
export const stackCards: StackCard[] = [
  {
    alias: "Warehouse",
    id: "warehouse",
    group: "platform",
    kind: "Database",
    name: "Warehouse",
    tagline: "Where everything read is kept, cleaned and made ready.",
    count: null,
    marks: ["postgresql"],
    more: [
      "Collected, cleaned and ready tables.",
      "Private network only. SQL access by allowlist.",
    ],
    tie: null,
  },
  {
    alias: "Readers",
    id: "readers",
    group: "platform",
    kind: "Server",
    name: "Readers",
    tagline:
      "Read the outside sources: Apple Music, Spotify, Shazam, Billboard, Deezer, MusicBrainz.",
    count: null,
    marks: ["python"],
    more: [
      "Collects playlists, charts and recording identifiers.",
      "Built, switched off: SoundCloud and Bandcamp.",
    ],
    tie: null,
  },
  {
    alias: "Workbench",
    id: "workbench",
    group: "platform",
    kind: "Server",
    name: "Workbench",
    tagline: "Runs an analyst's trial queries, apart from the readers.",
    count: null,
    marks: ["python", "duckdb"],
    more: [
      "Live: run a query, preview a change, compare two days.",
      "A proposal opens as a patch until code access is set.",
    ],
    tie: null,
  },
  {
    alias: "The clock",
    id: "clock",
    group: "platform",
    kind: "On a timer",
    name: "Clock",
    tagline: "Starts every hourly, daily and weekly read on its own.",
    count: null,
    marks: ["dbt", "python"],
    more: [
      "Live: hourly, nightly and weekly rounds.",
      "Each round reads, then rebuilds the ready tables.",
    ],
    tie: null,
  },
  {
    alias: "MusicBrainz copy",
    id: "musicbrainz",
    group: "platform",
    kind: "Database",
    name: "MusicBrainz copy",
    tagline:
      "A private copy of the open music catalog, used to match one song across platforms.",
    count: null,
    marks: ["musicbrainz"],
    more: [
      "Live: song matching reads it every hour.",
      "Refreshed by an operator command, not on a timer.",
    ],
    tie: null,
  },
  {
    alias: "Data API",
    id: "data-api",
    group: "platform",
    kind: "Server",
    name: "Data API",
    tagline:
      "Hands the ready numbers to applications, each with its rights attached.",
    count: null,
    marks: ["hono"],
    more: [
      "Live: this app reads through it.",
    ],
    tie: null,
  },
  {
    alias: "Console",
    id: "console",
    group: "platform",
    kind: "Server",
    name: "Console",
    tagline:
      "The data team's control panel: every source, every read, every alert.",
    count: null,
    marks: ["hono"],
    more: [
      "Live: sources, readings, alerts, Workbench and Explorer.",
      "Private; this app opens it with your own sign-in.",
    ],
    tie: null,
  },
  {
    alias: "Side door",
    id: "side-door",
    group: "platform",
    kind: "Server",
    name: "Side door",
    tagline: "A locked door to the warehouse for approved tools and addresses.",
    count: null,
    marks: [],
    more: [
      "Live: answers only approved addresses.",
      "Built for outside SQL tools.",
    ],
    tie: null,
  },
  {
    alias: "This app",
    id: "this-app",
    group: "platform",
    kind: "Server",
    name: "This app",
    tagline:
      "What you are looking at. Reads the ready numbers; sign-in by personal link.",
    count: null,
    marks: ["nextjs"],
    more: ["Live: the only public door into the data platform."],
    tie: null,
  },
];
export const stackGroups: { key: StackGroup; id: string; label: string }[] = [
  { key: "platform", id: "music-data-platform", label: "Data platform" },
];
// Text only: nothing is deployed for these. Each carries its evidence.
export const plannedStack = [
  {
    name: "Lake storage on object storage",
    state: "Installed, no tables yet",
    evidence: "pg_lake is installed; no lake table exists.",
  },
  {
    name: "Cloud dbt",
    state: "Designed, not used",
    evidence: "No cloud credentials are set.",
  },
  {
    name: "Snowflake",
    state: "No credentials",
    evidence: "An adapter swap is written down; nothing is deployed.",
  },
  {
    name: "Monitoring dashboards",
    state: "Configuration only",
    evidence: "A collector configuration exists; no app runs it.",
  },
];

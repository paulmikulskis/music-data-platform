import type { LinkPreview } from "../../lib/links";
import { LinkOut } from "../link-out";

export type TeamLinks = Record<string, LinkPreview | null>;
const steps = [
  { primary: "team-1-contributing", groups: [] },
  {
    primary: "team-2-practice",
    groups: [{ label: "Practice", ids: ["team-2-quickstart"] }],
  },
  {
    primary: "team-3-starters",
    groups: [
      {
        label: "R and Python",
        ids: [
          "team-3-notebook",
          "team-3-r-package",
          "team-3-rstudio",
          "team-3-r-guide",
        ],
      },
    ],
  },
  {
    primary: "team-4-workbench",
    groups: [
      {
        label: "Workbench",
        ids: ["team-4-guide", "team-4-page-code", "team-4-publisher"],
      },
    ],
  },
  {
    primary: "team-5-ship",
    groups: [
      {
        label: "SQL examples",
        ids: [
          "team-5-lands",
          "team-5-contracts",
          "team-5-example",
          "team-5-command",
          "team-5-view-to-table",
          "team-5-lift",
        ],
      },
      {
        label: "Proposal checks",
        ids: ["team-5-ready", "team-5-workflows", "team-5-sql-check"],
      },
      {
        label: "R projects",
        ids: ["team-5-r-form", "team-5-projects", "team-5-r-template"],
      },
    ],
  },
];
const layers = [
  { id: "cleaned", label: "Clean one source", path: "dbt/models/staging" },
  { id: "joined", label: "Join sources", path: "dbt/models/intermediate" },
  { id: "ready", label: "Serve a new number", path: "dbt/models/marts/global" },
  {
    id: "enriched",
    label: "Use enriched results",
    path: "dbt/models/marts/global",
  },
  { id: "rules", label: "Add rules or mappings", path: "dbt/seeds" },
];
export function StepLinks({
  index,
  links,
}: {
  index: number;
  links: TeamLinks;
}) {
  const step = steps[index];
  if (!step) return null;
  return (
    <div className="team-step-links">
      <LinkOut preview={links[step.primary] ?? null} />
      <details className="analyst-more">
        <summary>More for analysts</summary>
        {index === 0 && <a href="#sql-layers">Find the SQL folders →</a>}
        {step.groups.map((group) => {
          const content = group.ids.map((id) => (
            <LinkOut key={id} preview={links[id] ?? null} />
          ));
          return step.groups.length > 1 ? (
            <details key={group.label}>
              <summary>{group.label}</summary>
              {content}
            </details>
          ) : (
            <div key={group.label}>{content}</div>
          );
        })}
      </details>
    </div>
  );
}
export function SqlLayers({ links }: { links: TeamLinks }) {
  return (
    <section
      id="sql-layers"
      className="sql-layers"
      aria-label="Where the SQL lives"
    >
      <h2>Where the SQL lives</h2>
      <p>Add a SQL file in the folder for its job.</p>
      <ul>
        {layers.map((layer) => (
          <li key={layer.id}>
            <h3>{layer.label}</h3>
            <p data-proper-name>{layer.path}</p>
            <LinkOut preview={links[`team-layer-${layer.id}`] ?? null} />
          </li>
        ))}
      </ul>
      <details className="analyst-more">
        <summary>SQL help</summary>
        {["rights", "conventions", "glossary"].map((id) => (
          <LinkOut key={id} preview={links[`team-layer-${id}`] ?? null} />
        ))}
      </details>
    </section>
  );
}

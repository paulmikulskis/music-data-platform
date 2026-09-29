import { readLinks } from "../../server/links";
import { LinkOut } from "../../components/link-out";
import { StepLinks, SqlLayers } from "../../components/team/links";
import { room } from "../../server/room";
import { Shell } from "../../components/shell";
import { TeamDesk } from "../../components/team/desk";
import { TeamQuestion } from "../../components/team/question";
import { LoginRequest } from "../../components/team/login-request";
import { MarkCredits } from "../../components/stack/credits";
import { teamQuestions } from "../../lib/team-questions";
import { analystSteps } from "../../lib/analyst-steps";
export const dynamic = "force-dynamic";
export const metadata = { title: "Music Data Platform · Analyst onboarding" };
export default async function Team() {
  const current = await room();
  const question = teamQuestions[0]!;
  const reader = readLinks();
  const ids = [
    "team-1-contributing",
    "team-2-practice",
    "team-2-quickstart",
    "team-3-starters",
    "team-3-notebook",
    "team-3-r-package",
    "team-3-rstudio",
    "team-3-r-guide",
    "team-4-workbench",
    "team-4-guide",
    "team-4-page-code",
    "team-4-publisher",
    "team-5-ship",
    "team-5-lands",
    "team-5-contracts",
    "team-5-example",
    "team-5-command",
    "team-5-view-to-table",
    "team-5-lift",
    "team-5-ready",
    "team-5-workflows",
    "team-5-sql-check",
    "team-5-r-form",
    "team-5-projects",
    "team-5-r-template",
    "team-layer-cleaned",
    "team-layer-joined",
    "team-layer-ready",
    "team-layer-enriched",
    "team-layer-rules",
    "team-layer-rights",
    "team-layer-conventions",
    "team-layer-glossary",
  ];
  const links = Object.fromEntries(ids.map((id) => [id, reader.get(id)]));
  return (
    <Shell handle={current.handle} csrf={current.csrf_token}>
      <section className="team-page">
        <h1>Analyst onboarding</h1>
        <p className="page-line">SQL, R and Python on the same numbers.</p>
        <nav className="team-start" aria-label="Analyst starting points">
          <a href="#invite">Start here ↓</a>
          <a href="#sql-layers">SQL folders ↓</a>
        </nav>
        <TeamDesk links={{
          cylinder: reader.get("team-desk-anchors", "cylinder"),
          laptop: reader.get("team-desk-anchors", "laptop"),
          screen: reader.get("team-desk-anchors", "screen"),
        }} />
        <h2 className="section-label">From invite to a first answer</h2>
        <ol className="team-steps analyst-steps">
          {analystSteps.map((step, i) => (
            <li key={step.id} id={step.id}>
              <span className="step-number">{i + 1}</span>
              <h3>{step.title}</h3>
              <p>{step.text}</p>
              <StepLinks index={i} links={links} />
            </li>
          ))}
        </ol>
        <TeamQuestion
          id={question.id}
          question={question.question}
          sql={question.sql}
          code={reader.get("team-question-code")}
          workbench={reader.get("team-question-workbench")}
        />
        <section className="team-open" aria-label="What they can open">
          <h2 className="section-label">What they can open</h2>
          <p>Reviewed tables expose approved global fields with source and permission metadata.</p>
          <LoginRequest />
          <p>Staff Explorer access is separate and wider.</p>
          <LinkOut preview={reader.get("team-open-served")} />
          <LinkOut preview={reader.get("team-open-explorer")} />
          <SqlLayers links={links} />
        </section>
        <MarkCredits />
      </section>
    </Shell>
  );
}

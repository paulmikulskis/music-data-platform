import { room } from "../../server/room";
import { readArtifacts } from "../../server/artifacts";
import { readLinks } from "../../server/links";
import { stackView } from "../../server/stack";
import { Shell } from "../../components/shell";
import { StackOverview } from "../../components/stack/overview";
import { StackCards } from "../../components/stack/cards";
import { MarkCredits } from "../../components/stack/credits";
import { dayLabel } from "../../lib/presentation";
export const dynamic = "force-dynamic";
export const metadata = { title: "Music Data Platform · Stack" };
export default async function Stack() {
  const current = await room();
  const view = stackView(readArtifacts, readLinks());
  return (
    <Shell handle={current.handle} csrf={current.csrf_token}>
      <section className="stack-page">
        <h1>Servers and databases</h1>
        <p className="page-line">
          Deployment facts as of {dayLabel(view.as_of)}. Checks run when this
          page opens.
        </p>
        <StackOverview />
        <StackCards view={view} />
        <p className="team-link">
          <a href="/team">Analyst onboarding →</a>
        </p>
        <MarkCredits />
      </section>
    </Shell>
  );
}

import { marks } from "@mdp/contracts/marks";
// Trademark lines stay fixed; provider names and owners come from the shared registry.
export function MarkCredits() {
  return (
    <footer className="mark-credits">
      <p>
        No affiliation with the companies shown. Marks link to their owners.{" "}
        {marks.dbt.label} and the dbt logo are trademarks of {marks.dbt.owner}.
        Postgres, {marks.postgresql.label} and the Slonik Logo are trademarks or
        registered trademarks of the {marks.postgresql.owner}, and used with
        their permission. {marks.musicbrainz.label} and{" "}
        {marks.listenbrainz.label} ({marks.musicbrainz.owner}), and{" "}
        {marks.r.label} ({marks.r.owner}):{" "}
        <a
          href="https://creativecommons.org/licenses/by-sa/4.0/"
          target="_blank"
          rel="noopener noreferrer"
        >
          CC BY-SA 4.0
        </a>
        . {marks.wikipedia.label} ({marks.wikipedia.owner}):{" "}
        <a
          href="https://creativecommons.org/licenses/by-sa/3.0/"
          target="_blank"
          rel="noopener noreferrer"
        >
          CC BY-SA 3.0
        </a>
        . <a href="/sources?credits=1">All mark credits →</a>
      </p>
    </footer>
  );
}

import { Mark } from "../mark";
export function TeamPanel() {
  return (
    <section className="team-panel">
      <div>
        <h2>Analyst access</h2>
        <p>SQL, R and Python on the same numbers.</p>
        <a href="/team#try">Try a question →</a>
      </div>
      <div className="tool-tiles">
        {["postgresql", "r", "python", "dbt"].map((tool) => (
          <div className={`tool-tile ${tool}`} key={tool}>
            <Mark name={tool} />
            <a href={`/team#${tool === "dbt" ? "screen" : "first-question"}`}>
              Steps →
            </a>
          </div>
        ))}
      </div>
    </section>
  );
}

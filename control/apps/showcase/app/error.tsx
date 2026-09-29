"use client";
import { Shell } from "../components/shell";
export default function ErrorPage() {
  return (
    <Shell>
      <section className="empty">
        <h1>Music Data Platform is out of reach.</h1>
        <p>The page could not load.</p>
        <button className="primary" onClick={() => window.location.reload()}>
          Retry ↻
        </button>
      </section>
    </Shell>
  );
}

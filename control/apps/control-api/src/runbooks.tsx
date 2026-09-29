import { readFileSync } from "node:fs";

// These status guides ship with the console, so old or missing seed rows cannot hide them.
const guides = new Map(
  ["runners_held", "service_unreachable"].map((name) => {
    const body = readFileSync(
      new URL(`../../../../ops/runbooks/${name}.md`, import.meta.url),
      "utf8",
    );
    return [
      name.replaceAll("_", "-"),
      { title: body.split("\n")[0]?.replace(/^#\s*/, "") ?? name, body_md: body },
    ];
  }),
);

export function bundledRunbook(slug: string) {
  return guides.get(slug);
}

export function RunbookBody({ body }: { body: string }) {
  // Keep the console's escaped text rendering, with headings for section links.
  return body.split(/^(?=## )/m).map((section) => {
    if (!section.startsWith("## ")) return <pre>{section}</pre>;
    const newline = section.indexOf("\n");
    const title = section.slice(3, newline < 0 ? undefined : newline).trim();
    const id = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    return (
      <section id={id}>
        <h2>{title}</h2>
        <pre>{newline < 0 ? "" : section.slice(newline + 1)}</pre>
      </section>
    );
  });
}

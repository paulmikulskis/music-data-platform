// The browser runner uses Hono's JSX settings. Render React in its own compiler context.
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Tending, tendedCounters } from "../../components/tending";
import { TendingProvider, type Source } from "../../components/sources";
import { syntheticSources } from "../synthetic-fixture";

const sources = syntheticSources.sources.map(
  (source): Source =>
    source.source_key === "sc_hubs" ? { ...source, enabled: true } : source,
);
const counters = tendedCounters(
  sources,
  null,
  Date.parse(syntheticSources.queried_at),
);
process.stdout.write(
  renderToStaticMarkup(
    createElement(
      "div",
      null,
      <TendingProvider value={{ sources, songs: null }}>
        <Tending />
      </TendingProvider>,
      ...counters
        .filter((counter) => ["charts", "playlists"].includes(counter.key))
        .map((counter) =>
          createElement(
            "p",
            { className: "hover-line", key: counter.key },
            counter.line,
          ),
        ),
    ),
  ),
);

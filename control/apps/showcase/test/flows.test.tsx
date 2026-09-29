import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  FunctionName,
  FunctionFlow,
  FunctionCardRows,
  functionSentence,
} from "../components/flow";
import { SourceCard, TendingProvider } from "../components/sources";
import { Sheet } from "../components/sheet";
import { functionFlows } from "@mdp/contracts/flows";
import { syntheticSources } from "./synthetic-fixture";
import { sourceFunctionState } from "../lib/function-copy";
import type { LinkPreview } from "../lib/links";

it("uses live watch-list counts without turning missing counts into zero", () => {
  const state = { enabled: true, lastRead: "2026-09-28T12:00:00Z" };
  expect(functionSentence("sp_playlist", { ...state, count: 7 })).toContain(
    "the 7 Spotify playlists",
  );
  expect(functionSentence("sp_playlist", state)).toContain(
    "the Spotify playlists",
  );
  expect(functionSentence("sp_playlist")).toMatch(/^Built, waiting on/);
  expect(functionSentence("sp_playlist")).not.toContain("runs");
});
it("renders the flows.csv sentence in the Spotify source sheet", () => {
  const source = syntheticSources.sources.find(
    (row) => row.source_key === "sp_playlist",
  );
  expect(source).toBeDefined();
  if (!source) throw new Error("Open the production source fixture.");
  const sentence = functionFlows.sp_playlist.card.what.replace(
    "{count}",
    String(source.tracked?.count),
  );
  const html = renderToStaticMarkup(
    <Sheet title="Spotify" close={() => {}}>
      <SourceCard
        source={{ ...source, description: "Obsolete description." }}
      />
    </Sheet>,
  );
  expect(html).toContain(
    renderToStaticMarkup(<p data-function-description>{sentence}</p>),
  );
  expect(html).not.toContain("Obsolete description");
});
it.each(["bc_discover", "bc_daily_list", "bc_radio"])(
  "keeps historical reads separate from current operation for %s",
  (sourceKey) => {
    const source = syntheticSources.sources.find(
      (row) => row.source_key === sourceKey,
    );
    expect(source?.enabled).toBe(false);
    expect(source?.last_read).toBeTruthy();
    if (!source) throw new Error("Open the production source fixture.");
    const sentence = functionSentence(sourceKey, sourceFunctionState(source));
    expect(sentence).toMatch(/^Switched off\./);
    expect(sentence).toContain(
      `Waiting on ${functionFlows[sourceKey].card.waiting_on}. When enabled:`,
    );
    const html = renderToStaticMarkup(
      <TendingProvider value={{ sources: [source], songs: null }}>
        <FunctionName sourceKey={sourceKey} still />
        <FunctionCardRows sourceKey={sourceKey} />
      </TendingProvider>,
    );
    expect(html).toContain("Last read ");
    expect(html).toContain(source.last_read);
    expect(html).toContain("When enabled: every day");
    expect(html).not.toContain("Runs on Music Data Platform");
  },
);
it("honours a paused lineage state over an older enabled page record", () => {
  const source = syntheticSources.sources.find(
    (row) => row.source_key === "bc_discover",
  );
  if (!source) throw new Error("Open the production source fixture.");
  const html = renderToStaticMarkup(
    <TendingProvider
      value={{ sources: [{ ...source, enabled: true }], songs: null }}
    >
      <FunctionName
        sourceKey="bc_discover"
        enabled={false}
        paused
        lastRead={source.last_read}
        still
      />
    </TendingProvider>,
  );
  expect(html).toContain("Paused. Waiting on");
  expect(html).toContain("When enabled:");
  expect(html).toContain("Last read ");
  expect(html).toContain(source.last_read);
});
it("keeps the supplied Console link without its preview drawing", () => {
  const preview: LinkPreview = {
    id: "proof-open-console",
    variant: "sp_playlist",
    label: "Open the function",
    kind: "page",
    destination: { route: "/functions/sp_playlist", back: "/sources" },
    revision: null,
    objectType: null,
    body: ["readers", "last check"],
    properNames: [],
    frame: [],
    access: "",
    line: null,
    available: true,
  };
  const html = renderToStaticMarkup(
    <FunctionCardRows sourceKey="sp_playlist" links={[preview]} />,
  );
  expect(html).toContain("Open the function");
  expect(html).toContain("/s/open?");
  expect(html).not.toContain("link-preview");
  expect(html).not.toContain("last check");
});
it("keeps the platform ahead of access and the job", () => {
  const html = renderToStaticMarkup(
    <FunctionFlow sourceKey="sp_playlist" compact />,
  );
  expect(html.indexOf('data-stage="Source"')).toBeLessThan(
    html.indexOf('data-stage="Access"'),
  );
  expect(html.indexOf('data-stage="Access"')).toBeLessThan(
    html.indexOf('data-stage="Collection job"'),
  );
  expect(html).toContain("Spotify");
  expect(html).toContain("Spotify");

});
it("keeps still names inside the existing card and omits unprovided console links", () => {
  const html = renderToStaticMarkup(
    <TendingProvider value={{ sources: [], songs: null }}>
      <FunctionName sourceKey="sp_playlist" still />
      <FunctionCardRows sourceKey="sp_playlist" />
    </TendingProvider>,
  );
  expect(html).not.toContain("hover-trigger");
  expect(html).toContain("Ideas");
  expect(html).toContain("See the rows");
  expect(html).not.toContain("Open the function");
  expect(html).not.toContain("/functions/sp_playlist");
});
it("keeps every reviewed idea possible and every card actionable", () => {
  for (const [key, flow] of Object.entries(functionFlows)) {
    if (flow.hidden) continue;
    const html = renderToStaticMarkup(<FunctionCardRows sourceKey={key} />);
    expect(html).toContain("Ideas");
    expect(html).toContain("href=");
    expect(flow.card.ideas.every((idea) => idea.startsWith("Could "))).toBe(
      true,
    );
  }
});

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { sourceBrand } from "@mdp/contracts/source-wording";
import { brandIcon, brandName, brandTint } from "../lib/brands";
import { Glyph, SourceBadges, TendingProvider } from "../components/sources";
import { syntheticSources } from "./synthetic-fixture";

describe("source glyphs", () => {
  for (const brand of sourceBrand.options) {
    it(`draws ${brand} with its icon path or two letters`, () => {
      const html = renderToStaticMarkup(<Glyph brand={brand} />);
      const icon = brandIcon(brand);
      expect(html).not.toContain("<img");
      expect(html).toContain('aria-hidden="true"');
      if (icon?.path) {
        expect(html).toContain('<svg class="glyph"');
        expect(html).toContain('width="14" height="14"');
        expect(html).toContain(`<path d="${icon.path}">`);
      } else {
        expect(html).toContain('class="glyph-letters"');
        expect(html).toContain(`>${brandName(brand).slice(0, 2)}</span>`);
      }
    });
  }

  for (const still of [false, true]) {
    it(`keeps SVG badges and brand tints in ${still ? "hover rows" : "source links"}`, () => {
      const sources = syntheticSources.sources.filter(
        (source) => source.brand && source.last_read,
      );
      const html = renderToStaticMarkup(
        <TendingProvider value={{ sources, songs: null }}>
          <SourceBadges
            keys={sources.map((source) => source.source_key)}
            max={20}
            still={still}
          />
        </TendingProvider>,
      );
      const badges = html.match(/<a class="source-badge"[^>]*>.*?<\/a>/g) ?? [];
      expect(badges.length).toBeGreaterThanOrEqual(6);
      for (const badge of badges) {
        expect(badge).toContain('<svg class="glyph"');
        expect(badge).not.toContain("<img");
      }
      for (const brand of [
        "spotify",
        "apple_music",
        "shazam",
        "billboard",
        "deezer",
        "musicbrainz",
      ] as const) {
        expect(html).toContain(`--brand:${brandTint(brand)}`);
      }
    });
  }
});

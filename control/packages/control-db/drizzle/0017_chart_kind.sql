-- Shazam charts: a chart target kind for sz_chart's frozen chart specs. Numbered 0017 after
-- 's 0016.
ALTER TABLE "control"."target_spec" DROP CONSTRAINT "target_spec_resource_kind";--> statement-breakpoint
ALTER TABLE "control"."target_spec" ADD CONSTRAINT "target_spec_resource_kind" CHECK ("control"."target_spec"."resource_kind" IN ('account','curated','discovered','panel','hashtag','track','query','location','media','comment','playlist','curator','artist_page','hub','audio_asset','chart'));
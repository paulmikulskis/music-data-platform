-- Generated from the target specification declaration.
ALTER TABLE "control"."target_spec" DROP CONSTRAINT "target_spec_resource_kind";--> statement-breakpoint
ALTER TABLE "control"."target_spec" ADD CONSTRAINT "target_spec_resource_kind" CHECK ("control"."target_spec"."resource_kind" IN ('account','curated','discovered','panel','hashtag','track','query','location','media','comment','playlist','curator','artist_page','hub','audio_asset','chart'));

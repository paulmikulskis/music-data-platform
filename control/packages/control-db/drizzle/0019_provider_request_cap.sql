-- Vendor provider request cap: each provider budget caps its vendor requests per period.
ALTER TABLE "control"."budget" ADD COLUMN "cap_requests" bigint;--> statement-breakpoint
ALTER TABLE "control"."budget" ADD CONSTRAINT "budget_cap_requests_check" CHECK ("control"."budget"."cap_requests" >= 0);
-- Custom migration. Default SELECT grants must not expose credentials or sessions to functions_rt.
REVOKE ALL ON control.showcase_actor, control.showcase_inventory, control.showcase_link,
  control.showcase_seen, control.showcase_session, control.showcase_share FROM PUBLIC, functions_rt;
--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON control.showcase_inventory, control.showcase_link,
  control.showcase_seen, control.showcase_session, control.showcase_share TO control_rt;
--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON control.showcase_actor TO control_rt;



export const serviceRoutes = [
  { path: "/v1/functions/{source_key}", method: "get" },
  { path: "/v1/functions/{source_key}/run", method: "post" },
  { path: "/v1/invoke", method: "post" },
  { path: "/v1/runs/{run_id}", method: "get" },
  { path: "/v1/runs", method: "get" },
  { path: "/v1/runs/{run_id}/cancel", method: "post" },
  { path: "/v1/bind_cycle", method: "post" },
  { path: "/v1/repair", method: "post" },
  { path: "/v1/backfill", method: "post" },
  { path: "/v1/migrate", method: "post" },
  { path: "/v1/registry/sync", method: "post" },
  { path: "/v1/dbt/webhook", method: "post" },
  { path: "/v1/health/detail", method: "get" },
];

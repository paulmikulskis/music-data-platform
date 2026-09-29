import { sql } from "drizzle-orm";
import {
  check,
  unique,
  bigint,
  boolean,
  date,
  index,
  integer,
  jsonb,
  primaryKey,
  text,
  timestamp,
  uuid,
} from "drizzle-orm/pg-core";
import { control } from "./enums.js";
import { warehouse } from "./config.js";
const time = () => timestamp({ withTimezone: true });
export const showcaseLink = control.table("showcase_link", {
  nonce: text().primaryKey(),
  handle: text().notNull(),
  expires_at: time().notNull(),
  used_at: time(),
  revoked_at: time(),
  created_by: text().notNull(),
});
export const showcaseSession = control.table("showcase_session", {
  id_hash: text().primaryKey(),
  handle: text().notNull(),
  csrf_token: text().notNull(),
  created_at: time().notNull().defaultNow(),
  last_seen_at: time().notNull().defaultNow(),
  expires_at: time().notNull(),
  revoked_at: time(),
  user_agent: text(),
});
export const showcaseActor = control.table("showcase_actor", {
  api_key_id: uuid().primaryKey(),
  handle: text().notNull(),
  display_name: text().notNull(),
  first_seen_at: time().notNull().defaultNow(),
  retired_at: time(),
});
export const showcaseShare = control.table("showcase_share", {
  slug: text().primaryKey(),
  handle: text().notNull(),
  query_id: text().notNull(),
  class: text().notNull(),
  params: jsonb().notNull(),
  payload: jsonb().notNull(),
  created_at: time().notNull().defaultNow(),
  expires_at: time().notNull(),
  revoked_at: time(),
  views: integer().notNull().default(0),
});
export const showcaseSeen = control.table(
  "showcase_seen",
  {
    handle: text().notNull(),
    scope: text().notNull(),
    close_no: bigint({ mode: "bigint" }).notNull(),
    seen_at: time().notNull(),
  },
  (t) => [primaryKey({ columns: [t.handle, t.scope] })],
);
export const showcaseInventory = control.table(
  "showcase_inventory",
  {
    day: date().notNull(),
    warehouse: text().notNull(),
    layer: text().notNull(),
    relations: integer().notNull(),
    // An unanalyzed relation makes the estimate unknown (inventory contract).
    rows_est: bigint({ mode: "bigint" }),
    bytes: bigint({ mode: "bigint" }).notNull(),
    captured_at: time().notNull(),
    complete: boolean().notNull(),
  },
  (t) => [
    primaryKey({ columns: [t.day, t.warehouse, t.layer] }),
    index("showcase_inventory_warehouse_day_idx").on(
      t.warehouse,
      t.day,
      t.layer,
    ),
  ],
);

export const showcaseCall = control.table(
  "showcase_call",
  {
    id: uuid().primaryKey().defaultRandom(),
    author: text().notNull(),
    author_kind: text().notNull(),
    idempotency_key: text().notNull(),
    song_key: text().notNull(),
    anchors: jsonb().notNull(),
    snapshot: text().notNull(),
    facts: jsonb().notNull(),
    submitted_at: time().notNull().defaultNow(),
    facts_day: date().notNull(),
    close_no: bigint({ mode: "bigint" }).notNull(),
    week_start: date().notNull(),
    draft_week: date(),
    undone_at: time(),
    hidden_at: time(),
    hidden_by: text(),
  },
  (t) => [
    unique("showcase_call_author_idempotency_key_unique").on(
      t.author,
      t.idempotency_key,
    ),
    index("showcase_call_week_idx").on(t.week_start, t.author_kind),
    check(
      "showcase_call_author_kind_check",
      sql`${t.author_kind} IN ('ear', 'rule')`,
    ),
    check(
      "showcase_call_rules_author_check",
      sql`(${t.author_kind} = 'rule') = (${t.author} = 'rules')`,
    ),
  ],
);

export const showcaseRule = control.table("showcase_rule", {
  id: text().primaryKey(),
  title: text().notNull(),
  conditions: jsonb().notNull(),
  limit_per_draft: integer().notNull(),
  created_by: text().notNull(),
  created_at: time().notNull().defaultNow(),
  backed_by: text(),
  backed_at: time(),
  retired_at: time(),
  drafted_from_call: uuid().references(() => showcaseCall.id),
});
export const showcaseDraft = control.table("showcase_draft", {
  week_start: date().primaryKey(),
  opens_at: time().notNull(),
  closes_at: time().notNull(),
  candidates: jsonb().notNull(),
  rules: jsonb().notNull(),
  closed_at: time(),
  closed_by: text(),
  close_key: text(),
});
export const showcaseCallRule = control.table(
  "showcase_call_rule",
  {
    call_id: uuid()
      .notNull()
      .references(() => showcaseCall.id),
    rule_id: text()
      .notNull()
      .references(() => showcaseRule.id),
  },
  (t) => [primaryKey({ columns: [t.call_id, t.rule_id] })],
);

// Only completed, exact captures are published. The input hash identifies the reviewed projection and filters.
export const showcaseRelationCount = control.table(
  "showcase_relation_count",
  {
    warehouse_id: uuid()
      .notNull()
      .references(() => warehouse.id),
    relation: text().notNull(),
    build_key: text().notNull(),
    captured_at: time().notNull(),
    row_count: bigint({ mode: "bigint" }).notNull(),
    latest_at: time(),
    basis: text().notNull(),
    input_hash: text().notNull(),
  },
  (t) => [
    primaryKey({ columns: [t.warehouse_id, t.relation, t.build_key] }),
    check("showcase_count_nonnegative", sql`${t.row_count} >= 0`),
    check(
      "showcase_count_global",
      sql`${t.relation} ~ '^(marts|intermediate|staging)\\.[a-z][a-z0-9_]*$'`,
    ),
    check("showcase_count_exact", sql`${t.basis} = 'exact'`),
    check(
      "showcase_count_identity",
      sql`${t.input_hash} ~ '^[a-f0-9]{64}$' AND length(${t.build_key}) BETWEEN 1 AND 2048`,
    ),
    check(
      "showcase_count_time",
      sql`${t.latest_at} IS NULL OR ${t.latest_at} <= ${t.captured_at}`,
    ),
  ],
);

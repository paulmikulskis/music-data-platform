import { text, timestamp, uuid } from 'drizzle-orm/pg-core';
import { control } from './enums.js';
import { tenant } from './config.js';
export const apiKey=control.table('api_key',{
 id:uuid().primaryKey().defaultRandom(),key_hash:text().notNull().unique(),label:text().notNull(),
 warehouse_role:text(),
 tenant_id:uuid().references(()=>tenant.id),role:text().notNull().default('reader'),
 created_at:timestamp({withTimezone:true}).notNull().defaultNow(),expires_at:timestamp({withTimezone:true}),revoked_at:timestamp({withTimezone:true})
});

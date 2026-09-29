// Use installed tooling, but read SQL exclusively from the pinned build snapshot.
import { createRequire } from 'node:module';
const require = createRequire(`${process.cwd()}/packages/control-db/package.json`);
const postgres = require('postgres');
const { drizzle } = require('drizzle-orm/postgres-js');
const { migrate } = require('drizzle-orm/postgres-js/migrator');
const client = postgres(process.env.MDP_CONTROL_DATABASE_URL, { max: 1 });
try {
  await migrate(drizzle(client), { migrationsFolder: process.env.MDP_MIGRATIONS_DIR });
  console.log('PASS committed control migrations applied');
} finally {
  await client.end();
}

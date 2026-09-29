import { defineConfig } from 'drizzle-kit';

export default defineConfig({
  dialect: 'postgresql',
  schema: './src/schema/*.ts',
  out: './drizzle',
  dbCredentials: { url: process.env.MDP_CONTROL_DATABASE_URL ?? '' },
  schemaFilter: ['control'],
});

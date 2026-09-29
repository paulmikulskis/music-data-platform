import { platformRouter } from "./router.platform.js";
import { statusRouter } from "./router.status.js";
import { apiKeysRouter } from "./router.api-keys.js";
import { tenantsRouter } from "./router.tenants.js";
import { targetsRouter } from "./router.targets.js";
import { streamlinesRouter, runsRouter, functionsRouter, llmStepsRouter } from "./router.functions-runs.js";
import { dbtRouter } from "./router.dbt.js";
import { budgetsRouter, costsRouter, alertsRouter, screenRouter } from "./router.budgets-alerts.js";
import { referenceRouter } from "./router.reference.js";
import { lineageRouter } from "./router.lineage.js";
import { impl } from "./router.shared.js";
export type { Context } from "./router.shared.js";
import { workbenchRouter } from "./workbench-router.js";

import { showcaseRouter } from "./router.showcase.js";

export const router = impl.router({
  showcase: showcaseRouter,
  status: statusRouter,
  platform: platformRouter,
  workbench: workbenchRouter,
  apiKeys: apiKeysRouter,
  tenants: tenantsRouter,
  targets: targetsRouter,
  streamlines: streamlinesRouter,
  runs: runsRouter,
  functions: functionsRouter,
  dbt: dbtRouter,
  budgets: budgetsRouter,
  costs: costsRouter,
  alerts: alertsRouter,
  reference: referenceRouter,
  llmSteps: llmStepsRouter,
  lineage: lineageRouter,
  screen: screenRouter,
});

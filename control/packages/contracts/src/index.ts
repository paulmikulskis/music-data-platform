import { statusContract } from "./status.js";
import { apiKeysContract } from "./api-keys.js";
import { tenantsContract } from "./tenants.js";
import { targetsContract } from "./targets.js";
import {
  streamlinesContract,
  runsContract,
  functionsContract,
  llmStepsContract,
} from "./functions-runs.js";
import { dbtContract } from "./dbt.js";
import {
  budgetsContract,
  costsContract,
  alertsContract,
  screenContract,
} from "./budgets-alerts.js";
import { referenceContract } from "./reference.js";
import { lineageContract } from "./lineage.js";
import { platformContract } from "./platform.js";
export {
  holdings,
  inventoryLayer,
  platformEvent,
  platformSource,
  platformSources,
  runnerState,
  nightWindow,
  nightTargets,
  nightAttempt,
  platformNight,
  relationCountKey,
  relationCount,
  relationCountsInput,
  relationCounts,
} from "./platform.js";
export {
  sourceBrand,
  sourceFamily,
  sourceWording,
  sourceReaders,
  sourceFallback,
  type SourceWording,
} from "./source-wording.js";
export { apiKeyDto, apiKeyCreateInput } from "./api-keys.js";
export { tenantDto } from "./tenants.js";
export {
  sourceRef,
  targetSetDto,
  targetDto,
} from "./targets.js";
export {
  streamlineDto,
  runDto,
  eventDto,
  llmStepDto,
  admission,
  poll,
  functionPage,
  fixtureScenario,
  manualResult,
  dryProbeResult,
} from "./functions-runs.js";
export { coreLaunchDto, jobDto } from "./dbt.js";
export { alertDto, budgetDto, weekly } from "./budgets-alerts.js";
export {
  referenceAlertClasses,
  referenceSourceDto,
  referenceSourceState,
} from "./reference.js";
export { errorSchema, id, source, empty, jsonRow } from "./shared.js";
export { serviceRoutes } from "./service.js";
export {
  healthSummaryDto,
  heartbeatStatusDto,
  cycleStatusDto,
  cadenceStatusDto,
  alertGroupDto,
  sendsStatusDto,
  versionDto,
  versionsStatusDto,
  platformStatusDto,
  type PlatformStatus,
} from "./status.js";
export * from "./workbench.js";
import { workbenchContract } from "./workbench.js";

import { showcaseContract } from "./showcase.js";

export const contract = {
  showcase: showcaseContract,
  platform: platformContract,
  status: statusContract,
  workbench: workbenchContract,
  tenants: tenantsContract,
  apiKeys: apiKeysContract,
  targets: targetsContract,
  streamlines: streamlinesContract,
  runs: runsContract,
  functions: functionsContract,
  dbt: dbtContract,
  budgets: budgetsContract,
  costs: costsContract,
  alerts: alertsContract,
  reference: referenceContract,
  llmSteps: llmStepsContract,
  lineage: lineageContract,
  screen: screenContract,
};

export {
  errorCatalog,
  errorHint,
  type ErrorClass,
  type ErrorHint,
} from "./error-catalog.js";

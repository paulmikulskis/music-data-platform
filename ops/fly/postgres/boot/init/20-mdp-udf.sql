-- Install as superuser. Config is private; credentials come only from the environment.
BEGIN;
CREATE EXTENSION IF NOT EXISTS plpython3u;
DO $role$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mdp_udf_owner') THEN
    CREATE ROLE mdp_udf_owner NOLOGIN;
  END IF;
END $role$;
CREATE SCHEMA IF NOT EXISTS mdp;
GRANT USAGE ON SCHEMA mdp TO mdp_udf_owner, dbt_transform;
CREATE TABLE IF NOT EXISTS mdp.config(key text PRIMARY KEY, value text);
ALTER TABLE mdp.config OWNER TO mdp_udf_owner;
REVOKE ALL ON mdp.config FROM PUBLIC, service_read, dbt_transform, workbench_wh, reader_wh, loader_wh;

CREATE OR REPLACE FUNCTION mdp.invoke(source_key text, params jsonb)
RETURNS TABLE(run_id text, status text, coverage text, rows_written bigint,
 rows_rejected bigint, dump_id text, landed_seq bigint, trace_url text, message text)
LANGUAGE plpython3u SECURITY DEFINER SET search_path = mdp, pg_temp
AS $python$
import hashlib
import json
import math
import time
from urllib.parse import quote
import httpx

p = json.loads(params) if isinstance(params, str) else params
p = p or {}
try:
    budget = float(p.get('deadline_s', 870))
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError()
except (TypeError, ValueError):
    plpy.error('invoke_timeout: deadline_s must be positive and finite')
deadline = time.monotonic() + budget
config = {r['key']: r['value'] for r in plpy.execute('SELECT key,value FROM mdp.config')}
if not config.get('service_url') or not config.get('service_token'):
    plpy.error('service_unreachable: service configuration is missing')
base = config['service_url'].rstrip('/')
key = hashlib.sha256('|'.join(str(v or '') for v in (
    source_key, p.get('dbt_run_id'), p.get('model'), p.get('target_set'), p.get('input_relation')
)).encode()).hexdigest()
headers = {'Authorization': 'Bearer ' + config['service_token'], 'Idempotency-Key': key}
payload = {k: p.get(k) for k in (
    'cadence', 'target_set', 'dbt_run_id', 'invocation_id', 'model', 'input_relation', 'target_kinds'
)}
payload['source_key'] = source_key
# The last answer the service gave: what a deadline message reports.
last = {'run_id': None, 'status': 'running', 'receipt': ''}

def timed_out():
    # The caller's deadline ended while the service was still working: say what was running.
    rid = last['run_id']
    if rid is None:
        plpy.error('invoke_timeout: admission did not answer within the %gs invocation deadline. '
                   'Check the functions service health, then retry the cycle.' % budget)
    plpy.error('invoke_timeout: run %s still %s at the %gs invocation deadline%s. Open /runs/%s'
               % (rid, last['status'], budget, last['receipt'], rid))

def remaining():
    left = deadline - time.monotonic()
    if left <= 0:
        timed_out()
    return left

def request(client, method, path, body=None):
    left = remaining()
    timeout = httpx.Timeout(min(10.0, left), connect=min(3.0, left))
    response = client.request(method, base + path, json=body, timeout=timeout)
    remaining()
    if response.status_code >= 500:
        # Admission may have committed before the gateway failed: retry with the same key.
        raise httpx.ReadError('ambiguous service response')
    try:
        data = response.json()
    except ValueError:
        raise httpx.ReadError('ambiguous non-JSON service response')
    if response.is_error:
        plpy.error(str(data.get('error_class', 'service_error')) + ': ' + str(data.get('message', 'Request failed')))
    return data

with httpx.Client(headers=headers, trust_env=False) as client:
    pause = 0.5
    while last['run_id'] is None:
        try:
            # The service ends its attempt inside what is left of this deadline.
            payload['deadline_s'] = remaining()
            admitted = request(client, 'POST', '/v1/invoke', payload)
            last['run_id'] = admitted['run_id']
        except (httpx.ConnectError, httpx.ConnectTimeout):
            plpy.error('service_unreachable: connection to functions service failed')
        except (httpx.TransportError, KeyError, TypeError):
            # The Idempotency-Key returns the same run, so a slow admission is retried until the deadline.
            if deadline - time.monotonic() <= pause:
                timed_out()
            plpy.execute('select 1')
            time.sleep(pause)
            pause = min(2 * pause, 5.0)
    rid = last['run_id']
    failures = 0
    while True:
        # PL/Python cannot be interrupted inside a blocking socket call. Bounded HTTP
        # calls plus this SPI boundary deliver statement_timeout between HTTP polls.
        plpy.execute('select 1')
        try:
            result = request(client, 'GET', '/v1/runs/' + quote(str(rid), safe=''))
        except httpx.TransportError:
            # A busy service can miss one poll: retry with a short backoff, and give up
            # after three misses in a row. A backoff that would reach the deadline is the deadline.
            failures += 1
            pause = 0.5 * 2 ** (failures - 1)
            if failures >= 3:
                plpy.error('service_unreachable: run polling failed 3 times in a row')
            if deadline - time.monotonic() <= pause:
                timed_out()
            time.sleep(pause)
            continue
        failures = 0
        status = result['run']['status']
        receipts = result.get('receipts', [])
        last['status'] = status
        counted = next((r for r in receipts if r.get('targets_total') is not None), None)
        if counted:
            last['receipt'] = '; last receipt %s/%s targets' % (counted.get('targets_succeeded'), counted['targets_total'])
        if status in ('succeeded', 'failed', 'partial', 'paused', 'cancelled', 'superseded'):
            # A source declared blocks_cycle=False reports its own failure or floor miss as receipt
            # rows, so the cycle still closes; the run's alerts keep the miss visible.
            blocking = not receipts or any(r.get('blocks_cycle') is not False for r in receipts)
            if status in ('cancelled', 'superseded') or (status == 'failed' and blocking):
                failed = next((r for r in receipts if r.get('message')), {})
                message = failed.get('message') or result['run'].get('error_message')
                error_class = failed.get('error_class') or result['run'].get('error_class')
                if not message:
                    plpy.error('invoke_failed: run %s ended %s. Open /runs/%s' % (rid, status, rid))
                if error_class and not message.startswith(error_class + ':'):
                    message = error_class + ': ' + message
                plpy.error(message.rstrip('.') + '. Open /runs/' + str(rid))
            for receipt in receipts if blocking else ():
                partial = receipt.get('coverage') == 'partial' or receipt.get('status') == 'partial'
                if partial and (receipt.get('target_coverage_met') is False or (receipt.get('target_coverage_met') is not True and receipt.get('allow_partial') is not True)):
                    plpy.error(receipt.get('message') or 'partial_not_allowed: incomplete coverage')
            columns = ('run_id','status','coverage','rows_written','rows_rejected',
                       'dump_id','landed_seq','trace_url','message')
            return [tuple(r.get(k) for k in columns) for r in receipts]
        time.sleep(min(2.0, remaining()))
$python$;
ALTER FUNCTION mdp.invoke(text,jsonb) OWNER TO mdp_udf_owner;
REVOKE ALL ON FUNCTION mdp.invoke(text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mdp.invoke(text,jsonb) TO dbt_transform;

DROP FUNCTION IF EXISTS mdp.bind_cycle(text,text,text,text,text,text,text);
CREATE OR REPLACE FUNCTION mdp.bind_cycle(cadence text, scope text, dbt_run_id text,
 reason_category text, job_id text, cycle_id text DEFAULT NULL, runner text DEFAULT 'cloud',
 global_inputs text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpython3u SECURITY DEFINER SET search_path = mdp, pg_temp
AS $python$
import json
import httpx
config = {r['key']: r['value'] for r in plpy.execute('SELECT key,value FROM mdp.config')}
if not config.get('service_url') or not config.get('service_token'):
    plpy.error('service_unreachable: service configuration is missing')
payload = dict(cadence=cadence, scope=scope, dbt_run_id=dbt_run_id,
               reason_category=reason_category, job_id=job_id, cycle_id=cycle_id, runner=runner,
               global_inputs=json.loads(global_inputs) if global_inputs is not None else None)
with httpx.Client(timeout=httpx.Timeout(120.0, connect=3.0), trust_env=False) as client:
    try:
        response = client.post(config['service_url'].rstrip('/') + '/v1/bind_cycle',
            headers={'Authorization': 'Bearer ' + config['service_token']}, json=payload)
        result = response.json()
    except (httpx.HTTPError, ValueError):
        plpy.error('service_unreachable: cycle binding request failed')
    if response.is_error:
        plpy.error(str(result.get('error_class', 'service_error')) + ': ' + str(result.get('message', 'Binding failed')))
    return json.dumps(result)
$python$;
ALTER FUNCTION mdp.bind_cycle(text,text,text,text,text,text,text,text) OWNER TO mdp_udf_owner;
REVOKE ALL ON FUNCTION mdp.bind_cycle(text,text,text,text,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mdp.bind_cycle(text,text,text,text,text,text,text,text) TO dbt_transform;

-- psql reads the caller's environment, also at first boot. Missing values preserve
-- an existing configuration; never substitute a compiled-in URL or token.
\getenv mdp_service_url MDP_SERVICE_URL
\getenv mdp_service_token MDP_SERVICE_TOKEN
\if :{?mdp_service_url}
INSERT INTO mdp.config VALUES ('service_url', :'mdp_service_url')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;
\endif
\if :{?mdp_service_token}
INSERT INTO mdp.config VALUES ('service_token', :'mdp_service_token')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;
\endif
COMMIT;

-- Provision an EGRESS HOST_PORT network rule for the public functions hostname.
-- Provision MDP_SERVICE_TOKEN and MDP_SERVICE_URL as GENERIC_STRING secrets separately.
-- Secrets are deployment inputs; never embed their values in this file.
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION mdp_functions_access
  ALLOWED_NETWORK_RULES = (mdp_service_rule)
  ALLOWED_AUTHENTICATION_SECRETS = (mdp_service_token, mdp_service_url)
  ENABLED = TRUE;

CREATE OR REPLACE FUNCTION mdp_invoke(source_key VARCHAR, params VARIANT)
RETURNS TABLE(run_id VARCHAR, status VARCHAR, coverage VARCHAR, rows_written NUMBER(38,0),
 rows_rejected NUMBER(38,0), dump_id VARCHAR, landed_seq NUMBER(38,0), trace_url VARCHAR, message VARCHAR)
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
PACKAGES = ('requests')
HANDLER = 'Invoke'
EXTERNAL_ACCESS_INTEGRATIONS = (mdp_functions_access)
SECRETS = ('service_token' = mdp_service_token, 'service_url' = mdp_service_url)
AS $$
import hashlib
import math
import time
import requests
import _snowflake

class Invoke:
    def process(self, source_key, params):
        p = params or {}
        budget = float(p.get('deadline_s', 870))
        if not math.isfinite(budget) or budget <= 0:
            raise RuntimeError('invoke_timeout: deadline_s must be positive and finite')
        deadline = time.monotonic() + budget
        base = _snowflake.get_generic_secret_string('service_url').rstrip('/')
        token = _snowflake.get_generic_secret_string('service_token')
        if not base.startswith('https://'):
            raise RuntimeError('service_unreachable: HTTPS public service URL required')
        key = hashlib.sha256('|'.join(str(v or '') for v in (
            source_key, p.get('dbt_run_id'), p.get('model'), p.get('target_set'), p.get('input_relation')
        )).encode()).hexdigest()
        payload = {k: p.get(k) for k in (
            'cadence', 'target_set', 'dbt_run_id', 'invocation_id', 'model', 'input_relation', 'target_kinds'
        )}
        payload['source_key'] = source_key
        columns = ('run_id','status','coverage','rows_written','rows_rejected',
                   'dump_id','landed_seq','trace_url','message')
        # The last answer the service gave: what a deadline message reports.
        last = {'run_id': None, 'status': 'running', 'receipt': ''}
        def timed_out():
            rid = last['run_id']
            if rid is None:
                raise RuntimeError('invoke_timeout: admission did not answer within the %gs invocation deadline. '
                                   'Check the functions service health, then retry the cycle.' % budget)
            raise RuntimeError('invoke_timeout: run %s still %s at the %gs invocation deadline%s. Open /runs/%s'
                               % (rid, last['status'], budget, last['receipt'], rid))
        def remaining():
            left = deadline - time.monotonic()
            if left <= 0:
                timed_out()
            return left
        with requests.Session() as client:
            client.headers.update({'Authorization': 'Bearer ' + token, 'Idempotency-Key': key})
            def request(method, path, body=None):
                left = remaining()
                response = client.request(method, base + path, json=body,
                    timeout=(min(3.0, left), min(10.0, left)))
                remaining()
                if response.status_code >= 500:
                    raise requests.ConnectionError('ambiguous service response')
                result = response.json()
                if response.status_code >= 400:
                    raise RuntimeError(result.get('error_class','service_error') + ': ' + result.get('message','Request failed'))
                return result
            pause = 0.5
            while last['run_id'] is None:
                try:
                    # Same admission rule as the Postgres UDF: the attempt ends inside this deadline,
                    # and the Idempotency-Key makes a retried admission return the same run.
                    payload['deadline_s'] = remaining()
                    last['run_id'] = request('POST','/v1/invoke',payload)['run_id']
                except (requests.RequestException, ValueError, KeyError, TypeError):
                    if deadline - time.monotonic() <= pause:
                        timed_out()
                    time.sleep(pause)
                    pause = min(2 * pause, 5.0)
            rid = last['run_id']
            failures = 0
            while True:
                try:
                    result = request('GET','/v1/runs/' + str(rid))
                except requests.RequestException:
                    # Same poll tolerance as the Postgres UDF: short backoff, three misses in a row.
                    failures += 1
                    pause = 0.5 * 2 ** (failures - 1)
                    if failures >= 3:
                        raise RuntimeError('service_unreachable: run polling failed 3 times in a row')
                    if deadline - time.monotonic() <= pause:
                        timed_out()
                    time.sleep(pause)
                    continue
                failures = 0
                status = result['run']['status']
                receipts = result.get('receipts',[])
                last['status'] = status
                counted = next((r for r in receipts if r.get('targets_total') is not None), None)
                if counted:
                    last['receipt'] = '; last receipt %s/%s targets' % (counted.get('targets_succeeded'), counted['targets_total'])
                if status in ('succeeded','failed','partial','paused','cancelled','superseded') and result.get('repairs_pending', 0) == 0:
                    blocking = not receipts or any(r.get('blocks_cycle') is not False for r in receipts)
                    if status in ('cancelled','superseded') or (status == 'failed' and blocking):
                        failed = next((r for r in receipts if r.get('message')), {})
                        message = failed.get('message') or result['run'].get('error_message')
                        error_class = failed.get('error_class') or result['run'].get('error_class')
                        if not message:
                            raise RuntimeError('invoke_failed: run %s ended %s. Open /runs/%s' % (rid, status, rid))
                        if error_class and not message.startswith(error_class + ':'):
                            message = error_class + ': ' + message
                        raise RuntimeError(message.rstrip('.') + '. Open /runs/' + str(rid))
                    for receipt in receipts:
                        for load in receipt.get('loads', []):
                            if load.get('status') != 'loaded':
                                raise RuntimeError('load_incomplete: final load is ' + str(load.get('status')))
                        partial = receipt.get('coverage') == 'partial' or receipt.get('status') == 'partial'
                        if blocking and partial and (receipt.get('target_coverage_met') is False or (receipt.get('target_coverage_met') is not True and receipt.get('allow_partial') is not True)):
                            raise RuntimeError(receipt.get('message') or 'partial_not_allowed: incomplete coverage')
                        yield tuple(int(receipt[k]) if k in ('rows_written','rows_rejected','landed_seq') and receipt.get(k) is not None else receipt.get(k) for k in columns)
                    return
                time.sleep(min(2.0,remaining()))
$$;

-- A Retry or restore build that closes the current scheduled cycle resolves its cadence alerts too.
CREATE OR REPLACE FUNCTION control.resolve_recovered_alerts(p_run uuid, p_cycle uuid) RETURNS integer
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, control AS $$
  WITH scheduled AS (
    SELECT * FROM control.cycle c
    WHERE left(c.opened_by_dbt_run_id,7) <> 'manual:'
      AND left(c.opened_by_dbt_run_id,9) <> 'backfill:'
      AND left(c.opened_by_dbt_run_id,7) <> 'canary:'
  ), recovered_run AS (
    SELECT r.*, c.cadence, s.source_key,
      coalesce((SELECT max(started_at) FROM control.run_attempt WHERE run_id=r.id),r.created_at) AS recovery_started
    FROM control.run r
    JOIN scheduled c ON c.id=r.cycle_id
    JOIN control.streamline s ON s.id=r.streamline_id
    WHERE r.id=p_run AND r.kind='invoke' AND r.status='succeeded' AND r.coverage='full'
      AND r.rows_written>0 AND r.error_class IS NULL
      AND left(r.work_key,7) <> 'manual:'
      AND NOT coalesce((r.resolved_config->>'fixture')::boolean,false)
  ), recovered_cycle AS (
    -- The cycle's newest attempt built (bronze and transform) successfully, its close is
    -- mirrored, and the cycle ran real work: fixture-only and manual-only cycles prove nothing.
    -- A scheduled build, a Retry and a restore all count. A non-scheduled attempt counts only on
    -- the newest non-superseded scheduled cycle, so a Replay of an older cycle proves nothing.
    SELECT c.*, build.updated_at AS completed_at FROM scheduled c
    JOIN control.scope_close sc ON sc.scope=c.scope
    JOIN control.cycle_attempt attempt ON attempt.cycle_id=c.id
      AND attempt.reason_category IN ('scheduled','other')
    JOIN control.run build ON build.work_key='dbt:' || attempt.dbt_run_id AND build.status='succeeded'
      AND build.kind='dbt' AND build.scope=c.scope
    WHERE c.id=p_cycle AND c.status='closed' AND c.close_no IS NOT NULL
      AND (attempt.reason_category='scheduled' OR NOT EXISTS (SELECT 1 FROM scheduled later
        WHERE later.cadence=c.cadence AND later.scope=c.scope AND later.status<>'superseded'
          AND later.opened_at>c.opened_at))
      AND sc.mirrored_close_no>=c.close_no
      AND EXISTS (SELECT 1 FROM control.run real WHERE real.cycle_id=c.id AND real.kind='invoke'
        AND left(real.work_key,7)<>'manual:'
        AND NOT coalesce((real.resolved_config->>'fixture')::boolean,false))
      AND NOT EXISTS (SELECT 1 FROM control.cycle_attempt newer WHERE newer.cycle_id=c.id
        AND newer.bound_at>attempt.bound_at)
  ), candidates AS (
    SELECT a.id, a.updated_at AS failure_at, 'Scheduled run recovered with full coverage. Open /runs/' || r.id::text AS reason
    FROM control.alert a CROSS JOIN recovered_run r
    WHERE a.resolved_at IS NULL AND a.opened_at<=r.recovery_started AND (
      (a.subject_type='run' AND a.severity='warning'
        AND a.class IN ('partial_coverage','vendor_4xx','vendor_retryable','envelope_mismatch',
          'accounting_mismatch','invoke_timeout','deadline_expired','service_unreachable',
          'control_api_unavailable','function_failed')
        AND EXISTS (SELECT 1 FROM control.run old JOIN scheduled c ON c.id=old.cycle_id
          WHERE old.id=a.run_id AND old.created_at<=r.created_at AND old.streamline_id=r.streamline_id
            AND old.scope=r.scope AND c.cadence=r.cadence AND left(old.work_key,7)<>'manual:'))
      OR (a.class='source_canary_failed' AND a.subject_type='streamline'
        AND a.subject_id=r.source_key || ':' || r.scope
        AND (SELECT log.after->>'status' FROM control.audit_log log
          WHERE log.action='source.canary' AND log.subject=a.subject_id AND log.at>a.updated_at
          ORDER BY log.at DESC,log.id DESC LIMIT 1)='passed')
    )
    UNION ALL
    SELECT a.id, a.updated_at AS failure_at, 'Cycle build succeeded and its close is mirrored. Open /ops#alerts' AS reason
    FROM control.alert a CROSS JOIN recovered_cycle c
    WHERE a.class='cadence_failed' AND a.resolved_at IS NULL AND a.updated_at<=c.completed_at
      AND (
        (a.subject_type='cycle' AND EXISTS (SELECT 1 FROM scheduled old
          WHERE old.id::text=a.subject_id AND old.scope=c.scope AND old.cadence=c.cadence
            AND old.opened_at<=c.opened_at))
        OR (a.subject_type='dbt_job' AND EXISTS (SELECT 1 FROM control.dbt_job j
          WHERE j.scope=c.scope AND j.cadence=c.cadence
            AND (a.subject_id=j.job_id OR split_part(a.subject_id,'@',1)=j.job_id)))
      )
  ), resolved AS (
    UPDATE control.alert a SET resolved_at=now(), updated_at=now(),
      resolved_by='system:recovery', resolution_reason=c.reason
    FROM candidates c WHERE a.id=c.id AND a.resolved_at IS NULL AND a.updated_at=c.failure_at
    RETURNING a.*
  ), audited AS (
    INSERT INTO control.audit_log(actor,action,subject,after)
    SELECT resolved_by,'alerts.resolve',id::text,
      jsonb_build_object('state','succeeded','reason',resolution_reason,
        'run_id',p_run,'cycle_id',p_cycle,'resolved_at',resolved_at)
    FROM resolved RETURNING id
  ) SELECT count(*)::integer FROM audited
$$;

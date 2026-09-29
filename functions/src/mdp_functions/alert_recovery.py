"""Repeated partial coverage is measured within one source, cadence and scope."""

from mdp_functions.health_policy import scheduled_cycle_sql


def repeated_partial(conn, run):
    previous = conn.execute(
        f"""SELECT prior.coverage='partial' AS partial
        FROM control.run current JOIN control.cycle c ON c.id=current.cycle_id
        CROSS JOIN LATERAL (
            SELECT old.coverage FROM control.run old
            JOIN control.cycle previous ON previous.id=old.cycle_id
            WHERE old.streamline_id=current.streamline_id AND old.scope=current.scope
              AND previous.cadence=c.cadence AND old.cycle_id<>current.cycle_id
              AND previous.opened_at<c.opened_at AND old.kind='invoke'
              AND left(old.work_key,7)<>'manual:' AND {scheduled_cycle_sql('previous.opened_by_dbt_run_id')}
              AND NOT coalesce((old.resolved_config->>'fixture')::boolean,false)
            ORDER BY previous.opened_at DESC,old.created_at DESC,old.id DESC LIMIT 1
        ) prior
        WHERE current.id=%s AND current.kind='invoke' AND left(current.work_key,7)<>'manual:'
          AND {scheduled_cycle_sql('c.opened_by_dbt_run_id')}
          AND NOT coalesce((current.resolved_config->>'fixture')::boolean,false)""",
        (run["id"],),
    ).fetchone()
    return bool(previous and previous["partial"])

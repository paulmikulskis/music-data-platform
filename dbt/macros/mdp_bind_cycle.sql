{% macro mdp_bind_cycle() %}
  {% if not execute or mdp_is_local() %}{{ return('') }}{% endif %}
  {# dbt 1.12 RetryTask rewrites WHICH to the prior command; retain the CLI check. #}
  {% if flags.WHICH == 'retry' or 'retry' in (flags.INVOCATION_COMMAND | default('')).split() %}
    {{ exceptions.raise_compiler_error('partial_retry_refused: rerun the full cadence job to preserve the cycle manifest') }}
  {% endif %}
  {% if flags.WHICH not in ('run', 'build', 'seed', 'snapshot', 'test') %}
    {{ return('') }}
  {% endif %}
  {% set query %}
select mdp.bind_cycle(
    {{ mdp_literal(env_var('DBT_MDP_CADENCE')) }},
    {{ mdp_literal(env_var('DBT_MDP_SCOPE', 'global')) }},
    {{ mdp_literal(env_var('DBT_CLOUD_RUN_ID')) }},
    {{ mdp_literal(env_var('DBT_CLOUD_RUN_REASON_CATEGORY', 'other')) }},
    {{ mdp_literal(env_var('DBT_CLOUD_JOB_ID')) }},
    {{ mdp_literal(var('cycle_id', none)) }},
    {{ mdp_literal(env_var('MDP_RUNNER', 'cloud')) }},
    {# The generated global inputs of this job's tenant models; bind refuses a stale dbt_job row. #}
    {{ mdp_literal(tojson(mdp_global_inputs(env_var('DBT_MDP_CADENCE')) if env_var('DBT_MDP_SCOPE', 'global') != 'global' else [])) }})
  {% endset %}
  {# Return SQL for the hook executor; rendering must never open a cycle. #}
  {{ return(query) }}
{% endmacro %}

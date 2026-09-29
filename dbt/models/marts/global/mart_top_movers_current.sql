{{ config(tags=['cadence:daily']) }}
with rows as (
    select *, source_keys as _source_keys from {{ ref('mart_top_movers') }}
    where day = {{ mdp_cycle_day() }}
)
{{ mdp_annotate('rows', learning_inputs=['learning_eligible'], resale_inputs=['resale_permitted']) }}

{{ config(tags=['cadence:daily']) }}
with chart as (
    select
        cast('hot-100' as varchar) as chart_name,
        chart_week, chart_position, track_title, artist_name,
        song_key, billboard_match_method, confidence, matched_by_group, weeks_on_chart, is_debut,
        cast('billboard_hot100' as varchar) as source_key,
        source_keys as _source_keys,
        cast({{ mdp_literal(mdp_context().cycle_id) }} as text) as _cycle_id,
        cast({{ mdp_literal(invocation_id) }} as text) as _built_by
    from {{ ref('int_billboard_song__daily') }}
)
{{ mdp_annotate('chart') }}

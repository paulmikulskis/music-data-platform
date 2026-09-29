{{ config(tags=['cadence:daily']) }}

-- The rights columns pass through from mart_playlist_events, whose annotation covers each row.
with entries as (
    select *,
        row_number() over (partition by platform,playlist_id,variant,stream,item_type,platform_item_id order by observed_at,occurrence_key,interval_id)=1 as first_ever_entry,
        source_keys as _source_keys
    from {{ ref('mart_playlist_events') }}
    where owner_class in ('editorial','dsp_algorithmic','chart')
        and event_type='add' and entered_after is not null
)
{{ mdp_annotate('entries') }}

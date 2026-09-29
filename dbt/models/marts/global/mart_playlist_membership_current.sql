{{ config(tags=['cadence:daily']) }}

with current_members as (
    {{ mdp_membership_current(ref('int_playlist__snapshots'), ref('int_playlist__membership'), ref('int_playlist__observations')) }}
), annotated as (
    select m.*, r.source_keys as _source_keys
    from current_members m
    left join {{ ref('int_playlist__rights') }} r on r.snapshot_id=m.snapshot_id
)
{{ mdp_annotate('annotated') }}

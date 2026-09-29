{{ config(tags=['cadence:daily']) }}
-- design: the same measures per market: each contributing playlist counts once within its variant,
-- and variants are never summed together.
with annotated as (
    {{ mdp_track_followers(variant=true) }}
)
{{ mdp_annotate('annotated') }}

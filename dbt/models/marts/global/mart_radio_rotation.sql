{{ config(tags=['cadence:daily']) }}

-- Recording × week: a station's plays of a recording in an ISO week, its heaviest rotation, and
-- whether the week held its first play, its rotation add or an upgrade. Operator-only: it declares no
-- grain, so the data API never serves it, until the kexp_plays registry row's review_ref names KEXP's
-- written permission.
with rotation as (
    {{ mdp_radio_rotation('UTC') }}
)
{{ mdp_annotate('rotation') }}

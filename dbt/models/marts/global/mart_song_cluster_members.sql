{{ config(tags=['cadence:daily']) }}
with members as (
    select song_key, cluster_key, cast(cluster_key as text) as representative_song_key,
        cluster_methods, source_keys as _source_keys
    from {{ ref('int_song_cluster__daily') }}
)
{{ mdp_annotate('members') }}

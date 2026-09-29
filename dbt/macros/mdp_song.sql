{# Apple playlist ids and Shazam Apple song ids share one namespace. #}
{% macro mdp_song_platform(column) -%}
case when {{ column }} in ('apple_music', 'apple') then 'apple' else {{ column }} end
{%- endmacro %}

{# Read a stamp in the same SQL statement as its evidence rows. Local unstamped inputs stay explicit. #}
{% macro mdp_song_input_build(model_name) -%}
{% set relation = ref(model_name) %}
{% set builds = none %}
{% if execute %}
{% set found = run_query("select count(*) from information_schema.tables where table_schema = 'marts' and table_name = '_build'") %}
{% if found.columns[0].values()[0] > 0 %}
{% set builds = api.Relation.create(database=target.database, schema='marts', identifier='_build') %}
{% endif %}
{% endif %}
{% set missing = mdp_json_object([('relation', mdp_literal(relation.schema ~ '.' ~ relation.identifier)), ('scope', "'global'"), ('cycle_id', 'cast(null as text)'), ('close_no', 'cast(null as text)'), ('built_at', 'cast(null as text)')]) %}
{% if builds %}
coalesce((select {{ mdp_json_object([('relation', 'b.relation'), ('scope', "'global'"), ('cycle_id', 'b.cycle_id'), ('close_no', 'cast(b.close_no as text)'), ('built_at', "mdp_stamp_time")]) }}
 from (select *, {{ mdp_timestamp_text(playlist_utc('built_at')) }} || 'Z' as mdp_stamp_time from {{ builds }}) b
 where b.relation = {{ mdp_literal(relation.schema ~ '.' ~ relation.identifier) }}), {{ missing }})
{% else %}
{{ mdp_json_object([('relation', mdp_literal(relation.schema ~ '.' ~ relation.identifier)), ('scope', "'global'"), ('cycle_id', 'cast(null as text)'), ('close_no', 'cast(null as text)'), ('built_at', 'cast(null as text)')]) }}
{% endif %}
{%- endmacro %}

{% macro mdp_song_events() %}
select k.song_key, e.*, cast(e.observed_at as date) as day
from {{ ref('mart_playlist_events') }} e
join {{ ref('int_song_key__daily') }} k
    on k.platform = {{ mdp_song_platform('e.platform') }} and k.platform_track_id = e.platform_track_id
where not e.is_baseline
{% endmacro %}

{% macro mdp_song_first_artist(column) -%}
{% if target.type == 'duckdb' %}json_extract_string({{ column }}, '$[0]')
{% else %}(cast({{ column }} as jsonb) ->> 0){% endif %}
{%- endmacro %}

{# Movement rules have one named home in seeds/movement_parameters.csv. #}
{% macro mdp_movement_parameter(name) -%}
(select max(value) from {{ ref('movement_parameters') }} where name = {{ mdp_literal(name) }})
{%- endmacro %}

{% macro mdp_song_list_kind(owner, seeded_kind) -%}
case when {{ owner }} in ('editorial', 'chart') then coalesce({{ seeded_kind }}, {{ owner }}) else {{ owner }} end
{%- endmacro %}

{# Playlist adds and follower growth belong to the same independent family. #}
{% macro mdp_song_positive_families() -%}
(case when playlist_adds > 0 or follower_exposure_gain > 0 then 1 else 0 end
 + case when shazam_spread_gain > 0 then 1 else 0 end
 + case when stream_rate_gain > 0 then 1 else 0 end)
{%- endmacro %}

{# new_entries ranks emerging artists first among equal scores; the other lists ignore stage. #}
{% macro mdp_song_stage_order(alias) -%}
case when {{ alias }}.movement_list = 'new_entries' and {{ alias }}.artist_stage = 'emerging' then 0 else 1 end
{%- endmacro %}

{% macro mdp_song_age_parameter(name) -%}
(select max(value) from {{ ref('song_age_parameters') }} where name = {{ mdp_literal(name) }})
{%- endmacro %}

{% macro mdp_song_days(days) -%}
cast({{ days }} as text) || case when {{ days }} = 1 then ' day' else ' days' end
{%- endmacro %}

{# Fold identical printed titles and credits within a served list, without changing movement identity or scores. #}
{% macro mdp_song_fold_list(relation, columns, family=false) %}
with labeled as (
    select *,
        case when nullif(trim(title_text), '') is not null and nullif(trim(artist_text), '') is not null
            then {{ mdp_fold_name('title_text') }} else song_key end as title_group,
        case when nullif(trim(title_text), '') is not null and nullif(trim(artist_text), '') is not null
            then {{ mdp_fold_name('artist_text') }} else song_key end as artist_group
    from {{ relation }}
), picks as (
    select *, min(rank) over (partition by movement_list{% if family %}, family{% endif %}, title_group, artist_group) as kept_rank
    from labeled
), folds as (
    select movement_list{% if family %}, family{% endif %}, title_group, artist_group,
        {{ mdp_json_agg('song_key', 'rank') }} as folded_song_keys
    from picks where rank <> kept_rank group by 1, 2, 3{% if family %}, 4{% endif %}
)
select
{% for name in columns if name not in ['learning_eligible', 'resale_permitted', 'source_keys'] %}
    {% if name == 'evidence' %}
    cast(case when f.folded_song_keys is null then r.evidence else (
        select {{ mdp_json_agg(mdp_song_fold_evidence('item.value', 'f.folded_song_keys'), 'item.value') }}
        from (select r.evidence as locators) locators {{ mdp_json_elements('locators', 'item') }}
    ) end as text) as evidence,
    {% else %}r.{{ name }},{% endif %}
{% endfor %}
    r._source_keys
from picks r left join folds f using (movement_list{% if family %}, family{% endif %}, title_group, artist_group)
where r.rank = r.kept_rank
{% endmacro %}

{% macro mdp_song_fold_evidence(locator, keys) -%}
{% set details = mdp_json_object([('folded_song_keys', 'cast(' ~ keys ~ ' as ' ~ ('json' if target.type == 'duckdb' else 'jsonb') ~ ')')]) %}
{% if target.type == 'duckdb' %}json_merge_patch(cast({{ locator }} as json), {{ details }})
{% else %}(cast({{ locator }} as jsonb) || {{ details }}){% endif %}
{%- endmacro %}

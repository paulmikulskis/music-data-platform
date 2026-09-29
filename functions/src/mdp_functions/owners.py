"""Playlist owner rules, in one place for the parsers, staging, and the raw owner-name repair.

Each parser classes the owner it observes in the payload and lands that class as
`owner_class_observed`; a target's frozen label (`owner_class`) never decides an owner question. A row
landed before that column existed takes its observed class from the same payload evidence: Spotify's
own account or the embed's algotorial attribute, an Apple curator named "Apple Music ...",
SoundCloud's own account or a system playlist, and Bandcamp's own lists.

Only an owner observed to be the platform's own account (`PUBLIC_OWNERS`) keeps its id and name;
any other owner may be a private person. `mdp sources export` renders these expressions into
`dbt/macros/mdp_owner_rules.sql`, which staging calls; the repair applies the Postgres rendering to
landed rows, and the loader applies it to each dump it lands, so the served rule, the repaired rule,
and a re-landed old dump cannot drift.
"""

import hashlib
import json

PUBLIC_OWNERS = ("editorial", "chart", "dsp_algorithmic")
# Spotify's own editorial account (the only documented editorial owner id).
SPOTIFY_EDITORIAL_IDS = ("spotify",)
# SoundCloud's own account.
SOUNDCLOUD_ACCOUNT_ID = "193"
# Bandcamp's own lists and the class each parser observes for them.
BANDCAMP_OWN_SURFACES = {"bc_discover": "chart", "bc_daily_list": "editorial", "bc_radio": "editorial"}
# Lists whose owner is a platform column: its name is served, never a writer's byline.
PLATFORM_OWNER_NAMES = {"bc_daily_list": "Bandcamp Daily"}
# Apple's editorial curators are named "Apple Music ..." (the evidence for rows landed before the
# parser recorded the appleCurator kind as the observed class).
APPLE_EDITORIAL_PREFIX = "Apple Music"
# Apple's catalog playlist id; a listener's library list is `pl.u-...` and always a user's.
APPLE_CATALOG_ID = r"pl\.[0-9a-f]{32}"
APPLE_LIBRARY_PREFIX = "pl.u-"
# The bare owner link Apple's own charts and New Music Daily carry instead of an appleCurator link.
APPLE_PLATFORM_OWNER = "Apple Music"
# A listener profile id (`sp.<uuid>`); an appleCurator owner id is Apple's numeric store id.
APPLE_PROFILE_PREFIX = "sp."
APPLE_CURATOR_ID = r"[0-9]+"
DIALECTS = ("postgres", "duckdb")
# The raw tables that land an owner name.
NAMED_TABLES = ("raw.playlist_snapshots", "raw.playlist_candidates")
_ALGOTORIAL = '{"key":"isAlgotorial","value":"true"}'


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _list(values) -> str:
    return "(" + ",".join(_quote(v) for v in values) + ")"


def algotorial_attribute(dialect: str) -> str:
    """Spotify's embed marks a generated list with an attribute landed as the text 'true'."""
    if dialect == "postgres":
        return f"coalesce(cast(attributes as jsonb) @> {_quote('[' + _ALGOTORIAL + ']')}::jsonb, false)"
    return f"coalesce(json_contains(attributes, {_quote(_ALGOTORIAL)}), false)"


def apple_catalog_id(dialect: str) -> str:
    pattern = _quote("^" + APPLE_CATALOG_ID + "$")
    return f"playlist_id ~ {pattern}" if dialect == "postgres" else f"regexp_matches(playlist_id, {pattern})"


def apple_curator_id(dialect: str) -> str:
    pattern = _quote("^" + APPLE_CURATOR_ID + "$")
    return f"owner_id ~ {pattern}" if dialect == "postgres" else f"regexp_matches(owner_id, {pattern})"


def observed_class(dialect: str, recorded: str = "owner_class_observed") -> str:
    """The observed owner class of a raw.playlist_snapshots row. `recorded` is the landed column, or
    `null` where a warehouse predates it. For Apple, a listener profile id or a library list is a
    user's; a catalog list with an appleCurator (numeric) owner id is the platform's, and so is one
    landed with no owner id and a JSON-LD date (`platform_version`), which carried the bare "Apple
    Music" link (its name was dropped when an earlier parser read it as a user's), a chart only by its
    label."""
    bandcamp = "\n".join(
        f"        when fetch_surface = {_quote(surface)} then {_quote(cls)}"
        for surface, cls in BANDCAMP_OWN_SURFACES.items()
    )
    return f"""coalesce({recorded}, case
        when platform = 'spotify' and fetch_surface = 'sp_playlist_embed' and {algotorial_attribute(dialect)} then 'dsp_algorithmic'
        when platform = 'spotify' and owner_id in {_list(SPOTIFY_EDITORIAL_IDS)} then 'editorial'
        when platform = 'spotify' and owner_id is not null then 'user'
        when platform = 'apple_music' and playlist_id like {_quote(APPLE_LIBRARY_PREFIX + '%')} then 'user'
        when platform = 'apple_music' and owner_id like {_quote(APPLE_PROFILE_PREFIX + '%')} then 'user'
        when platform = 'apple_music' and owner_name like {_quote(APPLE_EDITORIAL_PREFIX + '%')} then 'editorial'
        when platform = 'apple_music' and {apple_catalog_id(dialect)} and {apple_curator_id(dialect)} then 'editorial'
        when platform = 'apple_music' and {apple_catalog_id(dialect)} and owner_id is null and platform_version is not null then 'editorial'
        when platform = 'soundcloud' and playlist_id like 'soundcloud:%' then 'dsp_algorithmic'
        when platform = 'soundcloud' and owner_id = {_quote(SOUNDCLOUD_ACCOUNT_ID)} then 'editorial'
{bandcamp}
        else 'unknown' end)"""


def is_public(observed: str) -> str:
    return f"{observed} in {_list(PUBLIC_OWNERS)}"


def served_owner_name(observed: str) -> str:
    """The owner name a mart may serve: the platform column's name, the landed name of a platform
    owner, or none."""
    columns = "\n".join(
        f"    when fetch_surface = {_quote(surface)} then {_quote(name)}"
        for surface, name in PLATFORM_OWNER_NAMES.items()
    )
    return f"""case
{columns}
    when {is_public(observed)} then owner_name end"""


def served_class(observed: str) -> str:
    """The served class: the observed one. A frozen label fills only an observed unknown and may
    refine editorial to chart; it never replaces an observed dsp_algorithmic or user."""
    return f"""case when {observed} = 'unknown' then coalesce(owner_class, 'unknown')
    when {observed} = 'editorial' and owner_class = 'chart' then 'chart'
    else {observed} end"""


def landing_redaction(table: str, dialect: str, recorded: str) -> str:
    """The UPDATE the loader runs on the rows of a `NAMED_TABLES` dump it just landed, bound to the
    dump id, so a repair or backfill that re-lands a dump from before the rule leaves the names the
    repair left. A dump landed under the rule is unchanged. `recorded` is as for `observed_class`."""
    param = "%s" if dialect == "postgres" else "?"
    if table == "raw.playlist_candidates":
        return ("UPDATE raw.playlist_candidates SET hint_owner_name = NULL "
                f"WHERE _dump_id = {param} AND hint_owner_name IS NOT NULL")
    served = served_owner_name(observed_class(dialect, recorded))
    if dialect == "postgres":
        served = served.replace("%", "%%")
    return (f"UPDATE raw.playlist_snapshots SET owner_name = ({served}) WHERE _dump_id = {param} "
            f"AND owner_name IS NOT NULL AND owner_name IS DISTINCT FROM ({served})")


def rule_version() -> str:
    """A hash of every rendering and constant the rules use, recorded with each repair."""
    parts = {
        "public": PUBLIC_OWNERS,
        "names": PLATFORM_OWNER_NAMES,
        "observed": {d: observed_class(d) for d in DIALECTS},
        "served_name": served_owner_name("observed_class"),
        "served_class": served_class("observed_class"),
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def dbt_macros() -> str:
    """The generated `dbt/macros/mdp_owner_rules.sql`."""
    postgres, duckdb = (observed_class(d) for d in DIALECTS)
    arg = "{{ observed }}"
    macros = [
        ("mdp_owner_observed_class()",
         "{%- if target.type == 'postgres' -%}\n" + postgres + "\n{%- else -%}\n" + duckdb + "\n{%- endif -%}"),
        ("mdp_owner_is_public(observed)", is_public(arg)),
        ("mdp_owner_served_name(observed)", served_owner_name(arg)),
        ("mdp_owner_served_class(observed)", served_class(arg)),
    ]
    header = "{# Generated by `mdp sources export` from mdp_functions/owners.py (rule " + rule_version()[:12] + "); do not edit. #}\n"
    return header + "".join(
        "\n{% macro " + name + " -%}\n" + body + "\n{%- endmacro %}\n" for name, body in macros
    )

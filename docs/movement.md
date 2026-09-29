# Movement

Read `marts.mart_arrivals_current` for songs entering tracked lists or charts.
Filter `movement_list` to select a list. Use the explicit columns in showcase queries.


## Lists

Movement splits into three lists that never mix. Each list scores and ranks only its own songs.

| `movement_list` | Songs |
|---|---|
| `new_entries` | New songs by emerging, developing or unknown-stage artists |
| `established_entries` | New songs by established artists with a catalog reading |
| `catalog_entries` | Catalog songs entering lists or charts again |
| `unplaced` | Unknown age and on no new-music list in the window |

For example, a song with a 1998 ISRC that enters Top 50 USA lands in `catalog_entries`, never in `new_entries`.
A song of unknown age on Fresh Finds lands in `new_entries` with `age_class = 'unknown'`.
People set every rule and weight here. Nothing is learned.


## List kinds

`owner_class` stays the platform's label.
Movement reads its own `list_kind` from [list tiers](../dbt/seeds/playlist_reach_tiers.csv):

| `list_kind` | Lists | Parameter for add points |
|---|---|---|
| `chart` | Top 50, Top 100, Viral Hits and Shazam charts | No add points |
| `new_music` | Lists built for new releases: New Music Friday, Fresh Finds, Up Next, Breaking lists | `editorial_add_points` |
| `editorial` | Other editorial lists | `editorial_add_points` |

`list_kind` refines only a list the platform labels editorial or chart.
A user or algorithmic list keeps its platform label, so a seed row never makes it count.
A list with no seed row keeps its platform label. An algorithmic add reads `algorithmic_add_points`.
To reclassify a list, edit its `list_kind` and run `dbt seed`.


## Release age

No table holds release dates. `age_class` comes from a proxy, and `age_basis` names it:

1. `isrc_year`: characters 6 and 7 of the ISRC, the year the code was issued. The oldest code on a
   song wins. A reissue gets a new code, so this proxy can make a song look newer, never older.
2. `apple_id_band`: the `apple_new_id_min` and `apple_new_year_min` rows define the newer band.
   The `apple_catalog_id_max` and `apple_catalog_year_max` rows define the older band.
   [Song age parameters](../dbt/seeds/song_age_parameters.csv) records each value and `measured_on`.
   Ids between the bands stay unknown. Remeasure the bands before the next calendar year.
3. `none`: no proxy, so `age_class` is `unknown`.

`new` uses `isrc_new_max_years` from the same seed. `catalog` means older.
The `song_age_parameters_current` test fails once the cycle year passes `measured_on`.
Review the ISRC rule and remeasure the Apple bands, then run `dbt seed`.
Neither proxy is a release date. Write "ISRC year 1998", never "released in 1998".
A song of unknown age that sits on a `new_music` list in the window counts as new for its list.
It does not need to have entered the list in the window.


## Artist stage

`artist_stage` comes from MusicBrainz catalog depth, and `artist_stage_basis` reads `musicbrainz_catalog`.
Once a week, `mb_artist_catalog` reads each credited artist on the private mirror.
It counts the release groups that credit the artist by kind (Album, Single, EP; Compilation, Live and so on)
and finds the year of the earliest dated release.
The rules sit in [artist stage thresholds](../dbt/seeds/artist_stage_thresholds.csv), and
[artist stage](../dbt/models/intermediate/int_artist_stage__daily.sql) applies them:

| Stage | Seed rule |
|---|---|
| `established` | `established_min_release_groups` counted kinds (`established_counted_primary_types`, less `established_excluded_secondary_types`), or `established_min_years` since the first release |
| `emerging` | At most `emerging_max_release_groups` groups and either no dated release or a first release within `emerging_max_years` |
| `developing` | Any other artist MusicBrainz holds |
| `unknown` | No linked MusicBrainz artist, a special purpose artist such as Various Artists or [anonymous], or no reading yet |

For example, an act with a long catalog reads `established`
and lands in `established_entries`. An act with two singles in 2025 reads `emerging`.
An act two years old with ten singles reads `developing` and stays in `new_entries`.

A song takes its most established credited artist, so "A feat. B" reads `established` when B is.
The song stays `unknown` until every credited artist has a reading, so a half-read song never reads `emerging`.
Credited artists come from the song's recording credit and each platform track's first artist
([song artists](../dbt/models/intermediate/int_song_artists__daily.sql)).
A new lookup reaches movement at the next daily close, so a failed lookup leaves the older reading.
Open `int_artist_catalog__receipts` to see this cycle's lookup run.
To change a rule, edit its value in the seed and run `dbt seed`.
Playlist data never sets a stage: it would call a veteran with one tracked song emerging.

`mb_artist_catalog` ships disabled, like the other mirror functions. Until an operator turns it on
(the `enabled` knob on its streamline, after `MDP_MB_DB_URL` is set on mdp-functions), every stage reads `unknown`
and new songs stay in `new_entries`.


## Known limits

Movement groups combine copies only where the grouping rules have evidence.
Identical printed titles and credits can still sit on separate groups.
Within each arrival list or early-signal family, the better-ranked group keeps its place.
Its evidence names the other groups in `folded_song_keys`. Scores and identity stay separate.
Open the song's evidence to inspect those keys.

Only established artists with catalog readings move from `new_entries` to `established_entries`.
Other established artists can still appear with an unknown stage.
`mart_readiness` has an `artist_stage:<movement_list>` row for each current list.
`stage_known_songs` counts songs with a reading; `list_songs` includes unknown songs.
It counts each served song once across current movers, early signals and arrivals.
The showcase shows these counts beside each list. Open a song to inspect its reading.


## Scoring

Add points follow `list_kind` (see [list kinds](#list-kinds)).
An entry into a visible list head multiplies those points by `head_confidence`.
Each list counts once per song and window, across variants and repeated entries.
Baseline observations do not prove an arrival. Open `evidence` for the event and its input build.

`follower_exposure_gain` sums the followers of entered lists at entry time.
It keeps its existing API name. A stationary song receives no gain when its list grows.
An absent song has zero exposure when its platform is collected that day.
A list without a follower count has unknown exposure. Open the list profile to inspect its count.
`new_entries` counts editorial and new-music lists only, for reach and for list tier. Chart lists count
through chart spread instead.
Adds and reach inside the window still count on a day the platform was not collected.
For example, an Apple song that entered Up Next yesterday stays in `new_entries` when the Apple collector misses today.

Each family uses observed history minus one day, capped by `window_max_days`.
Missing collection days add no history. The window still measures UTC calendar days.
New Shazam entries need `shazam_min_observed_days` within `shazam_history_days`.
Shazam absence is zero on a collected day. Its first collection is a baseline.
Spread compares with the latest observed chart day at or before the window start.
Early Shazam signals report the elapsed days back to that observation, including any collection gap.
A missed day stays missing and never becomes zero. Open song history to inspect the comparison.
Stream comparisons use two equally long halves, each capped by `window_max_days`.
Each half needs `stream_min_days` rated days. A rated day uses the raw provider daily increment.
Read intervals outside `stream_interval_min_hours` and `stream_interval_max_hours`,
counter resets and collection gaps have no rate.
Open `mart_readiness` for the earliest possible comparison date.

Chart spread counts newly observed country markets on chart lists and Shazam charts.
A market counts once across providers. Global charts have no country market.
Only the list tier seed names a list's country; the storefront a list was fetched from is not a market.
This component stays outside the two-family score because it overlaps playlists and Shazam.
Open arrivals to see the country list and its entry evidence.

Mover weights read `playlist_adds_weight`, `follower_exposure_gain_weight`,
`shazam_spread_gain_weight` and `stream_rate_gain_weight` from
[movement parameters](../dbt/seeds/movement_parameters.csv).
Unseeded list tiers read `tier_one_followers` and `tier_two_followers` from the same seed.
Edit a named row and run `dbt seed` to change a rule.
Positive movement in two independent families is required.
Early signals carry exactly one positive family.
Mover percentiles compare a song only with songs in its own list.
`established_entries` and `catalog_entries` rank by chart spread first, then by score. `new_entries` never uses it.
In `new_entries`, an emerging artist ranks first among equal scores. Arrivals carry no score,
so emerging arrivals lead `new_entries`. The other lists ignore stage.
Ties use the best entered list tier, then country count, then latest entry time.
Exact remaining ties order by `md5(day || song_key)`. The order is fixed for a day and unrelated to key order,
so no key prefix sorts first. `mart_arrivals_current.rank` uses the same order without a score.
See [list tiers](../dbt/seeds/movement.yml) before adding a list.


## Shazam countries and Apple replacements

Shazam countries use ISO codes in `mart_shazam_chart_daily` and movement entries.
For example, `united-kingdom` becomes `GB` through `dbt/seeds/shazam_markets.csv`.
The chart key keeps its original path.
`WORLD`, `GLOBAL` and unknown slugs have no country and cannot add a market.
Several city charts in one country count as one market.
Open `mart_arrivals_current.markets` to see the countries entered.

`apple_id_successor` is a provisional movement rule in `song_cluster_rules.csv`.
It compares adjacent calendar days on the same Shazam chart.
The old id must disappear from all observed Shazam charts before the new id first appears.
Folded titles and full credits must agree.
Primary Apple artist ids must agree when present; two missing ids can use the full credit.
Different nonempty ISRCs refuse a match, including conflicts within either id's history.
Each id must have exactly one candidate partner across all charts.
The rank distance is at most `apple_successor_position_fraction` of the smaller observed chart size.
That seed is 0.2: up to 40 positions on a 200-position chart, or 10 on a 50-position chart.
Open `int_song_apple_successors__daily` to inspect candidate id pairs.

The rule stays off until its complete census clears the movement precision gate.
The seed records a prior deployment measurement; its underlying observations are not included in this snapshot.
The required lower bound is 95%.
Open the audit before changing the switch.

When enabled, the rule uses the existing group recount.
Presence on a chart under the predecessor does not become an arrival under the successor.
A genuinely new chart still counts.
Strict song identity and `mart_song_aliases` stay unchanged because a provisional chart match
is insufficient evidence for a permanent identity redirect.
Use `mart_song_cluster_members` to find the movement representative of either strict key.


## Measured precision

`cluster_confidence` reports measured precision as the lowest Wilson 95% lower bound
among a group's merge rules, not the probability that a particular group is correct.
The seed values, measured on a prior deployment, are 0.9690 for `title_artist_duration`, 0.9603 for `isrc_crosswalk`
(including native ISRC matches), 0.9259 for `apple_variant`, and 0.4385 for
`apple_id_successor`; the last two rules stay off.
Singletons carry one because no merge occurs.
Open the cluster audit and
successor audit for sample counts.

Mover components use `cume_dist()` among positive values in their day's movement list.
Two tied lowest values among three receive 2/3; a singleton receives one.
Every qualified mover has a positive score.
Open `mart_top_movers.score_parts` to inspect the weighted parts.

{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}

-- The wiki_pageviews input: QID x project x title from each current act QID's newest weekly
-- lookup, so a renamed article is fetched under its new title and keeps its series by QID and project.
-- The ids come from the spine and the titles from wiki_sitelinks, so the input declares both source keys.
-- Its version carries the bound cycle's day: each daily cycle refetches the three days before it. An act
-- reads at most PROJECTS_PER_ACT articles (functions wikimedia.py): its editions in seeds/wiki_projects.csv
-- rank order, then the rest by project; project_rank is the article's place in that order, which
-- wiki_pageviews reads by (input_order), so under its time budget every act's first edition comes first.
{% set projects_per_act = 10 %}
with qids as (
    select distinct qid from {{ ref('int_wiki__artist_qids') }}
), newest as (
    select s.*, max(s.sitelinks_week) over (partition by s.qid) as newest_week
    from {{ ref('stg_wiki__sitelinks') }} s
    join qids q on q.qid = s.qid
), articles as (
    select qid, project, max(title) as title
    from newest
    where sitelinks_week = newest_week
    group by qid, project
), ranked as (
    select a.*, row_number() over (partition by a.qid order by coalesce(p.rank, 1000), a.project) as project_rank
    from articles a
    left join {{ ref('wiki_projects') }} p on p.project = a.project
), inputs as (
    select a.*, c.pageview_day
    from ranked a
    cross join (select {{ mdp_cycle_day() }} as pageview_day) c
    where a.project_rank <= {{ projects_per_act }}
)
select
    cast(qid as text) as qid,
    cast(project as text) as project,
    cast(title as text) as title,
    cast(project_rank as integer) as project_rank,
    cast(pageview_day as date) as pageview_day,
    cast('["mb_spine","wiki_sitelinks"]' as text) as _source_keys,
    {{ mdp_input_identity(['qid', 'project'], ['title', 'pageview_day']) }}
from inputs

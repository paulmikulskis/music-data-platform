select platform,playlist_id,variant
from {{ ref('mart_playlist_membership_current') }}
group by 1,2,3
having count(distinct stream)<>1 or count(*)<>count(distinct occurrence_key)

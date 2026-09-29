select platform,playlist_id,variant,stream,occurrence_key,interval_id,event_type,observed_at,count(*) as n
from {{ ref('mart_playlist_events') }}
group by 1,2,3,4,5,6,7,8 having count(*) > 1

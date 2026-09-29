-- Every qualified mover has positive movement. Open score_parts for the weighted components.
select song_key, day, momentum_score
from {{ ref('mart_top_movers') }}
where momentum_score is null or momentum_score <= 0 or momentum_score > 1

"""Offline identity validation on operator-supplied, independent truth rows."""
import json
import math
import re
import unicodedata


def fold(value):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().lower())


def verdict(row, truth):
    if truth is None:
        return 'ambiguous', 'missing_recording'
    if row.get('platform_isrc'):
        return ('correct', 'isrc_agrees') if fold(row['platform_isrc']) in [fold(x) for x in truth.get('isrcs', [])] else ('wrong', 'isrc_disagrees')
    artists = row.get('artist_names') or []
    if isinstance(artists, str):
        artists = json.loads(artists)
    if not artists or any(truth.get(k) is None for k in ('title', 'primary_credit', 'duration_ms')) or row.get('duration_ms') is None:
        return 'ambiguous', 'missing_metadata'
    for ok, reason in [(fold(row['title']) == fold(truth['title']), 'title'), (fold(artists[0]) == fold(truth['primary_credit']), 'primary_artist'), (abs(row['duration_ms'] - truth['duration_ms']) <= 3000, 'duration')]:
        if not ok:
            return 'wrong', reason + '_disagrees'
    return 'correct', 'metadata_agrees'


def wilson(successes, total):
    if not total:
        return None
    z = 1.96
    p = successes / total
    center = p + z*z/(2*total)
    margin = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total))
    denominator = 1 + z*z/total
    return (center-margin)/denominator, (center+margin)/denominator

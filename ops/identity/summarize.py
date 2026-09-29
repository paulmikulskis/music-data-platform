"""Select a confidence floor without treating unknown truth as a failure."""
from measure import wilson


def choose_floor(rows):
    for floor in sorted({.9, *(float(r['recording_confidence']) for r in rows if float(r['recording_confidence']) >= .9)}):
        known = [r for r in rows if float(r['recording_confidence']) >= floor and r['verdict'] != 'ambiguous']
        interval = wilson(sum(r['verdict'] == 'correct' for r in known), len(known))
        if interval is None or len(known) < 125 or interval[0] >= .97:
            return floor, interval
    return 1.01, None

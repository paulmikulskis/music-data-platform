"""Validate a full annotation census before calculating identity precision."""
RELAXED = {'title_suffix_only', 'artist_alias_or_credit_order', 'duration_only'}
CLASSES = RELAXED | {'different_recording_same_work', 'real_mislink', 'other_title_text'}


def key(row):
    return tuple(row[k] for k in ('platform', 'platform_track_id', 'mb_recording_gid', 'recording_method'))


def summarize(rows, labels):
    wrong = {key(r) for r in rows if r['verdict'] == 'wrong'}
    annotations = {key(r): r['class'] for r in labels}
    assert len(annotations) == len(labels)
    assert set(annotations) == wrong
    assert set(annotations.values()) <= CLASSES
    def summary(group):
        known = [r for r in group if r['verdict'] != 'ambiguous']
        correct = sum(r['verdict'] == 'correct' for r in known)
        relaxed = sum(annotations.get(key(r)) in RELAXED for r in known)
        mislinks = sum(annotations.get(key(r)) == 'real_mislink' for r in group)
        residual = sum(r['verdict'] == 'wrong' and annotations[key(r)] not in RELAXED for r in group)
        return {'strict_precision': correct / len(known) if known else None, 'relaxed_precision': (correct + relaxed) / len(known) if known else None, 'real_mislinks': mislinks, 'real_mislink_share': mislinks / len(group) if group else None, 'all_residual_share': residual / len(group) if group else None}
    return summary([r for r in rows if r['original_sample']]), summary(rows)

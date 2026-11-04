from __future__ import annotations

from datetime import datetime
from typing import Any


MAX_PATCH_CHANGES = 200


def build_segment_edit_patch(
    before_segments: list[dict[str, Any]],
    after_segments: list[dict[str, Any]],
    *,
    source: str,
    actor: str = 'client',
) -> dict[str, Any]:
    before_by_id = {
        _segment_key(item, index): item
        for index, item in enumerate(before_segments or [])
    }
    changes: list[dict[str, Any]] = []
    for index, after in enumerate(after_segments or []):
        segment_id = _segment_key(after, index)
        before = before_by_id.get(segment_id) or {}
        for field in ('speakerKey', 'text', 'startMs', 'endMs'):
            if before.get(field) != after.get(field):
                changes.append({
                    'segment_id': segment_id,
                    'field': field,
                    'before': before.get(field),
                    'after': after.get(field),
                })

    return {
        'schema_version': 1,
        'source': str(source or 'unknown'),
        'actor': str(actor or 'client'),
        'created_at': datetime.utcnow().replace(microsecond=0).isoformat() + 'Z',
        'changed_count': len(changes),
        'truncated': len(changes) > MAX_PATCH_CHANGES,
        'changes': changes[:MAX_PATCH_CHANGES],
    }


def _segment_key(segment: dict[str, Any], index: int) -> str:
    return str(segment.get('segmentId') or segment.get('id') or index)

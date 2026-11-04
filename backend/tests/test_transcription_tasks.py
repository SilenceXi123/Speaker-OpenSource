from app.services.transcription_tasks import (
    _can_reclaim_finalize_lease_owner,
    _fetch_transcription_payload_parts,
)


def test_fetch_transcription_payload_parts_preserves_order() -> None:
    class FakeClient:
        def fetch_transcription_payload(self, transcription_url: str) -> dict:
            return {'url': transcription_url}

    result = _fetch_transcription_payload_parts(FakeClient(), [
        {'transcription_url': 'https://example.com/part-1.json', 'offset_ms': 0, 'speaker_offset': 0},
        {'transcription_url': 'https://example.com/part-2.json', 'offset_ms': 1000, 'speaker_offset': 1000},
        {'transcription_url': 'https://example.com/part-3.json', 'offset_ms': 2000, 'speaker_offset': 2000},
    ])

    assert result == [
        ({'url': 'https://example.com/part-1.json'}, 'https://example.com/part-1.json', 0, 0),
        ({'url': 'https://example.com/part-2.json'}, 'https://example.com/part-2.json', 1000, 1000),
        ({'url': 'https://example.com/part-3.json'}, 'https://example.com/part-3.json', 2000, 2000),
    ]


def test_finalize_lease_recovery_accepts_legacy_current_and_dead_process_owners() -> None:
    def alive(process_id: int) -> bool:
        return process_id == 222

    assert _can_reclaim_finalize_lease_owner(
        'job-1',
        'job-finalize:job-1',
        current_process_id=111,
        process_is_alive=alive,
    )
    assert _can_reclaim_finalize_lease_owner(
        'job-1',
        'job-finalize:job-1:pid:111',
        current_process_id=111,
        process_is_alive=alive,
    )
    assert _can_reclaim_finalize_lease_owner(
        'job-1',
        'job-finalize:job-1:pid:333',
        current_process_id=111,
        process_is_alive=alive,
    )


def test_finalize_lease_recovery_preserves_live_or_unrelated_owners() -> None:
    def alive(process_id: int) -> bool:
        return process_id == 222

    assert not _can_reclaim_finalize_lease_owner(
        'job-1',
        'job-finalize:job-1:pid:222',
        current_process_id=111,
        process_is_alive=alive,
    )
    assert not _can_reclaim_finalize_lease_owner(
        'job-1',
        'job-finalize:another-job:pid:333',
        current_process_id=111,
        process_is_alive=alive,
    )
    assert not _can_reclaim_finalize_lease_owner(
        'job-1',
        'job-finalize:job-1:pid:not-a-number',
        current_process_id=111,
        process_is_alive=alive,
    )

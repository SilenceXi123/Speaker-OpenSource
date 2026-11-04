from decimal import Decimal
from pathlib import Path

from app.core.config import settings
from app.schemas.provider_cost import ProviderCallContext
from app.services import oss_storage, render
from app.services.oss_storage import build_public_url


def test_build_public_url_uses_public_base_url() -> None:
    object_key = 'uploads/source/demo file.mp4'
    result = build_public_url(object_key)

    assert result.startswith(settings.oss_public_base_url.rstrip('/'))
    assert 'demo%20file.mp4' in result


def test_owned_oss_object_key_accepts_only_same_origin_base_path(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        'oss_public_base_url',
        'https://speaker-test.example.com/public/assets',
    )

    assert oss_storage.owned_oss_object_key(
        'https://speaker-test.example.com/public/assets/voices/demo%20voice.wav?token=ignored'
    ) == 'voices/demo voice.wav'
    assert oss_storage.owned_oss_object_key(
        'http://speaker-test.example.com/public/assets/voices/http.wav'
    ) is None
    assert oss_storage.owned_oss_object_key(
        'https://speaker-test.example.com:443/public/assets/voices/default-port.wav'
    ) == 'voices/default-port.wav'
    assert oss_storage.owned_oss_object_key(
        'https://speaker-test.example.com:8443/public/assets/voices/other-port.wav'
    ) is None
    assert oss_storage.owned_oss_object_key(
        'https://external.example.com/public/assets/voices/demo.wav'
    ) is None
    assert oss_storage.owned_oss_object_key(
        'https://speaker-test.example.com/public/assets-other/demo.wav'
    ) is None
    assert oss_storage.owned_oss_object_key(
        'ftp://speaker-test.example.com/public/assets/voices/demo.wav'
    ) is None
    assert oss_storage.owned_oss_object_key(
        'https://speaker-test.example.com/public/assets/%2e%2e/private.wav'
    ) is None
    monkeypatch.setattr(
        settings,
        'oss_public_base_url',
        'ftp://speaker-test.example.com/public/assets',
    )
    assert oss_storage.owned_oss_object_key(
        'https://speaker-test.example.com/public/assets/voices/demo.wav'
    ) is None


def _context() -> ProviderCallContext:
    return ProviderCallContext(project_id='project-1', pipeline_scope='project_asset')


def test_oss_put_and_delete_record_object_and_request_facts(monkeypatch) -> None:
    observations = []

    class FakeResult:
        status = 200
        request_id = 'oss-request-1'

    class FakeBucket:
        def put_object(self, *_args, **_kwargs):
            return FakeResult()

        def delete_object(self, *_args, **_kwargs):
            return FakeResult()

    monkeypatch.setattr(settings, 'oss_access_key_id', 'test-id')
    monkeypatch.setattr(settings, 'oss_access_key_secret', 'test-secret')
    monkeypatch.setattr(settings, 'oss_region', 'oss-cn-test')
    monkeypatch.setattr(settings, 'oss_bucket', 'speaker-test')
    monkeypatch.setattr(
        settings,
        'oss_public_base_url',
        'https://speaker-test.invalid/public',
    )
    monkeypatch.setattr(oss_storage.oss2, 'Auth', lambda *_args: object())
    monkeypatch.setattr(oss_storage.oss2, 'Bucket', lambda *_args: FakeBucket())
    monkeypatch.setattr(
        oss_storage,
        'record_legacy_provider_observation',
        lambda context, **kwargs: observations.append((context, kwargs)),
    )

    result = oss_storage._put_bytes_to_oss(
        payload=b'123456',
        object_key='uploads/source/demo.wav',
        content_type='audio/wav',
        provider_context=_context(),
        observation_key='oss:put:demo',
    )
    oss_storage.delete_oss_object(
        'uploads/source/demo.wav',
        provider_context=_context(),
        observation_key='oss:delete:demo',
        object_size_bytes=6,
    )

    assert result.size_bytes == 6
    put = observations[0][1]
    delete = observations[1][1]
    assert put['provider'] == delete['provider'] == 'aliyun_oss'
    assert put['model'] == delete['model'] == 'speaker-test'
    assert put['operation'] == 'put_object'
    assert delete['operation'] == 'delete_object'
    assert put['observed_call'].request_id == 'oss-request-1'
    assert put['observed_call'].usage == {'object_bytes': 6, 'request_count': 1}
    assert {meter.meter: meter.quantity for meter in put['observed_call'].meters} == {
        'storage_bytes': Decimal('6'),
        'request_count': Decimal('1'),
    }
    assert delete['observed_call'].usage == {'object_bytes': 6, 'request_count': 1}


def test_oss_download_records_actual_egress_without_signed_url(monkeypatch, tmp_path) -> None:
    captured = {}
    signed_url = 'https://speaker-test.oss-cn-beijing.aliyuncs.com/source.wav?Signature=secret'

    class FakeResponse:
        status_code = 200
        headers = {'x-oss-request-id': 'oss-get-request-1'}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def iter_bytes():
            return iter((b'123', b'4567'))

    monkeypatch.setattr(settings, 'oss_public_base_url', 'https://speaker-test.oss-cn-beijing.aliyuncs.com')
    monkeypatch.setattr(settings, 'oss_bucket', 'speaker-test')
    monkeypatch.setattr(render.httpx, 'stream', lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(
        render,
        'record_legacy_provider_observation',
        lambda context, **kwargs: captured.update(context=context, **kwargs),
    )

    target = render.download_remote_file(
        signed_url,
        Path(tmp_path),
        provider_context=_context(),
        observation_key='oss:get:source-wav',
    )

    observation = captured['observed_call']
    assert target.read_bytes() == b'1234567'
    assert captured['provider'] == 'aliyun_oss'
    assert captured['operation'] == 'get_object'
    assert observation.request_id == 'oss-get-request-1'
    assert observation.usage == {'egress_bytes': 7, 'request_count': 1}
    assert 'Signature' not in str(observation)
    assert 'secret' not in str(observation)


def test_oss_fact_writer_failure_does_not_change_upload_result(monkeypatch) -> None:
    class FakeResult:
        status = 200
        request_id = 'oss-request-2'

    class FakeBucket:
        def put_object(self, *_args, **_kwargs):
            return FakeResult()

    monkeypatch.setattr(settings, 'oss_access_key_id', 'test-id')
    monkeypatch.setattr(settings, 'oss_access_key_secret', 'test-secret')
    monkeypatch.setattr(oss_storage.oss2, 'Auth', lambda *_args: object())
    monkeypatch.setattr(oss_storage.oss2, 'Bucket', lambda *_args: FakeBucket())
    monkeypatch.setattr(
        oss_storage,
        'record_legacy_provider_observation',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('writer failed token=secret')),
    )

    result = oss_storage._put_bytes_to_oss(
        payload=b'payload',
        object_key='uploads/source/demo.wav',
        content_type='audio/wav',
        provider_context=_context(),
        observation_key='oss:put:writer-failure',
    )

    assert result.size_bytes == 7

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import admin_knowledge as admin_knowledge_api
from app.core.config import settings
from app.db.session import Base
from app.models.canonical_import_batch import CanonicalImportBatch
from app.models.canonical_passage import CanonicalPassage
from app.models.canonical_passage_variant import CanonicalPassageVariant
from app.services.admin_auth import build_admin_session_token
from app.services.canonical_knowledge_admin import (
    CanonicalImportItem,
    build_knowledge_dashboard,
    get_knowledge_passage,
    import_canonical_candidates,
    list_knowledge_passages,
    load_chinese_poetry_selection,
    review_all_passages,
    review_passages,
)
from main import app


def _session_factory():
    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _items() -> list[CanonicalImportItem]:
    return [
        CanonicalImportItem(
            title='静夜思',
            author='李白',
            dynasty='唐',
            genre='五言绝句',
            canonical_text='床前明月光，\n疑是地上霜。\n举头望明月，\n低头思故乡。',
            source_ref='https://example.test/tang#1',
        ),
        CanonicalImportItem(
            title='静夜思',
            author='李白',
            dynasty='唐',
            genre='五言绝句',
            canonical_text='床前看月光，\n疑是地上霜。\n举头望山月，\n低头思故乡。',
            source_ref='https://example.test/tang#2',
        ),
        CanonicalImportItem(
            title='春晓',
            author='孟浩然',
            dynasty='唐',
            genre='五言绝句',
            canonical_text='春眠不觉晓，\n处处闻啼鸟。\n夜来风雨声，\n花落知多少。',
            source_ref='https://example.test/tang#3',
        ),
    ]


def _import(session):
    return import_canonical_candidates(
        session,
        tenant_id='default',
        source_name='chinese-poetry/chinese-poetry',
        source_url='https://github.com/chinese-poetry/chinese-poetry',
        source_version='abcdef1234567890',
        source_license='MIT',
        selection_label='精选测试',
        items=_items(),
        created_by='tester',
    )


def test_load_chinese_poetry_selection_converts_traditional_and_preserves_lines(tmp_path) -> None:
    source = tmp_path / 'tang.json'
    source.write_text(json.dumps([{
        'id': 'poem-1',
        'title': '靜夜思',
        'author': '李白',
        'paragraphs': ['牀前明月光，', '疑是地上霜。'],
        'tags': ['唐诗三百首', '五言絕句'],
    }], ensure_ascii=False), encoding='utf-8')

    items = load_chinese_poetry_selection(
        source,
        dynasty='唐',
        genre='唐诗',
        source_path='全唐诗/唐诗三百首.json',
        source_version='abcdef1',
    )

    assert len(items) == 1
    assert items[0].title == '静夜思'
    assert items[0].canonical_text == '床前明月光，\n疑是地上霜。'
    assert items[0].metadata['source_item_id'] == 'poem-1'


def test_import_candidates_is_pending_deduplicated_and_tracks_variants() -> None:
    factory = _session_factory()
    session = factory()
    try:
        report = _import(session)
        session.commit()

        assert report.reused is False
        assert report.batch.status == 'completed'
        assert report.batch.candidate_count == 3
        assert report.batch.passage_count == 2
        assert report.batch.variant_count == 1
        assert report.batch.duplicate_count == 0
        assert session.scalar(select(CanonicalPassage).where(
            CanonicalPassage.title == '静夜思'
        )).review_status == 'pending'
        assert session.scalar(select(CanonicalPassageVariant)).review_status == 'pending'

        reused = _import(session)
        assert reused.reused is True
        assert reused.batch.id == report.batch.id
        assert session.query(CanonicalImportBatch).count() == 1
        assert session.query(CanonicalPassage).count() == 2
        assert session.query(CanonicalPassageVariant).count() == 1
    finally:
        session.close()


def test_same_author_and_title_with_different_content_are_separate_works() -> None:
    factory = _session_factory()
    session = factory()
    try:
        report = import_canonical_candidates(
            session,
            tenant_id='default',
            source_name='test-source',
            source_url='https://example.test',
            source_version='abcdef123',
            source_license='test',
            selection_label='同词牌测试',
            items=[
                CanonicalImportItem(
                    title='浣溪沙', author='苏轼', dynasty='宋', genre='宋词',
                    canonical_text='山下兰芽短浸溪，松间沙路净无泥。',
                ),
                CanonicalImportItem(
                    title='浣溪沙', author='苏轼', dynasty='宋', genre='宋词',
                    canonical_text='簌簌衣巾落枣花，村南村北响缫车。',
                ),
            ],
        )
        session.commit()

        assert report.batch.passage_count == 2
        assert report.batch.variant_count == 0
        assert session.query(CanonicalPassage).count() == 2
        assert session.query(CanonicalPassageVariant).count() == 0
    finally:
        session.close()


def test_review_promotes_only_confirmed_passage_to_automatic_knowledge() -> None:
    factory = _session_factory()
    session = factory()
    try:
        _import(session)
        passage = session.scalar(select(CanonicalPassage).where(
            CanonicalPassage.title == '静夜思'
        ))
        review_passages(
            session,
            tenant_id='default',
            passage_ids=[passage.id],
            review_status='approved',
            reviewed_by='admin',
        )
        session.commit()

        dashboard = build_knowledge_dashboard(session, tenant_id='default')
        assert dashboard['total'] == 2
        assert dashboard['review_status'] == {
            'approved': 1,
            'pending': 1,
            'rejected': 0,
        }
        assert dashboard['automatic_eligible'] == 1
        session.refresh(passage)
        assert passage.source_type == 'manually_reviewed'
        assert passage.source_priority == 100
        assert passage.reviewed_by == 'admin'

        review_passages(
            session,
            tenant_id='default',
            passage_ids=[passage.id],
            review_status='rejected',
            reviewed_by='admin',
        )
        session.commit()
        assert passage.source_type == 'community_dataset'
        assert passage.source_priority == 40
        assert build_knowledge_dashboard(session, tenant_id='default')['automatic_eligible'] == 0
    finally:
        session.close()


def test_review_all_passages_applies_current_filters() -> None:
    factory = _session_factory()
    session = factory()
    try:
        _import(session)
        updated_count = review_all_passages(
            session,
            tenant_id='default',
            review_status='approved',
            reviewed_by='admin',
            matching_review_status='pending',
            query='李白',
        )
        session.commit()

        assert updated_count == 1
        assert session.scalar(select(CanonicalPassage).where(
            CanonicalPassage.author == '李白'
        )).review_status == 'approved'
        assert session.scalar(select(CanonicalPassage).where(
            CanonicalPassage.author == '孟浩然'
        )).review_status == 'pending'
    finally:
        session.close()


def test_admin_list_search_and_detail_include_import_provenance() -> None:
    factory = _session_factory()
    session = factory()
    try:
        _import(session)
        session.commit()
        listed = list_knowledge_passages(
            session,
            tenant_id='default',
            query='李白',
            review_status='pending',
        )
        assert listed['count'] == 1
        passage_id = listed['items'][0]['id']
        assert listed['items'][0]['import_batch']['selection_label'] == '精选测试'
        detail = get_knowledge_passage(
            session,
            tenant_id='default',
            passage_id=passage_id,
        )
        assert detail is not None
        assert detail['variant_count'] == 1
        assert len(detail['variants']) == 1
    finally:
        session.close()


def test_admin_knowledge_api_requires_login_and_can_review(monkeypatch) -> None:
    factory = _session_factory()
    session = factory()
    try:
        _import(session)
        session.commit()
        passage_id = session.scalar(select(CanonicalPassage.id))
    finally:
        session.close()

    monkeypatch.setattr(settings, 'admin_username', 'admin')
    monkeypatch.setattr(settings, 'admin_password_hash', 'configured-password-hash')
    monkeypatch.setattr(settings, 'admin_session_secret', 'session-secret')
    monkeypatch.setattr(settings, 'admin_session_cookie_name', 'speaker_admin_session')
    monkeypatch.setattr(settings, 'admin_session_max_age_seconds', 3600)
    monkeypatch.setattr(admin_knowledge_api, 'SessionLocal', factory)
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get('/api/v1/admin/knowledge/summary').status_code == 401
    client.cookies.set(settings.admin_session_cookie_name, build_admin_session_token('admin'))
    summary = client.get('/api/v1/admin/knowledge/summary')
    assert summary.status_code == 200
    assert summary.json()['review_status']['pending'] == 2

    reviewed = client.post(
        f'/api/v1/admin/knowledge/passages/{passage_id}/review',
        json={'review_status': 'approved'},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()['review_status'] == 'approved'

    reviewed_all = client.post(
        '/api/v1/admin/knowledge/passages/review-all',
        json={
            'review_status': 'approved',
            'filters': {'review_status': 'pending'},
        },
    )
    assert reviewed_all.status_code == 200
    assert reviewed_all.json()['updated_count'] == 1
    assert client.get('/api/v1/admin/knowledge/summary').json()['review_status'] == {
        'approved': 2,
        'pending': 0,
        'rejected': 0,
    }

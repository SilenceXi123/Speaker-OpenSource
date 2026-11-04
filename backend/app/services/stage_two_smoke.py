from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.models.job import Job
from app.models.job_event import JobEvent
from app.services.pipeline_artifact_schema import PipelineArtifactSchemaError, validate_pipeline_artifacts


PASS = 'pass'
WARN = 'warn'
FAIL = 'fail'
SKIP = 'skip'

_STATUS_RANK = {
    PASS: 0,
    SKIP: 0,
    WARN: 1,
    FAIL: 2,
}

_REQUIRED_COMPLETED_EVENTS = {
    'job_created',
    'provider_task_submitted',
    'processing_update',
    'job_completed',
}

_ARTIFACT_EVENTS = {
    'pipeline_artifact_checked',
    'pipeline_artifact_exported',
}

_SENSITIVE_KEY_MARKERS = (
    'api_key',
    'apikey',
    'access_key',
    'accesskey',
    'secret',
    'token',
    'authorization',
    'signed_url',
    'signature',
    'prompt',
    'raw_payload',
    'full_subtitle',
    'subtitle_text',
    'local_path',
)

_SENSITIVE_VALUE_MARKERS = (
    'x-oss-signature=',
    'ossaccesskeyid=',
    'signature=',
    'authorization:',
    'bearer ',
    'sk-',
    'dashscope_api_key',
    '/users/',
    '/volumes/',
    '/www/wwwroot/',
)


@dataclass
class SmokeCheck:
    key: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'key': self.key,
            'status': self.status,
            'message': self.message,
            'details': self.details,
        }


def build_stage_two_smoke_report(*, job: Job, events: Iterable[JobEvent]) -> dict[str, Any]:
    """Build a read-only stage-two smoke report for one job.

    The report is a smoke/audit view only. It does not mutate job state, does not
    rebuild artifacts, and does not treat shadow artifacts as the production truth.
    """

    artifacts = job.pipeline_artifacts if isinstance(job.pipeline_artifacts, dict) else {}
    event_list = list(events or [])
    checks: list[SmokeCheck] = []

    schema_ok = _add_pipeline_schema_check(checks, artifacts)
    if schema_ok:
        checks.extend([
            _check_pipeline_kind(job, artifacts),
            _check_cue_hash_consistency(artifacts),
            _check_runtime_lexicon_bundle(job, artifacts),
            _check_cue_validator(artifacts),
            _check_transcript_isolation(job, artifacts),
            _check_user_edit_patch_coverage(artifacts, event_list),
        ])
    else:
        checks.extend([
            SmokeCheck('pipeline_kind', SKIP, 'pipeline artifacts 无效，跳过 pipeline 类型检查'),
            SmokeCheck('cue_hash_consistency', SKIP, 'pipeline artifacts 无效，跳过 cue hash 同源检查'),
            SmokeCheck('runtime_lexicon_bundle', SKIP, 'pipeline artifacts 无效，跳过词库 bundle 检查'),
            SmokeCheck('cue_validator', SKIP, 'pipeline artifacts 无效，跳过 CueValidator 检查'),
            SmokeCheck('transcript_isolation', SKIP, 'pipeline artifacts 无效，跳过扒词隔离检查'),
        ])

    checks.extend([
        _check_job_event_coverage(job, event_list),
        _check_artifact_event_coverage(event_list),
        _check_payload_safety(event_list),
    ])

    summary = _summarize_checks(checks)
    return {
        'job_id': str(job.id),
        'job_kind': str(job.kind or ''),
        'job_status': str(job.status or ''),
        'overall_status': _overall_status(checks),
        'summary': summary,
        'checks': [check.to_dict() for check in checks],
    }


def _add_pipeline_schema_check(checks: list[SmokeCheck], artifacts: dict[str, Any]) -> bool:
    try:
        validate_pipeline_artifacts(artifacts)
    except PipelineArtifactSchemaError as exc:
        checks.append(SmokeCheck(
            'pipeline_artifacts_schema',
            FAIL,
            f'pipeline artifacts 无效：{exc}',
        ))
        return False
    checks.append(SmokeCheck(
        'pipeline_artifacts_schema',
        PASS,
        'pipeline artifacts schema 有效',
        {'schema_version': artifacts.get('schema_version')},
    ))
    return True


def _check_pipeline_kind(job: Job, artifacts: dict[str, Any]) -> SmokeCheck:
    artifact_pipeline = str(artifacts.get('pipeline') or 'transcription')
    job_kind = str(job.kind or 'transcription')
    if artifact_pipeline != job_kind:
        return SmokeCheck(
            'pipeline_kind',
            FAIL,
            'artifact pipeline 与任务 kind 不一致',
            {'job_kind': job_kind, 'artifact_pipeline': artifact_pipeline},
        )
    return SmokeCheck(
        'pipeline_kind',
        PASS,
        'artifact pipeline 与任务 kind 一致',
        {'job_kind': job_kind},
    )


def _check_cue_hash_consistency(artifacts: dict[str, Any]) -> SmokeCheck:
    artifact_hashes = artifacts.get('artifact_hashes') or {}
    export_manifest = artifacts.get('export_manifest') or {}
    subtitle_cues_snapshot = artifacts.get('subtitle_cues_snapshot') or {}
    artifact_hash = str(artifact_hashes.get('subtitle_cues_hash') or '')
    export_hash = str(export_manifest.get('cue_hash') or '')
    snapshot_count = _safe_int(subtitle_cues_snapshot.get('count'))
    manifest_count = _safe_int(export_manifest.get('cue_count'))
    preview_matches_export = export_manifest.get('preview_hash_matches_export')

    if not artifact_hash or not export_hash:
        return SmokeCheck('cue_hash_consistency', FAIL, 'cue hash 缺失')
    if artifact_hash != export_hash:
        return SmokeCheck(
            'cue_hash_consistency',
            FAIL,
            'artifact cue hash 与 export manifest cue hash 不一致',
            {'artifact_hash': artifact_hash, 'export_hash': export_hash},
        )
    if snapshot_count != manifest_count:
        return SmokeCheck(
            'cue_hash_consistency',
            WARN,
            'subtitle cue 快照数量与 export manifest cue_count 不一致',
            {'snapshot_count': snapshot_count, 'manifest_count': manifest_count},
        )
    if preview_matches_export is False:
        return SmokeCheck(
            'cue_hash_consistency',
            WARN,
            'export manifest 标记 preview_hash_matches_export 为 false',
        )
    return SmokeCheck(
        'cue_hash_consistency',
        PASS,
        'cue hash、cue_count 和导出快照同源',
        {'cue_hash': export_hash, 'cue_count': manifest_count},
    )


def _check_runtime_lexicon_bundle(job: Job, artifacts: dict[str, Any]) -> SmokeCheck:
    if str(job.kind or '') != 'transcription':
        return SmokeCheck('runtime_lexicon_bundle', SKIP, '非生成字幕任务不检查 RuntimeLexiconBundle')
    bundle = artifacts.get('runtime_lexicon_bundle_snapshot')
    if not isinstance(bundle, dict):
        return SmokeCheck('runtime_lexicon_bundle', WARN, '任务没有 RuntimeLexiconBundle 快照')
    terms = list(bundle.get('terms') or [])
    term_set_hash = str(bundle.get('term_set_hash') or '').strip()
    artifact_hash = str((artifacts.get('artifact_hashes') or {}).get('runtime_lexicon_bundle_hash') or '').strip()
    if not terms:
        return SmokeCheck(
            'runtime_lexicon_bundle',
            SKIP,
            '任务未使用项目词库，跳过有词库 smoke 项',
            {'term_count': 0, 'term_set_hash': term_set_hash or artifact_hash},
        )
    if not term_set_hash:
        return SmokeCheck('runtime_lexicon_bundle', FAIL, '有项目词但缺少 term_set_hash')
    if artifact_hash and artifact_hash != term_set_hash:
        return SmokeCheck(
            'runtime_lexicon_bundle',
            FAIL,
            'runtime lexicon bundle hash 与 artifact hash 不一致',
            {'term_set_hash': term_set_hash, 'artifact_hash': artifact_hash},
        )
    return SmokeCheck(
        'runtime_lexicon_bundle',
        PASS,
        'RuntimeLexiconBundle 已进入 artifact 且 hash 可复现',
        {'term_count': len(terms), 'term_set_hash': term_set_hash},
    )


def _check_cue_validator(artifacts: dict[str, Any]) -> SmokeCheck:
    report = artifacts.get('cue_validation_report') or {}
    p0_count = _safe_int(report.get('p0_count'))
    issue_count = _safe_int(report.get('issue_count'))
    if p0_count > 0:
        return SmokeCheck(
            'cue_validator',
            FAIL,
            'CueValidator 存在 P0 问题，应阻止导出',
            {'p0_count': p0_count, 'issue_count': issue_count},
        )
    if issue_count > 0:
        return SmokeCheck(
            'cue_validator',
            WARN,
            'CueValidator 无 P0，但存在需关注问题',
            {'p0_count': p0_count, 'issue_count': issue_count},
        )
    return SmokeCheck(
        'cue_validator',
        PASS,
        'CueValidator 无 P0/P1 问题',
        {'p0_count': p0_count, 'issue_count': issue_count},
    )


def _check_transcript_isolation(job: Job, artifacts: dict[str, Any]) -> SmokeCheck:
    if str(job.kind or '') != 'transcript':
        return SmokeCheck('transcript_isolation', SKIP, '非扒词任务跳过扒词隔离检查')
    cue_count = _safe_int((artifacts.get('subtitle_cues_snapshot') or {}).get('count'))
    terms = list(((artifacts.get('runtime_lexicon_bundle_snapshot') or {}).get('terms') or []))
    if cue_count > 0 or terms:
        return SmokeCheck(
            'transcript_isolation',
            FAIL,
            '生成扒词任务不应产生成片字幕 cue 或加载字幕词库保护',
            {'cue_count': cue_count, 'term_count': len(terms)},
        )
    return SmokeCheck(
        'transcript_isolation',
        PASS,
        '生成扒词任务保持隔离，未加载字幕词库保护',
        {'cue_count': cue_count, 'term_count': 0},
    )


def _check_job_event_coverage(job: Job, events: list[JobEvent]) -> SmokeCheck:
    event_types = {str(event.event_type or '') for event in events}
    status = str(job.status or '')
    required = set()
    if status in {'completed', 'completed_with_warnings'}:
        required = set(_REQUIRED_COMPLETED_EVENTS)
    elif status == 'failed':
        required = {'job_created', 'job_failed'}
    elif status == 'aborted':
        required = {'job_created', 'job_aborted'}
    else:
        required = {'job_created'}
    missing = sorted(required - event_types)
    if missing:
        return SmokeCheck(
            'job_events_coverage',
            WARN,
            '任务事件缺少基础路径事件',
            {'missing_event_types': missing, 'observed_event_types': sorted(event_types)},
        )
    return SmokeCheck(
        'job_events_coverage',
        PASS,
        '任务基础事件覆盖完整',
        {'observed_event_types': sorted(event_types)},
    )


def _check_artifact_event_coverage(events: list[JobEvent]) -> SmokeCheck:
    event_types = {str(event.event_type or '') for event in events}
    missing = sorted(_ARTIFACT_EVENTS - event_types)
    if missing:
        return SmokeCheck(
            'artifact_event_coverage',
            WARN,
            'artifact 检查/下载事件未完整出现',
            {'missing_event_types': missing},
        )
    return SmokeCheck('artifact_event_coverage', PASS, 'artifact 检查和下载事件已记录')


def _check_user_edit_patch_coverage(artifacts: dict[str, Any], events: list[JobEvent]) -> SmokeCheck:
    edit_count = _safe_int((artifacts.get('user_edit_patches_snapshot') or {}).get('count'))
    if edit_count <= 0:
        return SmokeCheck('user_edit_patch_coverage', SKIP, '任务没有用户编辑 patch，跳过编辑事件覆盖检查')
    event_types = {str(event.event_type or '') for event in events}
    if 'user_edit_patch_applied' not in event_types:
        return SmokeCheck(
            'user_edit_patch_coverage',
            WARN,
            '任务存在用户编辑 patch，但缺少 user_edit_patch_applied 事件',
            {'edit_patch_count': edit_count},
        )
    return SmokeCheck(
        'user_edit_patch_coverage',
        PASS,
        '用户编辑 patch 与事件记录均存在',
        {'edit_patch_count': edit_count},
    )


def _check_payload_safety(events: list[JobEvent]) -> SmokeCheck:
    hits: list[dict[str, Any]] = []
    for event in events:
        hits.extend(_scan_event_payload(event))
    if hits:
        return SmokeCheck(
            'payload_safety',
            FAIL,
            'job_events payload 存在敏感字段或敏感值风险',
            {'hit_count': len(hits), 'hits': hits[:20]},
        )
    return SmokeCheck('payload_safety', PASS, 'job_events payload 未发现敏感字段或敏感值')


def _scan_event_payload(event: JobEvent) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    if _contains_sensitive_value(str(event.message or '')):
        hits.append({'event_id': event.id, 'field': 'message', 'reason': 'sensitive_value'})
    if isinstance(event.details, dict):
        for key, value in _flatten_dict(event.details):
            normalized_key = key.lower()
            if any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS):
                hits.append({'event_id': event.id, 'field': key, 'reason': 'sensitive_key'})
                continue
            if _contains_sensitive_value(str(value)):
                hits.append({'event_id': event.id, 'field': key, 'reason': 'sensitive_value'})
    return hits


def _flatten_dict(payload: dict[str, Any], prefix: str = '') -> Iterable[tuple[str, Any]]:
    for key, value in payload.items():
        path = f'{prefix}.{key}' if prefix else str(key)
        if isinstance(value, dict):
            yield from _flatten_dict(value, path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                item_path = f'{path}[{index}]'
                if isinstance(item, dict):
                    yield from _flatten_dict(item, item_path)
                else:
                    yield item_path, item
        else:
            yield path, value


def _contains_sensitive_value(value: str) -> bool:
    normalized = value.lower()
    return any(marker in normalized for marker in _SENSITIVE_VALUE_MARKERS)


def _summarize_checks(checks: list[SmokeCheck]) -> dict[str, int]:
    return {
        PASS: sum(1 for check in checks if check.status == PASS),
        WARN: sum(1 for check in checks if check.status == WARN),
        FAIL: sum(1 for check in checks if check.status == FAIL),
        SKIP: sum(1 for check in checks if check.status == SKIP),
    }


def _overall_status(checks: list[SmokeCheck]) -> str:
    worst = max((_STATUS_RANK.get(check.status, 0) for check in checks), default=0)
    if worst >= _STATUS_RANK[FAIL]:
        return FAIL
    if worst >= _STATUS_RANK[WARN]:
        return WARN
    return PASS


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

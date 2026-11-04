import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

async function load() {
  const context = vm.createContext({ localStorage: { getItem: () => null }, window: { location: { protocol: 'https:', hostname: 'test.local' } } });
  vm.runInContext(await readFile(new URL('../../src/frontend/js/state.js', import.meta.url), 'utf8'), context);
  vm.runInContext(await readFile(new URL('../../src/frontend/js/subtitle-quality.js', import.meta.url), 'utf8'), context);
  return context.SpeakerSubtitleQuality;
}
const cues = [{ index: 1, start_ms: 0, end_ms: 900, clean_text: '原字', speaker_key: 'speaker_0' },
  { index: 2, start_ms: 1000, end_ms: 1800, clean_text: '另一句', speaker_key: 'speaker_1' }];
const detail = artifacts => ({ id: 'job-1', project_id: 'project-1', pipeline_artifacts: artifacts });
const artifacts = () => ({ source_revision: { job_id: 'job-1' }, subtitle_cues_snapshot: { count: 2, items: cues },
  cue_validation_report: { issues: [{ code: 'isolated_single_cjk_cue', severity: 'P0', index: 1, message: '字幕被切成孤立单字' }], issue_count: 1 },
  evidence_subtitle_v2: { validation: { issues: [{ code: 'token_loss', severity: 'P0' }] }, corrections: [{ token_ids: ['token-000001'], source_text: '查记村', replacement_text: '查济村', start_ms: 3000, end_ms: 3800 }] },
  dialogue_broadcast_normalization: { changes: [{ before: '好好', after: '好', start_ms: 5000, end_ms: 5400, reasons: ['inline_repetition'] }] } });

test('missing and truncated reports never claim all subtitles correct', async () => {
  const qc = await load();
  assert.match(qc.render({ detail: detail({}), cues, projectId: 'project-1' }), /无检查结果/);
  const source = artifacts();
  source.cue_validation_report = { issues: [], issue_count: 501, truncated: true };
  const html = qc.render({ detail: detail(source), cues, projectId: 'project-1' });
  assert.match(html, /仅展示部分记录/);
  assert.match(html, /501/);
  assert.doesNotMatch(html, /未发现结构异常|全部通过|全部正确|准确率/);
});

test('current issues use saved cues while generation corrections remain explicit history', async () => {
  const qc = await load();
  const report = qc.build({ detail: detail(artifacts()), cues, projectId: 'project-1' });
  assert.equal(report.current.issues.length, 1);
  assert.equal(report.current.issues[0].text, '原字');
  assert.equal(report.current.issues[0].startMs, 0);
  assert.equal(report.history.rows.length, 2);
  assert.equal(report.history.rows[0].before, '查记村');
  assert.equal(report.history.rows[0].after, '查济村');
  assert.equal(report.history.rows[0].startMs, 3000);
  const source = artifacts();
  const edited = [{ ...cues[0], clean_text: '人工校正', start_ms: 200 }, cues[1]];
  assert.equal(qc.build({ detail: detail(source), cues: edited, projectId: 'project-1' }).current.status, 'stale');
  source.subtitle_cues_snapshot.items = edited;
  source.cue_validation_report.issues = [];
  source.cue_validation_report.issue_count = 0;
  const refreshed = qc.build({ detail: detail(source), cues: edited, projectId: 'project-1' });
  assert.equal(refreshed.current.issues.length, 0);
  assert.equal(refreshed.history.rows[0].before, '查记村');
  assert.match(qc.render({ detail: detail(source), cues: edited, projectId: 'project-1' }), /生成时.*历史|生成修复历史/);
  source.cue_validation_report.scope = 'generation';
  assert.equal(qc.build({ detail: detail(source), cues: edited, projectId: 'project-1' }).current.status, 'stale');
  source.cue_validation_report.scope = 'current_timeline';
  source.cue_validation_report.timeline_revision = 'older';
  source.timeline_revision = 'current';
  assert.equal(qc.build({ detail: detail(source), cues: edited, projectId: 'project-1' }).current.status, 'stale');
  delete source.cue_validation_report.timeline_revision;
  source.subtitle_cues_snapshot.items = [];
  assert.equal(qc.build({ detail: detail(source), cues: edited, projectId: 'project-1' }).current.status, 'stale');
});

test('old token IDs and missing times never fabricate a playback position', async () => {
  const qc = await load();
  const source = artifacts();
  delete source.evidence_subtitle_v2.corrections[0].start_ms;
  delete source.evidence_subtitle_v2.corrections[0].end_ms;
  source.asr_tokens_snapshot = { items: [{ asr_token_id: 'token-000001', start_ms: 9000, end_ms: 9900 }] };
  const result = qc.build({ detail: detail(source), cues, projectId: 'project-1' });
  assert.equal(result.history.rows[0].startMs, null);
  assert.match(qc.render({ detail: detail(source), cues, projectId: 'project-1' }), /无可靠位置/);
});

test('numeric and speaker safety refusals are preserved as historical review candidates, never applied repairs', async () => {
  const qc = await load();
  const source = artifacts();
  source.evidence_subtitle_v2.rejected_corrections = [{ source_text: '十五', replacement_text: '五十', start_ms: 1000, end_ms: 1800, reason: 'numeric_fact_change' }];
  source.dialogue_broadcast_normalization.skipped_changes = [{ before: '对 对', after: '对', start_ms: 5000, end_ms: 5600, reason: 'speaker_not_confirmed_same' }];
  const result = qc.build({ detail: detail(source), cues, projectId: 'project-1' });
  assert.equal(result.history.rows.length, 2);
  assert.equal(result.history.retained.length, 2);
  assert.equal(result.history.retained[0].before, '十五');
  assert.equal(result.history.retained[0].startMs, 1000);
  const html = qc.render({ detail: detail(source), cues, projectId: 'project-1' });
  assert.match(html, /生成时保留原文 · 建议人工复核/);
  assert.match(html, /未采用的候选/);
  assert.match(html, /数字事实可能变化/);
  assert.match(html, /人物身份未确认相同/);
});

test('report text is escaped and playback rejects switched jobs and projects', async () => {
  const qc = await load();
  const source = artifacts();
  source.evidence_subtitle_v2.corrections[0].source_text = '<img src=x onerror=alert(1)>';
  source.evidence_subtitle_v2.corrections[0].replacement_text = '"&<script>';
  const html = qc.render({ detail: detail(source), cues, projectId: 'project-1' });
  assert.match(html, /&lt;img/);
  assert.match(html, /&quot;&amp;&lt;script&gt;/);
  assert.doesNotMatch(html, /<img|<script>/);
  const dataset = { qcJobId: 'job-1', qcProjectId: 'project-1', cueStart: '0' };
  const context = { detail: detail(source), jobId: 'job-1', projectId: 'project-1' };
  assert.equal(qc.isCurrentTarget(dataset, context), true);
  assert.equal(qc.isCurrentTarget(dataset, { ...context, jobId: 'job-2' }), false);
  assert.equal(qc.isCurrentTarget(dataset, { ...context, projectId: 'project-2' }), false);
  assert.equal(qc.isCurrentTarget({ ...dataset, cueStart: '' }, context), false);
  assert.equal(qc.isCurrentTarget({ ...dataset, cueStart: '-1' }, context), false);
  assert.equal(qc.build({ detail: detail(source), cues, projectId: 'project-2' }).current.status, 'stale');
});

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

async function load({ desktop = true, offline = false, nativeAvailable = true } = {}) {
  const calls = [], jobs = new Map();
  const context = vm.createContext({ URL, URLSearchParams, FormData, Blob, File, crypto: { randomUUID: () => 'new-id' }, navigator: { onLine: !offline },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: { querySelector: () => null },
    window: { location: { protocol: 'https:', hostname: 'test.local' }, addEventListener() {}, setInterval() { return 1; }, __TAURI__: { core: { convertFileSrc: p => `asset://${p}` } } },
    request: async () => { if (offline) throw new Error('offline'); return { status: 'ok' }; },
    renderAll() {}, showToast() {}, normalizeError: e => e, assertRecognitionFileSize() {},
    getCurrentProjectId: () => context.state.currentProjectId,
    decorateJobDetail: x => x, setCurrentJobId: id => { context.state.currentJobId = id; },
    refreshJobs: async () => {}, loadJob: async id => { context.state.jobDetail = jobs.get(id); },
    requestV2: async () => { throw new Error('unexpected upload'); },
    loadClientBridgeFile: async () => new Blob(['audio'], { type: 'audio/wav' }),
  });
  vm.runInContext(await readFile(new URL('../../src/frontend/js/state.js', import.meta.url), 'utf8'), context);
  vm.runInContext('globalThis.state = state;', context);
  context.state.currentProjectId = 'p1'; context.state.currentProjectName = '项目';
  context.state.projects = [{ id: 'p1', project_id: 'p1', name: '项目' }];
  const bridge = { isAvailable: () => desktop, detectLocalSpeech: async () => ({ available: true, model_installed: true }),
    transcribeLocalSpeech: async path => { calls.push(['asr', path]); return { engine: 'apple_speech', segments: [{ text: '你好', start_ms: 0, end_ms: 1000 }] }; },
    cancelLocalSpeech: async () => { calls.push(['cancel']); },
    offlineSourcePath: async (_scope, id, projectId) => { calls.push(['source', id, projectId]); return '/source.wav'; },
    offlineSubtitles: async (scope, operation, payload) => {
      calls.push([operation, scope, payload]);
      if (operation === 'capability') return { available: nativeAvailable, reason: nativeAvailable ? '' : '此架构未准备好本地字幕工具' };
      if (operation === 'cancel') return { cancelled: true };
      if (operation === 'projects') return context.state.projects;
      if (operation === 'save_projects') return payload.projects;
      if (operation === 'list') return [...jobs.values()];
      if (operation === 'build') { const job = { ...payload.job, local_only: true, local_recognition: payload.recognition, status: 'completed', local_cues: [], local_speakers: { speakers: [], mappings: {} } }; jobs.set(job.id, job); return job; }
      if (operation === 'get') { const job = jobs.get(payload.id); if (!job || job.project_id !== payload.project_id) throw new Error('项目不匹配'); return job; }
      if (operation === 'edit') return { ...jobs.get(payload.id), local_full_srt: 'changed' };
      throw new Error(`unexpected ${operation}`);
    },
  };
  context.window.SpeakerDesktopBridge = bridge;
  vm.runInContext(await readFile(new URL('../../src/frontend/js/local-recognition.js', import.meta.url), 'utf8'), context);
  await context.SpeakerLocalRecognition.initialize();
  return { local: context.SpeakerLocalRecognition, state: context.state, calls, jobs, context, bridge };
}

test('cloud stays default; offline selects local and browser cannot claim native availability', async () => {
  const a = await load(); assert.equal(a.local.wantsLocal(), false);
  a.local.connectionFailed(); assert.equal(a.local.wantsLocal(), true);
  assert.match(a.local.renderBanner(), /离线模式/);
  const b = await load({ desktop: false, offline: true });
  assert.equal(b.state.localRecognition.capability.available, false);
  assert.match(b.local.renderControls(), /桌面客户端/);
  const c = await load({ nativeAvailable: false });
  assert.equal(c.state.localRecognition.capability.available, false);
  assert.match(c.state.localRecognition.capability.reason, /架构/);
});

test('structured native failures retain a readable message', async () => {
  const { context } = await load();
  assert.equal(context.normalizeError({ code: 'empty_audio', message: '音频没有有效帧' }).message, '音频没有有效帧');
});

test('server rejection preserves local original without retrying recognition or claiming offline', async () => {
  const { local, state, context, calls, jobs } = await load();
  state.pickedMediaFile = { path: '/source.wav', name: 'source.wav' };
  context.requestV2 = async () => { const error = new Error('限流'); error.status = 429; throw error; };
  const result = await local.create();
  assert.equal(result.local_only, true); assert.equal(jobs.size, 1);
  assert.equal(state.localRecognition.offline, false);
  assert.match(state.localRecognition.error, /已保留本机原稿.*限流/);
  assert.equal(calls.filter(([operation]) => operation === 'asr').length, 1);
});

test('offline recognition persists in its original project without upload or UI cross-project attachment', async () => {
  const { local, state, calls, jobs, bridge } = await load({ offline: true });
  state.pickedMediaFile = { path: '/source.wav', name: 'source.wav' }; state.pickedMediaProjectId = 'p1';
  bridge.transcribeLocalSpeech = async () => { state.currentProjectId = 'p2'; return { segments: [{ text: '你好', start_ms: 0, end_ms: 1000 }] }; };
  const created = await local.create();
  assert.equal(created.project_id, 'p1'); assert.equal(created._uiDetached, true);
  assert.equal(jobs.size, 1); assert.equal(state.currentJobId, '');
  assert.ok(calls.some(([operation]) => operation === 'build'));
});

test('unavailable models and missing native paths never start recognition or upload', async () => {
  const { local, state, calls } = await load({ offline: true });
  state.localRecognition.capability = { available: false, reason: '模型未安装' };
  await assert.rejects(local.create(), /模型未安装/);
  state.localRecognition.capability.available = true;
  state.pickedMediaFile = { name: 'browser.wav' };
  await assert.rejects(local.create(), /桌面.*选择/);
  assert.equal(calls.some(([operation]) => operation === 'asr' || operation === 'build'), false);
});

test('local request routing uses native project checks, returns saved cues, and refuses cloud-only operations', async () => {
  const { local, state, jobs, calls } = await load({ offline: true });
  jobs.set('local-one', { id: 'local-one', project_id: 'p1', local_only: true, local_cues: [{ index: 1 }], local_full_srt: 'SRT' });
  const response = await local.request('/transcriptions/jobs/local-one/subtitles/preview?variant=full');
  assert.equal(response.content, 'SRT'); assert.equal(response.cues.length, 1);
  assert.equal(local.request('/transcriptions/jobs/server-one'), null);
  await assert.rejects(local.request('/transcriptions/jobs/local-one/script-context', { method: 'POST' }), /联网/);
  state.currentProjectId = 'p2';
  await assert.rejects(local.request('/transcriptions/jobs/local-one'), /项目不匹配/);
  assert.ok(calls.some(([operation, , payload]) => operation === 'get' && payload.project_id === 'p2'));
});

test('cancel discards a late native result and does not persist a partial job', async () => {
  const { local, state, bridge, jobs } = await load({ offline: true });
  state.pickedMediaFile = { path: '/source.wav', name: 'source.wav' };
  let finish;
  bridge.transcribeLocalSpeech = () => new Promise(resolve => { finish = resolve; });
  const pending = local.create();
  await new Promise(resolve => setTimeout(resolve, 0));
  await local.cancel(); finish({ segments: [{ text: 'late', start_ms: 0, end_ms: 500 }] });
  await assert.rejects(pending, /取消/); assert.equal(jobs.size, 0); assert.equal(state.localRecognition.busy, '');
});

test('sync of a local-only project requires an explicit server project and preserves local source', async () => {
  const { local, state, jobs } = await load();
  state.projects.push({ id: 'lp', project_id: 'lp', local_only: true, name: '本机项目' });
  const job = { id: 'local-one', project_id: 'lp', local_only: true, local_recognition: { segments: [] } }; jobs.set(job.id, job);
  await assert.rejects(local.sync(job), /选择服务器项目/);
  assert.equal(jobs.get(job.id), job);
});

test('sync sends original recognition and saved speaker/length settings without overwriting edited local cues', async () => {
  const { local, context, jobs, bridge } = await load();
  const original = { id: 'local-one', project_id: 'p1', original_filename: 'source.wav', local_only: true,
    local_recognition: { segments: [{ text: '原始识别' }] }, local_cues: [{ clean_text: '人工校字' }],
    raw_payload: { _apple_speech: { speaker_name: '主持人' } }, processing_metrics: { subtitle_length_profile: { mode: 'strict', max_chars: 18 } }, use_project_scripts: false };
  jobs.set(original.id, original);
  let sent;
  context.requestV2 = async (path, options) => { sent = { path, form: options.body }; return { job: { id: 'server-one', project_id: 'p1' } }; };
  const native = bridge.offlineSubtitles;
  let linked;
  bridge.offlineSubtitles = async (scope, operation, payload) => {
    if (operation === 'mark_synced') { linked = payload; return { ...original, server_job_id: payload.server_job_id }; }
    return native(scope, operation, payload);
  };
  await local.sync(original);
  assert.equal(sent.path, '/projects/p1/transcriptions/local-jobs');
  assert.equal(sent.form.get('speaker_name'), '主持人');
  assert.equal(sent.form.get('subtitle_length_mode'), 'strict');
  assert.equal(sent.form.get('subtitle_max_chars'), '18');
  assert.equal(sent.form.get('use_project_scripts'), 'false');
  assert.equal(sent.form.get('client_job_id'), 'one');
  assert.equal(sent.form.get('recognition'), JSON.stringify(original.local_recognition));
  assert.equal(jobs.get(original.id).local_cues[0].clean_text, '人工校字');
  assert.equal(linked.server_project_id, 'p1');
});

test('an edit finishing after a project switch never updates the next project UI', async () => {
  const { local, state, jobs, bridge } = await load({ offline: true });
  jobs.set('local-one', { id: 'local-one', project_id: 'p1', local_only: true, local_cues: [{ clean_text: '旧' }] });
  const native = bridge.offlineSubtitles;
  bridge.offlineSubtitles = async (scope, operation, payload) => {
    const result = await native(scope, operation, payload);
    if (operation === 'edit') state.currentProjectId = 'p2';
    return result;
  };
  await assert.rejects(local.request('/transcriptions/jobs/local-one/subtitles/text', { method: 'PUT', body: JSON.stringify({ cues: [{ index: 1, clean_text: '新' }] }) }), /项目或服务器已变化/);
});

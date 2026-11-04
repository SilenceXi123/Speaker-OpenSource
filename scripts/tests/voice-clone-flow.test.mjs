import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const models = [
  { id: 'cosyvoice:cosyvoice-v3.5-plus', provider: 'cosyvoice', model: 'cosyvoice-v3.5-plus', label: 'CosyVoice 3.5 Plus', recommended: true, available: true, capabilities: { instruction: true }, constraints: { text_max_characters: 2000, instruction_max_weight: 57, instruction_han_weight: 2 } },
  { id: 'qwen:qwen3-tts-vc-2026-01-22', provider: 'qwen', model: 'qwen3-tts-vc-2026-01-22', label: 'Qwen3-TTS', available: true, capabilities: { instruction: false }, constraints: {} },
];
const sample = { id: 'sample-1', identity_id: 'identity-1', display_name: '主持人', clone_readiness_status: 'ready', match_type: 'new_identity', sample_slots_remaining: 2 };
const ready = { id: 'profile-1', identity_id: 'identity-1', group_key: 'identity-1', source_clone_sample_id: 'sample-1', source_sample_ready: true, voice_name: '主持人', status: 'ready', selectable: true, voice_id: 'voice-1', provider: models[0].provider, target_model: models[0].model };
const qwenAudioModels = ['qwen-audio-3.0-tts-plus', 'qwen-audio-3.0-tts-flash', 'qwen-audio-3.1-tts-flash'].map(model => ({
  id: `qwen_audio:${model}`, provider: 'qwen_audio', model, label: model, available: true,
  capabilities: { instruction: true }, constraints: { text_max_characters: 500, instruction_max_weight: 1600, instruction_han_weight: 1 },
}));

async function setup() {
  const sources = await Promise.all(['voice-clone-library', 'state', 'api', 'events'].map(name => readFile(new URL(`../../src/frontend/js/${name}.js`, import.meta.url), 'utf8')));
  const context = vm.createContext({
    Blob, FormData, URL, URLSearchParams, Headers, AbortController, setTimeout, clearTimeout,
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    window: { location: { protocol: 'https:', hostname: 'example.test' }, setTimeout, clearTimeout, requestAnimationFrame: f => f() },
    document: { body: { dataset: {} }, querySelector: () => null, querySelectorAll: () => [] },
    renderAll() {}, showToast() {}, recordClientLog() {}, setProjectRailOpen() {},
  });
  sources.forEach(source => vm.runInContext(source, context));
  vm.runInContext('showToast = () => {}; recordClientLog = async () => {};', context);
  const state = vm.runInContext('state', context);
  state.currentProjectId = 'project-1';
  state.serviceStatus.voiceClone = { available: true, models };
  state.voiceClone.displayName = '主持人';
  state.voiceClone.addFlow.open = true;
  const requests = [];
  context.network = async (path, options = {}) => {
    requests.push({ path, options });
    if (path.endsWith('/samples')) return { ...sample };
    if (path.endsWith('/profiles') && options.method === 'POST') return { ...ready };
    if (path.includes('/library/') && path.endsWith('/profiles')) return [];
    if (path.includes('/synthesis-records')) return [];
    throw new Error(`Unexpected request ${path}`);
  };
  const refreshData = context.refreshVoiceCloneData;
  vm.runInContext('requestV2 = (...args) => network(...args); refreshVoiceCloneData = async () => true;', context);
  const file = new Blob(['audio']);
  file.name = 'host.wav';
  state.voiceClone.sampleDraft = { projectId: 'project-1', file, fileName: file.name };
  const form = { matches: selector => selector === '#voice-clone-upload-sample-form', querySelector: selector => selector === '#voice-clone-sample-input' ? { files: [file] } : { disabled: false, textContent: '创建声音' } };
  return { context, state, requests, file, refreshData, submit: () => context.handleDocumentSubmit({ target: form, preventDefault() {} }) };
}
const targetFor = (selector, dataset = {}) => ({ dataset, closest(candidate) { return candidate === selector ? this : null; }, matches() { return false; } });

test('an unavailable configured model does not block an available catalog model', async () => {
  const { context, state, requests, submit } = await setup();
  state.serviceStatus.voiceClone = { enabled: true, available: false, models: [{ ...models[0], available: false }, { ...models[1] }] };
  const alternative = { ...ready, provider: models[1].provider, target_model: models[1].model };
  assert.equal(context.isVoiceCloneServiceAvailable(), true);
  assert.equal(context.isVoiceCloneServiceAvailable(ready), false);
  assert.equal(context.isVoiceCloneServiceAvailable(alternative), true);
  state.voiceClone.addFlow.open = false;
  await context.handleDocumentClick({ target: targetFor('#voice-clone-new-button') });
  assert.equal(state.voiceClone.addFlow.open, true);
  await context.switchVoiceCloneModel(models[1].id);
  const network = context.network;
  context.network = async (path, options = {}) => path.endsWith('/profiles') && options.method === 'POST'
    ? (requests.push({ path, options }), alternative) : network(path, options);
  await submit();
  assert.equal(state.voiceClone.profileId, alternative.id);
  assert.equal(JSON.parse(requests.find(({ path, options }) => path.endsWith('/profiles') && options.method === 'POST').options.body).provider, models[1].provider);
  context.network = async (path, options = {}) => options.method === 'POST' ? { id: 'record', status: 'ready' } : [];
  assert.equal((await context.createVoiceClonePreview()).id, 'record');
});

test('maintenance disables every catalog model and legacy status still controls old servers', async () => {
  const { context, state, requests, submit } = await setup();
  state.serviceStatus.voiceClone = { enabled: false, available: true, models };
  assert.equal(context.isVoiceCloneServiceAvailable(), false);
  assert.equal(context.isVoiceCloneServiceAvailable(ready), false);
  await assert.rejects(context.switchVoiceCloneModel(models[1].id), /不可用/);
  await submit();
  assert.equal(requests.length, 0);
  state.serviceStatus.voiceClone = { available: true, provider: models[0].provider, target_model: models[0].model };
  assert.equal(context.isVoiceCloneServiceAvailable(ready), true);
  state.serviceStatus.voiceClone.available = false;
  assert.equal(context.isVoiceCloneServiceAvailable(ready), false);
});

test('GET timeout stays active until JSON or text response bodies are consumed', async () => {
  for (const contentType of ['application/json', 'text/plain']) {
    const { context } = await setup();
    let abortAtDeadline;
    let cleared = 0;
    let bodyStarted;
    const readingBody = new Promise(resolve => { bodyStarted = resolve; });
    context.window.setTimeout = callback => { abortAtDeadline = callback; return 1; };
    context.window.clearTimeout = () => { cleared += 1; };
    context.fetch = async (_url, { signal }) => {
      const readBody = () => { bodyStarted(); return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('Body stream aborted')), { once: true })); };
      return { ok: true, headers: { get: () => contentType }, json: readBody, text: readBody };
    };
    const pending = context.requestUrl('/slow-library');
    await readingBody;
    assert.equal(cleared, 0);
    abortAtDeadline();
    await assert.rejects(pending, /连接后端超时/);
    assert.equal(cleared, 1);
  }
});

test('late profile selection cannot close a newly opened creation drawer', async () => {
  for (const switchProject of [false, true]) {
    const { context, state } = await setup();
    state.projects = [{ project_id: 'project-1' }, { project_id: 'project-2' }];
    state.voiceClone.profiles = [ready];
    state.voiceClone.profileId = ready.id;
    let resolveRecords;
    context.network = () => new Promise(resolve => { resolveRecords = resolve; });
    const pending = context.handleDocumentClick({ target: targetFor('[data-voice-profile]', { voiceProfile: ready.id, closeVoiceDrawer: 'true' }) });
    context.closeVoiceCloneCreationDrawer();
    if (switchProject) context.setCurrentProject('project-2');
    await context.handleDocumentClick({ target: targetFor('#voice-clone-new-button') });
    assert.equal(state.voiceClone.addFlow.open, true);
    resolveRecords([]);
    await pending;
    assert.equal(state.voiceClone.addFlow.open, true);
  }
});

test('catalog chooses recommended configured model and rejects arbitrary model IDs', async () => {
  const { context, state } = await setup();
  context.applyVoiceCloneProviderDefaults();
  assert.equal(state.voiceClone.targetModel, models[0].model);
  assert.equal(context.getVoiceCloneModelOptions().length, 2);
  assert.equal(context.getVoiceCloneInstructionWeight('𰀀A'), 3);
  assert.equal(context.selectVoiceCloneModel('invented:model'), false);
  assert.equal(state.voiceClone.targetModel, models[0].model);
  state.serviceStatus.voiceClone.models[0].available = false;
  assert.equal(context.isVoiceCloneServiceAvailable(ready), false);
  state.serviceStatus.voiceClone.models[0].available = true;
});

test('one submit uploads, checks identity, creates and returns to the generation draft', async () => {
  const { state, requests, submit } = await setup();
  state.voiceClone.previewText = '保留文案';
  await submit();
  assert.equal(requests.filter(({ options }) => options.method === 'POST').length, 2);
  assert.equal(state.voiceClone.profileId, ready.id);
  assert.equal(state.voiceClone.addFlow.open, false);
  assert.equal(state.voiceClone.view, 'generate');
  assert.equal(state.voiceClone.previewText, '保留文案');
  assert.equal(state.voiceClone.busy, '');
});

test('Qwen-Audio catalog models create from an upload and pass character-counted instructions', async () => {
  for (const model of qwenAudioModels) {
    const { context, state, requests, submit } = await setup();
    state.serviceStatus.voiceClone.models = [...models, ...qwenAudioModels];
    const profile = { ...ready, provider: model.provider, target_model: model.model };
    const network = context.network;
    context.network = async (path, options = {}) => {
      if (options.method === 'POST' && (path.endsWith('/profiles') || path.endsWith('/preview'))) {
        requests.push({ path, options });
        return path.endsWith('/profiles') ? profile : { id: 'record-1', status: 'ready' };
      }
      return network(path, options);
    };
    await context.handleDocumentChange({ target: { value: model.id, matches: selector => selector === '#voice-clone-model-select' } });
    await submit();
    const creation = JSON.parse(requests.find(({ path }) => path.endsWith('/profiles')).options.body);
    assert.equal(creation.provider, model.provider);
    assert.equal(creation.target_model, model.model);
    assert.equal(creation.clone_sample_id, sample.id);
    assert.equal(state.voiceClone.profileId, profile.id);
    state.voiceClone.previewText = '欢迎收听';
    state.voiceClone.voiceInstruction = '温柔𰀀A';
    assert.equal(context.getVoiceCloneInstructionWeight(state.voiceClone.voiceInstruction, model), 4);
    await context.createVoiceClonePreview();
    assert.deepEqual(JSON.parse(requests.find(({ path }) => path.endsWith('/preview')).options.body), { text: '欢迎收听', instruction: '温柔𰀀A' });
    state.voiceClone.voiceInstruction = '温'.repeat(model.constraints.instruction_max_weight + 1);
    await assert.rejects(context.createVoiceClonePreview(), /1600 字符限制/);
    assert.equal(requests.filter(({ path }) => path.endsWith('/preview')).length, 1);
  }
});

test('Qwen-Audio switch creates only after confirmation and reuses an existing model version', async () => {
  const { context, state, requests } = await setup();
  state.serviceStatus.voiceClone.models = [...models, ...qwenAudioModels];
  state.voiceClone.profiles = [ready];
  state.voiceClone.profileId = ready.id;
  for (const [index, model] of qwenAudioModels.entries()) {
    const profile = { ...ready, id: `audio-profile-${index}`, provider: model.provider, target_model: model.model };
    context.network = async (path, options = {}) => { requests.push({ path, options }); return options.method === 'POST' ? profile : []; };
    const before = requests.filter(({ options }) => options.method === 'POST').length;
    await context.switchVoiceCloneModel(model.id);
    assert.equal(state.voiceClone.pendingModelId, model.id);
    assert.equal(requests.filter(({ options }) => options.method === 'POST').length, before);
    await context.handleDocumentClick({ target: targetFor('#voice-clone-create-model-button') });
    const creation = JSON.parse(requests.findLast(({ options }) => options.method === 'POST').options.body);
    assert.equal(creation.provider, model.provider);
    assert.equal(creation.target_model, model.model);
    assert.equal(creation.clone_sample_id, ready.source_clone_sample_id);
    assert.equal(state.voiceClone.profileId, profile.id);
    assert.equal(state.voiceClone.pendingModelId, '');
    state.voiceClone.profileId = ready.id;
    context.network = async path => path.includes('/library/') ? [profile] : [];
    await context.switchVoiceCloneModel(model.id);
    assert.equal(state.voiceClone.profileId, profile.id);
    assert.equal(state.voiceClone.pendingModelId, '');
  }
});

test('single-use model switching reuses only this reference and keeps it usable for comparisons', async () => {
  for (const differentReference of [false, true]) {
    const { context, state, file, requests } = await setup();
    const nextModel = { ...qwenAudioModels[0], id: 'qwen_audio_next:qwen-audio-3.1-tts-next', provider: 'qwen_audio_next', model: 'qwen-audio-3.1-tts-next' };
    const flashModel = { ...models[0], id: 'cosyvoice:cosyvoice-v3.5-flash', model: 'cosyvoice-v3.5-flash' };
    const nextProfile = { ...ready, provider: nextModel.provider, target_model: nextModel.model };
    const flashProfile = { ...ready, id: 'flash-profile', target_model: flashModel.model,
      source_clone_sample_id: differentReference ? 'another-reference' : sample.id };
    state.serviceStatus.voiceClone = { enabled: true, models: [nextModel, flashModel], discard_voice_after_download: true };
    state.voiceClone.profiles = [nextProfile];
    state.voiceClone.profileId = nextProfile.id;
    state.voiceClone.previewText = '今天的测试内容。';
    state.voiceClone.addFlow = { uploadedFile: file, uploadedSample: sample, createdProfileId: nextProfile.id };
    context.network = async (path, options = {}) => {
      requests.push({ path, options });
      if (path.includes('/library/')) return [nextProfile, flashProfile];
      if (path.endsWith('/preview')) return { id: 'compared-audio', status: 'ready' };
      return [];
    };
    await context.switchVoiceCloneModel(flashModel.id);
    if (differentReference) {
      assert.equal(state.voiceClone.profileId, nextProfile.id);
      assert.equal(state.voiceClone.pendingModelId, flashModel.id);
      assert.equal(requests.some(({ options }) => options.method === 'POST'), false);
      continue;
    }
    assert.equal(state.voiceClone.profileId, flashProfile.id);
    assert.equal((await context.createVoiceClonePreview()).id, 'compared-audio');
    await context.switchVoiceCloneModel(nextModel.id);
    assert.equal(state.voiceClone.profileId, nextProfile.id);
    assert.equal((await context.createVoiceClonePreview()).id, 'compared-audio');
    assert.equal(requests.some(({ path, options }) => path.endsWith('/profiles') && options.method === 'POST'), false);
  }
});

test('Qwen-Audio absent or unavailable in the catalog cannot create or generate', async () => {
  const { context, state, requests } = await setup();
  const model = qwenAudioModels[0];
  assert.equal(context.selectVoiceCloneModel(model.id), false);
  state.serviceStatus.voiceClone.models = [...models, { ...model, available: false }];
  await assert.rejects(context.switchVoiceCloneModel(model.id), /暂不可用/);
  context.selectVoiceCloneModel(model.id);
  state.voiceClone.sampleId = sample.id;
  await assert.rejects(context.createVoiceCloneProfile(), /暂不可用/);
  state.voiceClone.profiles = [{ ...ready, provider: model.provider, target_model: model.model }];
  state.voiceClone.profileId = ready.id;
  await assert.rejects(context.createVoiceClonePreview(), /暂不可用/);
  assert.equal(requests.length, 0);
  assert.equal(context.isVoiceCloneServiceAvailable(), true);
});

test('create retry retains the successfully uploaded sample and original inputs', async () => {
  const { context, state, requests, file, submit } = await setup();
  const network = context.network;
  let failed = false;
  context.network = async (path, options) => {
    if (path.endsWith('/profiles') && options?.method === 'POST' && !failed) { failed = true; throw new Error('云端暂不可用'); }
    return network(path, options);
  };
  await submit();
  assert.match(state.voiceClone.error, /云端暂不可用/);
  assert.equal(state.voiceClone.sampleDraft.file, file);
  assert.equal(state.voiceClone.addFlow.uploadedSample.id, sample.id);
  await submit();
  assert.equal(requests.filter(({ path }) => path.endsWith('/samples')).length, 1);
  assert.equal(state.voiceClone.profileId, ready.id);
  assert.equal(state.voiceClone.error, '');
});

test('matched identity without a ready version creates using that identity sample', async () => {
  const { context, state, requests, submit } = await setup();
  const network = context.network;
  context.network = async (path, options) => path.endsWith('/samples') ? { ...sample, match_type: 'speaker_embedding' } : network(path, options);
  await submit();
  const creation = requests.find(({ options }) => options.method === 'POST');
  assert.equal(JSON.parse(creation.options.body).clone_sample_id, sample.id);
  assert.equal(state.voiceClone.addFlow.open, false);
});

test('matched ready identity requires explicit reuse or creation and does not create automatically', async () => {
  const { context, state, requests, submit } = await setup();
  const network = context.network;
  context.network = async (path, options) => path.endsWith('/samples') ? { ...sample, match_type: 'exact_audio' } : path.includes('/library/') ? [{ ...ready }] : network(path, options);
  await submit();
  assert.equal(state.voiceClone.addFlow.stage, 'matched');
  assert.equal(state.voiceClone.addFlow.open, true);
  assert.equal(requests.some(({ options }) => options.method === 'POST'), false);
  assert.equal(state.voiceClone.addFlow.existingProfiles[0].id, ready.id);
});

test('creation poll includes request time in its 30 second deadline and retries only GET', async () => {
  const { context, state, requests } = await setup();
  let now = 0;
  context.clockNow = () => now;
  vm.runInContext('Date.now = clockNow', context);
  context.window.setTimeout = (callback, delay) => { if (delay <= 1000) { now += delay; queueMicrotask(callback); } return 1; };
  context.window.clearTimeout = () => {};
  context.network = async (path, options = {}) => {
    requests.push({ path, options });
    if (options.method !== 'POST') now += Math.min(options.timeoutMs, 10000);
    return { ...ready, status: 'creating', selectable: false };
  };
  state.voiceClone.sampleId = sample.id;
  state.voiceClone.samples = [sample];
  const first = await context.createVoiceCloneProfile();
  assert.equal(first.status, 'creating');
  assert.equal(now, 30000);
  assert.equal(state.voiceClone.library.creatingProfiles[0].id, ready.id);
  await context.createVoiceCloneProfile();
  assert.equal(requests.filter(({ options }) => options.method === 'POST').length, 1);
});

test('an unconfirmed creation preserves reconciliation guidance and retries with one GET', async () => {
  const { context, state, requests } = await setup();
  const unresolved = { ...ready, status: 'creating', selectable: false, voice_id: null, provider_message: '云端创建结果未确认，请勿重复创建；请稍后刷新或联系管理员核对音色' };
  state.voiceClone.sampleId = sample.id;
  state.voiceClone.samples = [sample];
  context.network = async (path, options = {}) => { requests.push({ path, options }); return unresolved; };
  context.window.setTimeout = () => { throw new Error('Unconfirmed creation must not enter the polling loop'); };
  const created = await context.createVoiceCloneProfile();
  assert.equal(created.status, 'creating');
  assert.equal(state.voiceClone.addFlow.error, unresolved.provider_message);
  assert.equal(requests.length, 1);
  await context.createVoiceCloneProfile();
  assert.equal(requests.length, 2);
  assert.equal(requests.filter(({ options }) => options.method === 'POST').length, 1);
  assert.equal(requests[1].path.endsWith('/profiles/profile-1'), true);
  assert.equal(state.voiceClone.addFlow.error, unresolved.provider_message);
});

test('closed creation dialog cannot select a late cloud result', async () => {
  const { context, state } = await setup();
  state.voiceClone.sampleId = sample.id;
  let resolveCreate;
  context.network = () => new Promise(resolve => { resolveCreate = resolve; });
  let open = true;
  const pending = context.createVoiceCloneProfile({ shouldApply: () => open });
  open = false;
  state.voiceClone.profileId = 'new-selection';
  resolveCreate({ ...ready });
  await pending;
  assert.equal(state.voiceClone.profileId, 'new-selection');
});

test('download preserves the server record; failed audio cannot be downloaded', async () => {
  const { context, state } = await setup();
  state.voiceClone.records = [{ id: 'record-1', status: 'ready' }, { id: 'failed', status: 'failed' }];
  const downloads = [];
  context.downloadBinaryFile = async url => { downloads.push(url); return true; };
  context.buildVoiceRecordFileName = record => record.id + '.wav';
  await context.handleDocumentClick({ target: targetFor('[data-voice-record-download]', { voiceRecordDownload: 'record-1' }) });
  await context.handleDocumentClick({ target: targetFor('[data-voice-record-download]', { voiceRecordDownload: 'failed' }) });
  assert.equal(downloads.length, 1);
  assert.equal(state.voiceClone.records.length, 2);
});

test('model switch reuses existing version and missing version waits for confirmation', async () => {
  const { context, state, requests } = await setup();
  const alternative = { ...ready, id: 'profile-2', provider: models[1].provider, target_model: models[1].model };
  state.voiceClone.profiles = [ready];
  state.voiceClone.profileId = ready.id;
  context.network = async (path, options = {}) => { requests.push({ path, options }); return path.includes('/library/') ? [alternative] : []; };
  await context.switchVoiceCloneModel(models[1].id);
  assert.equal(state.voiceClone.profileId, alternative.id);
  assert.equal(state.voiceClone.pendingModelId, '');
  state.voiceClone.profiles = [ready];
  state.voiceClone.profileId = ready.id;
  state.voiceClone.library.profilesByGroup = {};
  context.network = async (path, options = {}) => { requests.push({ path, options }); return []; };
  await context.switchVoiceCloneModel(models[1].id);
  assert.equal(state.voiceClone.profileId, ready.id);
  assert.equal(state.voiceClone.pendingModelId, models[1].id);
  assert.equal(requests.some(({ options }) => options.method === 'POST'), false);
});

test('pagination requests next cursor and appends all groups', async () => {
  const { context, state, requests } = await setup();
  state.voiceClone.library = { ...state.voiceClone.library, groups: [{ group_key: 'one' }], nextCursor: 'page-two', total: 31 };
  context.network = async (path, options = {}) => {
    requests.push({ path, options });
    return path.endsWith('/profiles/creating') ? [] : { groups: [{ group_key: 'two', recommended_profile: ready }], total: 31, next_cursor: null };
  };
  await context.handleDocumentClick({ target: targetFor('#voice-clone-load-more-button') });
  assert.equal(state.voiceClone.library.groups.length, 2);
  assert.ok(requests.some(({ path }) => path.includes('cursor=page-two')));
});

test('instruction overflow is rejected before synthesis without truncating the draft', async () => {
  const { context, state, requests } = await setup();
  state.voiceClone.profiles = [ready];
  state.voiceClone.profileId = ready.id;
  state.voiceClone.previewText = '要生成的文案';
  state.voiceClone.voiceInstruction = '温'.repeat(29);
  await assert.rejects(context.createVoiceClonePreview(), /57|缩短|超出/);
  assert.equal(requests.length, 0);
  assert.equal(state.voiceClone.voiceInstruction.length, 29);
});

test('switching projects away and back rejects an old upload result', async () => {
  const { context, state, file } = await setup();
  let resolveUpload;
  context.network = () => new Promise(resolve => { resolveUpload = resolve; });
  const pending = context.uploadVoiceCloneSampleFile('主持人', file);
  state.voiceClone.requestGeneration += 2;
  state.voiceClone.displayName = '当前草稿';
  resolveUpload({ ...sample });
  await pending;
  assert.equal(state.voiceClone.sampleId, '');
  assert.equal(state.voiceClone.displayName, '当前草稿');
});

test('a failed generation keeps its draft and clears busy after library recovery', async () => {
  const { context, state } = await setup();
  state.voiceClone.profiles = [ready];
  state.voiceClone.profileId = ready.id;
  state.voiceClone.previewText = '继续保留这段文案';
  context.network = async () => { const error = new Error('失效'); error.code = 'voice_clone_profile_unavailable'; throw error; };
  vm.runInContext('refreshVoiceCloneLibrary = async () => { state.voiceClone.requestGeneration += 1; return true; };', context);
  await context.handleDocumentClick({ target: targetFor('#voice-clone-preview-button') });
  assert.equal(state.voiceClone.busy, '');
  assert.match(state.voiceClone.error, /失效/);
  assert.equal(state.voiceClone.previewText, '继续保留这段文案');
});

test('instruction-less model sends null and preserves the optional instruction draft', async () => {
  const { context, state, requests } = await setup();
  state.voiceClone.profiles = [{ ...ready, provider: models[1].provider, target_model: models[1].model }];
  state.voiceClone.profileId = ready.id;
  state.voiceClone.voiceInstruction = '温柔';
  context.network = async (path, options = {}) => { requests.push({ path, options }); return options.method === 'POST' ? { id: 'record-1', status: 'ready' } : []; };
  await context.createVoiceClonePreview();
  assert.equal(JSON.parse(requests[0].options.body).instruction, null);
  assert.equal(state.voiceClone.voiceInstruction, '温柔');
});

test('current model select keeps its selected value while fetching versions', async () => {
  const { context, state } = await setup();
  state.voiceClone.profiles = [ready];
  state.voiceClone.profileId = ready.id;
  const target = {
    value: models[1].id,
    matches: selector => selector === '#voice-clone-current-model-select',
    set textContent(_value) { this.value = ''; },
    get textContent() { return 'CosyVoice Qwen'; },
  };
  await context.handleDocumentChange({ target });
  assert.equal(state.voiceClone.pendingModelId, models[1].id);
});

test('explicit model creation reuses a legacy voiceprint sample without uploading', async () => {
  const { context, state, requests } = await setup();
  state.voiceClone.profiles = [{ ...ready, source_clone_sample_id: null, source_sample_id: 'voiceprint-1' }];
  state.voiceClone.profileId = ready.id;
  state.voiceClone.pendingModelId = models[1].id;
  await context.handleDocumentClick({ target: targetFor('#voice-clone-create-model-button') });
  const creation = requests.find(({ options }) => options.method === 'POST');
  assert.ok(creation);
  assert.equal(JSON.parse(creation.options.body).sample_id, 'voiceprint-1');
  assert.equal(JSON.parse(creation.options.body).clone_sample_id, undefined);
  assert.equal(requests.some(({ path }) => path.endsWith('/samples')), false);
});

test('choosing a different file cannot leave a previous identity sample selected after upload failure', async () => {
  const { context, state, file } = await setup();
  await context.uploadVoiceCloneSampleFile('主持人', file);
  const otherFile = new Blob(['other']);
  context.network = async () => { throw new Error('检查失败'); };
  await assert.rejects(context.uploadVoiceCloneSampleFile('另一个声音', otherFile), /检查失败/);
  assert.equal(state.voiceClone.sampleId, '');
  assert.equal(state.voiceClone.addFlow.uploadedSample, null);
  assert.notEqual(state.voiceClone.sharedPlayer.sampleId, sample.id);
});

test('refresh recovers a cloud creation after its dialog was closed without another create', async () => {
  const { context, state, requests, refreshData } = await setup();
  state.voiceClone.addFlow.open = false;
  let cloudReady = false;
  context.network = async (path, options = {}) => {
    requests.push({ path, options });
    if (path.endsWith('/profiles/creating')) return cloudReady ? [] : [{ ...ready, status: 'creating', selectable: false }];
    if (path.endsWith('/profiles/profile-1')) { cloudReady = true; return { ...ready }; }
    if (path.includes('/library?')) return { groups: cloudReady ? [{ group_key: 'identity-1', recommended_profile: ready }] : [], total: cloudReady ? 1 : 0 };
    if (path.endsWith('/synthesis-records')) return [];
    throw new Error('Unexpected path ' + path);
  };
  await refreshData();
  assert.equal(state.voiceClone.profileId, ready.id);
  assert.equal(state.voiceClone.library.creatingProfiles.length, 0);
  assert.equal(state.voiceClone.addFlow.open, false);
  assert.equal(requests.some(({ options }) => options.method === 'POST'), false);
});

test('composition updates only the numeric count and does not replace the editor', async () => {
  const { context, state } = await setup();
  state.voiceClone.profiles = [ready];
  state.voiceClone.profileId = ready.id;
  const counter = { textContent: '' };
  const button = { disabled: false };
  context.document.querySelector = selector => selector === '#voice-clone-character-count' ? counter : selector === '#voice-clone-preview-button' ? button : null;
  let renders = 0;
  context.renderAll = () => { renders += 1; };
  await context.handleDocumentInput({ isComposing: true, target: { value: '中文', matches: selector => selector === '#voice-clone-preview-text' } });
  assert.equal(counter.textContent, '2');
  assert.equal(renders, 0);
  assert.equal(button.disabled, false);
});

test('identity details loads every reference sample and closed details discard late samples', async () => {
  const { context, state } = await setup();
  const samples = [sample, { ...sample, id: 'sample-2', original_filename: 'second.wav' }];
  context.network = async () => samples;
  const loaded = await context.loadVoiceCloneGroupSamples('identity-1');
  assert.equal(loaded.length, 2);
  assert.equal(state.voiceClone.library.samplesByGroup['identity-1'][1].id, 'sample-2');
  let resolveSamples;
  context.network = () => new Promise(resolve => { resolveSamples = resolve; });
  let open = true;
  const pending = context.loadVoiceCloneGroupSamples('identity-2', { shouldApply: () => open });
  open = false;
  resolveSamples([{ ...sample, id: 'stale' }]);
  await pending;
  assert.equal(state.voiceClone.library.samplesByGroup['identity-2'], undefined);
  assert.equal(state.voiceClone.samples.some(item => item.id === 'stale'), false);
});

test('a reference sample with no profile opens explicit creation without another upload', async () => {
  const { context, state, requests } = await setup();
  state.voiceClone.library.groups = [{ group_key: 'identity-1', identity_id: 'identity-1', display_name: '主持人', version_count: 0 }];
  state.voiceClone.library.samplesByGroup = { 'identity-1': [sample] };
  state.voiceClone.library.expandedGroupKey = 'identity-1';
  state.voiceClone.previewText = '保留已有文案';
  await context.handleDocumentClick({ target: targetFor('[data-voice-clone-sample-create]', { voiceCloneSampleCreate: sample.id, voiceGroupKey: 'identity-1' }) });
  assert.equal(state.voiceClone.sampleId, sample.id);
  assert.equal(state.voiceClone.addFlow.stage, 'matched');
  assert.equal(state.voiceClone.addFlow.open, true);
  assert.equal(state.voiceClone.library.expandedGroupKey, '');
  assert.equal(state.voiceClone.previewText, '保留已有文案');
  assert.equal(requests.length, 0);
  await context.handleDocumentClick({ target: targetFor('#voice-clone-create-button') });
  assert.ok(requests.some(({ options }) => options.method === 'POST'));
  assert.equal(requests.some(({ path }) => path.endsWith('/samples')), false);
});

test('failed version recovery keeps its model and uses its existing sample', async () => {
  const { context, state, requests } = await setup();
  const failed = { ...ready, status: 'failed', selectable: false, provider: models[1].provider, target_model: models[1].model };
  state.voiceClone.library.groups = [{ group_key: 'identity-1', recommended_profile: failed }];
  await context.handleDocumentClick({ target: targetFor('[data-voice-profile-recreate]', { voiceProfileRecreate: failed.id }) });
  assert.equal(state.voiceClone.sampleId, sample.id);
  assert.equal(state.voiceClone.targetModel, models[1].model);
  assert.equal(state.voiceClone.addFlow.open, true);
  assert.equal(requests.length, 0);
});

test('sample deletion removes cached identity references and uploaded reuse data', async () => {
  const { context, state } = await setup();
  state.voiceClone.samples = [sample];
  state.voiceClone.samplePage.items = [sample];
  state.voiceClone.library.samplesByGroup = { 'identity-1': [sample] };
  state.voiceClone.addFlow.uploadedSample = sample;
  state.voiceClone.addFlow.existingSamples = [sample];
  state.voiceClone.sharedPlayer = { sampleId: sample.id, url: '/old.wav' };
  context.network = async () => ({});
  await context.deleteVoiceCloneSample(sample.id);
  assert.equal(state.voiceClone.samples.length, 0);
  assert.equal(state.voiceClone.samplePage.items.length, 0);
  assert.equal(state.voiceClone.library.samplesByGroup['identity-1'].length, 0);
  assert.equal(state.voiceClone.addFlow.uploadedSample, null);
  assert.notEqual(state.voiceClone.sharedPlayer.sampleId, sample.id);
});

test('library refresh synchronizes open details after creation completes and a version is deleted', async () => {
  const { context, state } = await setup();
  const second = { ...ready, id: 'profile-2', provider: models[1].provider, target_model: models[1].model };
  let profiles = [ready, second];
  state.voiceClone.library.expandedGroupKey = 'identity-1';
  state.voiceClone.library.profilesByGroup = { 'identity-1': [{ ...ready, status: 'creating' }, second] };
  state.voiceClone.library.samplesByGroup = { 'identity-1': [{ ...sample, profile_count: 2 }] };
  context.network = async (path, options = {}) => {
    if (options.method === 'DELETE') { profiles = [second]; return null; }
    if (path.includes('/library?')) return { groups: [{ group_key: 'identity-1', identity_id: 'identity-1', version_count: profiles.length, recommended_profile: second }], total: 1 };
    if (path.endsWith('/library/identity-1/profiles')) return profiles;
    if (path.endsWith('/library/identity-1/samples')) return [{ ...sample, profile_count: profiles.length }];
    if (path.endsWith('/profiles/creating') || path.endsWith('/synthesis-records')) return [];
    throw new Error(`Unexpected request ${path}`);
  };

  await context.refreshVoiceCloneLibrary();
  assert.equal(state.voiceClone.library.profilesByGroup['identity-1'][0].status, 'ready');
  await context.deleteVoiceCloneProfile(ready.id);
  assert.deepEqual(Array.from(state.voiceClone.library.profilesByGroup['identity-1'], profile => profile.id), [second.id]);
  assert.equal(state.voiceClone.library.samplesByGroup['identity-1'][0].profile_count, 1);
  assert.equal(state.voiceClone.library.expandedGroupKey, 'identity-1');
});

test('search updates pagination and total without replacing the focused search field', async () => {
  const { context, state } = await setup();
  const input = { value: '', selectionStart: 0, selectionEnd: 0 };
  const list = { scrollTop: 17, outerHTML: '' };
  const pagination = { outerHTML: '' };
  const total = { textContent: 'old' };
  context.document.activeElement = input;
  context.document.querySelector = selector => ({ '#voice-clone-library-list': list, '#voice-clone-library-pagination': pagination, '#voice-clone-library-total': total }[selector] || null);
  context.elements = { simpleWorkspace: { scrollTop: 29 } };
  context.renderVoiceIdentityList = () => '<div id="voice-clone-library-list"></div>';
  context.renderVoiceCloneLibraryPagination = () => `<div id="voice-clone-library-pagination">${state.voiceClone.library.nextCursor ? '<button id="voice-clone-load-more-button">加载更多</button>' : ''}</div>`;
  let pendingSearch;
  context.window.setTimeout = callback => { pendingSearch = callback; return 1; };
  context.refreshVoiceCloneLibrary = async () => { state.voiceClone.library.nextCursor = 'page-two'; state.voiceClone.library.total = 50; return true; };
  context.scheduleVoiceCloneSearch('profile');
  await pendingSearch();
  assert.match(pagination.outerHTML, /voice-clone-load-more-button/);
  assert.equal(total.textContent, '50');
  context.refreshVoiceCloneLibrary = async () => { state.voiceClone.library.nextCursor = null; state.voiceClone.library.total = 2; return true; };
  context.scheduleVoiceCloneSearch('profile');
  await pendingSearch();
  assert.doesNotMatch(pagination.outerHTML, /voice-clone-load-more-button/);
  assert.equal(total.textContent, '2');
  assert.equal(context.document.activeElement, input);
  assert.equal(list.scrollTop, 17);
  assert.equal(context.elements.simpleWorkspace.scrollTop, 29);
});

test('voice expiry is fixed at 72 hours and honors an earlier server dependency deadline', async () => {
  const { context } = await setup();
  const created = '2026-09-01 08:00:00';
  const deadline = Date.parse('2026-09-04T08:00:00Z');
  const asset = { created_at: created, updated_at: '2099-01-01', last_used_at: '2099-01-01' };
  assert.equal(context.getVoiceAssetExpiresAt(asset).getTime(), deadline);
  assert.equal(context.isVoiceAssetExpired(asset, deadline - 1), false);
  assert.equal(context.isVoiceAssetExpired(asset, deadline), true);
  assert.equal(context.getVoiceAssetExpiresAt({ ...asset, expires_at: '2026-09-02T08:00:00Z' }).getTime(), deadline - 48 * 3600000);
  assert.equal(context.isVoiceAssetExpired({}), false);
  for (const normalize of ['normalizeSample', 'normalizeProfile', 'normalizeGroup']) {
    assert.equal(context.SpeakerVoiceCloneLibrary[normalize]({ expires_at: '2026-09-02T08:00:00Z' }).expires_at, '2026-09-02T08:00:00Z');
  }
});

test('project records load without a selected voice and do not cross an ABA project switch', async () => {
  const { context, state, requests } = await setup();
  state.voiceClone.profileId = '';
  const record = { id: 'record', created_at: new Date().toISOString(), status: 'ready' };
  context.network = async path => { requests.push({ path }); return [record]; };
  assert.equal(await context.refreshVoiceCloneRecords(), true);
  assert.equal(requests[0].path, '/projects/project-1/voice-cloning/synthesis-records');
  assert.equal(state.voiceClone.records[0].id, 'record');
  let resolve;
  context.network = () => new Promise(done => { resolve = done; });
  const pending = context.refreshVoiceCloneRecords();
  state.voiceClone.requestGeneration += 2;
  resolve([{ id: 'stale' }]);
  assert.equal(await pending, false);
  assert.equal(state.voiceClone.records[0].id, 'record');
});

test('expired voices and samples cannot generate or create while the draft and project records survive', async () => {
  const { context, state, requests } = await setup();
  const expired = { ...ready, expires_at: '2000-01-01T00:00:00Z' };
  state.voiceClone.profiles = [expired];
  state.voiceClone.profileId = expired.id;
  state.voiceClone.previewText = '到期后保留这段文案';
  state.voiceClone.records = [{ id: 'still-valid' }];
  assert.equal(context.SpeakerVoiceCloneLibrary.isSelectableProfile(expired), false);
  await assert.rejects(context.createVoiceClonePreview(), /到期|72小时/);
  state.voiceClone.samples = [{ ...sample, expires_at: expired.expires_at }];
  state.voiceClone.sampleId = sample.id;
  await assert.rejects(context.createVoiceCloneProfile(), /到期|72小时/);
  assert.equal(requests.length, 0);
  assert.equal(state.voiceClone.previewText, '到期后保留这段文案');
  assert.equal(state.voiceClone.records[0].id, 'still-valid');
});

test('expired upload cache is replaced only by a new upload, and expired confirmation and download never request', async () => {
  const { context, state, requests, file } = await setup();
  state.voiceClone.addFlow.uploadedFile = file;
  state.voiceClone.addFlow.uploadedSample = { ...sample, id: 'expired', expires_at: '2000-01-01T00:00:00Z' };
  await context.uploadVoiceCloneSampleFile('主持人', file);
  assert.equal(requests.filter(item => item.path.endsWith('/samples')).length, 1);
  state.people = [{ samples: [{ id: 'voiceprint', expires_at: '2000-01-01T00:00:00Z' }] }];
  await assert.rejects(context.confirmVoiceSample('voiceprint'), /到期|72小时/);
  state.voiceClone.records = [{ id: 'old', status: 'ready', expires_at: '2000-01-01T00:00:00Z' }];
  let downloads = 0;
  context.downloadBinaryFile = async () => { downloads += 1; };
  await context.handleDocumentClick({ target: targetFor('[data-voice-record-download]', { voiceRecordDownload: 'old' }) });
  assert.equal(downloads, 0);
  assert.equal(state.voiceClone.records.length, 1);
});

test('an audio player left open across expiry refuses playback without deleting its asset', async () => {
  const { context } = await setup();
  let paused = 0;
  const audio = { dataset: { voiceExpiresAt: '2000-01-01T00:00:00Z' }, pause() { paused += 1; }, removeAttribute() {}, load() {} };
  context.handleVoiceAudioPlay({ target: audio });
  assert.equal(paused, 1);
});

test('server expiry at submit keeps independent audio history and user text while clearing the invalid voice', async () => {
  const { context, state } = await setup();
  state.voiceClone.profiles = [ready];
  state.voiceClone.profileId = ready.id;
  state.voiceClone.previewText = '服务端到期后保留的文案';
  state.voiceClone.records = [{ id: 'valid-audio' }];
  context.network = async () => { const error = new Error('已超过72小时'); error.code = 'voice_asset_expired'; throw error; };
  context.refreshVoiceCloneLibrary = async () => true;
  await assert.rejects(context.createVoiceClonePreview(), error => error.code === 'voice_asset_expired');
  assert.equal(state.voiceClone.profileId, '');
  assert.equal(state.voiceClone.records[0].id, 'valid-audio');
  assert.equal(state.voiceClone.previewText, '服务端到期后保留的文案');
});

test('an expired open details button cannot preview, reuse or recreate its reference sample', async () => {
  for (const [selector, dataset] of [
    ['[data-voice-clone-sample-preview]', { voiceCloneSamplePreview: sample.id }],
    ['[data-voice-clone-sample-select]', { voiceCloneSampleSelect: sample.id }],
    ['[data-voice-clone-sample-create]', { voiceCloneSampleCreate: sample.id, voiceGroupKey: 'identity-1' }],
  ]) {
    const { context, state, requests } = await setup();
    state.voiceClone.samples = [{ ...sample, expires_at: '2000-01-01T00:00:00Z' }];
    state.voiceClone.addFlow.open = false;
    await context.handleDocumentClick({ target: targetFor(selector, dataset) });
    assert.equal(state.voiceClone.sampleId, '');
    assert.equal(state.voiceClone.addFlow.open, false);
    assert.match(state.voiceClone.error, /到期/);
    assert.equal(requests.length, 0);
  }
});

test('expiry remembered by a closed drawer is scoped to its sample and does not block another voice', async () => {
  const { context, state, requests } = await setup();
  state.voiceClone.sampleId = sample.id;
  state.voiceClone.samples = [sample];
  state.voiceClone.addFlow.sourceAsset = { id: 'another-sample', expires_at: '2000-01-01T00:00:00Z' };
  await context.createVoiceCloneProfile();
  assert.ok(requests.some(({ path, options }) => path.endsWith('/profiles') && options.method === 'POST'));
  state.voiceClone.addFlow.sourceAsset.id = sample.id;
  await assert.rejects(context.createVoiceCloneProfile(), /到期/);
});

test('generation click displays server expiry after recovery clears the selected voice', async () => {
  const { context, state } = await setup();
  state.voiceClone.profiles = [ready];
  state.voiceClone.profileId = ready.id;
  state.voiceClone.previewText = '到期反馈必须保留这段文案';
  state.voiceClone.records = [{ id: 'valid-project-audio' }];
  context.network = async () => { const error = new Error('已超过72小时'); error.code = 'voice_asset_expired'; throw error; };
  vm.runInContext('refreshVoiceCloneLibrary = async () => { state.voiceClone.requestGeneration += 1; return true; };', context);
  await context.handleDocumentClick({ target: targetFor('#voice-clone-preview-button') });
  assert.match(state.voiceClone.error, /到期/);
  assert.equal(state.voiceClone.profileId, '');
  assert.equal(state.voiceClone.busy, '');
  assert.equal(state.voiceClone.previewText, '到期反馈必须保留这段文案');
  assert.equal(state.voiceClone.records[0].id, 'valid-project-audio');
});


test('single-use creation uploads a file and bypasses the old voice reuse picker', async () => {
  const { context, state, requests, submit } = await setup();
  state.serviceStatus.voiceClone.discard_voice_after_download = true;
  const network = context.network;
  context.network = async (path, options) => path.endsWith('/samples') ? { ...sample, match_type: 'exact_audio' } : network(path, options);
  await submit();
  assert.equal(state.voiceClone.profileId, ready.id);
  assert.equal(state.voiceClone.addFlow.createdProfileId, ready.id);
  assert.equal(requests.filter(({ path, options }) => path.endsWith('/profiles') && options.method === 'POST').length, 1);
  assert.equal(requests.some(({ path }) => path.includes('/library/')), false);
});

test('download confirmation clears the reference but generation keeps it for comparisons', async () => {
  const { context, state, file, submit } = await setup();
  state.serviceStatus.voiceClone.discard_voice_after_download = true;
  await submit();
  const result = { id: 'record-single-use', status: 'ready', voice_clone_profile_id: ready.id };
  state.voiceClone.voiceInstruction = '温柔';
  state.voiceClone.previewText = '保留文案';
  const calls = [];
  context.network = async (path, options = {}) => { calls.push(path); return options.method === 'POST' ? result : [result]; };
  assert.equal((await context.createVoiceClonePreview()).id, result.id);
  assert.equal(state.voiceClone.profileId, ready.id);
  assert.equal((await context.createVoiceClonePreview()).id, result.id);
  assert.equal(state.voiceClone.addFlow.uploadedFile, file);
  await context.releaseVoiceCloneAfterDownload(result, 'project-1');
  assert.equal(state.voiceClone.profileId, '');
  assert.equal(state.voiceClone.sampleId, '');
  assert.equal(state.voiceClone.sampleDraft.file, null);
  assert.equal(state.voiceClone.addFlow.uploadedFile, undefined);
  assert.equal(state.voiceClone.voiceInstruction, '温柔');
  assert.equal(state.voiceClone.previewText, '保留文案');
  assert.equal(state.voiceClone.records[0].id, result.id);
  await assert.rejects(context.createVoiceClonePreview(), /创建或选择/);
  assert.equal(calls.filter(path => path.endsWith('/preview')).length, 2);
  assert.equal(calls.filter(path => path.endsWith('/download-complete')).length, 1);
});

test('single-use mode rejects a voice selected without a newly uploaded reference', async () => {
  const { context, state, requests } = await setup();
  state.serviceStatus.voiceClone.discard_voice_after_download = true;
  state.voiceClone.profiles = [ready]; state.voiceClone.profileId = ready.id;
  await assert.rejects(context.createVoiceClonePreview(), /上传新的参考音频/);
  assert.equal(requests.length, 0);
});


test('download cancellation never acknowledges cleanup; saved file does', async () => {
  for (const saved of [false, true]) {
    const { context, state, requests, submit } = await setup();
    state.serviceStatus.voiceClone.discard_voice_after_download = true;
    await submit();
    state.voiceClone.records = [{ id: 'record-download', status: 'ready', voice_clone_profile_id: ready.id }];
    context.window.SpeakerDesktopBridge = { isAvailable: () => true, pickSaveFile() {}, writeFile() {} };
    context.saved = saved;
    vm.runInContext('downloadBinaryFile = async () => saved;', context);
    context.network = async (path, options) => { requests.push({ path, options }); return null; };
    await context.handleDocumentClick({ target: targetFor('[data-voice-record-download]', { voiceRecordDownload: 'record-download' }) });
    assert.equal(requests.filter(({ path }) => path.endsWith('/download-complete')).length, Number(saved));
    assert.equal(state.voiceClone.profileId, saved ? '' : ready.id);
  }
});

test('download completion refreshes all batch outputs without clearing other project data', async () => {
  const { context, state, submit } = await setup();
  state.serviceStatus.voiceClone.discard_voice_after_download = true;
  state.serviceStatus.voiceClone.discard_outputs_after_download = true;
  await submit();
  const downloaded = { id: 'chosen', status: 'ready', voice_clone_profile_id: ready.id };
  const sibling = { id: 'comparison', status: 'ready', voice_clone_profile_id: ready.id };
  const unrelated = { id: 'unrelated', status: 'ready', voice_clone_profile_id: 'other-batch' };
  state.voiceClone.records = [downloaded, sibling, unrelated];
  context.network = async (path, options = {}) => options.method === 'POST' ? null : [unrelated];
  await context.releaseVoiceCloneAfterDownload(downloaded, 'project-1');
  assert.deepEqual(Array.from(state.voiceClone.records, record => record.id), ['unrelated']);
});

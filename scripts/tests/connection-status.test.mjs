import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

async function load(fetch, { online = true } = {}) {
  const timers = [];
  const listeners = new Map();
  const context = vm.createContext({
    URL, URLSearchParams, Headers, AbortController, FormData,
    navigator: { onLine: online },
    localStorage: { getItem: () => null },
    document: { querySelector: () => null },
    normalizeError: error => error, renderAll() {}, applyVoiceCloneProviderDefaults() {},
    window: { location: { protocol: 'https:', hostname: 'fixture' }, addEventListener: (name, callback) => listeners.set(name, callback),
      setTimeout, clearTimeout, setInterval: callback => { timers.push(callback); return timers.length; } },
    fetch,
  });
  for (const name of ['state', 'api', 'local-recognition']) {
    vm.runInContext(await readFile(new URL(`../../src/frontend/js/${name}.js`, import.meta.url), 'utf8'), context);
  }
  vm.runInContext('globalThis.state = state;', context);
  return { context, timers, listeners, local: context.SpeakerLocalRecognition, state: context.state };
}

function response(value = { status: 'ok' }, status = 200) {
  return { ok: status < 400, status, headers: new Headers({ 'content-type': 'application/json' }), json: async () => value };
}

test('a failed business request does not claim offline when server health succeeds', async () => {
  const paths = [];
  const { context, state } = await load(async url => {
    paths.push(new URL(url).pathname);
    if (url.endsWith('/health')) return response();
    throw new TypeError('Load failed');
  });
  await assert.rejects(context.request('/voiceprints/provider-status'), /连接后端失败/);
  assert.equal(state.localRecognition.offline, false);
  assert.deepEqual(paths, ['/api/v1/voiceprints/provider-status', '/api/v1/health']);
});

test('unreachable server uses offline mode and automatically reloads projects after recovery', async () => {
  let available = false;
  const { context, state, local, timers } = await load(async url => {
    if (!available) throw new TypeError('Load failed');
    if (url.endsWith('/projects')) return response([{ id: 'p1', name: '恢复项目', job_count: 2 }]);
    if (url.endsWith('/summaries')) return response([]);
    return response();
  });
  await local.initialize();
  assert.equal(state.localRecognition.offline, true);
  available = true;
  assert.equal(timers.length, 1);
  await timers[0]();
  assert.equal(state.localRecognition.offline, false);
  assert.equal(state.projects[0].id, 'p1');
  assert.equal(state.projectLoad.status, 'ready');
});

test('startup probes the LAN server even when the OS reports no internet connection', async () => {
  const { state, local } = await load(async () => response(), { online: false });
  await local.initialize();
  assert.equal(state.localRecognition.offline, false);
});

test('HTTP application errors retain their status and do not change connection mode', async () => {
  const { context, state } = await load(async () => response({ detail: '数据库繁忙' }, 503));
  await assert.rejects(context.request('/config/status'), error => error.status === 503);
  assert.equal(state.localRecognition.offline, false);
});

test('an OS offline event probes the LAN rather than disabling a reachable server', async () => {
  const paths = [];
  const { context, state, local, listeners } = await load(async url => {
    paths.push(new URL(url).pathname);
    return response();
  });
  await local.initialize();
  context.navigator.onLine = false;
  await listeners.get('offline')();
  assert.equal(state.localRecognition.offline, false);
  assert.deepEqual(paths, ['/api/v1/health', '/api/v1/health']);
});

test('an OS offline event still enables local mode when LAN health actually fails', async () => {
  let reachable = true;
  const { state, local, listeners } = await load(async () => {
    if (!reachable) throw new TypeError('LAN unavailable');
    return response();
  });
  await local.initialize();
  reachable = false;
  await listeners.get('offline')();
  assert.equal(state.localRecognition.offline, true);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

test('offline bridge preserves server scope and project match for source access', async () => {
  const calls = [];
  const context = { window: { __TAURI__: { core: { invoke: async (command, args) => { calls.push({ command, args }); return '/tmp/source.wav'; } } } } };
  vm.runInNewContext(readFileSync(new URL('../../desktop-bridge.js', import.meta.url), 'utf8'), context);
  const bridge = context.window.SpeakerDesktopBridge;
  await bridge.offlineSubtitles('https://beijing/api/v1', 'get', { id: 'local-id', project_id: 'project-1' });
  assert.equal(await bridge.offlineSourcePath('https://beijing/api/v1', 'local-id', 'project-1'), '/tmp/source.wav');
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    { command: 'offline_subtitles', args: { scope: 'https://beijing/api/v1', operation: 'get', payload: { id: 'local-id', project_id: 'project-1' } } },
    { command: 'offline_source_path', args: { scope: 'https://beijing/api/v1', id: 'local-id', projectId: 'project-1' } },
  ]);
});

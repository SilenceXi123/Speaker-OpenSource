import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

test('local speech bridge keeps explicit model preparation separate from transcription', async () => {
  const calls = [];
  const context = { window: { __TAURI__: { core: { invoke: async (command, args) => { calls.push({ command, args }); return { ok: true }; } } } } };
  vm.runInNewContext(readFileSync(new URL('../../desktop-bridge.js', import.meta.url), 'utf8'), context);
  const bridge = context.window.SpeakerDesktopBridge;
  await bridge.detectLocalSpeech();
  await bridge.transcribeLocalSpeech('/tmp/中文 source.wav');
  await bridge.cancelLocalSpeech();
  await bridge.prepareLocalSpeech();
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    { command: 'detect_local_speech' },
    { command: 'transcribe_local_speech', args: { path: '/tmp/中文 source.wav' } },
    { command: 'cancel_local_speech' },
    { command: 'prepare_local_speech' },
  ]);
});

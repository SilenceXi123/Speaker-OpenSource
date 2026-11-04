// Isolated frontend build; all native operations and HTTP endpoints are test fixtures.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, mkdir, rm } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const root = fileURLToPath(new URL('../../', import.meta.url));
const build = await mkdtemp(join(tmpdir(), 'speaker-local-ui-'));
const output = join(root, 'output/playwright/local-recognition');
await mkdir(output, { recursive: true });
execFileSync(process.execPath, [join(root, 'scripts/build-frontend.mjs')], { cwd: root, env: { ...process.env, SPEAKER_BUILD_OUTPUT_DIR: build } });
const engine = process.env.LOCAL_BROWSER_ENGINE || 'webkit';
const browser = await (engine === 'webkit' ? webkit.launch({ headless: true }) : chromium.launch({ channel: 'chrome', headless: true }));
try {
  const page = await browser.newPage({ viewport: { width: 1480, height: 980 } });
  const errors = [], imports = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    globalThis.nativeCalls = []; globalThis.localJobs = {}; globalThis.cachedProjects = [];
    globalThis.speechCapability = { available: true, model_installed: true };
    globalThis.recognition = { engine: 'apple_speech', locale: 'zh_CN', duration_ms: 2000, sample_rate_hz: 16000, channel_count: 1, system_version: '26.0', segments: [{ text: '这是本机字幕', start_ms: 0, end_ms: 1800, words: [{ text: '这是本机字幕', start_ms: 0, end_ms: 1800 }] }] };
    window.__TAURI__ = { core: { convertFileSrc: () => 'https://speaker-local.test/source.wav', invoke: async (command, args = {}) => {
      nativeCalls.push([command, args]);
      if (command === 'detect_local_speech') return speechCapability;
      if (command === 'prepare_local_speech') return globalThis.speechCapability = { available: true, model_installed: true };
      if (command === 'transcribe_local_speech') return recognition;
      if (command === 'cancel_local_speech') return { cancelled: true };
      if (command === 'pick_media') return { files: [{ path: '/fixture/source.wav', name: 'source.wav' }] };
      if (command === 'offline_source_path') return '/fixture/source.wav';
      if (command === 'pick_save_file') return '/fixture/output.srt';
      if (command === 'write_file_base64') return true;
      if (command === 'read_file_base64') return btoa('mock media');
      if (command === 'check_client_update') return { available: false };
      if (command !== 'offline_subtitles') return {};
      const { operation, payload } = args;
      if (operation === 'capability') return { available: true, architecture: 'aarch64' };
      if (operation === 'projects') return cachedProjects;
      if (operation === 'save_projects') return globalThis.cachedProjects = payload.projects;
      if (operation === 'list') return Object.values(localJobs);
      if (operation === 'cancel') return { cancelled: true };
      if (operation === 'build') {
        const cues = [{ index: 1, clean_text: '这是本机字幕', start_ms: 0, end_ms: 1800, speaker_key: 'speaker_0', speaker_name: '未区分说话人' }];
        const job = { ...payload.job, local_only: true, local_recognition: payload.recognition, status: 'completed', subtitle_strategy: 'evidence',
          normalized_segments: [{ text: '这是本机字幕', startMs: 0, endMs: 1800, speakerId: 'speaker_0' }], local_cues: cues, local_full_srt: '1\n00:00:00,000 --> 00:00:01,800\n这是本机字幕\n', local_clean_srt: '这是本机字幕',
          local_speakers: { speakers: [], mappings: { speaker_0: '未区分说话人' }, memory_suggestions: [] },
          pipeline_artifacts: { source_revision: { job_id: payload.job.id }, subtitle_cues_snapshot: { count: 1, items: cues }, cue_validation_report: { issues: [], issue_count: 0 } } };
        localJobs[job.id] = job; return job;
      }
      const job = localJobs[payload.id];
      if (!job || job.project_id !== payload.project_id) throw new Error('项目不匹配');
      if (operation === 'get') return job;
      if (operation === 'edit') {
        if (payload.action === 'subtitle_text') { job.local_cues[0].clean_text = payload.changes[0].clean_text; job.local_full_srt = payload.changes[0].clean_text; }
        return job;
      }
      if (operation === 'export') return { filename: `local.${payload.format}`, mime_type: 'text/plain', content_base64: btoa('fixture subtitle') };
      if (operation === 'mark_synced') { job.server_job_id = payload.server_job_id; return job; }
      if (operation === 'delete') { delete localJobs[payload.id]; return {}; }
      throw new Error(`unexpected ${operation}`);
    } } };
  });
  await page.route('**/*', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/source.wav') return route.fulfill({ contentType: 'audio/wav', body: '' });
    if (path.includes('/api/')) {
      if (path.endsWith('/local-jobs')) { imports.push(route.request().postDataBuffer()); return route.fulfill({ status: 429, json: { detail: 'fixture rate limit' } }); }
      let value = [];
      if (path.endsWith('/projects')) value = [{ id: 'p1', name: '本地识别测试' }];
      if (path.endsWith('/health')) value = { status: 'ok' };
      if (path.endsWith('/provider-status')) value = { available: false };
      if (path.endsWith('/config/status')) value = {};
      return route.fulfill({ json: value });
    }
    const name = path === '/' ? 'index.html' : path.slice(1);
    if (!['index.html', 'app.js', 'ui.js', 'xml-repair.js', 'desktop-bridge.js', 'styles.css', 'workbench.css'].includes(name) && !/^ui-icons\/[\w-]+\.svg$/.test(name)) return route.fulfill({ status: 404, body: '' });
    return route.fulfill({ body: await readFile(join(build, name)), contentType: name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : name.endsWith('.svg') ? 'image/svg+xml' : 'text/html' });
  });
  await page.goto('https://speaker-local.test/');
  await page.locator('[data-project-enter="p1"]').click();
  assert.equal(await page.locator('#recognition-engine-select').inputValue(), 'cloud');
  await page.locator('#recognition-engine-select').selectOption('apple');
  assert.match(await page.locator('.local-recognition-controls').innerText(), /素材与识别结果会上传/);
  await page.evaluate(() => { SpeakerLocalRecognition.connectionFailed(); renderAll(); });
  assert.match(await page.locator('#local-mode-notices').innerText(), /离线模式/);
  await page.locator('#media-picker-trigger').click();
  await page.locator('#create-job-button').click();
  await page.waitForSelector('.subtitle-quality-panel');
  assert.match(await page.locator('#local-mode-notices').innerText(), /本机字幕原稿.*未经云端 AI 校对/);
  assert.equal(imports.length, 0);
  await page.locator('[data-cue-text-input]').first().fill('已人工校字');
  await page.locator('#subtitle-combined-save-button').click();
  await page.waitForFunction(() => Object.values(localJobs)[0]?.local_cues[0].clean_text === '已人工校字');
  await page.locator('.timeline-step[data-step="export"]').click();
  await page.locator('[data-download-path*="format=srt"]').first().click();
  await page.waitForFunction(() => nativeCalls.some(([command]) => command === 'write_file_base64'));
  await page.locator('[data-local-action="reconnect"]').click();
  await page.locator('[data-local-action="sync"]').click();
  await page.waitForFunction(() => state.localRecognition.error.includes('fixture rate limit'));
  assert.equal(imports.length, 1);
  assert.equal(await page.evaluate(() => state.localRecognition.offline), false);
  assert.equal(await page.evaluate(() => Object.values(localJobs)[0].local_cues[0].clean_text), '已人工校字');
  assert.equal(await page.evaluate(() => nativeCalls.filter(([command]) => command === 'transcribe_local_speech').length), 1);
  await page.screenshot({ path: join(output, `${engine}-local-review.png`) });
  await page.locator('.timeline-step[data-step="upload"]').click();
  await page.evaluate(() => { speechCapability = { available: false, download_available: true, reason: '中文模型未安装，请联网准备' }; });
  await page.locator('[data-local-action="detect"]').click();
  assert.equal(await page.locator('#create-job-button').isDisabled(), true);
  assert.match(await page.locator('[data-local-action="prepare"]').innerText(), /联网下载/);
  await page.locator('[data-local-action="prepare"]').click();
  assert.equal(await page.locator('#create-job-button').isDisabled(), false);
  await page.screenshot({ path: join(output, `${engine}-local-setup.png`) });
  await page.setViewportSize({ width: 720, height: 900 });
  await page.screenshot({ path: join(output, `${engine}-local-720.png`) });
  assert.deepEqual(errors, []);
  console.log(`${engine}: offline create/edit/export, native capability/prepare, explicit upload and 429 preservation passed`);
} finally { await browser.close(); await rm(build, { recursive: true, force: true }); }

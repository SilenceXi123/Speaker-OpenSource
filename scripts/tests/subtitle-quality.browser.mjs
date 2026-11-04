// Mocked APIs/media only. Run with Playwright installed or available via NODE_PATH.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFile, mkdtemp, mkdir, rm } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const root = fileURLToPath(new URL('../../', import.meta.url));
const build = await mkdtemp(join(tmpdir(), 'speaker-subtitle-qc-'));
const output = join(root, 'output/playwright/subtitle-quality');
await mkdir(output, { recursive: true });
execFileSync(process.execPath, [join(root, 'scripts/build-frontend.mjs')], { cwd: root, env: { ...process.env, SPEAKER_BUILD_OUTPUT_DIR: build } });
const engine = process.env.QC_BROWSER_ENGINE || 'chromium';
const browser = await (engine === 'webkit' ? webkit.launch({ headless: true }) : chromium.launch({ channel: 'chrome', headless: true }));
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const wav = Buffer.alloc(44 + 16000 * 2 * 8);
  wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28); wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40);
  await page.route('**/*', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/source-media')) {
      const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range || '');
      if (range) {
        const start = Number(range[1]), end = range[2] ? Math.min(Number(range[2]), wav.length - 1) : wav.length - 1;
        return route.fulfill({ status: 206, body: wav.subarray(start, end + 1), contentType: 'audio/wav', headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${wav.length}` } });
      }
      return route.fulfill({ body: wav, contentType: 'audio/wav', headers: { 'Accept-Ranges': 'bytes' } });
    }
    if (path.includes('/api/')) {
      let value = [];
      if (path.endsWith('/projects')) value = [{ id: 'qc-project', name: '质检测试', status: 'active' }];
      if (path.endsWith('/health')) value = { status: 'ok' };
      if (path.endsWith('/provider-status')) value = { available: false };
      if (path.endsWith('/config/status')) value = {};
      return route.fulfill({ json: value });
    }
    const name = path === '/' ? 'index.html' : path.slice(1);
    if (!['index.html', 'app.js', 'ui.js', 'xml-repair.js', 'desktop-bridge.js', 'styles.css', 'workbench.css'].includes(name) && !/^ui-icons\/[\w-]+\.svg$/.test(name)) return route.fulfill({ status: 404, body: '' });
    return route.fulfill({ body: await readFile(join(build, name)), contentType: name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : name.endsWith('.svg') ? 'image/svg+xml' : 'text/html' });
  });
  await page.goto('http://speaker-qc.test/');
  await page.waitForLoadState('networkidle');
  await page.evaluate(() => {
    setCurrentProject('qc-project'); state.currentProjectName = '质检测试';
    state.currentJobId = 'qc-job'; state.activeStep = 'review';
    state.cues = [{ index: 1, start_ms: 0, end_ms: 900, clean_text: '啊', speaker_key: 'speaker_0', speaker_name: '主持人' }, { index: 2, start_ms: 1000, end_ms: 1900, clean_text: '人工校正后的第二句', speaker_key: 'speaker_0', speaker_name: '主持人' }];
    state.jobDetail = { id: 'qc-job', project_id: 'qc-project', kind: 'transcription', status: 'completed', original_filename: 'qc.wav', pipeline_artifacts: {
      source_revision: { job_id: 'qc-job' }, subtitle_cues_snapshot: { count: 2, items: state.cues },
      cue_validation_report: { issue_count: 1, issues: [{ code: 'isolated_single_cjk_cue', severity: 'P0', index: 1 }] },
      evidence_subtitle_v2: { validation: { issues: [] }, corrections: [{ source_text: '<img src=x onerror=alert(1)>查记村', replacement_text: '查济村', start_ms: 3000, end_ms: 3600 }, { source_text: '旧字', replacement_text: '新字', token_ids: ['token-000001'] }] },
    } };
    renderAll();
  });
  for (const [width, height] of [[1480, 980], [1280, 820], [720, 900]]) {
    await page.setViewportSize({ width, height });
    const panel = page.locator('.subtitle-quality-panel');
    if (!(await panel.getAttribute('open'))) await panel.locator('summary').click();
    assert.match(await panel.innerText(), /导出前需处理/);
    assert.match(await panel.innerText(), /生成修复历史/);
    assert.match(await panel.innerText(), /无可靠位置/);
    assert.equal(await panel.locator('img').count(), 0);
    assert.ok(await panel.evaluate(element => element.scrollWidth <= element.clientWidth + 1), 'QC panel horizontally overflows');
    await page.screenshot({ path: join(output, `${engine}-${width}x${height}.png`) });
    await panel.locator('summary').click();
  }
  await page.setViewportSize({ width: 1480, height: 980 });
  await page.locator('.subtitle-quality-panel summary').click();
  await page.waitForFunction(() => document.querySelector('#source-media-player')?.duration === 8);
  await page.locator('[data-subtitle-quality-seek][data-cue-start="3000"]').click();
  await page.waitForFunction(() => { const player = document.querySelector('#source-media-player'); return !player.seeking && player.currentTime >= 3 && player.currentTime < 4; }, null, { timeout: 5000 }).catch(async error => {
    console.error(await page.locator('#source-media-player').evaluate(player => ({ currentTime: player.currentTime, duration: player.duration, readyState: player.readyState, paused: player.paused, seeking: player.seeking, seekable: [...Array(player.seekable.length)].map((_, index) => [player.seekable.start(index), player.seekable.end(index)]), error: player.error?.message })));
    throw error;
  });
  await page.locator('#source-media-player').evaluate(player => { player.pause(); player.currentTime = 4; });
  await page.waitForFunction(() => { const player = document.querySelector('#source-media-player'); return !player.seeking && player.currentTime === 4; });
  await page.evaluate(() => { state.currentJobId = 'another-job'; });
  await page.locator('[data-subtitle-quality-seek][data-cue-start="0"]').click();
  assert.equal(await page.locator('#source-media-player').evaluate(player => player.currentTime), 4, 'Stale QC button crossed a task switch');
  await page.evaluate(() => { state.currentJobId = 'qc-job'; state.currentProjectId = 'another-project'; });
  await page.locator('[data-subtitle-quality-seek][data-cue-start="0"]').click();
  assert.equal(await page.locator('#source-media-player').evaluate(player => player.currentTime), 4, 'Stale QC button crossed a project switch');
  await page.evaluate(() => { state.currentProjectId = 'qc-project'; state.jobDetail.pipeline_artifacts = {}; renderAll(); });
  assert.match(await page.locator('.subtitle-quality-panel summary').innerText(), /无检查结果/);
  assert.deepEqual(errors, []);
  console.log(`${engine}: QC at three sizes, safe text, actual media seek and task/project switch guards passed`);
} finally {
  await browser.close();
  await rm(build, { recursive: true, force: true });
}

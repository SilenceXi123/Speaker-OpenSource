// Run with Playwright installed or available through NODE_PATH.
// VOICE_LAYOUT_ENGINE=webkit selects the macOS client's rendering engine family.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFile, mkdtemp, mkdir, rm } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const root = fileURLToPath(new URL('../../', import.meta.url));
const build = await mkdtemp(join(tmpdir(), 'speaker-record-layout-'));
const output = join(root, 'output/playwright/audio-record-layout');
await mkdir(output, { recursive: true });
execFileSync(process.execPath, [join(root, 'scripts/build-frontend.mjs')], {
  cwd: root, env: { ...process.env, SPEAKER_BUILD_OUTPUT_DIR: build },
});
const engine = process.env.VOICE_LAYOUT_ENGINE || 'chromium';
const browser = await (engine === 'webkit' ? webkit.launch({ headless: true })
  : chromium.launch({ channel: 'chrome', headless: true }));
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const models = [{ id: 'qwen:qwen3-tts-vc-2026-01-22', provider: 'qwen', model: 'qwen3-tts-vc-2026-01-22', label: 'Qwen3-TTS', available: true, recommended: true, capabilities: { instruction: false }, constraints: { text_max_characters: 500 } }];
  models.push({ id: 'cosyvoice:cosyvoice-v3.5-plus', provider: 'cosyvoice', model: 'cosyvoice-v3.5-plus', label: 'CosyVoice 3.5 Plus', available: true, capabilities: { instruction: true }, constraints: { text_max_characters: 500, instruction_max_weight: 57, instruction_han_weight: 2 } });
  models.push(...[['qwen-audio-3.0-tts-plus', 'Qwen-Audio 3.0 Plus'], ['qwen-audio-3.0-tts-flash', 'Qwen-Audio 3.0 Flash'], ['qwen-audio-3.1-tts-flash', 'Qwen-Audio 3.1 Flash']].map(([model, label]) => ({
    id: `qwen_audio:${model}`, provider: 'qwen_audio', model, label, available: true,
    capabilities: { instruction: true }, constraints: { text_max_characters: 500, instruction_max_weight: 1600, instruction_han_weight: 1 },
  })));
  const provider = { enabled: true, available: true, provider: 'qwen', target_model: models[0].model, models };
  // All requests stay inside this fixture, including the backend and audio URLs.
  await page.route('**/*', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.includes('/api/')) {
      let value = [];
      if (path.endsWith('/projects')) value = [{ id: 'layout-project', name: '音频布局测试', status: 'active' }];
      if (path.endsWith('/provider-status')) value = path.includes('voice-cloning') ? provider : { available: false };
      if (path.endsWith('/health')) value = { status: 'ok' };
      if (path.endsWith('/config/status')) value = {};
      if (path.endsWith('/library')) value = { groups: [], total: 0 };
      return route.fulfill({ json: value });
    }
    const name = path === '/' ? 'index.html' : path.slice(1);
    if (!['index.html', 'app.js', 'ui.js', 'xml-repair.js', 'desktop-bridge.js', 'styles.css', 'workbench.css'].includes(name)
      && !/^ui-icons\/[\w-]+\.svg$/.test(name)) return route.fulfill({ status: 404, body: '' });
    return route.fulfill({ body: await readFile(join(build, name)), contentType: name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : name.endsWith('.svg') ? 'image/svg+xml' : 'text/html' });
  });
  await page.goto('http://speaker-layout.test/');
  await page.waitForLoadState('networkidle');
  await page.evaluate(provider => {
    setCurrentProject('layout-project'); state.currentProjectName = '音频布局测试';
    state.activeStep = 'voiceCloning'; state.serviceStatus.voiceClone = provider;
    const profile = { id: 'layout-voice', voice_name: '嘉宾 000', provider: 'qwen', target_model: provider.target_model, status: 'ready', selectable: true, source_sample_ready: true, voice_id: 'fixture-voice', expires_at: new Date(Date.now() + 72 * 3600000).toISOString() };
    state.voiceClone.profiles = [profile]; state.voiceClone.profileId = profile.id;
    state.voiceClone.provider = profile.provider; state.voiceClone.targetModel = profile.target_model;
  }, provider);
  for (const [width, height] of [[1480, 980], [1280, 720], [720, 640]]) {
    await page.setViewportSize({ width, height });
    // Increase and reduce the list, as generating and deleting records does.
    for (const count of [1, 8, 30, 2]) {
      await page.evaluate(count => {
        const now = Date.now();
        state.voiceClone.records = Array.from({ length: count }, (_, i) => ({
          id: 'record-' + i, project_id: 'layout-project', status: 'ready',
          voice_name_snapshot: '旁白 ' + (i + 1), provider_snapshot: 'qwen', target_model_snapshot: 'qwen3-tts-vc-2026-01-22',
          created_at: new Date(now).toISOString(), expires_at: new Date(now + 72 * 3600000).toISOString(),
          input_text: i % 2 ? '这是一段比较长的生成音频文案，需要自动换行，并且保证播放器完整显示。'.repeat(12) : '这是第' + (i + 1) + '条音频。',
        }));
        renderAll();
      }, count);
      assert.equal(await page.locator('#voice-clone-preview-button').isEnabled(), true);
      await page.locator('#voice-clone-preview-text').focus();
      await page.keyboard.press('Tab');
      assert.equal(await page.evaluate(() => document.activeElement.id), 'voice-clone-profile-select', 'Settings must precede audio records in the keyboard order');
      await page.locator('.voice-generation-body').evaluate(element => { element.scrollTop = 0; });
      if (width > 720) {
        await page.screenshot({ path: join(output, `${engine}-${width}x${height}-first-screen.png`) });
        assert.ok(await page.locator('.record-row audio').first().evaluate(element => {
          const player = element.getBoundingClientRect(), area = document.querySelector('.voice-generation-body').getBoundingClientRect();
          return player.top >= area.top && player.bottom <= area.bottom;
        }), 'First generated audio player must be fully visible without scrolling');
      }
      const geometry = await page.evaluate(() => {
        const bounds = element => { const r = element.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, left: r.left, right: r.right }; };
        return { container: bounds(document.querySelector('.voice-recent-records')),
          rows: [...document.querySelectorAll('.record-row')].map(row => ({ card: bounds(row), audio: bounds(row.querySelector('audio')) })) };
      });
      assert.equal(geometry.rows.length, count);
      for (const [index, { card, audio }] of geometry.rows.entries()) {
        assert.ok(card.top >= geometry.container.top - 1 && card.bottom <= geometry.container.bottom + 1, 'Record escaped its collapsed container');
        assert.ok(audio.top >= card.top && audio.bottom <= card.bottom && audio.right <= card.right && audio.left >= card.left, 'Player escaped its card');
        if (index) assert.ok(geometry.rows[index - 1].card.bottom <= card.top, 'Record cards overlap');
      }
      for (const index of new Set([0, Math.floor(count / 2), count - 1])) {
        const audio = page.locator('.record-row audio').nth(index);
        await audio.scrollIntoViewIfNeeded();
        assert.ok(await audio.evaluate(element => {
          const r = element.getBoundingClientRect(), area = document.querySelector('.voice-generation-body').getBoundingClientRect();
          return r.top >= area.top - 1 && r.bottom <= area.bottom + 1
            && document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === element;
        }), 'Player is clipped or covered after scrolling');
      }
      if (count === 8) await page.screenshot({ path: join(output, `${engine}-${width}x${height}.png`) });
    }
  }
  assert.equal(await page.locator('#voice-clone-instruction-input').isDisabled(), true);
  assert.match(await page.locator('.voice-instruction-meta').innerText(), /Qwen3-TTS 复刻版不支持语气指令/);
  assert.match(await page.locator('.voice-instruction-meta').innerText(), /CosyVoice 3.5 Plus/);
  await page.evaluate(() => {
    Object.assign(state.voiceClone.profiles[0], { provider: 'cosyvoice', target_model: 'cosyvoice-v3.5-plus' });
    renderAll();
  });
  assert.equal(await page.locator('#voice-clone-instruction-input').isEnabled(), true);
  await page.locator('[data-voice-instruction-example="温柔舒缓"]').click();
  assert.equal(await page.locator('#voice-clone-instruction-input').inputValue(), '温柔舒缓');
  assert.match(await page.locator('.voice-instruction-meta').innerText(), /汉字按 2 计/);
  for (const model of models.filter(item => item.provider === 'qwen_audio')) {
    assert.match(await page.locator(`#voice-clone-current-model-select option[value="${model.id}"]`).innerText(), new RegExp(model.label.replaceAll('.', '\\.')));
    await page.evaluate(model => {
      Object.assign(state.voiceClone.profiles[0], { provider: model.provider, target_model: model.model });
      state.voiceClone.voiceInstruction = '';
      renderAll();
    }, model);
    assert.equal(await page.locator('#voice-clone-current-model-select').inputValue(), model.id);
    assert.equal(await page.locator('#voice-clone-instruction-input').isEnabled(), true);
    await page.locator('[data-voice-instruction-example="自然亲切"]').click();
    assert.equal(await page.locator('#voice-clone-instruction-input').inputValue(), '自然亲切');
    assert.equal(await page.locator('.voice-instruction-meta').innerText(), '4 / 1600 字符');
    await page.locator('#voice-clone-instruction-input').fill('温'.repeat(1601));
    assert.equal(await page.locator('#voice-clone-preview-button').isDisabled(), true);
    await page.locator('#voice-clone-instruction-input').fill('温柔𰀀A');
    assert.equal(await page.locator('#voice-clone-instruction-count').innerText(), '4');
    assert.equal(await page.locator('#voice-clone-preview-button').isEnabled(), true);
    await page.evaluate(id => {
      state.serviceStatus.voiceClone.models.find(item => item.id === id).available = false;
      renderAll();
    }, model.id);
    assert.equal(await page.locator(`#voice-clone-current-model-select option[value="${model.id}"]`).getAttribute('disabled'), '');
    assert.equal(await page.locator('#voice-clone-preview-button').isDisabled(), true);
  }
  assert.deepEqual(errors, []);
  console.log(`${engine}: 12 record layouts and all three Qwen-Audio instruction controls passed`);
} finally {
  await browser.close();
  await rm(build, { recursive: true, force: true });
}

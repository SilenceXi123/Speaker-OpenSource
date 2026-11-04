import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const retentionLibrary = await loadLibrary();
const retentionHelpers = {
  getVoiceAssetExpiresAt: retentionLibrary.getAssetExpiresAt,
  isVoiceAssetExpired: retentionLibrary.isAssetExpired,
  assertVoiceAssetAvailable(asset) {
    if (retentionLibrary.isAssetExpired(asset)) throw new Error("声音数据已到期，请重新上传");
  },
};


function searchTarget(id, value) {
  return {
    id,
    value,
    matches(selector) {
      return selector.split(",").some((item) => item.trim() === `#${id}`);
    },
  };
}


test("voice-clone search waits for Chinese composition to finish", async () => {
  const source = await readFile(
    new URL("../../src/frontend/js/events.js", import.meta.url),
    "utf8",
  );
  const context = vm.createContext({
    ...retentionHelpers,
    state: {
      cueSearchComposing: false,
      voiceClone: {
        profileSearch: "",
        sampleSearch: "",
        displayName: "",
        createDrawer: {},
        searchComposing: { profile: false, sample: false, drawer: false },
      },
    },
    document: { querySelector: () => null },
    window: {
      clearTimeout,
      setTimeout,
      requestAnimationFrame(callback) { callback(); },
    },
  });
  vm.runInContext(source, context);
  context.searchCalls = [];
  vm.runInContext("scheduleVoiceCloneSearch = (kind) => searchCalls.push(kind);", context);
  const target = searchTarget("voice-clone-profile-search-input", "欧阳夏丹");

  context.handleDocumentCompositionStart({ target });
  context.handleDocumentInput({ target, isComposing: true });

  assert.deepEqual(context.searchCalls, []);

  context.handleDocumentCompositionEnd({ target });

  assert.deepEqual(context.searchCalls, ["profile"]);
  assert.equal(context.state.voiceClone.profileSearch, "欧阳夏丹");
});


test("voice-clone search keeps the workbench viewport stable", async () => {
  const source = await readFile(
    new URL("../../src/frontend/js/events.js", import.meta.url),
    "utf8",
  );
  const workspace = { scrollTop: 0 };
  const current = {};
  Object.defineProperty(current, "outerHTML", {
    set() { workspace.scrollTop = 64; },
  });
  const context = vm.createContext({
    ...retentionHelpers,
    state: { voiceClone: {} },
    elements: { simpleWorkspace: workspace },
    document: {
      querySelector(selector) {
        return selector === "#voice-clone-library-list" ? current : null;
      },
    },
    renderVoiceIdentityList: () => "<div></div>",
    window: { clearTimeout, setTimeout },
  });
  vm.runInContext(source, context);

  context.replaceVoiceCloneLibraryList();

  assert.equal(workspace.scrollTop, 0);
});


test("voice-clone project switching continues when job history refresh fails", async () => {
  const source = await readFile(
    new URL("../../src/frontend/js/events.js", import.meta.url),
    "utf8",
  );
  const calls = [];
  const state = {
    currentProjectId: "project-old",
    activeStep: "voiceClone",
    projects: [
      { project_id: "project-old", program_name: "旧项目" },
      { project_id: "project-new", program_name: "新项目" },
    ],
    pickedTaskScriptFiles: [],
    pickedLowerThirdFiles: [],
    pickedSubtitleBackplateFiles: {},
    transcript: {},
    voiceClone: {},
    jobDetail: null,
    lexicon: {},
  };
  const input = {
    value: "project-new",
    matches(selector) { return selector === "#current-project-select"; },
  };
  const context = vm.createContext({
    ...retentionHelpers,
    state,
    CLONE_PAGE_IDS: new Set(["voiceClone"]),
    TRANSCRIPT_PAGE_IDS: new Set(),
    TASK_STEPS: [],
    document: { querySelector: () => null },
    window: { clearTimeout, setTimeout, requestAnimationFrame(callback) { callback(); } },
    clearPendingTaskUploads() {},
    setCurrentProject(projectId) { state.currentProjectId = projectId; },
    refreshJobs: async () => { throw new Error("任务服务暂时不可用"); },
    refreshProjectScripts: async () => [],
    refreshVoiceCloneData: async () => { calls.push("voice-clone"); },
    refreshLexiconData: async () => {},
    renderAll: () => { calls.push("render"); },
    isJobInCurrentProject: () => true,
    setCurrentJobId() {},
    getWorkflowKindForStep: () => "transcription",
    selectJobForStep: async () => null,
    showToast() {},
    normalizeError: (error) => error,
    withUiTask: async (_button, _label, callback) => callback(),
  });
  vm.runInContext(source, context);

  await context.handleDocumentChange({ target: input });

  assert.equal(state.currentProjectId, "project-new");
  assert.deepEqual(calls, ["voice-clone", "render"]);
});


test("profile search keeps compact identity rows, list scroll, and input focus", async () => {
  const [rendererSource, eventSource] = await Promise.all([
    readFile(new URL("../../src/frontend/js/renderers.js", import.meta.url), "utf8"),
    readFile(new URL("../../src/frontend/js/events.js", import.meta.url), "utf8"),
  ]);
  const profile = selectableProfile();
  const group = identityGroup("identity-1", "撒贝宁", ["小撒"], profile);
  const workspace = { scrollTop: 17 };
  const searchInput = { id: "voice-clone-profile-search-input" };
  const replacement = { scrollTop: 0 };
  let currentList;
  let replacedHtml = "";
  let renderAllCalls = 0;
  const originalList = { scrollTop: 39 };
  Object.defineProperty(originalList, "outerHTML", {
    set(value) {
      replacedHtml = value;
      workspace.scrollTop = 88;
      currentList = replacement;
    },
  });
  currentList = originalList;
  let pendingSearch = null;
  const context = vm.createContext({
    ...retentionHelpers,
    state: {
      currentProjectId: "project-1",
      serviceStatus: { voiceClone: { available: true, provider: "qwen" } },
      voiceClone: {
        profileId: profile.id,
        profiles: [profile],
        profileSearch: "撒贝宁",
        library: { groups: [group], loaded: true, expandedGroupKey: "", profilesByGroup: {} },
      },
    },
    elements: { simpleWorkspace: workspace },
    document: {
      activeElement: searchInput,
      querySelector(selector) {
        return selector === "#voice-clone-library-list" ? currentList : null;
      },
    },
    globalThis: {},
    refreshVoiceCloneLibrary: async () => {},
    renderAll: () => { renderAllCalls += 1; },
    showToast() {},
    window: {
      clearTimeout() {},
      setTimeout(callback) {
        pendingSearch = callback;
        return 1;
      },
    },
  });
  vm.runInContext(`
    function escapeHtml(value) { return String(value ?? ""); }
    function formatDate() { return "08/31 20:00"; }
  `, context);
  context.globalThis.SpeakerVoiceCloneLibrary = {
    getSourcePreview: () => null,
    isSelectableProfile: (item) => Boolean(item?.selectable),
    projectScopeLabel: () => "共享音色",
    resolveLibraryGroups: (groups) => groups,
    translateStatus: (status) => ({ ready: "可用", failed: "创建失败" }[status] || "状态未知"),
  };
  vm.runInContext(rendererSource, context);
  vm.runInContext(eventSource, context);

  context.scheduleVoiceCloneSearch("profile");
  await pendingSearch();

  assert.match(replacedHtml, /voice-identity-list/);
  assert.match(replacedHtml, /data-voice-identity="identity-1"/);
  assert.match(replacedHtml, /小撒/);
  assert.doesNotMatch(replacedHtml, /voice-library-card-meta/);
  assert.equal(replacement.scrollTop, 39);
  assert.equal(context.document.activeElement, searchInput);
  assert.equal(workspace.scrollTop, 17);
  assert.equal(renderAllCalls, 0);
});


function selectableProfile(id = "profile-1") {
  return {
    id,
    voice_name: "撒贝宁 新闻播报",
    provider: "qwen",
    target_model: "qwen3-tts-vc-2026-01-22",
    status: "ready",
    voice_id: `voice-${id}`,
    selectable: true,
    source_sample_ready: true,
    last_used_at: "2026-08-31T12:00:00Z",
  };
}


function identityGroup(id, displayName, aliases = [], profile = selectableProfile()) {
  return {
    group_key: id,
    identity_id: id,
    identity_display_name: displayName,
    identity_aliases: aliases,
    display_name: displayName,
    health_status: "ready",
    version_count: 2,
    recommended_profile: profile,
  };
}


async function loadLibrary() {
  const source = await readFile(
    new URL("../../src/frontend/js/voice-clone-library.js", import.meta.url),
    "utf8",
  );
  const context = vm.createContext({ globalThis: {} });
  vm.runInContext(source, context);
  return context.globalThis.SpeakerVoiceCloneLibrary;
}


async function renderVoiceCloneWorkbench({
  available,
  selectedProfile = null,
  groups = [],
  expandedGroupKey = "",
  profilesByGroup = {},
  sampleId = "",
  displayName = "",
  createDrawer = { open: false, step: "existing_profile" },
  addFlow = { open: false, stage: "pick" },
  studio = false,
  view = "generate",
  records = [],
  sharedPlayer = { url: "" },
  nextCursor = "",
  total = groups.length,
  singleUse = false,
}) {
  const source = await readFile(
    new URL("../../src/frontend/js/renderers.js", import.meta.url),
    "utf8",
  );
  const profiles = selectedProfile ? [selectedProfile] : [];
  const context = vm.createContext({
    ...retentionHelpers,
    state: {
      currentProjectId: "project-1",
      currentProjectName: "中国好团队 01",
      serviceStatus: {
        voiceClone: singleUse ? { available: true, discard_voice_after_download: true } : available
          ? { available: true, provider: "qwen", message: "服务可用" }
          : { available: false, provider: "qwen", message: "声音服务未连接" },
      },
      voiceClone: {
        provider: "qwen",
        targetModel: "qwen3-tts-vc-2026-01-22",
        profileId: selectedProfile?.id || "",
        profiles,
        sampleId,
        samples: [],
        view,
        records,
        profileSearch: "",
        sampleSearch: "",
        displayName,
        previewText: "你好",
        voiceInstruction: "",
        library: {
          groups,
          total,
          nextCursor,
          loaded: true,
          loading: false,
          availableGroupCount: 0,
          exceptionGroupCount: 0,
          reusableSampleCount: 0,
          creatingProfiles: [],
          profilesByGroup,
          expandedGroupKey,
        },
        samplePage: { loaded: false, items: [] },
        createDrawer,
        addFlow,
        sharedPlayer,
        sampleDraft: {},
      },
    },
    globalThis: {},
  });
  vm.runInContext(`
    function escapeHtml(value) { return String(value ?? ""); }
    function applyVoiceCloneProviderDefaults() {}
    function getVoiceCloneProviderOptions() { return [{ provider: "qwen", label: "Qwen3-TTS", target_model: "qwen3-tts-vc-2026-01-22", notes: [], sample_requirements: [] }]; }
    function getVoiceCloneProviderOption() { return getVoiceCloneProviderOptions()[0]; }
    function getVoiceCloneModelOptions() { return [{ id: "qwen:qwen3-tts-vc-2026-01-22", provider: "qwen", model: "qwen3-tts-vc-2026-01-22", label: "Qwen3-TTS", available: state.serviceStatus.voiceClone.available, capabilities: {instruction:false}, constraints: {text_max_characters:500}}, {id: "cosyvoice:cosyvoice-v3.5-plus", provider: "cosyvoice", model: "cosyvoice-v3.5-plus", label: "CosyVoice 3.5 Plus", available: state.serviceStatus.voiceClone.available, recommended: true, capabilities:{instruction:true},constraints:{text_max_characters:500,instruction_max_weight:57,instruction_han_weight:2}}]; }
    function getVoiceCloneModelOption(id) { return getVoiceCloneModelOptions().find(option => option.id === id); }
    function isVoiceCloneServiceAvailable() { return state.serviceStatus.voiceClone.available; }
    function getVoiceCloneInstructionWeight(value) { return Array.from(value || "").reduce((sum, c) => sum + (c.charCodeAt(0) > 255 ? 2 : 1), 0); }
    function isSelectableVoiceCloneProfile(profile) { return Boolean(profile?.selectable && !isVoiceAssetExpired(profile)); }
    function buildApiV2Url(path) { return path; }
    function buildProjectVoiceClonePath(_projectId, suffix) { return suffix; }
    function formatDate() { return "08/31 20:00"; }
    function formatVoiceCloneCost() { return ""; }
    function getVoiceCloneSampleDraftLabel() { return ""; }
  `, context);
  context.globalThis.SpeakerVoiceCloneLibrary = {
    getSourcePreview: () => null,
    isSelectableProfile: (profile) => Boolean(profile?.selectable && !retentionLibrary.isAssetExpired(profile)),
    projectScopeLabel: () => "当前项目",
    resolveLibraryGroups: (groups, fallbackProfiles) => (
      Array.isArray(groups) && groups.length
        ? groups
        : (fallbackProfiles || []).map((profile) => ({
            group_key: profile.id,
            display_name: profile.voice_name,
            health_status: profile.status || "ready",
            recommended_profile: profile,
            version_count: 1,
          }))
    ),
    translateStatus: (status) => ({ ready: "可用", failed: "创建失败" }[status] || "状态未知"),
    normalizeProfile: (profile) => profile,
  };
  vm.runInContext(source, context);
  return vm.runInContext(studio ? "renderVoiceCloneStudio()" : "renderVoiceCloneSingleTool()", context);
}


test("matched upload uses the server identity without offering a bypass", async () => {
  const profile = selectableProfile();
  const html = await renderVoiceCloneWorkbench({
    available: true,
    sampleId: "sample-2",
    groups: [identityGroup("identity-1", "撒贝宁", ["小撒"], profile)],
    addFlow: {
      open: true,
      stage: "matched",
      matchType: "speaker_embedding",
      matchedIdentity: { id: "identity-1", display_name: "撒贝宁" },
      sampleSlotsRemaining: 1,
      fileLabel: "host-clean.wav",
    },
    studio: true,
  });

  assert.match(html, /直接使用/);
  assert.match(html, /已补充为第 2 条样本/);
  assert.match(html, /id="voice-clone-create-button"/);
  assert.doesNotMatch(html, /data-voice-add-sample/);
  assert.doesNotMatch(html, /创建新身份/);
  assert.doesNotMatch(html, /跳过/);
});


test("matched sample stays previewable when the identity is absent from the usable library", async () => {
  const html = await renderVoiceCloneWorkbench({
    available: true,
    sampleId: "sample-2",
    groups: [],
    addFlow: {
      open: true,
      stage: "matched",
      matchType: "speaker_embedding",
      matchedIdentity: { id: "identity-1", display_name: "撒贝宁" },
      sampleSlotsRemaining: 1,
      existingSamples: [{
        id: "sample-2",
        display_name: "撒贝宁",
        original_filename: "host-clean.wav",
      }],
    },
    studio: true,
  });

  assert.match(html, /暂无可用版本/);
  assert.match(html, /可以使用已保存的参考音频继续创建/);
  assert.match(html, /data-voice-clone-sample-preview="sample-2"/);
  assert.match(html, /host-clean\.wav/);
  assert.doesNotMatch(html, /data-voice-group-profiles=/);
  assert.doesNotMatch(html, /data-voice-profile="[^"]+" data-close-voice-drawer="true"/);
  assert.match(html, /id="voice-clone-create-button"/);
});


test("exact audio reuses the existing sample without creating or attaching anything", async () => {
  const profile = selectableProfile();
  const html = await renderVoiceCloneWorkbench({
    available: true,
    groups: [identityGroup("identity-1", "撒贝宁", [], profile)],
    addFlow: {
      open: true,
      stage: "matched",
      matchType: "exact_audio",
      matchedIdentity: { id: "identity-1", display_name: "撒贝宁" },
      sampleSlotsRemaining: 2,
      existingSamples: [{ id: "sample-1", original_filename: "existing.wav" }],
    },
    studio: true,
  });

  assert.match(html, /完全相同，没有新增样本/);
  assert.match(html, /直接使用 撒贝宁 的推荐音色/);
  assert.match(html, /data-voice-clone-sample-preview="sample-1"/);
  assert.doesNotMatch(html, /data-voice-add-sample/);
  assert.match(html, /id="voice-clone-create-button"/);
  assert.doesNotMatch(html, /创建同身份音色版本/);
  assert.doesNotMatch(html, /再次上传/);
});


test("pick, checking, new identity, and error stages expose only their valid actions", async () => {
  const pickHtml = await renderVoiceCloneWorkbench({
    available: true,
    displayName: "主持人",
    addFlow: { open: true, stage: "pick" },
    studio: true,
  });
  const checkingHtml = await renderVoiceCloneWorkbench({
    available: true,
    addFlow: { open: true, stage: "checking", fileLabel: "host.wav" },
    studio: true,
  });
  const newIdentityHtml = await renderVoiceCloneWorkbench({
    available: true,
    displayName: "主持人",
    sampleId: "sample-1",
    addFlow: { open: true, stage: "new_identity", fileLabel: "host.wav" },
    studio: true,
  });
  const errorHtml = await renderVoiceCloneWorkbench({
    available: true,
    displayName: "主持人",
    addFlow: { open: true, stage: "error", fileLabel: "host.wav", error: "声纹服务暂时不可用" },
    studio: true,
  });

  assert.match(pickHtml, /系统会自动检查音频并查找已有声音/);
  assert.match(pickHtml, /创建声音/);
  assert.doesNotMatch(pickHtml, /voice-clone-provider-select/);
  assert.match(checkingHtml, /正在检查参考音频/);
  assert.doesNotMatch(checkingHtml, /voice-clone-upload-sample-form/);
  assert.match(newIdentityHtml, /参考音频已保存/);
  assert.match(newIdentityHtml, /voice-clone-model-select/);
  assert.match(newIdentityHtml, /id="voice-clone-create-button"/);
  assert.doesNotMatch(newIdentityHtml, /voice-clone-upload-sample-form/);
  assert.match(errorHtml, /声纹服务暂时不可用/);
  assert.match(errorHtml, /host\.wav/);
  assert.match(errorHtml, /主持人/);
  assert.match(errorHtml, /重试创建/);
});


test("full identity shows existing samples and blocks another upload", async () => {
  const html = await renderVoiceCloneWorkbench({
    available: true,
    addFlow: {
      open: true,
      stage: "full",
      sampleSlotsRemaining: 0,
      existingSamples: [
        { id: "sample-1", display_name: "主持人", original_filename: "one.wav" },
        { id: "sample-2", display_name: "主持人", original_filename: "two.wav" },
        { id: "sample-3", display_name: "主持人", original_filename: "three.wav" },
      ],
    },
    studio: true,
  });

  assert.match(html, /已保留 3 条样本/);
  assert.match(html, /one\.wav/);
  assert.match(html, /three\.wav/);
  assert.match(html, /data-voice-clone-sample-preview="sample-1"/);
  assert.doesNotMatch(html, /data-voice-add-sample/);
  assert.match(html, /id="voice-clone-create-button"/);
  assert.doesNotMatch(html, /上传并创建音色/);
});


test("the third embedding sample is already saved when the identity becomes full", async () => {
  const html = await renderVoiceCloneWorkbench({
    available: true,
    sampleId: "sample-3",
    addFlow: {
      open: true,
      stage: "full",
      matchType: "speaker_embedding",
      matchedIdentity: { id: "identity-1", display_name: "主持人" },
      sampleSlotsRemaining: 0,
      existingSamples: [{
        id: "sample-3",
        display_name: "主持人",
        original_filename: "third-clean.wav",
      }],
    },
    studio: true,
  });

  assert.match(html, /已补充为第 3 条样本/);
  assert.match(html, /data-voice-clone-sample-preview="sample-3"/);
  assert.match(html, /third-clean\.wav/);
  assert.match(html, /可以使用已保存的参考音频继续创建/);
  assert.match(html, /id="voice-clone-create-button"/);
  assert.doesNotMatch(html, /本次录音未保存/);
});


test("sample limit errors preserve the server sample summary", async () => {
  const source = await readFile(
    new URL("../../src/frontend/js/api.js", import.meta.url),
    "utf8",
  );
  const detail = {
    message: "声音身份最多保留 3 条声音复刻样本，请直接复用已有样本",
    identity_id: "identity-1",
    existing_samples: [{ id: "sample-1", original_filename: "one.wav" }],
  };
  const context = vm.createContext({
    ...retentionHelpers,
    state: { apiBase: "/api/v1" },
    FormData,
    Headers,
    fetch: async () => ({
      ok: false,
      status: 409,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ detail }),
    }),
    normalizeError: (error) => error,
    window: { clearTimeout, setTimeout },
  });
  vm.runInContext(source, context);

  await assert.rejects(
    context.requestUrl("/api/v2/projects/project-1/voice-cloning/samples"),
    (error) => {
      assert.equal(error.status, 409);
      assert.equal(error.detail.identity_id, "identity-1");
      assert.equal(error.detail.existing_samples[0].original_filename, "one.wav");
      return true;
    },
  );
});


test("structured request errors never render as undefined", async () => {
  const source = await readFile(
    new URL("../../src/frontend/js/api.js", import.meta.url),
    "utf8",
  );
  const context = vm.createContext({
    ...retentionHelpers,
    state: { apiBase: "/api/v1" },
    FormData,
    Headers,
    fetch: async () => ({
      ok: false,
      status: 422,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ detail: [{ msg: "搜索参数无效" }] }),
    }),
    normalizeError: (error) => error,
    window: { clearTimeout, setTimeout },
  });
  vm.runInContext(source, context);

  await assert.rejects(
    context.requestUrl("/api/v2/projects/project-1/voice-cloning/library?q=xiaosa"),
    /搜索参数无效/,
  );
});


test("upload response drives the matched stage without client bypass fields", async () => {
  const [librarySource, apiSource] = await Promise.all([
    readFile(new URL("../../src/frontend/js/voice-clone-library.js", import.meta.url), "utf8"),
    readFile(new URL("../../src/frontend/js/api.js", import.meta.url), "utf8"),
  ]);
  const state = {
    apiBase: "/api/v1",
    currentProjectId: "project-1",
    voiceClone: {
      displayName: "提交显示名",
      sampleId: "",
      samples: [],
      sampleDraft: { projectId: "project-1" },
      addFlow: { open: true, stage: "checking", fileLabel: "second.wav" },
    },
  };
  const context = vm.createContext({
    ...retentionHelpers,
    state,
    Blob,
    FormData,
    globalThis: {},
  });
  vm.runInContext(librarySource, context);
  vm.runInContext(apiSource, context);
  context.uploadResponse = {
    id: "sample-2",
    display_name: "服务端名称",
    original_filename: "second-clean.wav",
    duration_ms: 11800,
    clone_readiness_status: "ready",
    identity_id: "identity-1",
    identity_display_name: "撒贝宁",
    identity_aliases: ["小撒"],
    match_type: "speaker_embedding",
    match_score: 0.91,
    sample_slots_remaining: 1,
  };
  vm.runInContext(`
    requestV2 = async (_path, options) => {
      uploadFormFields = Array.from(options.body.keys());
      return uploadResponse;
    };
    refreshVoiceCloneData = async () => true;
  `, context);

  await context.uploadVoiceCloneSampleFile("提交显示名", new Blob(["audio"]));

  assert.equal(state.voiceClone.addFlow.stage, "matched");
  assert.equal(state.voiceClone.addFlow.matchedIdentity.id, "identity-1");
  assert.equal(state.voiceClone.addFlow.existingSamples[0].id, "sample-2");
  assert.equal(state.voiceClone.addFlow.existingSamples[0].display_name, "服务端名称");
  assert.equal(state.voiceClone.addFlow.existingSamples[0].original_filename, "second-clean.wav");
  assert.equal(state.voiceClone.addFlow.existingSamples[0].duration_ms, 11800);
  assert.deepEqual(Array.from(context.uploadFormFields), ["display_name", "file"]);
});


test("submitting add voice enters checking and runs the combined creation flow", async () => {
  const source = await readFile(
    new URL("../../src/frontend/js/events.js", import.meta.url),
    "utf8",
  );
  const file = { name: "host-clean.wav" };
  const state = {
    serviceStatus: { voiceClone: { available: true } },
    voiceClone: {
      displayName: "主持人",
      addFlow: { open: true, stage: "pick" },
      sampleDraft: { file, fileName: file.name },
    },
  };
  let stageAtUpload = "";
  let createCalls = 0;
  const button = { disabled: false, textContent: "检查并添加声音" };
  const form = {
    matches: (selector) => selector === "#voice-clone-upload-sample-form",
    querySelector(selector) {
      if (selector === "#voice-clone-upload-sample-button") return button;
      if (selector === "#voice-clone-sample-input") return { files: [] };
      return null;
    },
  };
  const context = vm.createContext({
    ...retentionHelpers,
    state,
    isVoiceCloneServiceAvailable: () => state.serviceStatus.voiceClone.available === true,
    document: {
      querySelector(selector) {
        return selector === "#voice-clone-display-name-input"
          ? { value: "主持人" }
          : null;
      },
    },
    renderAll() {},
    showToast() {},
    withUiTask: async (_button, _label, task) => task(),
    uploadVoiceCloneSampleAndCreateProfile: async () => {
      createCalls += 1;
      stageAtUpload = state.voiceClone.addFlow.stage;
      state.voiceClone.addFlow.stage = "new_identity";
      state.voiceClone.sampleId = "sample-1";
      return { id: "sample-1", match_type: "new_identity" };
    },
    URL: { revokeObjectURL() {} },
    window: { clearTimeout, setTimeout, requestAnimationFrame: (callback) => callback() },
  });
  vm.runInContext(source, context);

  await context.handleDocumentSubmit({ target: form, preventDefault() {} });

  assert.equal(stageAtUpload, "checking");
  assert.equal(state.voiceClone.addFlow.stage, "new_identity");
  assert.equal(state.voiceClone.addFlow.fileLabel, "host-clean.wav");
  assert.equal(createCalls, 1);
});


test("a closed add-flow session cannot overwrite a reopened draft", async () => {
  const [apiSource, eventSource] = await Promise.all([
    readFile(new URL("../../src/frontend/js/api.js", import.meta.url), "utf8"),
    readFile(new URL("../../src/frontend/js/events.js", import.meta.url), "utf8"),
  ]);
  let resolveUpload;
  const uploadResponse = new Promise((resolve) => { resolveUpload = resolve; });
  const oldFile = new Blob(["old-audio"]);
  Object.defineProperty(oldFile, "name", { value: "old.wav" });
  const newFile = new Blob(["new-audio"]);
  Object.defineProperty(newFile, "name", { value: "new.wav" });
  const state = {
    apiBase: "/api/v1",
    currentProjectId: "project-1",
    pendingDangerKey: "",
    serviceStatus: { voiceClone: { available: true } },
    voiceClone: {
      requestGeneration: 1,
      displayName: "旧显示名",
      sampleId: "",
      samples: [],
      profiles: [],
      records: [],
      createDrawer: { open: false },
      addFlow: { open: false, stage: "pick" },
      sampleDraft: { projectId: "project-1", file: oldFile, fileName: oldFile.name },
      library: { expandedGroupKey: "", profilesByGroup: {} },
    },
  };
  let displayInputValue = "旧显示名";
  let renderAllCalls = 0;
  let drawerFocusCalls = 0;
  let openerFocusCalls = 0;
  const opener = {
    closest(selector) { return selector === "#voice-clone-new-button" ? this : null; },
    matches() { return false; },
    focus() { openerFocusCalls += 1; },
  };
  const closeButton = { focus() { drawerFocusCalls += 1; } };
  const fileInput = { files: [], value: "" };
  const submitButton = { disabled: false, textContent: "检查并添加声音" };
  const form = {
    matches: (selector) => selector === "#voice-clone-upload-sample-form",
    querySelector(selector) {
      if (selector === "#voice-clone-upload-sample-button") return submitButton;
      if (selector === "#voice-clone-sample-input") return fileInput;
      return null;
    },
  };
  const context = vm.createContext({
    ...retentionHelpers,
    state,
    isVoiceCloneServiceAvailable: () => state.serviceStatus.voiceClone.available === true,
    Blob,
    FormData,
    globalThis: { SpeakerVoiceCloneLibrary: { normalizeProfile: (sample) => sample } },
    document: {
      body: { dataset: { railOpen: "false" } },
      querySelector(selector) {
        if (selector === "#voice-clone-display-name-input") return { value: displayInputValue };
        if (selector === "#voice-clone-drawer-close-button" && state.voiceClone.addFlow.open) return closeButton;
        if (selector.includes("#voice-clone-new-button") && !state.voiceClone.addFlow.open) return opener;
        return null;
      },
      querySelectorAll() { return []; },
    },
    normalizeError: (error) => error,
    recordClientLog: async () => {},
    renderAll() { renderAllCalls += 1; },
    setProjectRailOpen() {},
    showToast() {},
    URL: { revokeObjectURL() {} },
    window: { clearTimeout, setTimeout, requestAnimationFrame: (callback) => callback() },
  });
  vm.runInContext(apiSource, context);
  context.uploadResponse = uploadResponse;
  vm.runInContext(`
    requestV2 = async () => uploadResponse;
    refreshVoiceCloneData = async () => true;
  `, context);
  vm.runInContext(eventSource, context);

  await context.handleDocumentClick({ target: opener });
  const pendingSubmit = context.handleDocumentSubmit({ target: form, preventDefault() {} });
  await Promise.resolve();
  context.closeVoiceCloneCreationDrawer();
  await context.handleDocumentClick({ target: opener });
  displayInputValue = "新显示名";
  state.voiceClone.displayName = displayInputValue;
  state.voiceClone.sampleDraft = {
    projectId: "project-1",
    file: newFile,
    fileName: newFile.name,
  };
  const rendersBeforeOldResponse = renderAllCalls;

  resolveUpload({
    id: "old-sample",
    display_name: "旧显示名",
    identity_id: "identity-old",
    identity_display_name: "旧身份",
    match_type: "speaker_embedding",
    sample_slots_remaining: 1,
  });
  await pendingSubmit;

  assert.equal(state.voiceClone.addFlow.open, true);
  assert.equal(state.voiceClone.addFlow.stage, "pick");
  assert.equal(state.voiceClone.displayName, "新显示名");
  assert.equal(state.voiceClone.sampleId, "");
  assert.equal(state.voiceClone.sampleDraft.file, newFile);
  assert.equal(renderAllCalls, rendersBeforeOldResponse);
  assert.equal(drawerFocusCalls, 2);
  assert.equal(openerFocusCalls, 1);
});


test("a fourth sample becomes full while ordinary failures remain retryable", async () => {
  const [librarySource, eventSource] = await Promise.all([
    readFile(new URL("../../src/frontend/js/voice-clone-library.js", import.meta.url), "utf8"),
    readFile(new URL("../../src/frontend/js/events.js", import.meta.url), "utf8"),
  ]);
  const file = { name: "fourth.wav" };
  const state = {
    serviceStatus: { voiceClone: { available: true } },
    voiceClone: {
      displayName: "主持人",
      addFlow: { open: true, stage: "pick" },
      sampleDraft: { file, fileName: file.name },
    },
  };
  const form = {
    matches: (selector) => selector === "#voice-clone-upload-sample-form",
    querySelector(selector) {
      if (selector === "#voice-clone-upload-sample-button") return { disabled: false, textContent: "提交" };
      if (selector === "#voice-clone-sample-input") return { files: [] };
      return null;
    },
  };
  const limitError = new Error("声音身份最多保留 3 条声音复刻样本，请直接复用已有样本");
  limitError.status = 409;
  limitError.detail = {
    identity_id: "identity-1",
    existing_samples: [
      { id: 1, display_name: " 主持人 ", original_filename: "one.wav" },
      { id: "sample-2", original_filename: "two.wav" },
      { id: "sample-3", original_filename: "three.wav" },
    ],
  };
  let nextError = limitError;
  const context = vm.createContext({
    ...retentionHelpers,
    state,
    isVoiceCloneServiceAvailable: () => state.serviceStatus.voiceClone.available === true,
    globalThis: {},
    document: { querySelector: () => ({ value: "主持人" }) },
    normalizeError: (error) => error,
    renderAll() {},
    showToast() {},
    withUiTask: async (_button, _label, task) => { try { await task(); } catch (_error) {} },
    uploadVoiceCloneSampleAndCreateProfile: async () => { throw nextError; },
    URL: { revokeObjectURL() {} },
    window: { clearTimeout, setTimeout, requestAnimationFrame: (callback) => callback() },
  });
  vm.runInContext(librarySource, context);
  vm.runInContext(eventSource, context);

  await context.handleDocumentSubmit({ target: form, preventDefault() {} });
  assert.equal(state.voiceClone.addFlow.stage, "full");
  assert.equal(state.voiceClone.addFlow.existingSamples.length, 3);
  assert.equal(state.voiceClone.addFlow.existingSamples[0].id, "1");
  assert.equal(state.voiceClone.addFlow.existingSamples[0].display_name, "主持人");
  assert.equal(state.voiceClone.addFlow.existingSamples[0].original_filename, "one.wav");
  assert.equal(state.voiceClone.sampleDraft.file, file);

  nextError = new Error("声纹服务暂时不可用");
  await context.handleDocumentSubmit({ target: form, preventDefault() {} });
  assert.equal(state.voiceClone.addFlow.stage, "error");
  assert.equal(state.voiceClone.addFlow.fileLabel, "fourth.wav");
  assert.equal(state.voiceClone.displayName, "主持人");
  assert.equal(state.voiceClone.sampleDraft.file, file);
});


test("matched and new identities can explicitly create while preview and reuse never create", async () => {
  const source = await readFile(
    new URL("../../src/frontend/js/events.js", import.meta.url),
    "utf8",
  );
  const state = {
    pendingDangerKey: "",
    serviceStatus: { voiceClone: { available: true } },
    voiceClone: {
      displayName: "主持人",
      sampleId: "sample-1",
      samples: [{ id: "sample-1", display_name: "主持人" }],
      profiles: [{ ...selectableProfile(), id: "profile-1" }],
      records: [],
      createDrawer: { open: true },
      addFlow: {
        open: true,
        stage: "full",
        existingSamples: [{ id: "sample-1", display_name: "主持人" }],
      },
      library: { expandedGroupKey: "", profilesByGroup: {} },
    },
  };
  let createCalls = 0;
  const targetFor = (selector, dataset = {}) => ({
    dataset,
    closest(candidate) { return candidate === selector ? this : null; },
    matches() { return false; },
  });
  const context = vm.createContext({
    ...retentionHelpers,
    state,
    globalThis: {
      SpeakerVoiceCloneLibrary: { isSelectableProfile: (profile) => Boolean(profile?.selectable) },
    },
    document: {
      body: { dataset: { railOpen: "false" } },
      querySelector() { return null; },
      querySelectorAll() { return []; },
    },
    createVoiceCloneProfile: async () => { createCalls += 1; return { status: "ready" }; },
    refreshVoiceCloneRecords: async () => {},
    refreshVoiceCloneData: async () => true,
    URL: { revokeObjectURL() {} },
    loadVoiceCloneGroupProfiles: async () => [],
    loadVoiceCloneGroupSamples: async () => [],
    buildApiV2Url: (path) => path,
    buildProjectVoiceClonePath: (_projectId, suffix) => suffix,
    withUiTask: async (_button, _label, task) => task(),
    renderAll() {},
    setProjectRailOpen() {},
    showToast() {},
    window: { clearTimeout, setTimeout, requestAnimationFrame: (callback) => callback() },
  });
  vm.runInContext(source, context);

  await context.handleDocumentClick({
    target: targetFor("[data-voice-profile]", {
      voiceProfile: "profile-1",
      closeVoiceDrawer: "true",
    }),
  });
  assert.equal(createCalls, 0);

  state.voiceClone.addFlow = { ...state.voiceClone.addFlow, open: true, stage: "matched", matchType: "exact_audio" };
  await context.handleDocumentClick({
    target: targetFor("[data-voice-clone-sample-preview]", { voiceCloneSamplePreview: "sample-1" }),
  });
  assert.equal(createCalls, 0);

  state.voiceClone.addFlow = { ...state.voiceClone.addFlow, open: true, stage: "full" };
  await context.handleDocumentClick({
    target: targetFor("[data-voice-clone-sample-preview]", { voiceCloneSamplePreview: "sample-1" }),
  });
  assert.equal(createCalls, 0);

  state.voiceClone.addFlow = { ...state.voiceClone.addFlow, open: true, stage: "matched" };
  await context.handleDocumentClick({ target: targetFor("#voice-clone-create-button") });
  assert.equal(createCalls, 1);

  state.voiceClone.addFlow = { ...state.voiceClone.addFlow, open: true, stage: "matched" };
  await context.handleDocumentClick({
    target: targetFor("[data-voice-group-profiles]", {
      voiceGroupProfiles: "identity-1",
      voiceReturnIdentity: "identity-1",
      closeVoiceDrawer: "true",
    }),
  });
  assert.equal(createCalls, 1);
  assert.equal(state.voiceClone.addFlow.open, false);
  assert.equal(state.voiceClone.library.expandedGroupKey, "identity-1");

  state.voiceClone.addFlow = { ...state.voiceClone.addFlow, open: true, stage: "new_identity" };
  await context.handleDocumentClick({ target: targetFor("#voice-clone-create-button") });
  assert.equal(createCalls, 2);
  assert.equal(state.voiceClone.addFlow.open, false);
});


test("voice studio keeps the primary desk apart from the searchable library", async () => {
  const profile = selectableProfile();
  const options={available:true, selectedProfile:profile, groups:[identityGroup("identity-1","撒贝宁",["小撒"],profile)], studio:true};
  const html=await renderVoiceCloneWorkbench(options);
  assert.match(html, /voice-generation-desk/);
  assert.match(html, /id="voice-clone-preview-text"/);
  assert.match(html, /id="voice-clone-preview-button"/);
  assert.match(html, /生成记录/);
  assert.doesNotMatch(html, /voice-identity-rail/);
  const library=await renderVoiceCloneWorkbench({...options,view:"library"});
  assert.match(library, /data-voice-identity="identity-1"/);
  assert.match(library, /小撒/);
  assert.match(library, /id="voice-clone-profile-search-input"/);
  assert.doesNotMatch(library, /voice-generation-desk/);
});

test("voice studio marks the selected identity across historical versions", async () => {
  const recommended = { ...selectableProfile("profile-current"), identity_id: "identity-1" };
  const historical = { ...selectableProfile("profile-history"), identity_id: "identity-1" };
  const html = await renderVoiceCloneWorkbench({available:true, selectedProfile:historical, groups:[identityGroup("identity-1","撒贝宁",["小撒"],recommended)], view:"library"});
  assert.match(html, /voice-identity-row is-active[^>]*aria-current="true"/);
  assert.match(html, /data-voice-profile="profile-current"[^>]*aria-pressed="true"/);
});

test("small unfiltered refresh keeps the selected historical version active", async () => {
  const library = await loadLibrary();
  const recommended = { ...selectableProfile("profile-current"), identity_id: "identity-1" };
  const historical = { ...selectableProfile("profile-history"), identity_id: "identity-1" };
  const groups = [identityGroup("identity-1", "撒贝宁", ["小撒"], recommended)];

  const refreshed = library.reconcileLibrarySelection(groups, {
    selectedProfileId: historical.id,
    selectedProfile: historical,
  });
  const selectedProfile = refreshed.profiles.find((profile) => profile.id === refreshed.profileId);
  const html = await renderVoiceCloneWorkbench({
    available: true,
    selectedProfile,
    groups,
    studio: true,
    view: "library",
  });

  assert.equal(refreshed.profileId, "profile-history");
  assert.match(html, /voice-identity-row is-active[^>]*aria-current="true"/);
  assert.match(html, /data-voice-profile="profile-current"[^>]*aria-pressed="true"/);

});


test("small unfiltered refresh keeps a nullable-identity legacy historical version active", async () => {
  const library = await loadLibrary();
  const groupKey = "legacy:旧版主持";
  const recommended = { ...selectableProfile("profile-current"), identity_id: null };
  const historical = {
    ...selectableProfile("profile-history"),
    identity_id: null,
    group_key: groupKey,
    voice_name: "旧版主持 历史版本",
  };
  const groups = [{
    group_key: groupKey,
    identity_id: null,
    identity_display_name: null,
    identity_aliases: [],
    display_name: "旧版主持",
    health_status: "ready",
    version_count: 2,
    recommended_profile: recommended,
  }];

  const refreshed = library.reconcileLibrarySelection(groups, {
    selectedProfileId: historical.id,
    selectedProfile: historical,
  });
  const selectedProfile = refreshed.profiles.find((profile) => profile.id === refreshed.profileId);
  const html = await renderVoiceCloneWorkbench({
    available: true,
    selectedProfile,
    groups,
    studio: true,
    view: "library",
  });

  assert.equal(refreshed.profileId, "profile-history");
  assert.match(html, /data-voice-identity="legacy:旧版主持"[^>]*aria-current="true"/);
  assert.match(html, /data-voice-profile="profile-current"[^>]*aria-pressed="true"/);

});


test("identity and service status dots use semantic state colors", async () => {
  const ready = identityGroup("identity-1", "可用声音");
  const failed = { ...identityGroup("identity-2", "失败声音", [], selectableProfile("profile-2")), health_status: "failed" };
  const html = await renderVoiceCloneWorkbench({ available: true, groups: [ready, failed], studio: true, view: "library" });
  const css = await readFile(
    new URL("../../src/frontend/styles/v2-direction.css", import.meta.url),
    "utf8",
  );

  assert.match(html, /voice-identity-status is-ready[^>]*>可用</);
  assert.match(html, /voice-identity-status is-danger[^>]*>创建失败</);
  assert.match(css, /\.voice-identity-status\.is-ready::before\s*\{[^}]*background:\s*var\(--success\)/s);
  assert.match(css, /\.voice-identity-status\.is-creating::before\s*\{[^}]*background:\s*var\(--warning\)/s);
  assert.match(css, /\.voice-identity-status\.is-danger::before\s*\{[^}]*background:\s*var\(--danger\)/s);
  assert.match(css, /\.voice-service-status\.is-online \.voice-service-dot\s*\{[^}]*background:\s*var\(--success\)/s);
});


test("healthy service uses a compact status dot instead of a banner", async () => {
  const html = await renderVoiceCloneWorkbench({ available: true, studio: true });

  assert.match(html, /voice-service-dot/);
  assert.match(html, /aria-label="声音服务可用"/);
  assert.doesNotMatch(html, /声音服务运行正常<\/strong>/);
  assert.doesNotMatch(html, /voice-service-banner is-online/);
});


test("voice studio keeps generation actions outside the scrolling body at narrow widths", async () => {
  const css=await readFile(new URL("../../src/frontend/styles/v2-direction.css", import.meta.url),"utf8");
  assert.match(css, /\.voice-generation-desk\s*\{[^}]*grid-template-rows:\s*minmax\(0, 1fr\) auto/);
  assert.match(css, /\.voice-generation-body\s*\{[^}]*overflow:\s*auto/);
  assert.match(css, /@media \(max-width: 720px\)[\s\S]*?\.voice-compose-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/);
  assert.match(css, /\.voice-script-footnote > span:last-child\s*\{[^}]*flex-shrink:\s*0/);
});

test("identity source, model, versions, and deletion stay in the details drawer", async () => {
  const profile = {
    ...selectableProfile(),
    source_project_name: "新闻项目",
    source_original_filename: "host-clean.wav",
  };
  const group = identityGroup("identity-1", "撒贝宁", ["小撒"], profile);
  const html = await renderVoiceCloneWorkbench({
    available: true,
    selectedProfile: profile,
    groups: [group],
    expandedGroupKey: group.group_key,
    profilesByGroup: { [group.group_key]: [profile] },
    studio: true,
  });

  assert.match(html, /voice-identity-detail-drawer/);
  assert.match(html, /role="dialog"/);
  assert.match(html, /aria-modal="true"/);
  assert.match(html, /aria-labelledby="voice-identity-details-title"/);
  assert.match(html, /data-voice-identity-dialog="identity-1"/);
  assert.match(html, /qwen3-tts-vc-2026-01-22/);
  assert.match(html, /host-clean\.wav/);
  assert.match(html, /data-voice-profile-delete="profile-1"/);
});


test("empty voice studio keeps the desk visible and blocks generation", async () => {
  const html = await renderVoiceCloneWorkbench({ available: true });

  assert.match(html, /先选择一个声音/);
  assert.match(html, /voice-generation-desk/);
  assert.match(html, /id="voice-clone-preview-button"[^>]*disabled/);
});


test("offline voice service blocks creation and generation controls", async () => {
  const profile = {
    id: "profile-1",
    voice_name: "欧阳夏丹 · 新闻播报",
    provider: "qwen",
    target_model: "qwen3-tts-vc-2026-01-22",
    selectable: true,
  };
  const html = await renderVoiceCloneWorkbench({ available: false, selectedProfile: profile });

  assert.match(html, /声音服务未连接/);
  assert.match(html, /id="voice-clone-new-button"[^>]*disabled/);
  assert.match(html, /id="voice-clone-preview-button"[^>]*disabled/);
});


test("an open add flow disables provider-backed actions when service goes offline", async () => {
  const pickHtml = await renderVoiceCloneWorkbench({
    available: false,
    addFlow: { open: true, stage: "pick" },
    studio: true,
  });
  const newIdentityHtml = await renderVoiceCloneWorkbench({
    available: false,
    sampleId: "sample-1",
    addFlow: { open: true, stage: "new_identity" },
    studio: true,
  });

  assert.match(pickHtml, /声音服务未连接，暂时不能创建或上传声音/);
  assert.match(pickHtml, /id="voice-clone-create-drawer"[^>]*role="dialog"[^>]*aria-modal="true"[^>]*aria-labelledby="voice-clone-create-drawer-title"/);
  assert.match(pickHtml, /id="voice-clone-sample-input"[^>]*disabled/);
  assert.match(pickHtml, /id="voice-clone-upload-sample-button"[^>]*disabled/);
  assert.match(newIdentityHtml, /id="voice-clone-model-select"[^>]*disabled/);
  assert.match(newIdentityHtml, /id="voice-clone-create-button"[^>]*disabled/);
});


test("offline clone sample dropzone rejects click, change, browser drag-drop, and desktop drop before materializing", async () => {
  const source = await readFile(
    new URL("../../src/frontend/js/events.js", import.meta.url),
    "utf8",
  );
  let materializeCalls = 0;
  let preventedEvents = 0;
  let hoverAdds = 0;
  const toasts = [];
  const dropzone = {
    dataset: { fileDropTarget: "voice-clone-sample-input" },
    classList: {
      add() { hoverAdds += 1; },
      remove() {},
    },
    contains() { return false; },
    getAttribute(name) { return name === "aria-disabled" ? "true" : null; },
    closest(selector) { return selector === "[data-file-drop-target]" ? this : null; },
  };
  const input = {
    id: "voice-clone-sample-input",
    files: [{ name: "offline.wav" }],
    value: "offline.wav",
    closest(selector) { return selector === "[data-file-drop-target]" ? dropzone : null; },
    matches(selector) { return selector.split(",").map((item) => item.trim()).includes(`#${this.id}`); },
  };
  const clickTarget = {
    closest(selector) { return selector === "[data-file-drop-target]" ? dropzone : null; },
    matches() { return false; },
  };
  const state = {
    currentProjectId: "project-1",
    pendingDangerKey: "",
    serviceStatus: { voiceClone: { available: false } },
    voiceClone: {
      createDrawer: { open: true, step: "upload" },
      library: { expandedGroupKey: "", profilesByGroup: {} },
      profiles: [],
      records: [],
      samples: [],
      sampleDraft: {},
    },
  };
  const context = vm.createContext({
    ...retentionHelpers,
    state,
    materializeCalls,
    elements: { simpleWorkspace: { scrollTop: 0 } },
    globalThis: {},
    document: {
      body: { dataset: { railOpen: "false" } },
      elementFromPoint() { return input; },
      querySelector(selector) {
        if (selector === "[data-file-drop-target].is-dragover") return null;
        if (selector === "#current-project-select" || selector === "#project-search-results") return null;
        return null;
      },
      querySelectorAll() { return []; },
    },
    renderAll() {},
    setProjectRailOpen() {},
    showToast(message) { toasts.push(message); },
    window: {
      clearTimeout() {},
      setTimeout,
      requestAnimationFrame(callback) { callback(); },
    },
  });
  vm.runInContext(source, context);
  vm.runInContext(`materializeDropzoneFile = async () => {
    materializeCalls += 1;
    return null;
  };`, context);
  const preventDefault = () => { preventedEvents += 1; };

  await context.handleDocumentClick({ target: clickTarget, preventDefault });
  await context.handleDocumentChange({ target: input, preventDefault });
  context.handleDocumentDragOver({ target: input, preventDefault });
  context.handleDocumentDrop({
    target: input,
    preventDefault,
    dataTransfer: { files: [{ name: "browser.wav" }] },
  });
  await Promise.resolve();
  await context.handleNativeFileDrop({
    phase: "drop",
    position: { x: 10, y: 10 },
    files: [{ name: "desktop.wav", path: "/tmp/desktop.wav" }],
  });

  assert.equal(context.materializeCalls, 0);
  assert.equal(hoverAdds, 0);
  assert.ok(preventedEvents >= 3);
  assert.ok(toasts.some((message) => message.includes("声音服务未连接")));
});


test("creation drawer focuses on open and Escape restores the add voice trigger", async () => {
  const source = await readFile(
    new URL("../../src/frontend/js/events.js", import.meta.url),
    "utf8",
  );
  let closeFocusCalls = 0;
  let openerFocusCalls = 0;
  let escapePrevented = false;
  const closeButton = { focus() { closeFocusCalls += 1; } };
  const opener = {
    closest(selector) { return selector === "#voice-clone-new-button" ? this : null; },
    matches() { return false; },
    focus() { openerFocusCalls += 1; },
  };
  const state = {
    pendingDangerKey: "",
    serviceStatus: { voiceClone: { available: true } },
    voiceClone: {
      createDrawer: { open: false, step: "existing_profile" },
      library: { expandedGroupKey: "", profilesByGroup: {} },
      profiles: [],
      records: [],
    },
  };
  const context = vm.createContext({
    ...retentionHelpers,
    state,
    isVoiceCloneServiceAvailable: () => state.serviceStatus.voiceClone.available === true,
    globalThis: {
      SpeakerVoiceCloneLibrary: {
        updateDrawerState: (drawer, patch) => ({ ...drawer, ...patch }),
      },
    },
    document: {
      body: { dataset: { railOpen: "false" } },
      querySelector(selector) {
        if (selector === "#voice-clone-create-drawer" && state.voiceClone.createDrawer.open) return {};
        if (selector === "#voice-clone-drawer-close-button" && state.voiceClone.createDrawer.open) return closeButton;
        if (selector.includes("#voice-clone-new-button") && !state.voiceClone.createDrawer.open) return opener;
        return null;
      },
      querySelectorAll() { return []; },
    },
    renderAll() {},
    setProjectRailOpen() {},
    showToast() {},
    window: {
      clearTimeout() {},
      setTimeout,
      requestAnimationFrame(callback) { callback(); },
    },
  });
  vm.runInContext(source, context);

  await context.handleDocumentClick({ target: opener });
  assert.equal(closeFocusCalls, 1);
  context.handleDocumentKeyDown({
    key: "Escape",
    target: { closest: () => null, matches: () => false },
    preventDefault() { escapePrevented = true; },
  });

  assert.equal(escapePrevented, true);
  assert.equal(state.voiceClone.createDrawer.open, false);
  assert.equal(openerFocusCalls, 1);
});


test("identity details Escape cancels a stale versions load and restores its stable trigger", async () => {
  const [apiSource, eventSource] = await Promise.all([
    readFile(new URL("../../src/frontend/js/api.js", import.meta.url), "utf8"),
    readFile(new URL("../../src/frontend/js/events.js", import.meta.url), "utf8"),
  ]);
  let closeFocusCalls = 0;
  let triggerFocusCalls = 0;
  let escapePrevented = false;
  let renderAllCalls = 0;
  let resolveVersions;
  const versionsResponse = new Promise((resolve) => { resolveVersions = resolve; });
  const closeButton = { focus() { closeFocusCalls += 1; } };
  const trigger = {
    dataset: {
      voiceGroupProfiles: "identity-1",
      voiceIdentityDetailsTrigger: "identity-1",
    },
    closest(selector) {
      return selector === "[data-voice-group-profiles]" ? this : null;
    },
    matches() { return false; },
    focus() { triggerFocusCalls += 1; },
  };
  const keyTarget = { closest: () => null, matches: () => false };
  const state = {
    apiBase: "/api/v1",
    currentProjectId: "project-1",
    pendingDangerKey: "",
    serviceStatus: { voiceClone: { available: true } },
    voiceClone: {
      profileId: "",
      profiles: [],
      records: [],
      requestGeneration: 1,
      library: { expandedGroupKey: "", profilesByGroup: {} },
    },
  };
  const context = vm.createContext({
    ...retentionHelpers,
    state,
    globalThis: {
      SpeakerVoiceCloneLibrary: { normalizeProfile: (profile) => profile },
    },
    document: {
      body: { dataset: { railOpen: "false" } },
      querySelector(selector) {
        if (selector === "#voice-identity-details-close-button" && state.voiceClone.library.expandedGroupKey) return closeButton;
        if (selector === "#voice-identity-detail-drawer" && state.voiceClone.library.expandedGroupKey) {
          return { dataset: { voiceIdentityDialog: "identity-1" } };
        }
        return null;
      },
      querySelectorAll(selector) {
        return selector === "[data-voice-identity-details-trigger]" ? [trigger] : [];
      },
    },
    renderAll() { renderAllCalls += 1; },
    setProjectRailOpen() {},
    showToast() {},
    withUiTask: async (_button, _label, task) => task(),
    window: {
      clearTimeout() {},
      setTimeout,
      requestAnimationFrame(callback) { callback(); },
    },
  });
  vm.runInContext(apiSource, context);
  context.versionsResponse = versionsResponse;
  vm.runInContext("requestV2 = async () => versionsResponse;", context);
  vm.runInContext(eventSource, context);

  const openPromise = context.handleDocumentClick({ target: trigger });
  await Promise.resolve();
  assert.equal(state.voiceClone.library.expandedGroupKey, "identity-1");
  context.handleDocumentKeyDown({
    key: "Escape",
    target: keyTarget,
    preventDefault() { escapePrevented = true; },
  });
  resolveVersions([{ id: "version-after-close" }]);
  await openPromise;

  assert.equal(closeFocusCalls, 1);
  assert.equal(escapePrevented, true);
  assert.equal(state.voiceClone.library.expandedGroupKey, "");
  assert.equal(state.voiceClone.library.profilesByGroup["identity-1"], undefined);
  assert.equal(triggerFocusCalls, 1);
  assert.equal(renderAllCalls, 2);
});


test("both modal drawers contain Tab and Shift-Tab focus", async () => {
  const source = await readFile(
    new URL("../../src/frontend/js/events.js", import.meta.url),
    "utf8",
  );
  let activeDialog = "create";
  const focusCalls = [];
  const focusable = (name) => ({
    name,
    disabled: false,
    hidden: false,
    focus() { focusCalls.push(name); },
    getAttribute() { return null; },
    closest() { return null; },
    matches() { return false; },
  });
  const controls = {
    create: [focusable("create-first"), focusable("create-last")],
    details: [focusable("details-first"), focusable("details-last")],
  };
  const dialogs = Object.fromEntries(Object.entries(controls).map(([key, items]) => [key, {
    dataset: { voiceIdentityDialog: "identity-1" },
    contains(target) { return items.includes(target); },
    querySelectorAll() { return items; },
  }]));
  const context = vm.createContext({
    ...retentionHelpers,
    state: {
      pendingDangerKey: "",
      voiceClone: {
        createDrawer: { open: true },
        addFlow: { open: true },
        library: { expandedGroupKey: "identity-1" },
      },
    },
    document: {
      body: { dataset: { railOpen: "false" } },
      querySelector(selector) {
        if (selector === "#voice-clone-create-drawer") return activeDialog === "create" ? dialogs.create : null;
        if (selector === "#voice-identity-detail-drawer") return activeDialog === "details" ? dialogs.details : null;
        return null;
      },
      querySelectorAll() { return []; },
    },
    renderAll() {},
    setProjectRailOpen() {},
    window: { requestAnimationFrame(callback) { callback(); } },
  });
  vm.runInContext(source, context);

  for (const dialogName of ["create", "details"]) {
    activeDialog = dialogName;
    let prevented = 0;
    context.handleDocumentKeyDown({
      key: "Tab",
      shiftKey: false,
      target: controls[dialogName][1],
      preventDefault() { prevented += 1; },
    });
    context.handleDocumentKeyDown({
      key: "Tab",
      shiftKey: true,
      target: controls[dialogName][0],
      preventDefault() { prevented += 1; },
    });
    assert.equal(prevented, 2);
  }

  assert.deepEqual(focusCalls, [
    "create-first",
    "create-last",
    "details-first",
    "details-last",
  ]);
});


test("approved studio separates generation and library and keeps an empty draft editable", async () => {
  const generation = await renderVoiceCloneWorkbench({available:true});
  assert.match(generation, /data-voice-clone-view="generate"/);
  assert.match(generation, /data-voice-clone-view="library"/);
  assert.match(generation, /id="voice-clone-preview-text"(?![^>]*disabled)/);
  assert.match(generation, /id="voice-clone-current-model-select"/);
  assert.doesNotMatch(generation, /id="voice-clone-library-list"/);
  const library = await renderVoiceCloneWorkbench({available:true, view:"library", nextCursor:"next", total:45});
  assert.match(library, /45<\/strong> 个声音/);
  assert.match(library, /id="voice-clone-load-more-button"/);
  assert.doesNotMatch(library, /id="voice-clone-preview-text"/);
});

test("creation uses one named model form and sample playback remains inside details", async () => {
  const creation = await renderVoiceCloneWorkbench({available:true, addFlow:{open:true,stage:"pick"}});
  assert.match(creation, /<form[^>]*id="voice-clone-upload-sample-form"[\s\S]*id="voice-clone-display-name-input"[\s\S]*id="voice-clone-model-select"[\s\S]*id="voice-clone-upload-sample-button"[\s\S]*<\/form>/);
  assert.doesNotMatch(creation, /voice-clone-target-model-input/);
  const profile=selectableProfile(); const group=identityGroup("identity-1","声音",[],profile);
  const detail=await renderVoiceCloneWorkbench({available:true, groups:[group],expandedGroupKey:group.group_key, profilesByGroup:{[group.group_key]:[profile]}, sharedPlayer:{url:"/sample.wav",label:"样本"}});
  assert.match(detail, /id="voice-identity-detail-drawer"[\s\S]*id="voice-clone-shared-sample-player"[\s\S]*<\/aside>/);
  const failed=await renderVoiceCloneWorkbench({available:true,records:[{id:"failed",status:"failed",input_text:"文案"}]});
  assert.match(failed, /data-voice-record-download="failed"[^>]*disabled/);
});


test("an existing sample creation failure offers retry without uploading a fourth sample", async () => {
  const html = await renderVoiceCloneWorkbench({available:true,sampleId:"saved-sample",addFlow:{open:true,stage:"error",error:"暂时失败"}});
  assert.match(html, /id="voice-clone-create-button"[^>]*>重试创建/);
  assert.doesNotMatch(html, /id="voice-clone-upload-sample-form"/);
  assert.match(html, /参考音频已保存/);
});

test("expiry remains visible while expired voices block generation and live project audio remains playable", async () => {
  const voice = { ...selectableProfile(), expires_at: '2000-01-01T00:00:00Z' };
  const html = await renderVoiceCloneWorkbench({ available: true, selectedProfile: voice, records: [
    { id: 'live-record', voice_clone_profile_id: voice.id, voice_name_snapshot: '保留的音频', status: 'ready', expires_at: '2099-01-01T00:00:00Z', input_text: '文案仍在' },
    { id: 'expired-record', status: 'ready', expires_at: '2000-01-01T00:00:00Z' },
  ] });
  assert.match(html, /仅保留3天（创建后72小时）/);
  assert.match(html, /已到期 · 请添加声音，文案已保留/);
  assert.match(html, /voice-clone-preview-button[^>]*disabled/);
  assert.match(html, /voice-clone-current-model-select[^>]*disabled/);
  assert.match(html, /data-voice-record-download="live-record"[^>]*>下载/);
  assert.doesNotMatch(html, /data-voice-record-download="live-record"[^>]*disabled/);
  assert.match(html, /data-voice-record-download="expired-record"[^>]*disabled/);
  assert.match(html, /synthesis-records\/live-record\/audio/);
  assert.doesNotMatch(html, /synthesis-records\/expired-record\/audio/);
  assert.match(html, /data-voice-expires-at="2099-01-01T00:00:00.000Z"/);
  assert.match(html, /<textarea[^>]*>你好<\/textarea>/);
});

test("expired reference samples and voiceprint candidates expose expiry and disable reuse, confirmation and playback", async () => {
  const source = await readFile(new URL('../../src/frontend/js/renderers.js', import.meta.url), 'utf8');
  const expired = { id: 'expired', original_filename: 'old.wav', expires_at: '2000-01-01T00:00:00Z' };
  const context = vm.createContext({
    ...retentionHelpers,
    state: { voiceClone: { library: { samplesByGroup: { identity: [expired] } } } },
    sample: expired,
    escapeHtml: value => String(value ?? ''),
    formatDate: () => '01/01 08:00',
    formatVoiceSampleMeta: () => '',
    isVoiceCloneServiceAvailable: () => true,
  });
  vm.runInContext(source, context);
  const references = context.renderVoiceIdentitySamples({ group_key: 'identity' });
  assert.match(references, /0 \/ 3/);
  assert.match(references, /已到期/);
  assert.match(references, /data-voice-clone-sample-preview="expired"[^>]*disabled/);
  assert.match(references, /data-voice-clone-sample-create="expired"[^>]*disabled/);
  const candidates = context.renderVoiceprintSampleRow(expired, { candidate: true });
  assert.match(candidates, /已到期/);
  assert.match(candidates, /data-voiceprint-sample-confirm="expired"[^>]*disabled/);
  assert.doesNotMatch(candidates, /<audio/);
});


test('single-use studio shows upload and results without a reusable voice library', async () => {
  const html = await renderVoiceCloneWorkbench({ singleUse: true, view: 'library' });
  assert.match(html, /每次上传新的参考音频/);
  assert.match(html, /上传参考音频/);
  assert.match(html, /未下载的数据最多保留3天/);
  assert.doesNotMatch(html, /voice-clone-profile-select|管理声音库|voice-identity-rail|voice-studio-tabs/);
});

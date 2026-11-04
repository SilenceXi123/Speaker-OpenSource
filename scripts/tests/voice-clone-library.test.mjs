import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";


async function loadLibrary() {
  const source = await readFile(
    new URL("../../src/frontend/js/voice-clone-library.js", import.meta.url),
    "utf8",
  );
  const context = vm.createContext({ globalThis: {} });
  vm.runInContext(source, context);
  return context.globalThis.SpeakerVoiceCloneLibrary;
}

async function renderFilteredGroups(voiceClone) {
  const [librarySource, rendererSource] = await Promise.all([
    readFile(new URL("../../src/frontend/js/voice-clone-library.js", import.meta.url), "utf8"),
    readFile(new URL("../../src/frontend/js/renderers.js", import.meta.url), "utf8"),
  ]);
  const context = vm.createContext({
    globalThis: {},
    state: { voiceClone },
  });
  vm.runInContext(librarySource, context);
  vm.runInContext(rendererSource, context);
  return vm.runInContext("getFilteredVoiceCloneGroups()", context);
}


test("limits the initial library to 30 grouped cards", async () => {
  const library = await loadLibrary();
  const groups = Array.from({ length: 356 }, (_, index) => ({
    group_key: `${index}`,
  }));

  assert.equal(library.limitGroups(groups, 30).length, 30);
});


test("translates provider states for users", async () => {
  const library = await loadLibrary();

  assert.equal(library.translateStatus("ready"), "可用");
  assert.equal(library.translateStatus("creating"), "创建中");
  assert.equal(library.translateStatus("failed"), "创建失败");
});


test("library groups retain server identity facts without inventing legacy ones", async () => {
  const library = await loadLibrary();
  const group = library.normalizeGroup({
    group_key: "identity-1",
    identity_id: "identity-1",
    display_name: "撒贝宁",
    identity_display_name: "撒贝宁",
    identity_aliases: ["小撒", ""],
    version_count: 2,
    recommended_profile: { id: "profile-1", selectable: true },
  });
  const legacyGroup = library.normalizeGroup({
    group_key: "legacy:旧音色",
    display_name: "旧音色",
    identity_aliases: "not-an-array",
  });

  assert.equal(group.group_key, "identity-1");
  assert.equal(group.identity_id, "identity-1");
  assert.equal(group.identity_display_name, "撒贝宁");
  assert.deepEqual(group.identity_aliases, ["小撒"]);
  assert.equal(legacyGroup.group_key, "legacy:旧音色");
  assert.equal(legacyGroup.identity_id, null);
  assert.equal(legacyGroup.identity_display_name, null);
  assert.deepEqual(Array.from(legacyGroup.identity_aliases), []);
});


test("selects only ready profiles with a ready source sample", async () => {
  const library = await loadLibrary();

  assert.equal(library.isSelectableProfile({
    status: "ready",
    voice_id: "voice-1",
    selectable: true,
    source_sample_ready: true,
  }), true);
  assert.equal(library.isSelectableProfile({ status: "ready", voice_id: null }), false);
  assert.equal(library.isSelectableProfile({
    status: "ready",
    voice_id: "voice-2",
    selectable: false,
    source_sample_ready: false,
  }), false);
});


test("preserves profile identity fields and labels project scope", async () => {
  const library = await loadLibrary();
  const [group] = library.groupProfiles([{
    id: "profile-1",
    voice_name: "主持人",
    project_scope: "current_project",
    source_project_name: "节目 A",
    source_original_filename: "host-clean.wav",
    source_clone_sample_id: "sample-1",
    source_sample_ready: true,
    selectable: true,
    status: "ready",
    voice_id: "voice-1",
    identity_id: "identity-1",
    identity_display_name: "主持人",
    identity_aliases: ["主持人", "小撒", ""],
  }]);

  assert.equal(group.recommended_profile.project_scope, "current_project");
  assert.equal(group.recommended_profile.source_project_name, "节目 A");
  assert.equal(group.recommended_profile.source_original_filename, "host-clean.wav");
  assert.equal(group.recommended_profile.source_clone_sample_id, "sample-1");
  assert.equal(group.recommended_profile.source_sample_ready, true);
  assert.equal(group.recommended_profile.identity_id, "identity-1");
  assert.equal(group.recommended_profile.identity_display_name, "主持人");
  assert.deepEqual(group.recommended_profile.identity_aliases, ["主持人", "小撒"]);
  assert.equal(library.projectScopeLabel("current_project"), "当前项目");
  assert.equal(library.projectScopeLabel("used_in_current_project"), "本项目用过");
  assert.equal(library.projectScopeLabel("shared"), "共享音色");
});


test("normalizes clone samples without losing their preview label or identity facts", async () => {
  const library = await loadLibrary();

  const sample = library.normalizeSample?.({
    id: 17,
    display_name: " 主持人 ",
    original_filename: "host-clean.wav",
    project_id: "project-1",
    duration_ms: 12400,
    sample_rate_hz: 24000,
    channel_count: 1,
    clone_readiness_status: "ready",
    identity_id: " identity-1 ",
    identity_display_name: " 主持人 ",
    identity_aliases: [" 主持人A ", ""],
    match_type: "speaker_embedding",
    match_score: 0.91,
    sample_slots_remaining: 1,
  });

  assert.equal(sample?.id, "17");
  assert.equal(sample?.display_name, "主持人");
  assert.equal(sample?.original_filename, "host-clean.wav");
  assert.equal(sample?.duration_ms, 12400);
  assert.equal(sample?.sample_rate_hz, 24000);
  assert.equal(sample?.identity_id, "identity-1");
  assert.equal(sample?.identity_display_name, "主持人");
  assert.deepEqual(Array.from(sample?.identity_aliases || []), ["主持人A"]);
  assert.equal(sample?.match_type, "speaker_embedding");
  assert.equal(sample?.sample_slots_remaining, 1);
});


test("coalesces same-name profiles into one lightweight card", async () => {
  const library = await loadLibrary();
  const profiles = Array.from({ length: 73 }, (_, index) => ({
    id: `profile-${index}`,
    voice_name: index % 2 ? "主持人男" : " 主持人男 ",
    provider: "qwen",
    target_model: "qwen3-tts-vc-2026-01-22",
    status: "ready",
    voice_id: `voice-${index}`,
    selectable: true,
    source_sample_ready: true,
    last_used_at: new Date(2026, 0, index + 1).toISOString(),
    audio_public_url: `https://example.test/${index}.wav`,
  }));

  const groups = library.groupProfiles(profiles);

  assert.equal(groups.length, 1);
  assert.equal(groups[0].version_count, 73);
  assert.equal(groups[0].recommended_profile.id, "profile-72");
  assert.equal(JSON.stringify(groups).includes("audio_public_url"), false);
});


test("skips legacy groups with no selectable profile", async () => {
  const library = await loadLibrary();

  const groups = library.groupProfiles([{
    id: "unavailable-1",
    voice_name: "过期音色",
    status: "ready",
    voice_id: "voice-unavailable",
    selectable: false,
    source_sample_ready: false,
  }]);

  assert.equal(groups.length, 0);
});


test("routes clone and voiceprint source previews to their matching audio APIs", async () => {
  const library = await loadLibrary();

  const clonePreview = library.getSourcePreview(
    { source_clone_sample_id: "clone-sample" },
    "project-1",
  );
  const voiceprintPreview = library.getSourcePreview({ source_sample_id: "voiceprint-sample" });

  assert.equal(clonePreview.sampleId, "clone-sample");
  assert.equal(clonePreview.apiVersion, "v2");
  assert.equal(clonePreview.path, "/projects/project-1/voice-cloning/samples/clone-sample/audio");
  assert.equal(voiceprintPreview.sampleId, "voiceprint-sample");
  assert.equal(voiceprintPreview.apiVersion, "v1");
  assert.equal(voiceprintPreview.path, "/voiceprints/samples/voiceprint-sample/audio");
});


test("keeps the unavailable-profile message when the recovery refresh fails", async () => {
  const library = await loadLibrary();
  let cleared = false;

  const message = await library.recoverUnavailableProfile(
    () => { cleared = true; },
    async () => { throw new Error("刷新失败"); },
  );

  assert.equal(cleared, true);
  assert.equal(message, "该音色已失效并被清理，请重新选择或上传");
});


test("matches library groups by source project and original filename", async () => {
  const library = await loadLibrary();
  const group = {
    display_name: "白露 旁白",
    health_status: "ready",
    recommended_profile: {
      provider: "qwen",
      target_model: "qwen3-tts-vc",
      source_display_name: "白露",
      source_project_name: "共享来源项目 QA",
      source_original_filename: "bailu-clean.wav",
    },
  };

  assert.equal(library.groupMatchesQuery?.(group, "共享来源项目"), true);
  assert.equal(library.groupMatchesQuery?.(group, "bailu-clean.wav"), true);
});


test("keeps a backend-filtered group when its recommended version misses the query", async () => {
  const backendGroup = {
    group_key: "白露-旁白",
    display_name: "白露 旁白",
    health_status: "ready",
    recommended_profile: {
      id: "profile-recommended",
      voice_name: "白露 旁白",
      source_project_name: "当前推荐项目",
      source_original_filename: "recommended.wav",
    },
  };

  const groups = await renderFilteredGroups({
    library: { loaded: true, groups: [backendGroup] },
    profiles: [],
    profileSearch: "旧版本来源项目",
  });

  assert.equal(groups.length, 1);
  assert.equal(groups[0].group_key, "白露-旁白");
});


test("falls back to legacy profiles before the server library is loaded", async () => {
  const groups = await renderFilteredGroups({
    library: { loaded: false, groups: [] },
    profiles: [{
      id: "profile-legacy",
      voice_name: "旧版主持",
      status: "ready",
      voice_id: "voice-legacy",
      selectable: true,
      source_sample_ready: true,
    }],
    profileSearch: "旧版主持",
  });

  assert.equal(groups.length, 1);
  assert.equal(groups[0].recommended_profile.id, "profile-legacy");
});


test("keeps the current selectable profile during a filtered refresh", async () => {
  const library = await loadLibrary();
  const selectedProfile = {
    id: "profile-current",
    voice_name: "安然 主持",
    status: "ready",
    voice_id: "voice-current",
    selectable: true,
    source_sample_ready: true,
  };
  const filteredGroup = {
    recommended_profile: {
      id: "profile-match",
      voice_name: "白露 旁白",
      status: "ready",
      voice_id: "voice-match",
      selectable: true,
      source_sample_ready: true,
    },
  };

  const result = library.reconcileLibrarySelection?.([filteredGroup], {
    selectedProfileId: selectedProfile.id,
    selectedProfile,
    preserveSelectedProfile: true,
  }) || {};

  assert.equal(result.profileId, "profile-current");
  assert.equal(result.profiles?.some((profile) => profile.id === "profile-current"), true);
  assert.equal(result.profiles?.some((profile) => profile.id === "profile-match"), true);
});


test("keeps the current selectable profile while more library pages remain", async () => {
  const library = await loadLibrary();
  const selectedProfile = {
    id: "profile-page-two",
    voice_name: "陈墨 嘉宾",
    status: "ready",
    voice_id: "voice-page-two",
    selectable: true,
    source_sample_ready: true,
  };
  const firstPageGroup = {
    recommended_profile: {
      id: "profile-page-one",
      voice_name: "安然 主持",
      status: "ready",
      voice_id: "voice-page-one",
      selectable: true,
      source_sample_ready: true,
    },
  };

  const result = library.reconcileLibrarySelection?.([firstPageGroup], {
    selectedProfileId: selectedProfile.id,
    selectedProfile,
    preserveSelectedProfile: true,
  }) || {};

  assert.equal(result.profileId, "profile-page-two");
  assert.equal(result.profiles?.some((profile) => profile.id === "profile-page-two"), true);
});


test("clears a missing selected profile without choosing the next profile", async () => {
  const library = await loadLibrary();
  const nextGroup = {
    recommended_profile: {
      id: "profile-next",
      voice_name: "白露 旁白",
      status: "ready",
      voice_id: "voice-next",
      selectable: true,
      source_sample_ready: true,
    },
  };

  const result = library.reconcileLibrarySelection?.([nextGroup], {
    selectedProfileId: "profile-deleted",
    selectedProfile: null,
  }) || {};

  assert.equal(result.profileId, "");
  assert.equal(result.profiles?.[0]?.id, "profile-next");
});


test("auto-selects only when the caller allows it", async () => {
  const library = await loadLibrary();
  const firstGroup = {
    recommended_profile: {
      id: "profile-first",
      voice_name: "安然 主持",
      status: "ready",
      voice_id: "voice-first",
      selectable: true,
      source_sample_ready: true,
    },
  };

  const initialLoad = library.reconcileLibrarySelection?.([firstGroup], {}) || {};
  const explicitClear = library.reconcileLibrarySelection?.([firstGroup], {
    allowAutoSelect: false,
  }) || {};

  assert.equal(initialLoad.profileId, "profile-first");
  assert.equal(explicitClear.profileId, "");
});


test("keeps reuse-first creation order and preserves drawer state during search", async () => {
  const library = await loadLibrary();
  const current = {
    open: true,
    step: "existing_sample",
    profileQuery: "",
    sampleQuery: "",
  };

  const afterProfileSearch = library.updateDrawerState(current, {
    profileQuery: "撒贝宁",
  });
  const afterSampleSearch = library.updateDrawerState(afterProfileSearch, {
    sampleQuery: "新闻",
  });

  assert.deepEqual(
    Array.from(library.CREATION_FLOW),
    ["existing_profile", "existing_sample", "upload"],
  );
  assert.equal(afterSampleSearch.open, true);
  assert.equal(afterSampleSearch.step, "existing_sample");
  assert.equal(afterSampleSearch.profileQuery, "撒贝宁");
  assert.equal(afterSampleSearch.sampleQuery, "新闻");
});

test('sample-only identities keep zero versions and a nullable recommendation', async () => {
  const library = await loadLibrary();
  const group = library.normalizeGroup({ group_key: 'identity-draft', identity_id: 'identity-draft', display_name: '待创建声音', health_status: 'draft', version_count: 0, recommended_profile: null });
  assert.equal(group.version_count, 0);
  assert.equal(group.recommended_profile, null);
  assert.equal(library.translateStatus('draft'), '待创建');
  const selection = library.reconcileLibrarySelection([group]);
  assert.equal(selection.profileId, '');
  assert.equal(selection.profiles.length, 0);
});

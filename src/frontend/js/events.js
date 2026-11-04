let voiceCloneSearchTimer = null;
let voiceIdentityDetailsOpenToken = 0;
let voiceCloneAddFlowOpenToken = 0;
let voiceCloneBusyToken = 0;


function replaceVoiceCloneSearchResults(selector, html) {
  const current = document.querySelector(selector);
  if (!current) return;
  const scrollTop = elements.simpleWorkspace.scrollTop;
  const listScrollTop = current.scrollTop;
  current.outerHTML = html;
  elements.simpleWorkspace.scrollTop = scrollTop;
  const replacement = document.querySelector(selector);
  if (replacement && Number.isFinite(listScrollTop)) replacement.scrollTop = listScrollTop;
}

function replaceVoiceCloneLibraryList() {
  replaceVoiceCloneSearchResults("#voice-clone-library-list", renderVoiceIdentityList());
  if (document.querySelector("#voice-clone-library-pagination")) {
    replaceVoiceCloneSearchResults("#voice-clone-library-pagination", renderVoiceCloneLibraryPagination());
  }
  const total = document.querySelector("#voice-clone-library-total");
  if (total) total.textContent = String(state.voiceClone.library.total);
}

function replaceVoiceCloneSampleList() {
  replaceVoiceCloneSearchResults("#voice-clone-sample-list", renderVoiceCloneSampleLibrary());
  hydrateAudioPlayers();
}

function replaceVoiceCloneReuseSuggestions() {
  replaceVoiceCloneSearchResults("#voice-clone-reuse-suggestions", renderVoiceCloneReuseProfileSuggestions());
}

function scheduleVoiceCloneSearch(kind) {
  if (voiceCloneSearchTimer) window.clearTimeout(voiceCloneSearchTimer);
  voiceCloneSearchTimer = window.setTimeout(async () => {
    voiceCloneSearchTimer = null;
    try {
      if (kind === "drawer") {
        replaceVoiceCloneReuseSuggestions();
      } else if (kind === "profile") {
        state.voiceClone.library.query = state.voiceClone.profileSearch;
        await refreshVoiceCloneLibrary({ reset: true, allowAutoSelect: false });
        replaceVoiceCloneLibraryList();
      } else {
        state.voiceClone.samplePage.query = state.voiceClone.sampleSearch;
        await refreshVoiceCloneSamplePage({ reset: true });
        replaceVoiceCloneSampleList();
      }
    } catch (error) {
      showToast(error.message || "搜索失败，请稍后重试", "warning");
    }
  }, 150);
}


function bindEvents() {
  document.addEventListener("click", handleDocumentClick);
  document.addEventListener("play", handleVoiceAudioPlay, true);
  document.addEventListener("input", handleDocumentInput);
  document.addEventListener("change", handleDocumentChange);
  document.addEventListener("dragover", handleDocumentDragOver);
  document.addEventListener("dragleave", handleDocumentDragLeave);
  document.addEventListener("drop", handleDocumentDrop);
  document.addEventListener("submit", handleDocumentSubmit);
  document.addEventListener("keydown", handleDocumentKeyDown);
  document.addEventListener("focusin", handleDocumentFocusIn);
  document.addEventListener("compositionstart", handleDocumentCompositionStart);
  document.addEventListener("compositionend", handleDocumentCompositionEnd);
  bindNativeFileDrops();
}

function handleVoiceAudioPlay(event) {
  const audio = event.target;
  if (!isVoiceAssetExpired({ expires_at: audio?.dataset?.voiceExpiresAt })) return;
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  showToast("音频已到期，仅保留72小时。请重新上传或生成。", "warning");
}

function refuseExpiredVoiceAsset(asset) {
  if (!isVoiceAssetExpired(asset)) return false;
  state.voiceClone.error = "声音数据已到期，请重新上传或新建声音。当前文案已保留。";
  renderAll();
  return true;
}

function closeProjectSearchResults() {
  const input = document.querySelector("#current-project-select");
  const results = document.querySelector("#project-search-results");
  if (results) results.hidden = true;
  input?.setAttribute("aria-expanded", "false");
}

function renderProjectSearchResults(input) {
  const results = document.querySelector("#project-search-results");
  if (!input || !results) return;
  const query = String(input.value || "").trim().toLocaleLowerCase("zh-CN");
  const matches = [...state.projects]
    .filter((project) => !query || String(project.program_name || "").toLocaleLowerCase("zh-CN").includes(query))
    .sort((first, second) => {
      if (first.project_id === state.currentProjectId) return -1;
      if (second.project_id === state.currentProjectId) return 1;
      return String(first.program_name || "").localeCompare(String(second.program_name || ""), "zh-CN");
    });
  const visibleProjects = matches.slice(0, 7);
  results.innerHTML = visibleProjects.length
    ? `${visibleProjects.map((project) => {
        const isCurrent = project.project_id === state.currentProjectId;
        return `
          <button
            class="project-search-option ${isCurrent ? "is-current" : ""}"
            data-project-switch="${escapeHtml(project.project_id)}"
            data-project-name="${escapeHtml(project.program_name)}"
            type="button"
            role="option"
            aria-selected="${isCurrent ? "true" : "false"}"
          >
            <span>
              <strong>${escapeHtml(project.program_name)}</strong>
              <small>${Number(project.job_count || 0)} 个任务</small>
            </span>
            <small>${isCurrent ? "当前" : "切换"}</small>
          </button>
        `;
      }).join("")}${matches.length > visibleProjects.length ? `<p class="project-search-more">还有 ${matches.length - visibleProjects.length} 个结果，继续输入可缩小范围</p>` : ""}`
    : `<p class="project-search-empty">没有匹配项目</p>`;
  results.hidden = false;
  input.setAttribute("aria-expanded", "true");
}

function handleDocumentFocusIn(event) {
  if (event.target.matches("#current-project-select")) {
    renderProjectSearchResults(event.target);
  }
}

function focusVoiceIdentityDetailsDialog() {
  window.requestAnimationFrame(() => {
    document.querySelector("#voice-identity-details-close-button")?.focus();
  });
}

function focusVoiceCloneCreationDrawer() {
  window.requestAnimationFrame(() => {
    document.querySelector("#voice-clone-drawer-close-button")?.focus();
  });
}


function updateVoiceCloneGenerationControls() {
  const profile = state.voiceClone.profiles.find((item) => item.id === state.voiceClone.profileId);
  const model = profile ? getVoiceCloneModelOption(`${profile.provider}:${profile.target_model}`) : getVoiceCloneModelOption();
  const count = Array.from(state.voiceClone.previewText || "").length;
  const instructionCount = getVoiceCloneInstructionWeight(state.voiceClone.voiceInstruction, model);
  const textLimit = Number(model?.constraints?.text_max_characters || 0);
  const instructionLimit = Number(model?.constraints?.instruction_max_weight || 0);
  const counter = document.querySelector("#voice-clone-character-count");
  if (counter) counter.textContent = String(count);
  const instructionCounter = document.querySelector("#voice-clone-instruction-count");
  if (instructionCounter) instructionCounter.textContent = String(instructionCount);
  const button = document.querySelector("#voice-clone-preview-button");
  if (button) button.disabled = Boolean(state.voiceClone.busy || state.voiceClone.pendingModelId
    || !state.voiceClone.previewText?.trim() || (textLimit && count > textLimit)
    || (model?.capabilities?.instruction && instructionLimit && instructionCount > instructionLimit)
    || !globalThis.SpeakerVoiceCloneLibrary?.isSelectableProfile(profile)
    || !isVoiceCloneServiceAvailable(profile));
}

async function runVoiceCloneCreation(button, create) {
  if (state.voiceClone.busy) return;
  const targetProjectId = String(state.currentProjectId || "");
  const requestGeneration = state.voiceClone.requestGeneration;
  const token = voiceCloneAddFlowOpenToken;
  const busyToken = ++voiceCloneBusyToken;
  const isCurrent = () => String(state.currentProjectId || "") === targetProjectId
    && state.voiceClone.requestGeneration === requestGeneration && voiceCloneAddFlowOpenToken === token;
  state.voiceClone.busy = "create";
  state.voiceClone.error = "";
  state.voiceClone.addFlow.error = "";
  renderAll();
  await withUiTask(button, "创建中...", async () => {
    try {
      const profile = await create(isCurrent);
      if (!isCurrent()) return;
      if (profile?.status === "ready") {
        state.voiceClone.view = "generate";
        state.voiceClone.busy = "";
        clearVoiceCloneSampleDraft();
        closeVoiceCloneCreationDrawer();
        await refreshVoiceCloneData();
        renderAll();
      }
    } catch (error) {
      if (!isCurrent()) return;
      const detail = error?.detail || {};
      const normalizeSample = globalThis.SpeakerVoiceCloneLibrary?.normalizeSample || ((item) => item || {});
      state.voiceClone.error = normalizeError(error).message;
      state.voiceClone.addFlow = error?.status === 409 && Array.isArray(detail.existing_samples)
        ? { ...state.voiceClone.addFlow, stage: "full", matchType: "", matchedIdentity: { id: String(detail.identity_id || ""), display_name: state.voiceClone.displayName }, sampleSlotsRemaining: 0, existingSamples: detail.existing_samples.map(normalizeSample), error: state.voiceClone.error }
        : { ...state.voiceClone.addFlow, stage: "error", error: state.voiceClone.error };
      throw error;
    } finally {
      if (String(state.currentProjectId || "") === targetProjectId && voiceCloneBusyToken === busyToken && voiceCloneAddFlowOpenToken === token) { state.voiceClone.busy = ""; renderAll(); }
    }
  });
}

async function generateVoiceCloneAudio(button) {
  if (state.voiceClone.busy) return;
  const projectId = String(state.currentProjectId || "");
  const requestGeneration = state.voiceClone.requestGeneration;
  const profileId = state.voiceClone.profileId;
  const busyToken = ++voiceCloneBusyToken;
  const isActive = () => String(state.currentProjectId || "") === projectId
    && voiceCloneBusyToken === busyToken && state.voiceClone.busy === "generate";
  const isCurrent = () => String(state.currentProjectId || "") === projectId
    && state.voiceClone.requestGeneration === requestGeneration && state.voiceClone.profileId === profileId;
  state.voiceClone.busy = "generate";
  state.voiceClone.error = "";
  renderAll();
  await withUiTask(button, "生成中...", async () => {
    try {
      const record = await createVoiceClonePreview();
      if (!isCurrent()) return;
      if (record?.status === "failed") throw new Error(record.provider_message || "音频生成失败，请重试");
      showToast(record?.provider_message || "音频已生成，可以试听或下载", "success");
    } catch (error) {
      if (isActive() && (isCurrent() || ["voice_clone_profile_unavailable", "voice_asset_expired"].includes(error?.code))) {
        state.voiceClone.error = normalizeError(error).message;
      }
      if (isCurrent()) throw error;
    } finally {
      if (isActive()) {
        state.voiceClone.busy = "";
        renderAll();
      }
    }
  });
}

function openExistingVoiceCloneCreation({ profileId = "", sampleId = "", groupKey = "" } = {}) {
  if (state.voiceClone.busy) return;
  const library = state.voiceClone.library;
  const profile = [...state.voiceClone.profiles, ...Object.values(library.profilesByGroup || {}).flat(), ...(library.creatingProfiles || []), ...library.groups.map((group) => group.recommended_profile)]
    .find((item) => item?.id === profileId);
  const key = groupKey || profile?.group_key || profile?.identity_id || "";
  const group = library.groups.find((item) => item.group_key === key);
  const samples = library.samplesByGroup?.[key] || [];
  const cloneSampleId = sampleId || profile?.source_clone_sample_id || "";
  const sample = [...samples, ...state.voiceClone.samples].find((item) => item.id === cloneSampleId);
  const sourceSampleId = !cloneSampleId && profile?.source_sample_id || "";
  if (refuseExpiredVoiceAsset(sample) || refuseExpiredVoiceAsset(profile)) return;
  if ((!sample && !profile?.source_clone_sample_id && !sourceSampleId) || (sample && sample.clone_readiness_status === "rejected")) {
    state.voiceClone.error = "参考录音不可用，请重新选择样本";
    renderAll();
    return;
  }
  voiceCloneAddFlowOpenToken += 1;
  voiceIdentityDetailsOpenToken += 1;
  clearVoiceCloneSampleDraft();
  library.expandedGroupKey = "";
  state.voiceClone.sampleId = cloneSampleId || sourceSampleId;
  state.voiceClone.displayName = group?.identity_display_name || group?.display_name || sample?.display_name || profile?.voice_name || "";
  state.voiceClone.error = "";
  if (profile) selectVoiceCloneModel(`${profile.provider}:${profile.target_model}`);
  applyVoiceCloneProviderDefaults();
  state.voiceClone.createDrawer = { ...state.voiceClone.createDrawer, open: true };
  state.voiceClone.addFlow = {
    open: true, stage: "matched", matchType: "existing_sample", error: "",
    matchedIdentity: { id: group?.identity_id || profile?.identity_id || sample?.identity_id || (key.startsWith("legacy:") ? "" : key), display_name: state.voiceClone.displayName },
    existingSamples: samples.length ? samples : sample ? [sample] : [],
    existingProfiles: library.profilesByGroup?.[key] || [],
    fileLabel: sample?.original_filename || profile?.source_original_filename || "",
    sourceSampleId,
    sourceAsset: { id: cloneSampleId || sourceSampleId, expires_at: getVoiceAssetExpiresAt(sample || profile)?.toISOString() || null },
  };
  renderAll();
  focusVoiceCloneCreationDrawer();
}

function closeVoiceCloneCreationDrawer() {
  voiceCloneAddFlowOpenToken += 1;
  if (state.voiceClone.busy === "create") state.voiceClone.busy = "";
  state.voiceClone.createDrawer = { ...state.voiceClone.createDrawer, open: false };
  state.voiceClone.addFlow = { ...state.voiceClone.addFlow, open: false };
  renderAll();
  window.requestAnimationFrame(() => {
    document.querySelector("#voice-clone-open-create, #voice-clone-new-button")?.focus();
  });
}

function closeVoiceIdentityDetails(identityKey = "") {
  voiceIdentityDetailsOpenToken += 1;
  state.voiceClone.library.expandedGroupKey = "";
  renderAll();
  window.requestAnimationFrame(() => {
    Array.from(document.querySelectorAll("[data-voice-identity-details-trigger]"))
      .find((button) => button.dataset.voiceIdentityDetailsTrigger === identityKey)
      ?.focus();
  });
}

function trapModalFocus(event, dialog) {
  if (event.key !== "Tab" || !dialog) return false;
  const controls = Array.from(dialog.querySelectorAll(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )).filter((control) => !control.hidden && control.getAttribute("aria-hidden") !== "true");
  if (!controls.length) return false;
  const currentIndex = controls.indexOf(event.target);
  const wrapsBackward = event.shiftKey && currentIndex <= 0;
  const wrapsForward = !event.shiftKey && currentIndex === controls.length - 1;
  if (currentIndex < 0 || wrapsBackward || wrapsForward) {
    event.preventDefault();
    controls[event.shiftKey ? controls.length - 1 : 0].focus();
    return true;
  }
  return false;
}

function handleDocumentKeyDown(event) {
  const voiceCloneCreationDialog = document.querySelector("#voice-clone-create-drawer");
  const voiceIdentityDialog = document.querySelector("#voice-identity-detail-drawer");
  if (trapModalFocus(event, voiceCloneCreationDialog || voiceIdentityDialog)) return;
  if (event.key === "Escape" && voiceCloneCreationDialog) {
    event.preventDefault();
    closeVoiceCloneCreationDrawer();
    return;
  }
  if (event.key === "Escape" && voiceIdentityDialog) {
    event.preventDefault();
    closeVoiceIdentityDetails(voiceIdentityDialog.dataset.voiceIdentityDialog || "");
    return;
  }
  const focusedProjectOption = event.target.closest?.("[data-project-switch]");
  if (focusedProjectOption && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    focusedProjectOption.click();
    return;
  }
  if (focusedProjectOption && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
    const options = Array.from(document.querySelectorAll("#project-search-results [data-project-switch]"));
    const currentIndex = options.indexOf(focusedProjectOption);
    const direction = event.key === "ArrowDown" ? 1 : -1;
    const nextOption = options[(currentIndex + direction + options.length) % options.length];
    if (nextOption) {
      event.preventDefault();
      nextOption.focus();
    }
    return;
  }
  if (event.target.matches("#current-project-select") && event.key === "ArrowDown") {
    const firstResult = document.querySelector("#project-search-results [data-project-switch]");
    if (firstResult) {
      event.preventDefault();
      firstResult.focus();
    }
    return;
  }
  if (event.key === "Enter" && event.target.matches("#current-project-select")) {
    event.preventDefault();
    const query = String(event.target.value || "").trim().toLocaleLowerCase("zh-CN");
    const exactProject = state.projects.find((project) => (
      String(project.program_name || "").toLocaleLowerCase("zh-CN") === query
    ));
    const firstResult = document.querySelector("#project-search-results [data-project-switch]");
    if (exactProject) {
      event.target.value = exactProject.project_id;
      event.target.dispatchEvent(new Event("change", { bubbles: true }));
    } else if (firstResult) {
      firstResult.click();
    }
    return;
  }
  if (event.key === "Escape" && !document.querySelector("#project-search-results")?.hidden) {
    event.preventDefault();
    closeProjectSearchResults();
    return;
  }
  if (event.key === "Escape" && document.body.dataset.railOpen === "true") {
    setProjectRailOpen(false);
  }
}

function requireSecondClick(key, message = "再次点击确认操作") {
  if (state.pendingDangerKey === key) {
    state.pendingDangerKey = "";
    return true;
  }
  state.pendingDangerKey = key;
  showToast(message, "danger");
  return false;
}

function formatVoiceprintLearningToast(baseMessage, learning) {
  if (!learning) return baseMessage;
  const created = Number(learning.created_count || 0);
  const skipped = Number(learning.skipped_count || 0);
  const duplicates = Number(learning.duplicate_count || 0);
  const autoConfirmed = Number(learning.auto_confirmed_count || 0);
  const globalPromoted = Number(learning.global_promoted_count || 0);
  const pruned = Number(learning.pruned_count || 0);
  const conflicts = Number(learning.conflict_count || 0);
  const errors = Number(learning.error_count || 0);
  const details = [];
  if (created) details.push(`生成 ${created} 条声纹候选`);
  if (autoConfirmed) details.push(`自动确认 ${autoConfirmed} 条`);
  if (globalPromoted) details.push(`晋升 ${globalPromoted} 个通用人物`);
  if (pruned) details.push(`自动淘汰 ${pruned} 条旧样本`);
  if (duplicates) details.push(`重复 ${duplicates} 条`);
  if (conflicts) details.push(`冲突 ${conflicts} 条`);
  if (skipped && !created) details.push(`跳过 ${skipped} 条`);
  if (errors) details.push(`遇到 ${errors} 个问题`);
  return details.length ? `${baseMessage}，${details.join("，")}` : `${baseMessage}，没有生成新的声纹候选`;
}

function voiceprintLearningToastTone(learning) {
  if (!learning) return "success";
  if (Number(learning.error_count || 0) > 0 || Number(learning.conflict_count || 0) > 0) return "warning";
  if (
    Number(learning.created_count || 0) > 0
    || Number(learning.auto_confirmed_count || 0) > 0
    || Number(learning.global_promoted_count || 0) > 0
  ) return "success";
  return "neutral";
}

function syncSpeakerMappingDraft(speakerKey, speakerName) {
  const key = String(speakerKey || "");
  if (!key) return;
  const name = String(speakerName || "");
  state.speakerMappings = { ...state.speakerMappings, [key]: name };
  document.querySelectorAll("[data-speaker-input]").forEach((input) => {
    if (input.dataset.speakerInput === key && input.value !== name) input.value = name;
  });
}

function syncCueSpeakerDraft(cueIndex, speakerName) {
  const key = String(cueIndex || "");
  if (!key) return;
  state.cueSpeakerDrafts = { ...state.cueSpeakerDrafts, [key]: String(speakerName || "") };
}

function syncCueTextDraft(cueIndex, cueText) {
  const key = String(cueIndex || "");
  if (!key) return;
  state.cueTextDrafts = { ...state.cueTextDrafts, [key]: String(cueText || "") };
  if (state.cueDeletedDrafts?.[key]) {
    state.cueDeletedDrafts = { ...state.cueDeletedDrafts, [key]: false };
  }
}

function toggleCueTextDelete(cueIndex) {
  const key = String(cueIndex || "");
  if (!key) return;
  const nextDeleted = !Boolean(state.cueDeletedDrafts?.[key]);
  state.cueDeletedDrafts = { ...state.cueDeletedDrafts, [key]: nextDeleted };
}

function rerenderVoiceprintSearchInput(cursorPosition = null) {
  renderAll();
  const nextInput = document.querySelector("#voiceprint-search-input");
  if (nextInput) {
    nextInput.focus();
    const nextCursor = Number.isFinite(cursorPosition) ? cursorPosition : nextInput.value.length;
    nextInput.setSelectionRange(nextCursor, nextCursor);
  }
}

function syncCueSearchInput(target) {
  const cursorPosition = target.selectionStart;
  state.cueSearchQuery = target.value;
  state.cueSearchMatchIndex = 0;
  const matches = getCueSearchMatches();
  if (matches.length) {
    state.cuePage = Math.floor(matches[0].arrayIndex / CUE_PREVIEW_PAGE_SIZE);
  }
  renderAll();
  const nextInput = document.querySelector("#cue-search-input");
  if (nextInput) {
    nextInput.focus();
    const nextCursor = Number.isFinite(cursorPosition) ? cursorPosition : nextInput.value.length;
    nextInput.setSelectionRange(nextCursor, nextCursor);
  }
  window.requestAnimationFrame(() => {
    document.querySelector("[data-current-cue-match='true']")?.scrollIntoView({ block: "center" });
  });
}

function handleDocumentCompositionStart(event) {
  const target = event.target;
  if (target?.matches?.("#cue-search-input")) {
    state.cueSearchComposing = true;
    return;
  }
  const kind = target?.matches?.("#voice-clone-profile-search-input")
    ? "profile"
    : target?.matches?.("#voice-clone-sample-search-input")
      ? "sample"
      : target?.matches?.("#voice-clone-display-name-input")
        ? "drawer"
        : "";
  if (kind) state.voiceClone.searchComposing[kind] = true;
}

function handleDocumentCompositionEnd(event) {
  const target = event.target;
  if (target?.matches?.("#cue-search-input")) {
    state.cueSearchComposing = false;
    syncCueSearchInput(target);
    return;
  }
  const kind = target?.matches?.("#voice-clone-profile-search-input")
    ? "profile"
    : target?.matches?.("#voice-clone-sample-search-input")
      ? "sample"
      : target?.matches?.("#voice-clone-display-name-input")
        ? "drawer"
        : "";
  if (!kind) return;
  state.voiceClone.searchComposing[kind] = false;
  if (kind === "profile") state.voiceClone.profileSearch = target.value;
  if (kind === "sample") state.voiceClone.sampleSearch = target.value;
  if (kind === "drawer") {
    state.voiceClone.displayName = target.value;
    state.voiceClone.createDrawer.profileQuery = target.value;
  }
  scheduleVoiceCloneSearch(kind);
}

let nativeFileDropBound = false;

function revokeObjectUrl(url) {
  if (!url) return;
  URL.revokeObjectURL(url);
}

function deriveVoiceNameFromFileName(fileName = "") {
  return String(fileName || "")
    .split(/[\\/]/)
    .pop()
    .replace(/\.[^.]+$/, "")
    .trim();
}

function cloneSampleDraftDefaults() {
  return {
    projectId: "",
    file: null,
    fileName: "",
    objectUrl: "",
    duration: 0,
    trimStart: 0,
    trimEnd: 0,
    trimmedFile: null,
    trimmedObjectUrl: "",
    trimmedDuration: 0,
    speechProfile: null,
  };
}

function clearVoiceCloneSampleDraft() {
  const draft = state.voiceClone.sampleDraft || {};
  revokeObjectUrl(draft.objectUrl);
  revokeObjectUrl(draft.trimmedObjectUrl);
  state.voiceClone.sampleDraft = cloneSampleDraftDefaults();
}

function clearVoiceSampleDraft() {
  state.voiceSampleDraft = { file: null, fileName: "" };
  state.pickedVoiceSampleFile = null;
  state.pickedVoiceSampleSource = "";
}

function setMediaDraftFromFile(fileOrMeta, source = "browser") {
  if (!fileOrMeta) {
    state.pickedMediaFile = null;
    state.pickedMediaSource = "";
    state.pickedMediaProjectId = "";
    renderAll();
    return;
  }
  try {
    assertRecognitionFileSize(fileOrMeta);
  } catch (error) {
    state.pickedMediaFile = null;
    state.pickedMediaSource = "";
    state.pickedMediaProjectId = "";
    showToast(normalizeError(error).message, "danger");
    renderAll();
    return;
  }
  if (typeof File !== "undefined" && fileOrMeta instanceof File) {
    state.pickedMediaFile = { name: fileOrMeta.name || "media-file", path: "", browserFile: fileOrMeta };
    state.pickedMediaSource = source;
    state.pickedMediaProjectId = String(state.currentProjectId || "");
    renderAll();
    return;
  }
  if (typeof File !== "undefined" && fileOrMeta.browserFile instanceof File) {
    state.pickedMediaFile = {
      name: fileOrMeta.browserFile.name || fileOrMeta.name || "media-file",
      path: fileOrMeta.path || "",
      browserFile: fileOrMeta.browserFile,
    };
    state.pickedMediaSource = source;
    state.pickedMediaProjectId = String(state.currentProjectId || "");
    renderAll();
    return;
  }
  const path = String(fileOrMeta.path || "");
  const name = String(fileOrMeta.name || path.split(/[\\/]/).pop() || "media-file");
  const sizeBytes = Number(fileOrMeta.sizeBytes ?? fileOrMeta.size_bytes ?? 0);
  state.pickedMediaFile = {
    name,
    path,
    ...(Number.isFinite(sizeBytes) && sizeBytes > 0 ? { sizeBytes } : {}),
  };
  state.pickedMediaSource = source;
  state.pickedMediaProjectId = String(state.currentProjectId || "");
  const fallbackInput = document.querySelector("#video-file-input");
  if (fallbackInput) fallbackInput.value = "";
  renderAll();
}

function setTranscriptDraftFromFile(fileOrMeta, source = "browser") {
  if (!fileOrMeta) {
    state.transcript.pickedFile = null;
    state.transcript.pickedSource = "";
    state.transcript.pickedProjectId = "";
    renderAll();
    return;
  }
  try {
    assertRecognitionFileSize(fileOrMeta);
  } catch (error) {
    state.transcript.pickedFile = null;
    state.transcript.pickedProjectId = "";
    state.transcript.pickedSource = "";
    showToast(normalizeError(error).message, "danger");
    renderAll();
    return;
  }
  if (typeof File !== "undefined" && fileOrMeta instanceof File) {
    state.transcript.pickedFile = { name: fileOrMeta.name || "audio-file", path: "", browserFile: fileOrMeta };
    state.transcript.pickedSource = source;
    state.transcript.pickedProjectId = String(state.currentProjectId || "");
    renderAll();
    return;
  }
  if (typeof File !== "undefined" && fileOrMeta.browserFile instanceof File) {
    state.transcript.pickedFile = {
      name: fileOrMeta.browserFile.name || fileOrMeta.name || "audio-file",
      path: fileOrMeta.path || "",
      browserFile: fileOrMeta.browserFile,
    };
    state.transcript.pickedSource = source;
    state.transcript.pickedProjectId = String(state.currentProjectId || "");
    renderAll();
    return;
  }
  const path = String(fileOrMeta.path || "");
  const name = String(fileOrMeta.name || path.split(/[\\/]/).pop() || "audio-file");
  const sizeBytes = Number(fileOrMeta.sizeBytes ?? fileOrMeta.size_bytes ?? 0);
  state.transcript.pickedFile = {
    name,
    path,
    ...(Number.isFinite(sizeBytes) && sizeBytes > 0 ? { sizeBytes } : {}),
  };
  state.transcript.pickedSource = source;
  state.transcript.pickedProjectId = String(state.currentProjectId || "");
  const fallbackInput = document.querySelector("#transcript-file-input");
  if (fallbackInput) fallbackInput.value = "";
  renderAll();
}

function clearPendingTaskUploads() {
  state.pickedMediaFile = null;
  state.pickedMediaSource = "";
  state.pickedMediaProjectId = "";
  state.pickedTaskScriptFiles = [];
  state.pickedTaskScriptProjectId = "";
  state.lyricsCorrectionMode = false;
  state.pickedScriptFile = null;
  state.pickedScriptSource = "";
  state.scriptText = "";
  state.transcript.pickedFile = null;
  state.transcript.pickedSource = "";
  state.transcript.pickedProjectId = "";
  state.pickedLowerThirdFiles = [];
  state.pickedLowerThirdSource = "";
  state.pickedLowerThirdProjectId = "";
  state.pickedLowerThirdJobId = "";
  state.pickedSubtitleBackplateFile = null;
  state.pickedSubtitleBackplateSource = "";
  state.pickedSubtitleBackplateFiles = { with_speaker: null, without_speaker: null };
  state.pickedSubtitleBackplateSources = { with_speaker: "", without_speaker: "" };
  state.pickedSubtitleBackplateProjectIds = { with_speaker: "", without_speaker: "" };
  state.pickedSubtitleBackplateJobIds = { with_speaker: "", without_speaker: "" };
  const mediaInput = document.querySelector("#video-file-input");
  const taskScriptInput = document.querySelector("#task-script-file-input");
  const scriptInput = document.querySelector("#script-file-input");
  const transcriptInput = document.querySelector("#transcript-file-input");
  if (mediaInput) mediaInput.value = "";
  if (taskScriptInput) taskScriptInput.value = "";
  if (scriptInput) scriptInput.value = "";
  if (transcriptInput) transcriptInput.value = "";
  const lowerThirdInput = document.querySelector("#lower-third-input");
  if (lowerThirdInput) lowerThirdInput.value = "";
}

function probeAudioDuration(file) {
  return new Promise((resolve) => {
    if (!file) {
      resolve(0);
      return;
    }
    const audio = document.createElement("audio");
    const objectUrl = URL.createObjectURL(file);
    const cleanup = () => {
      audio.removeAttribute("src");
      audio.load();
      revokeObjectUrl(objectUrl);
    };
    audio.preload = "metadata";
    audio.onloadedmetadata = () => {
      const duration = Number(audio.duration || 0);
      cleanup();
      resolve(Number.isFinite(duration) && duration > 0 ? duration : 0);
    };
    audio.onerror = () => {
      cleanup();
      resolve(0);
    };
    audio.src = objectUrl;
  });
}

async function materializeDropzoneFile(fileOrMeta) {
  if (!fileOrMeta) return null;
  if (typeof File !== "undefined" && fileOrMeta instanceof File) return fileOrMeta;
  if (typeof File !== "undefined" && fileOrMeta.browserFile instanceof File) return fileOrMeta.browserFile;
  if (fileOrMeta.path) {
    const blob = await loadClientBridgeFile(fileOrMeta);
    const name = fileOrMeta.name || fileOrMeta.path.split(/[\\/]/).pop() || "audio-sample";
    return new File([blob], name, { type: blob.type || guessMimeType(name) });
  }
  return null;
}

async function setVoiceSampleDraftFromFile(fileOrMeta, source = "browser") {
  const file = await materializeDropzoneFile(fileOrMeta);
  if (!file) {
    clearVoiceSampleDraft();
    renderAll();
    return null;
  }
  state.voiceSampleDraft = { file, fileName: file.name || "识别样本" };
  state.pickedVoiceSampleFile = { name: file.name || "识别样本", path: "", browserFile: file };
  state.pickedVoiceSampleSource = source;
  renderAll();
  return file;
}

async function setScriptDraftFromFile(fileOrMeta, source = "browser") {
  const file = await materializeDropzoneFile(fileOrMeta);
  if (!file) {
    state.pickedScriptFile = null;
    state.pickedScriptSource = "";
    renderAll();
    return null;
  }
  state.pickedScriptFile = { name: file.name || "台本文件", path: "", browserFile: file };
  state.pickedScriptSource = source;
  renderAll();
  return file;
}

async function setTaskScriptDraftFromFiles(files, source = "browser", append = false) {
  const materialized = (await Promise.all(Array.from(files || []).map(materializeDropzoneFile))).filter(Boolean);
  const selected = materialized.map((file) => ({ name: file.name || "台本文件", path: "", browserFile: file }));
  const combined = append ? [...(state.pickedTaskScriptFiles || []), ...selected] : selected;
  state.pickedTaskScriptFiles = combined.filter((file, index, items) => (
    items.findIndex((item) => item.name === file.name && item.browserFile?.size === file.browserFile?.size) === index
  ));
  state.pickedTaskScriptProjectId = state.pickedTaskScriptFiles.length ? String(state.currentProjectId || "") : "";
  renderAll();
}

async function setLexiconDraftFromFile(fileOrMeta, source = "browser") {
  const file = await materializeDropzoneFile(fileOrMeta);
  if (!file) {
    state.lexicon.pickedFile = null;
    state.lexicon.pickedSource = "";
    renderAll();
    return null;
  }
  state.lexicon.pickedFile = { name: file.name || "台本文件", path: "", browserFile: file };
  state.lexicon.pickedSource = state.lexicon.pickedSource || file.name || "";
  renderAll();
  return file;
}

function resolveSubtitleBackplateRoleFromInput(inputId = "") {
  const value = String(inputId || "");
  if (value.includes("without_speaker")) return "without_speaker";
  if (value.includes("with_speaker")) return "with_speaker";
  return value === "subtitle-backplate-input" ? "with_speaker" : "";
}

function clearScriptAssistDraft() {
  state.pickedScriptFile = null;
  state.pickedScriptSource = "";
  state.scriptText = "";
}

async function setVoiceCloneSampleDraftFromFile(fileOrMeta) {
  const targetProjectId = String(state.currentProjectId || "");
  if (!targetProjectId) throw new Error("请先选择项目，再选择声音复刻样本");
  const file = await materializeDropzoneFile(fileOrMeta);
  if (String(state.currentProjectId || "") !== targetProjectId) {
    throw new Error("项目已切换，本次声音样本选择已取消。");
  }
  if (!file) {
    clearVoiceCloneSampleDraft();
    renderAll();
    return null;
  }
  clearVoiceCloneSampleDraft();
  const duration = await probeAudioDuration(file);
  const speechProfile = await analyzeVoiceActivity(file).catch(() => null);
  if (String(state.currentProjectId || "") !== targetProjectId) {
    throw new Error("项目已切换，本次声音样本分析已取消。");
  }
  const normalizedDuration = speechProfile?.duration || duration;
  const recommendedWindow = getRecommendedSpeechWindow(speechProfile, normalizedDuration);
  const nameInput = document.querySelector("#voice-clone-display-name-input");
  const pendingDisplayName = nameInput?.value.trim() || state.voiceClone.displayName;
  state.voiceClone.sampleDraft = {
    ...cloneSampleDraftDefaults(),
    projectId: targetProjectId,
    file,
    fileName: file.name || "复刻样本",
    objectUrl: URL.createObjectURL(file),
    duration: normalizedDuration,
    trimStart: recommendedWindow.start,
    trimEnd: recommendedWindow.end,
    speechProfile,
  };
  if (pendingDisplayName) {
    state.voiceClone.displayName = pendingDisplayName;
  } else {
    state.voiceClone.displayName = deriveVoiceNameFromFileName(file.name);
  }
  renderAll();
  return file;
}

async function applyFileToDropzone(inputId, fileOrMeta, source = "browser") {
  if (inputId === "voice-clone-sample-input" && !isVoiceCloneServiceAvailable()) {
    showToast("声音服务未连接，暂时不能选择或拖入复刻样本", "warning");
    return;
  }
  if (inputId === "lower-third-input") {
    const files = (Array.isArray(fileOrMeta) ? fileOrMeta : [fileOrMeta]).filter(Boolean);
    const pathFiles = files.filter((item) => item?.path);
    if (!pathFiles.length) {
      throw new Error("人名条需要保留本机路径，请点击选择图片，或在桌面客户端里拖入图片。");
    }
    state.pickedLowerThirdFiles = pathFiles;
    state.pickedLowerThirdSource = source;
    state.pickedLowerThirdProjectId = String(state.currentProjectId || "");
    state.pickedLowerThirdJobId = String(state.currentJobId || "");
    const fallbackInput = document.querySelector("#lower-third-input");
    if (fallbackInput) fallbackInput.value = "";
    renderAll();
    return;
  }
  const subtitleBackplateRole = resolveSubtitleBackplateRoleFromInput(inputId);
  if (subtitleBackplateRole) {
    const fileMeta = Array.isArray(fileOrMeta) ? fileOrMeta.find((item) => item?.path) : fileOrMeta;
    if (!fileMeta?.path) {
      throw new Error("唱词条底图需要保留本机路径，请点击选择图片，或在桌面客户端里拖入图片。");
    }
    state.pickedSubtitleBackplateFiles = {
      ...(state.pickedSubtitleBackplateFiles || {}),
      [subtitleBackplateRole]: fileMeta,
    };
    state.pickedSubtitleBackplateSources = {
      ...(state.pickedSubtitleBackplateSources || {}),
      [subtitleBackplateRole]: source,
    };
    state.pickedSubtitleBackplateProjectIds = {
      ...(state.pickedSubtitleBackplateProjectIds || {}),
      [subtitleBackplateRole]: String(state.currentProjectId || ""),
    };
    state.pickedSubtitleBackplateJobIds = {
      ...(state.pickedSubtitleBackplateJobIds || {}),
      [subtitleBackplateRole]: String(state.currentJobId || ""),
    };
    if (subtitleBackplateRole === "with_speaker") {
      state.pickedSubtitleBackplateFile = fileMeta;
      state.pickedSubtitleBackplateSource = source;
    }
    const fallbackInput = document.querySelector(`#${inputId}`);
    if (fallbackInput) fallbackInput.value = "";
    renderAll();
    return;
  }
  if (inputId === "video-file-input") {
    setMediaDraftFromFile(fileOrMeta, source);
    return;
  }
  if (inputId === "task-script-file-input") {
    await setTaskScriptDraftFromFiles([fileOrMeta], source, true);
    return;
  }
  if (inputId === "transcript-file-input") {
    setTranscriptDraftFromFile(fileOrMeta, source);
    return;
  }
  if (inputId === "voice-clone-sample-input") {
    await setVoiceCloneSampleDraftFromFile(fileOrMeta);
    return;
  }
  if (inputId === "voice-sample-input") {
    await setVoiceSampleDraftFromFile(fileOrMeta, source);
    return;
  }
  if (inputId === "review-script-file-input") {
    await setScriptDraftFromFile(fileOrMeta, source);
    return;
  }
  if (inputId === "lexicon-script-input") {
    await setLexiconDraftFromFile(fileOrMeta, source);
    return;
  }
  const input = inputId ? document.querySelector(`#${inputId}`) : null;
  const file = await materializeDropzoneFile(fileOrMeta);
  assignDroppedFile(input, file);
}

function clampNumber(value, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return min;
  return Math.min(Math.max(number, min), max);
}

function sanitizeWavFileName(fileName = "audio-sample") {
  const stem = deriveVoiceNameFromFileName(fileName) || "audio-sample";
  return `${stem}-trim.wav`;
}

function writeAscii(view, offset, value) {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}

function encodeMonoPcm16Wav(samples, sampleRate) {
  const dataSize = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, dataSize, true);
  let offset = 44;
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }
  return new Blob([buffer], { type: "audio/wav" });
}

async function createTrimmedWavFile(file, startSeconds, endSeconds) {
  const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextConstructor) throw new Error("当前客户端环境不支持本地音频裁剪");
  const audioContext = new AudioContextConstructor();
  try {
    const audioBuffer = await audioContext.decodeAudioData(await file.arrayBuffer());
    const sampleRate = audioBuffer.sampleRate;
    const startFrame = Math.floor(clampNumber(startSeconds, 0, audioBuffer.duration) * sampleRate);
    const endFrame = Math.ceil(clampNumber(endSeconds, 0, audioBuffer.duration) * sampleRate);
    const frameCount = Math.max(1, endFrame - startFrame);
    const channelCount = Math.max(1, audioBuffer.numberOfChannels);
    const mono = new Float32Array(frameCount);
    for (let channel = 0; channel < channelCount; channel += 1) {
      const channelData = audioBuffer.getChannelData(channel);
      for (let frame = 0; frame < frameCount; frame += 1) {
        mono[frame] += (channelData[startFrame + frame] || 0) / channelCount;
      }
    }
    const blob = encodeMonoPcm16Wav(mono, sampleRate);
    return new File([blob], sanitizeWavFileName(file.name), { type: "audio/wav" });
  } finally {
    audioContext.close?.().catch?.(() => {});
  }
}

function buildSpeechRanges(bars, duration) {
  const ranges = [];
  let activeStart = null;
  bars.forEach((bar) => {
    if (bar.speech && activeStart === null) activeStart = bar.start;
    if (!bar.speech && activeStart !== null) {
      ranges.push({ start: activeStart, end: bar.start });
      activeStart = null;
    }
  });
  if (activeStart !== null) ranges.push({ start: activeStart, end: duration });
  const minGap = 0.35;
  const minDuration = 0.28;
  return ranges.reduce((merged, range) => {
    if (range.end - range.start < minDuration) return merged;
    const previous = merged[merged.length - 1];
    if (previous && range.start - previous.end <= minGap) {
      previous.end = range.end;
    } else {
      merged.push({ ...range });
    }
    return merged;
  }, []);
}

async function analyzeVoiceActivity(file) {
  const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextConstructor || !file?.arrayBuffer) return null;
  const audioContext = new AudioContextConstructor();
  try {
    const audioBuffer = await audioContext.decodeAudioData(await file.arrayBuffer());
    const duration = Number(audioBuffer.duration || 0);
    if (!duration || !audioBuffer.length) return null;
    const barCount = Math.min(140, Math.max(64, Math.round(duration * 4)));
    const channelCount = Math.max(1, audioBuffer.numberOfChannels);
    const samplesPerBar = Math.max(1, Math.floor(audioBuffer.length / barCount));
    const rmsValues = [];
    for (let barIndex = 0; barIndex < barCount; barIndex += 1) {
      const startFrame = barIndex * samplesPerBar;
      const endFrame = Math.min(audioBuffer.length, startFrame + samplesPerBar);
      let sum = 0;
      let count = 0;
      for (let channel = 0; channel < channelCount; channel += 1) {
        const data = audioBuffer.getChannelData(channel);
        for (let frame = startFrame; frame < endFrame; frame += 12) {
          const sample = data[frame] || 0;
          sum += sample * sample;
          count += 1;
        }
      }
      rmsValues.push(Math.sqrt(sum / Math.max(1, count)));
    }
    const maxRms = Math.max(...rmsValues, 0.000001);
    const sorted = [...rmsValues].sort((a, b) => a - b);
    const floor = sorted[Math.floor(sorted.length * 0.18)] || 0;
    const high = sorted[Math.floor(sorted.length * 0.82)] || maxRms;
    const dynamicThreshold = floor + (high - floor) * 0.24;
    const threshold = Math.min(
      Math.max(maxRms * 0.08, dynamicThreshold, 0.003),
      maxRms * 0.48
    );
    const secondsPerBar = duration / barCount;
    const bars = rmsValues.map((rms, index) => {
      const neighborRms = Math.max(rms, rmsValues[index - 1] || 0, rmsValues[index + 1] || 0);
      const speech = neighborRms >= threshold;
      return {
        level: Math.max(0.06, Math.min(1, rms / maxRms)),
        speech,
        start: index * secondsPerBar,
        end: Math.min(duration, (index + 1) * secondsPerBar),
      };
    });
    const speechRanges = buildSpeechRanges(bars, duration);
    return { duration, bars, speechRanges, threshold, analyzed: true };
  } finally {
    audioContext.close?.().catch?.(() => {});
  }
}

function getRecommendedSpeechWindow(profile, duration) {
  const fallbackEnd = duration > 0 ? Math.min(duration, 20) : 0;
  const fallback = { start: 0, end: fallbackEnd };
  const ranges = profile?.speechRanges || [];
  if (!ranges.length) return fallback;
  const longest = ranges.reduce((best, range) => (
    range.end - range.start > best.end - best.start ? range : best
  ), ranges[0]);
  const paddedStart = Math.max(0, longest.start - 0.15);
  const paddedEnd = Math.min(duration, longest.end + 0.15);
  if (paddedEnd - paddedStart > 20) return { start: paddedStart, end: Math.min(duration, paddedStart + 20) };
  return { start: paddedStart, end: paddedEnd };
}

function applyRecommendedSpeechWindow() {
  const draft = state.voiceClone.sampleDraft || {};
  const nextWindow = getRecommendedSpeechWindow(draft.speechProfile, Number(draft.duration || 0));
  state.voiceClone.sampleDraft = { ...draft, trimStart: nextWindow.start, trimEnd: nextWindow.end };
}

function updateVoiceCloneTrimReadout() {
  const draft = state.voiceClone.sampleDraft || {};
  const startLabel = document.querySelector("#voice-clone-trim-start-label");
  const endLabel = document.querySelector("#voice-clone-trim-end-label");
  const durationLabel = document.querySelector("#voice-clone-trim-duration-label");
  const trimButton = document.querySelector("#voice-clone-trim-button");
  const start = Number(draft.trimStart || 0);
  const end = Number(draft.trimEnd || 0);
  const duration = Math.max(0, end - start);
  if (startLabel) startLabel.textContent = formatMillis(start * 1000);
  if (endLabel) endLabel.textContent = formatMillis(end * 1000);
  if (durationLabel) {
    durationLabel.textContent = `片段 ${formatMillis(duration * 1000)}${duration && duration < 10 ? " · 建议 10 秒以上" : ""}`;
  }
  if (trimButton) trimButton.disabled = !(draft.duration > 0 && duration >= 3);
}

function updateVoiceCloneTrimDraft(inputId, value) {
  const draft = state.voiceClone.sampleDraft || {};
  const duration = Number(draft.duration || 0);
  if (!duration) return;
  let start = clampNumber(draft.trimStart || 0, 0, duration);
  let end = clampNumber(draft.trimEnd || duration, 0, duration);
  if (inputId === "voice-clone-trim-start") {
    start = clampNumber(value, 0, Math.max(0, end - 0.05));
  } else {
    end = clampNumber(value, Math.min(duration, start + 0.05), duration);
  }
  state.voiceClone.sampleDraft = { ...draft, trimStart: start, trimEnd: end };
  const startInput = document.querySelector("#voice-clone-trim-start");
  const endInput = document.querySelector("#voice-clone-trim-end");
  if (startInput) startInput.value = String(start);
  if (endInput) endInput.value = String(end);
  updateVoiceCloneTrimReadout();
}

async function trimVoiceCloneSampleDraft() {
  const draft = state.voiceClone.sampleDraft || {};
  const targetProjectId = String(draft.projectId || "");
  const sourceFile = draft.file;
  if (!draft.file) throw new Error("请先选择或拖入复刻样本");
  const start = Number(draft.trimStart || 0);
  const end = Number(draft.trimEnd || 0);
  if (end - start < 3) throw new Error("裁剪片段至少保留 3 秒清晰人声");
  const trimmedFile = await createTrimmedWavFile(draft.file, start, end);
  if (
    String(state.currentProjectId || "") !== targetProjectId
    || state.voiceClone.sampleDraft?.file !== sourceFile
  ) {
    throw new Error("项目或样本已切换，本次裁剪结果已取消。");
  }
  revokeObjectUrl(draft.trimmedObjectUrl);
  state.voiceClone.sampleDraft = {
    ...draft,
    trimmedFile,
    trimmedObjectUrl: URL.createObjectURL(trimmedFile),
    trimmedDuration: end - start,
  };
}

function resetVoiceCloneTrimmedFile() {
  const draft = state.voiceClone.sampleDraft || {};
  revokeObjectUrl(draft.trimmedObjectUrl);
  state.voiceClone.sampleDraft = {
    ...draft,
    trimmedFile: null,
    trimmedObjectUrl: "",
    trimmedDuration: 0,
  };
}

function getDropzoneByPosition(position) {
  const x = Number(position?.x);
  const y = Number(position?.y);
  if (Number.isFinite(x) && Number.isFinite(y)) {
    return getFileDropZone(document.elementFromPoint(x, y));
  }
  const activeDropzone = document.querySelector("[data-file-drop-target].is-dragover");
  if (activeDropzone && !isFileDropZoneDisabled(activeDropzone)) return activeDropzone;
  const dropzones = Array.from(document.querySelectorAll("[data-file-drop-target]"))
    .filter((dropzone) => !isFileDropZoneDisabled(dropzone));
  return dropzones.length === 1 ? dropzones[0] : null;
}

function getDisabledDropzoneByPosition(position) {
  const x = Number(position?.x);
  const y = Number(position?.y);
  if (Number.isFinite(x) && Number.isFinite(y)) {
    const dropzone = getAnyFileDropZone(document.elementFromPoint(x, y));
    return isFileDropZoneDisabled(dropzone) ? dropzone : null;
  }
  const activeDropzone = document.querySelector("[data-file-drop-target][aria-disabled=\"true\"].is-dragover");
  if (activeDropzone) return activeDropzone;
  const dropzones = Array.from(document.querySelectorAll("[data-file-drop-target]"));
  return dropzones.length === 1 && isFileDropZoneDisabled(dropzones[0]) ? dropzones[0] : null;
}

function clearDropzoneHover() {
  document.querySelectorAll("[data-file-drop-target].is-dragover").forEach((dropzone) => {
    dropzone.classList.remove("is-dragover");
  });
}

async function handleNativeFileDrop(payload = {}) {
  if (payload.phase === "leave") {
    clearDropzoneHover();
    return;
  }
  const disabledDropzone = getDisabledDropzoneByPosition(payload.position);
  if (disabledDropzone) {
    clearDropzoneHover();
    if (payload.phase !== "over") showDisabledFileDropzoneStatus(disabledDropzone);
    return;
  }
  const dropzone = getDropzoneByPosition(payload.position);
  if (payload.phase === "over") {
    clearDropzoneHover();
    dropzone?.classList.add("is-dragover");
    return;
  }
  const fileMetas = payload.files || [];
  if (!dropzone || !fileMetas.length) return;
  clearDropzoneHover();
  const inputId = dropzone.dataset.fileDropTarget || "";
  await applyFileToDropzone(inputId, inputId === "lower-third-input" ? fileMetas : fileMetas[0], "desktop-drop");
}

function bindNativeFileDrops() {
  if (nativeFileDropBound) return;
  nativeFileDropBound = true;
  if (typeof window.SpeakerDesktopBridge?.listenFileDrop !== "function") return;
  window.SpeakerDesktopBridge.listenFileDrop((payload) => {
    handleNativeFileDrop(payload).catch((error) => showToast(normalizeError(error).message, "danger"));
  }).catch((error) => {
    recordClientLog("native-file-drop", normalizeError(error).message).catch(() => {});
  });
}

function getAnyFileDropZone(target) {
  return target?.closest?.("[data-file-drop-target]") || null;
}

function isFileDropZoneDisabled(dropzone) {
  return dropzone?.getAttribute?.("aria-disabled") === "true";
}

function getFileDropZone(target) {
  const dropzone = getAnyFileDropZone(target);
  return isFileDropZoneDisabled(dropzone) ? null : dropzone;
}

function showDisabledFileDropzoneStatus(dropzone) {
  const message = dropzone?.dataset.fileDropTarget === "voice-clone-sample-input"
    ? "声音服务未连接，暂时不能选择或拖入复刻样本"
    : "该文件入口当前不可用";
  showToast(message, "warning");
}

function rejectDisabledFileDropzone(target, event) {
  const dropzone = getAnyFileDropZone(target);
  if (!isFileDropZoneDisabled(dropzone)) return false;
  event?.preventDefault?.();
  showDisabledFileDropzoneStatus(dropzone);
  return true;
}

function updateFileDropzoneLabel(inputId, fileName = "") {
  const dropzone = document.querySelector(`[data-file-drop-target="${inputId}"]`);
  const label = dropzone?.querySelector(".file-drop-name");
  if (label) label.textContent = fileName ? `已选择：${fileName}` : "点击选择文件，或把音频拖到这里";
}

function assignDroppedFile(input, file) {
  if (!input || !file) return;
  if (typeof DataTransfer !== "function") {
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return;
  }
  const dataTransfer = new DataTransfer();
  dataTransfer.items.add(file);
  input.files = dataTransfer.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

function handleDocumentDragOver(event) {
  const disabledDropzone = getAnyFileDropZone(event.target);
  if (isFileDropZoneDisabled(disabledDropzone)) {
    event.preventDefault();
    return;
  }
  const dropzone = getFileDropZone(event.target);
  if (!dropzone) return;
  event.preventDefault();
  dropzone.classList.add("is-dragover");
}

function handleDocumentDragLeave(event) {
  const dropzone = getFileDropZone(event.target);
  if (!dropzone) return;
  if (dropzone.contains(event.relatedTarget)) return;
  dropzone.classList.remove("is-dragover");
}

function handleDocumentDrop(event) {
  if (rejectDisabledFileDropzone(event.target, event)) return;
  const dropzone = getFileDropZone(event.target);
  if (!dropzone) return;
  event.preventDefault();
  dropzone.classList.remove("is-dragover");
  const file = event.dataTransfer?.files?.[0] || null;
  const inputId = dropzone.dataset.fileDropTarget || "";
  if (inputId === "lower-third-input" || resolveSubtitleBackplateRoleFromInput(inputId)) {
    showToast("这些素材要写入 Premiere XML，请使用按钮选择或桌面客户端原生拖拽，避免素材脱机。", "danger");
    return;
  }
  applyFileToDropzone(inputId, file, "browser-drop").catch((error) => showToast(normalizeError(error).message, "danger"));
}

function jumpCueSearch(delta = 1) {
  const matches = getCueSearchMatches();
  if (!matches.length) {
    state.cueSearchMatchIndex = 0;
    return null;
  }
  const current = Number(state.cueSearchMatchIndex || 0);
  const next = ((current + delta) % matches.length + matches.length) % matches.length;
  state.cueSearchMatchIndex = next;
  const match = matches[next];
  state.cuePage = Math.floor(match.arrayIndex / CUE_PREVIEW_PAGE_SIZE);
  return match;
}

async function handleDocumentClick(event) {
  const target = event.target;
  if (target.closest?.("[data-local-action]") && await globalThis.SpeakerLocalRecognition?.handleClick(target)) return;
  if (rejectDisabledFileDropzone(target, event)) return;
  const railToggle = target.closest("[data-toggle-rail]");
  if (railToggle) {
    setProjectRailOpen(document.body.dataset.railOpen !== "true");
    return;
  }
  if (target.closest("[data-close-rail]")) {
    setProjectRailOpen(false);
    return;
  }
  const projectSearchOption = target.closest("[data-project-switch]");
  if (projectSearchOption) {
    const input = document.querySelector("#current-project-select");
    closeProjectSearchResults();
    if (input) {
      input.value = projectSearchOption.dataset.projectSwitch || "";
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    return;
  }
  if (!target.closest(".project-search-shell")) {
    closeProjectSearchResults();
  }
  const projectEntryButton = target.closest("[data-project-enter]");
  if (projectEntryButton) {
    const selectedProjectId = projectEntryButton.dataset.projectEnter;
    const projectChanged = Boolean(state.currentProjectId && state.currentProjectId !== selectedProjectId);
    if (projectChanged) clearPendingTaskUploads();
    setCurrentProject(selectedProjectId);
    resetDisplayedJobState(getWorkflowKindForStep(state.activeStep));
    await Promise.all([
      selectJobForStep(state.activeStep).catch(() => {}),
      refreshProjectScripts().catch(() => []),
    ]);
    setProjectRailOpen(false);
    renderAll();
    elements.simpleWorkspace.scrollTop = 0;
    return;
  }
  const retryProjectsButton = target.closest("#retry-projects-button");
  if (retryProjectsButton) {
    await withUiTask(retryProjectsButton, "重试中...", async () => {
      await refreshProjects();
    });
    renderAll();
    return;
  }
  const stepButton = target.closest("[data-step]");
  if (stepButton) {
    if (!state.currentProjectId) {
      showToast("请先进入项目，再使用功能", "warning");
      renderAll();
      return;
    }
    state.activeStep = stepButton.dataset.step;
    if (state.activeStep === "cloneSamples") state.voiceClone.view = "library";
    if (state.activeStep === "voiceCloneRecords") state.voiceClone.view = "generate";
    if (TRANSCRIPT_PAGE_IDS.has(state.activeStep) || TASK_STEPS.some((step) => step.id === state.activeStep)) {
      await selectJobForStep(state.activeStep).catch((error) => {
        recordClientLog("select-job", normalizeError(error).message);
      });
    }
    renderAll();
    setProjectRailOpen(false);
    if (CLONE_PAGE_IDS.has(state.activeStep)) {
      await withUiTask(stepButton, "载入中...", async () => {
        await Promise.allSettled([refreshVoiceCloneData(), refreshServiceStatus()]);
        renderAll();
      });
    } else if (state.activeStep === "voiceprints") {
      await withUiTask(stepButton, "载入中...", async () => {
        await Promise.allSettled([refreshPeople(), refreshProjects()]);
        renderAll();
      });
    } else if (state.activeStep === "lexicon") {
      await withUiTask(stepButton, "载入中...", async () => {
        await Promise.allSettled([refreshProjects(), refreshLexiconData()]);
        renderAll();
      });
    } else if (state.activeStep === "tools" || state.activeStep === "history") {
      await withUiTask(stepButton, "载入中...", async () => {
        await Promise.allSettled([refreshJobs(), refreshServiceStatus()]);
        renderAll();
      });
    }
    elements.simpleWorkspace.scrollTop = 0;
    return;
  }

  const refreshButton = target.closest("#refresh-data-button, #refresh-jobs-button, #refresh-voiceprints-button, #refresh-status-button");
  if (refreshButton) {
    await withUiTask(refreshButton, "刷新中...", async () => {
      await Promise.allSettled([refreshProjects(), refreshJobs(), refreshPeople(), refreshServiceStatus()]);
      if (TRANSCRIPT_PAGE_IDS.has(state.activeStep) || TASK_STEPS.some((step) => step.id === state.activeStep)) {
        await selectJobForStep(state.activeStep).catch(() => {});
      }
      if (CLONE_PAGE_IDS.has(state.activeStep)) await refreshVoiceCloneData().catch(() => {});
      if (state.activeStep === "lexicon") await refreshLexiconData().catch(() => {});
      renderAll();
    });
    return;
  }

  const lexiconRefreshButton = target.closest("#lexicon-refresh-button, [data-lexicon-refresh]");
  if (lexiconRefreshButton) {
    await withUiTask(lexiconRefreshButton, "刷新中...", async () => {
      await refreshLexiconData();
      renderAll();
    });
    return;
  }

  const mediaPicker = target.closest("#media-picker-trigger");
  if (mediaPicker) {
    await withUiTask(mediaPicker, "选择中...", async () => {
      const result = await pickDesktopFiles("media");
      if (result?.files?.length) {
        setMediaDraftFromFile(result.files[0], "desktop");
      } else {
        document.querySelector("#video-file-input")?.click();
      }
    });
    return;
  }

  const taskScriptPicker = target.closest("#task-script-picker-trigger");
  if (taskScriptPicker) {
    document.querySelector("#task-script-file-input")?.click();
    return;
  }

  const transcriptPicker = target.closest("#transcript-picker-trigger");
  if (transcriptPicker) {
    await withUiTask(transcriptPicker, "选择中...", async () => {
      const result = await pickDesktopFiles("media");
      if (result?.files?.length) {
        setTranscriptDraftFromFile(result.files[0], "desktop");
      } else {
        document.querySelector("#transcript-file-input")?.click();
      }
    });
    return;
  }

  const scriptPicker = target.closest("#review-script-picker-trigger");
  if (scriptPicker) {
    document.querySelector("#review-script-file-input")?.click();
    return;
  }

  const lexiconScriptPicker = target.closest("#lexicon-script-picker-trigger");
  if (lexiconScriptPicker) {
    document.querySelector("#lexicon-script-input")?.click();
    return;
  }

  const clearScriptButton = target.closest("#clear-script-assist-button");
  if (clearScriptButton) {
    clearScriptAssistDraft();
    renderAll();
    return;
  }

  const toggleScriptAssistButton = target.closest("[data-script-assist-toggle]");
  if (toggleScriptAssistButton) {
    state.scriptAssistExpanded = !state.scriptAssistExpanded;
    renderAll();
    return;
  }

  const updateScriptContextButton = target.closest("#review-script-update-button");
  if (updateScriptContextButton) {
    await withUiTask(updateScriptContextButton, "合并并应用中...", async () => {
      const context = await updateCurrentJobScriptContext();
      const sourceCount = Number(context?.summary?.source_count || context?.source_names?.length || 1);
      const evidenceMode = String(state.jobDetail?.subtitle_strategy || "").toLowerCase() === "evidence";
      if (evidenceMode) {
        showToast(`已加载 ${sourceCount} 份台本；V2纠错只在创建任务时自动执行`, "neutral");
      } else {
        const count = await applyScriptContextCorrections();
        showToast(`已加载 ${sourceCount} 份台本，本次修正 ${count} 条唱词`, count ? "success" : "neutral");
      }
      renderAll();
    });
    return;
  }

  const applyScriptCorrectionsButton = target.closest("[data-apply-script-corrections]");
  if (applyScriptCorrectionsButton) {
    await withUiTask(applyScriptCorrectionsButton, "纠错中...", async () => {
      const count = await applyScriptContextCorrections();
      showToast(count ? `已按台本修正 ${count} 条唱词` : "未发现可自动修正的台本错字", count ? "success" : "neutral");
      renderAll();
    });
    return;
  }

  const syncButton = target.closest("#sync-job-button");
  if (syncButton) {
    await withUiTask(syncButton, "同步中...", async () => {
      const completedStep = TRANSCRIPT_PAGE_IDS.has(state.activeStep) ? "transcript" : "review";
      await syncJobUntilDone({ completedStep });
      showToast(completedStep === "transcript" ? "扒词已完成，可以在本页试听、改名并导出" : "任务已完成，可以开始校对说话人", "success");
      renderAll();
    });
    return;
  }

  const loadJobButton = target.closest("[data-job-load]");
  if (loadJobButton) {
    await withUiTask(loadJobButton, "载入中...", async () => {
      const detail = await loadJob(loadJobButton.dataset.jobLoad);
      if (!detail) return;
      state.activeStep = deriveActiveStep();
      showToast("已载入任务", "success");
      renderAll();
    });
    return;
  }

  const abortJobButton = target.closest("[data-job-abort]");
  if (abortJobButton) {
    const jobId = abortJobButton.dataset.jobAbort;
    if (!requireSecondClick(`abort:${jobId}`, "再次点击中止这个任务")) return;
    if (state.currentJobId === jobId) requestSyncAbort();
    await withUiTask(abortJobButton, "中止中...", async () => {
      await request(`/transcriptions/jobs/${encodeURIComponent(jobId)}/abort`, { method: "POST" });
      await refreshJobs();
      if (state.currentJobId === jobId) await loadJob(jobId).catch(() => {});
      showToast("任务已中止", "neutral");
      renderAll();
    });
    return;
  }

  const deleteJobButton = target.closest("[data-job-delete]");
  if (deleteJobButton) {
    const jobId = deleteJobButton.dataset.jobDelete;
    if (!requireSecondClick(`delete:${jobId}`, "再次点击删除这个任务")) return;
    await withUiTask(deleteJobButton, "删除中...", async () => {
      await request(`/transcriptions/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
      if (state.loadJobTargetId === jobId) invalidateLoadJobRequests();
      if (state.currentJobId === jobId) {
        setCurrentJobId("", state.jobDetail?.kind || getWorkflowKindForStep(state.activeStep));
        state.jobDetail = null;
        state.cues = [];
        state.cuePage = 0;
        state.cueSpeakerDrafts = {};
        state.cueTextDrafts = {};
        state.cueDeletedDrafts = {};
        state.speakers = [];
        state.activeStep = "upload";
      }
      await refreshJobs();
      renderAll();
    });
    return;
  }

  const purgeProjectButton = target.closest("#purge-project-button");
  if (purgeProjectButton) {
    const projectName = state.currentProjectName;
    if (!requireSecondClick("purge-project", `再次点击永久清理「${projectName}」`)) return;
    await withUiTask(purgeProjectButton, "清理中...", async () => {
      const deletedName = state.currentProjectName;
      await purgeCurrentProject();
      showToast(deletedName ? `已永久清理项目：${deletedName}` : "项目已永久清理", "success");
      renderAll();
    });
    return;
  }

  const speakerSaveButton = target.closest("#speaker-save-button");
  if (speakerSaveButton) {
    await withUiTask(speakerSaveButton, "保存中...", async () => {
      const learning = await saveSpeakerMappings();
      showToast(formatVoiceprintLearningToast("说话人姓名已保存", learning), voiceprintLearningToastTone(learning));
      renderAll();
    });
    return;
  }

  const subtitleSpeakerSaveButton = target.closest("#subtitle-speaker-save-button");
  if (subtitleSpeakerSaveButton) {
    await withUiTask(subtitleSpeakerSaveButton, "保存中...", async () => {
      const learning = await saveSubtitleSpeakerOverrides();
      showToast(formatVoiceprintLearningToast("单条说话人已保存", learning), voiceprintLearningToastTone(learning));
      renderAll();
    });
    return;
  }

  const subtitleCombinedSaveButton = target.closest("#subtitle-combined-save-button");
  if (subtitleCombinedSaveButton) {
    await withUiTask(subtitleCombinedSaveButton, "保存中...", async () => {
      const result = await saveCombinedCueEdits();
      const suffix = result.savedTextCount ? `，唱词 ${result.savedTextCount} 条` : "";
      const message = formatVoiceprintLearningToast(`全部逐条修改已保存${suffix}`, result.voiceprintLearning);
      showToast(message, voiceprintLearningToastTone(result.voiceprintLearning));
      renderAll();
    });
    return;
  }

  const subtitleTextSaveButton = target.closest("#subtitle-text-save-button");
  if (subtitleTextSaveButton) {
    await withUiTask(subtitleTextSaveButton, "保存中...", async () => {
      const count = await saveSubtitleTextOverrides();
      showToast(`已保存 ${count} 条唱词修改`, "success");
      renderAll();
    });
    return;
  }

  const batchDeleteFillersButton = target.closest("#batch-delete-fillers-button");
  if (batchDeleteFillersButton) {
    const fillerCount = getStandaloneFillerCueCount();
    if (!requireSecondClick("batch-delete-fillers", fillerCount ? `再次点击清理全片 ${fillerCount} 条语气词` : "再次点击检查全片语气词")) return;
    await withUiTask(batchDeleteFillersButton, "清理中...", async () => {
      const count = await deleteStandaloneFillerCues();
      showToast(`已清理全片 ${count} 条语气词`, "success");
      renderAll();
    });
    return;
  }

  const cueTextDeleteButton = target.closest("[data-cue-text-delete]");
  if (cueTextDeleteButton) {
    toggleCueTextDelete(cueTextDeleteButton.dataset.cueTextDelete);
    renderAll();
    return;
  }

  const cueLexiconButton = target.closest("[data-cue-lexicon-term]");
  if (cueLexiconButton) {
    const cueIndex = String(cueLexiconButton.dataset.cueLexiconTerm || "");
    const row = cueLexiconButton.closest(".cue-combined-row");
    const inputValue = row?.querySelector("[data-cue-text-input]")?.value;
    const cue = (state.cues || []).find((item) => String(item.index || "") === cueIndex);
    const text = String(inputValue ?? state.cueTextDrafts?.[cueIndex] ?? cue?.clean_text ?? "").trim();
    await withUiTask(cueLexiconButton, "加入中...", async () => {
      await createLexiconTermFromText(text, "strong");
      showToast("已加入项目词库，重新构建字幕后生效", "success");
    });
    return;
  }

  const lexiconPromoteButton = target.closest("[data-lexicon-promote]");
  if (lexiconPromoteButton) {
    const row = lexiconPromoteButton.closest(".lexicon-item");
    const protection = row?.querySelector("[data-lexicon-protection]")?.value || "strong";
    await withUiTask(lexiconPromoteButton, "生效中...", async () => {
      await promoteLexiconCandidate(lexiconPromoteButton.dataset.lexiconPromote, protection);
      showToast("候选词已生效", "success");
      renderAll();
    });
    return;
  }

  const lexiconRejectButton = target.closest("[data-lexicon-reject]");
  if (lexiconRejectButton) {
    await withUiTask(lexiconRejectButton, "拒绝中...", async () => {
      await rejectLexiconCandidate(lexiconRejectButton.dataset.lexiconReject);
      showToast("候选词已拒绝", "neutral");
      renderAll();
    });
    return;
  }

  const lexiconDisableButton = target.closest("[data-lexicon-disable]");
  if (lexiconDisableButton) {
    const termId = lexiconDisableButton.dataset.lexiconDisable;
    if (!requireSecondClick(`disable-lexicon:${termId}`, "再次点击在本项目禁用这个词条")) return;
    await withUiTask(lexiconDisableButton, "禁用中...", async () => {
      await disableLexiconTermForProject(termId);
      showToast("词条已在本项目禁用", "success");
      renderAll();
    });
    return;
  }

  const lexiconRebuildButton = target.closest("#lexicon-rebuild-bundle-button");
  if (lexiconRebuildButton) {
    await withUiTask(lexiconRebuildButton, "重建中...", async () => {
      await rebuildJobRuntimeLexiconBundle();
      showToast("当前任务词库快照已重建", "success");
      renderAll();
    });
    return;
  }

  const cuePageButton = target.closest("[data-cue-page-delta]");
  if (cuePageButton) {
    const delta = Number(cuePageButton.dataset.cuePageDelta || 0);
    const pageCount = Math.max(1, Math.ceil((state.cues.length || 0) / CUE_PREVIEW_PAGE_SIZE));
    state.cuePage = Math.min(Math.max(0, (state.cuePage || 0) + delta), pageCount - 1);
    renderAll();
    return;
  }

  const cueSearchButton = target.closest("[data-cue-search-delta]");
  if (cueSearchButton) {
    jumpCueSearch(Number(cueSearchButton.dataset.cueSearchDelta || 0));
    renderAll();
    window.requestAnimationFrame(() => {
      document.querySelector("[data-current-cue-match='true']")?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
    return;
  }

  const cueButton = target.closest("[data-cue-start]");
  if (cueButton) {
    if (cueButton.hasAttribute?.("data-subtitle-quality-seek")) {
      if (!globalThis.SpeakerSubtitleQuality?.isCurrentTarget(cueButton.dataset, { detail: state.jobDetail, jobId: state.currentJobId, projectId: state.currentProjectId })) return;
      const cueIndex = Number(cueButton.dataset.qcCueIndex);
      if (Number.isInteger(cueIndex) && cueIndex > 0) document.querySelector(`.cue-combined-row [data-cue-index="${cueIndex}"]`)?.closest(".cue-combined-row")?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    const player = document.querySelector("#source-media-player");
    const startMs = Number(cueButton.dataset.cueStart || 0);
    if (player && Number.isFinite(startMs)) {
      player.currentTime = Math.max(0, startMs / 1000);
      await player.play().catch(() => {});
    }
    return;
  }

  const lowerThirdPicker = target.closest("#lower-third-picker-trigger");
  if (lowerThirdPicker) {
    await withUiTask(lowerThirdPicker, "选择中...", async () => {
      const result = await pickDesktopFiles("lowerThirds");
      if (result?.files?.length) {
        state.pickedLowerThirdFiles = result.files;
        state.pickedLowerThirdSource = "desktop";
        state.pickedLowerThirdProjectId = String(state.currentProjectId || "");
        state.pickedLowerThirdJobId = String(state.currentJobId || "");
        const fallbackInput = document.querySelector("#lower-third-input");
        if (fallbackInput) fallbackInput.value = "";
        renderAll();
      } else {
        document.querySelector("#lower-third-input")?.click();
      }
    });
    return;
  }

  const loadProjectLowerThirdsButton = target.closest("#load-project-lower-thirds-button");
  if (loadProjectLowerThirdsButton) {
    await withUiTask(loadProjectLowerThirdsButton, "载入中...", async () => {
      await loadProjectLowerThirds();
      showToast("已从项目库载入人名条", "success");
      renderAll();
    });
    return;
  }

  const subtitleBackplatePicker = target.closest("[data-subtitle-backplate-picker-role], #subtitle-backplate-picker-trigger");
  if (subtitleBackplatePicker) {
    const role = normalizeSubtitleBackplateRole(subtitleBackplatePicker.dataset.subtitleBackplatePickerRole || "with_speaker");
    const inputId = `subtitle-backplate-${role}-input`;
    const targetProjectId = String(state.currentProjectId || "");
    const targetJobId = String(state.currentJobId || "");
    await withUiTask(subtitleBackplatePicker, "选择中...", async () => {
      const result = await pickDesktopFiles("lowerThirds");
      if (String(state.currentProjectId || "") !== targetProjectId || String(state.currentJobId || "") !== targetJobId) {
        throw new Error("项目或任务已切换，本次唱词条底图选择已取消。");
      }
      if (result?.files?.length) {
        state.pickedSubtitleBackplateFiles = {
          ...(state.pickedSubtitleBackplateFiles || {}),
          [role]: result.files[0],
        };
        state.pickedSubtitleBackplateSources = {
          ...(state.pickedSubtitleBackplateSources || {}),
          [role]: "desktop",
        };
        state.pickedSubtitleBackplateProjectIds = {
          ...(state.pickedSubtitleBackplateProjectIds || {}),
          [role]: targetProjectId,
        };
        state.pickedSubtitleBackplateJobIds = {
          ...(state.pickedSubtitleBackplateJobIds || {}),
          [role]: targetJobId,
        };
        if (role === "with_speaker") {
          state.pickedSubtitleBackplateFile = result.files[0];
          state.pickedSubtitleBackplateSource = "desktop";
        }
        const fallbackInput = document.querySelector(`#${inputId}`);
        if (fallbackInput) fallbackInput.value = "";
        renderAll();
      } else {
        document.querySelector(`#${inputId}`)?.click();
      }
    });
    return;
  }

  const deleteSubtitleBackplateButton = target.closest("[data-subtitle-backplate-delete-role], #delete-subtitle-backplate-button");
  if (deleteSubtitleBackplateButton) {
    const role = normalizeSubtitleBackplateRole(deleteSubtitleBackplateButton.dataset.subtitleBackplateDeleteRole || "with_speaker");
    const label = role === "without_speaker" ? "无人名唱词条底图" : "有说话人唱词条底图";
    if (!requireSecondClick(`delete-subtitle-backplate:${role}`, `再次点击删除${label}`)) return;
    await withUiTask(deleteSubtitleBackplateButton, "删除中...", async () => {
      await deleteSubtitleBackplate(role);
      showToast("唱词条底图已删除", "success");
      renderAll();
    });
    return;
  }

  const loadProjectSubtitleBackplatesButton = target.closest("#load-project-subtitle-backplates-button");
  if (loadProjectSubtitleBackplatesButton) {
    await withUiTask(loadProjectSubtitleBackplatesButton, "载入中...", async () => {
      await loadProjectSubtitleBackplates();
      showToast("已从项目库载入唱词条底图", "success");
      renderAll();
    });
    return;
  }

  const downloadButton = target.closest("[data-download-path]");
  if (downloadButton) {
    await withUiTask(downloadButton, "下载中...", async () => {
      await downloadBinaryFile(downloadButton.dataset.downloadPath, downloadButton.dataset.downloadName || "speaker-download");
    });
    return;
  }

  const deleteVoiceprintSampleButton = target.closest("[data-voiceprint-sample-delete]");
  if (deleteVoiceprintSampleButton) {
    const sampleId = deleteVoiceprintSampleButton.dataset.voiceprintSampleDelete;
    const sampleName = deleteVoiceprintSampleButton.dataset.voiceprintSampleName || "这个样本";
    if (!requireSecondClick(`delete-sample:${sampleId}`, `再次点击删除样本：${sampleName}`)) return;
    await withUiTask(deleteVoiceprintSampleButton, "删除中...", async () => {
      await deleteVoiceSample(sampleId);
      showToast("声纹样本已删除", "success");
      renderAll();
    });
    return;
  }

  const confirmVoiceprintSampleButton = target.closest("[data-voiceprint-sample-confirm]");
  if (confirmVoiceprintSampleButton) {
    const sampleId = confirmVoiceprintSampleButton.dataset.voiceprintSampleConfirm;
    if (isVoiceAssetExpired(state.people.flatMap((person) => person.samples || []).find((sample) => sample.id === sampleId))) {
      renderAll();
      showToast("声纹已到期，仅保留72小时。请重新上传。", "warning");
      return;
    }
    await withUiTask(confirmVoiceprintSampleButton, "确认中...", async () => {
      await confirmVoiceSample(sampleId);
      showToast("候选声纹已确认入库", "success");
      renderAll();
    });
    return;
  }

  const deleteVoiceprintPersonButton = target.closest("[data-voiceprint-person-delete]");
  if (deleteVoiceprintPersonButton) {
    const personId = deleteVoiceprintPersonButton.dataset.voiceprintPersonDelete;
    const personName = deleteVoiceprintPersonButton.dataset.voiceprintPersonName || "这个人物";
    if (!requireSecondClick(`delete-person:${personId}`, `再次点击删除人物及其样本：${personName}`)) return;
    await withUiTask(deleteVoiceprintPersonButton, "删除中...", async () => {
      await deleteVoicePerson(personId);
      showToast("人物和声纹样本已删除", "success");
      renderAll();
    });
    return;
  }

  const personSelectButton = target.closest("[data-person-select]");
  if (personSelectButton) {
    state.voiceClone.personId = personSelectButton.dataset.personSelect;
    renderAll();
    return;
  }

  const voiceCloneViewButton = target.closest("[data-voice-clone-view]");
  if (voiceCloneViewButton) {
    const view = voiceCloneViewButton.dataset.voiceCloneView;
    if (view === "generate" || view === "library") state.voiceClone.view = view;
    renderAll();
    return;
  }

  const loadMoreCloneButton = target.closest("#voice-clone-load-more-button");
  if (loadMoreCloneButton) {
    if (state.voiceClone.library.loading || !state.voiceClone.library.nextCursor) return;
    await withUiTask(loadMoreCloneButton, "载入中...", async () => {
      await refreshVoiceCloneLibrary({ reset: false, allowAutoSelect: false });
      renderAll();
    });
    return;
  }

  const voiceCloneCancelModelButton = target.closest("#voice-clone-cancel-model-button");
  if (voiceCloneCancelModelButton) {
    state.voiceClone.pendingModelId = "";
    renderAll();
    return;
  }

  const voiceCloneCreateModelButton = target.closest("#voice-clone-create-model-button");
  if (voiceCloneCreateModelButton) {
    const profile = state.voiceClone.profiles.find((item) => item.id === state.voiceClone.profileId);
    if (refuseExpiredVoiceAsset(profile)) return;
    if (!(profile?.source_clone_sample_id || profile?.source_sample_id) || !selectVoiceCloneModel(state.voiceClone.pendingModelId)) return;
    state.voiceClone.sampleId = profile.source_clone_sample_id || profile.source_sample_id;
    state.voiceClone.displayName = profile.identity_display_name || profile.voice_name;
    await runVoiceCloneCreation(voiceCloneCreateModelButton, (shouldApply) => createVoiceCloneProfile({ shouldApply, sourceSampleId: profile.source_clone_sample_id ? "" : profile.source_sample_id }));
    return;
  }

  const voiceCloneNewButton = target.closest("#voice-clone-new-button");
  if (voiceCloneNewButton) {
    if (!isVoiceCloneServiceAvailable()) {
      showToast("声音服务未连接，暂时不能创建音色", "warning");
      return;
    }
    if (state.voiceClone.busy) return;
    voiceCloneAddFlowOpenToken += 1;
    state.voiceClone.sampleId = "";
    state.voiceClone.error = "";
    state.voiceClone.createDrawer = { ...state.voiceClone.createDrawer, open: true };
    state.voiceClone.addFlow = {
      open: true,
      stage: "pick",
      matchType: "",
      matchScore: null,
      matchedIdentity: null,
      sampleSlotsRemaining: null,
      existingSamples: [],
      fileLabel: "",
      error: "",
    };
    renderAll();
    focusVoiceCloneCreationDrawer();
    return;
  }

  const voiceCloneServiceRetryButton = target.closest("#voice-clone-service-retry-button");
  if (voiceCloneServiceRetryButton) {
    await withUiTask(voiceCloneServiceRetryButton, "连接中...", async () => {
      await refreshServiceStatus();
      if (isVoiceCloneServiceAvailable()) {
        await refreshVoiceCloneData();
      }
    });
    renderAll();
    return;
  }

  const voiceCloneDrawerCloseButton = target.closest("#voice-clone-drawer-close-button");
  if (voiceCloneDrawerCloseButton) {
    closeVoiceCloneCreationDrawer();
    return;
  }

  const openCloneSamplesButton = target.closest("#voice-clone-open-samples-button");
  if (openCloneSamplesButton) {
    await withUiTask(openCloneSamplesButton, "载入中...", async () => {
      await refreshVoiceCloneSamplePage({ reset: true });
      renderAll();
    });
    return;
  }

  const loadMoreCloneSamplesButton = target.closest("#voice-clone-load-more-samples-button");
  if (loadMoreCloneSamplesButton) {
    await withUiTask(loadMoreCloneSamplesButton, "载入中...", async () => {
      await refreshVoiceCloneSamplePage({ reset: false });
      renderAll();
    });
    return;
  }

  const voiceCloneSamplePreviewButton = target.closest("[data-voice-clone-sample-preview]");
  if (voiceCloneSamplePreviewButton) {
    const sampleId = voiceCloneSamplePreviewButton.dataset.voiceCloneSamplePreview || "";
    const sample = state.voiceClone.samples.find((item) => item.id === sampleId);
    if (refuseExpiredVoiceAsset(sample)) return;
    state.voiceClone.sharedPlayer = {
      sampleId,
      expires_at: getVoiceAssetExpiresAt(sample)?.toISOString() || null,
      label: sample?.display_name || sample?.original_filename || "",
      url: sampleId
        ? buildApiV2Url(buildProjectVoiceClonePath(
          state.currentProjectId,
          `/samples/${encodeURIComponent(sampleId)}/audio`,
        ))
        : "",
    };
    renderAll();
    document.querySelector("#voice-clone-shared-sample-player")?.play().catch(() => {});
    return;
  }

  const voiceProfileSourcePreviewButton = target.closest("[data-voice-profile-source-preview]");
  if (voiceProfileSourcePreviewButton) {
    const expiresAt = voiceProfileSourcePreviewButton.dataset.voiceExpiresAt;
    if (refuseExpiredVoiceAsset({ expires_at: expiresAt })) return;
    const sampleId = voiceProfileSourcePreviewButton.dataset.voiceProfileSourcePreview || "";
    const sourceApi = voiceProfileSourcePreviewButton.dataset.voiceProfileSourceApi || "v2";
    const sourcePath = voiceProfileSourcePreviewButton.dataset.voiceProfileSourcePath || "";
    state.voiceClone.sharedPlayer = {
      sampleId,
      expires_at: expiresAt,
      label: voiceProfileSourcePreviewButton.dataset.voiceProfileSourceLabel || "来源样本",
      url: sampleId
        ? sourceApi === "v1" ? buildApiUrl(sourcePath) : buildApiV2Url(sourcePath)
        : "",
    };
    renderAll();
    document.querySelector("#voice-clone-shared-sample-player")?.play().catch(() => {});
    return;
  }

  const voiceCloneSampleSelectButton = target.closest("[data-voice-clone-sample-select]");
  if (voiceCloneSampleSelectButton) {
    if (refuseExpiredVoiceAsset(state.voiceClone.samples.find((sample) => sample.id === voiceCloneSampleSelectButton.dataset.voiceCloneSampleSelect))) return;
    state.voiceClone.sampleId = voiceCloneSampleSelectButton.dataset.voiceCloneSampleSelect;
    const selectedSample = state.voiceClone.samples.find((sample) => sample.id === state.voiceClone.sampleId);
    if (selectedSample?.display_name) state.voiceClone.displayName = selectedSample.display_name;
    renderAll();
    return;
  }

  const voiceCloneSampleDeleteButton = target.closest("[data-voice-clone-sample-delete]");
  if (voiceCloneSampleDeleteButton) {
    const sampleId = voiceCloneSampleDeleteButton.dataset.voiceCloneSampleDelete;
    const sampleName = voiceCloneSampleDeleteButton.dataset.voiceCloneSampleName || "这个复刻样本";
    if (!requireSecondClick(`delete-clone-sample:${sampleId}`, `再次点击删除复刻样本：${sampleName}`)) return;
    await withUiTask(voiceCloneSampleDeleteButton, "删除中...", async () => {
      await deleteVoiceCloneSample(sampleId);
      showToast("复刻样本已删除", "success");
      renderAll();
    });
    return;
  }

  const voiceCloneRefreshButton = target.closest("#voice-clone-refresh-button");
  if (voiceCloneRefreshButton) {
    await withUiTask(voiceCloneRefreshButton, "刷新中...", async () => {
      await refreshVoiceCloneData();
      renderAll();
    });
    return;
  }

  const voiceInstructionExampleButton = target.closest("[data-voice-instruction-example]");
  if (voiceInstructionExampleButton) {
    state.voiceClone.voiceInstruction = voiceInstructionExampleButton.dataset.voiceInstructionExample || "";
    const input = document.querySelector("#voice-clone-instruction-input");
    if (input) input.value = state.voiceClone.voiceInstruction;
    updateVoiceCloneGenerationControls();
    return;
  }

  const voiceCloneCreateButton = target.closest("#voice-clone-create-button");
  if (voiceCloneCreateButton) {
    await runVoiceCloneCreation(voiceCloneCreateButton, (shouldApply) => createVoiceCloneProfile({ shouldApply, sourceSampleId: state.voiceClone.addFlow.sourceSampleId || "" }));
    return;
  }

  const voiceClonePreviewButton = target.closest("#voice-clone-preview-button");
  if (voiceClonePreviewButton) {
    await generateVoiceCloneAudio(voiceClonePreviewButton);
    return;
  }

  const recordRetryButton = target.closest("[data-voice-record-retry]");
  if (recordRetryButton) {
    const record = state.voiceClone.records.find((item) => item.id === recordRetryButton.dataset.voiceRecordRetry);
    if (!record || state.voiceClone.busy) return;
    if (refuseExpiredVoiceAsset(record)) return;
    state.voiceClone.previewText = record.text || record.input_text || "";
    state.voiceClone.voiceInstruction = record.instruction || "";
    state.voiceClone.pendingModelId = "";
    state.voiceClone.view = "generate";
    if (record.voice_clone_profile_id || record.profile_id) state.voiceClone.profileId = record.voice_clone_profile_id || record.profile_id;
    renderAll();
    await generateVoiceCloneAudio(recordRetryButton);
    return;
  }

  const recreateProfileButton = target.closest("[data-voice-profile-recreate]");
  if (recreateProfileButton) {
    openExistingVoiceCloneCreation({ profileId: recreateProfileButton.dataset.voiceProfileRecreate });
    return;
  }

  const createFromSampleButton = target.closest("[data-voice-clone-sample-create]");
  if (createFromSampleButton) {
    openExistingVoiceCloneCreation({ sampleId: createFromSampleButton.dataset.voiceCloneSampleCreate, groupKey: createFromSampleButton.dataset.voiceGroupKey });
    return;
  }

  const voiceGroupProfilesButton = target.closest("[data-voice-group-profiles]");
  if (voiceGroupProfilesButton) {
    const groupKey = voiceGroupProfilesButton.dataset.voiceGroupProfiles || "";
    const identityKey = voiceGroupProfilesButton.dataset.voiceReturnIdentity
      || voiceGroupProfilesButton.dataset.voiceIdentityDetailsTrigger
      || groupKey;
    if (voiceGroupProfilesButton.dataset.closeVoiceDrawer === "true") {
      voiceCloneAddFlowOpenToken += 1;
      state.voiceClone.createDrawer = { ...state.voiceClone.createDrawer, open: false };
      state.voiceClone.addFlow = { ...state.voiceClone.addFlow, open: false };
    }
    if (state.voiceClone.library.expandedGroupKey === groupKey) {
      closeVoiceIdentityDetails(identityKey);
      return;
    }
    const detailsOpenToken = ++voiceIdentityDetailsOpenToken;
    const isStillOpen = () => (
      voiceIdentityDetailsOpenToken === detailsOpenToken
      && state.voiceClone.library.expandedGroupKey === groupKey
    );
    state.voiceClone.library.expandedGroupKey = groupKey;
    renderAll();
    focusVoiceIdentityDetailsDialog();
    await withUiTask(voiceGroupProfilesButton, "载入中...", async () => {
      await Promise.all([
        loadVoiceCloneGroupProfiles(groupKey, { shouldApply: isStillOpen }),
        loadVoiceCloneGroupSamples(groupKey, { shouldApply: isStillOpen }),
      ]);
      if (!isStillOpen()) return;
      renderAll();
    });
    if (!isStillOpen()) return;
    focusVoiceIdentityDetailsDialog();
    return;
  }

  const profileButton = target.closest("[data-voice-profile]");
  if (profileButton) {
    const shouldCloseDrawer = profileButton.dataset.closeVoiceDrawer === "true";
    const profileId = profileButton.dataset.voiceProfile;
    const loadedProfile = Object.values(state.voiceClone.library.profilesByGroup || {})
      .flat()
      .find((profile) => profile.id === profileId);
    const profile = loadedProfile || state.voiceClone.profiles.find((item) => item.id === profileId);
    if (refuseExpiredVoiceAsset(profile)) return;
    if (!globalThis.SpeakerVoiceCloneLibrary?.isSelectableProfile(profile)) {
      showToast("该音色当前不可用，请选择可用音色。", "warning");
      return;
    }
    if (state.voiceClone.busy) return;
    const targetProjectId = String(state.currentProjectId || "");
    const requestGeneration = state.voiceClone.requestGeneration;
    const drawerToken = voiceCloneAddFlowOpenToken;
    state.voiceClone.profileId = profileId;
    state.voiceClone.pendingModelId = "";
    state.voiceClone.view = "generate";
    if (
      loadedProfile
      && !state.voiceClone.profiles.some((profile) => profile.id === loadedProfile.id)
    ) {
      state.voiceClone.profiles.push(loadedProfile);
    }
    await withUiTask(profileButton, "载入中...", async () => {
      await refreshVoiceCloneRecords({ projectId: targetProjectId, profileId, requestGeneration });
      if (String(state.currentProjectId || "") !== targetProjectId
        || state.voiceClone.requestGeneration !== requestGeneration
        || state.voiceClone.profileId !== profileId
        || voiceCloneAddFlowOpenToken !== drawerToken) return;
      if (shouldCloseDrawer) closeVoiceCloneCreationDrawer();
      else renderAll();
    });
    return;
  }

  const profileDeleteButton = target.closest("[data-voice-profile-delete]");
  if (profileDeleteButton) {
    const profileId = profileDeleteButton.dataset.voiceProfileDelete;
    const profileName = profileDeleteButton.dataset.voiceProfileName || "这个音色";
    if (!requireSecondClick(`delete-clone-profile:${profileId}`, `再次点击删除音色：${profileName}`)) return;
    await withUiTask(profileDeleteButton, "删除中...", async () => {
      await deleteVoiceCloneProfile(profileId);
      showToast("音色已删除", "success");
      renderAll();
    });
    return;
  }

  const recordDownloadButton = target.closest("[data-voice-record-download]");
  if (recordDownloadButton) {
    if (state.voiceClone.busy) return;
    const recordId = recordDownloadButton.dataset.voiceRecordDownload;
    const record = state.voiceClone.records.find((item) => item.id === recordId);
    if (!record || record.status !== "ready") return;
    if (refuseExpiredVoiceAsset(record)) return;
    const targetProjectId = String(state.currentProjectId || "");
    const busyToken = ++voiceCloneBusyToken;
    state.voiceClone.busy = "download";
    try {
      await withUiTask(recordDownloadButton, "下载中...", async () => {
      const downloaded = await downloadBinaryFile(
        buildApiV2Url(buildProjectVoiceClonePath(targetProjectId, `/synthesis-records/${encodeURIComponent(recordId)}/audio`)),
        buildVoiceRecordFileName(record || { id: recordId }),
      );
      if (!downloaded) return;
      // shortcut: browser downloads cannot confirm disk writes; add acknowledgement if web clients need automatic release.
      const savedLocally = window.SpeakerDesktopBridge?.isAvailable?.()
        && typeof window.SpeakerDesktopBridge.pickSaveFile === "function"
        && typeof window.SpeakerDesktopBridge.writeFile === "function";
      if (state.serviceStatus.voiceClone?.discard_voice_after_download && savedLocally) {
        try {
          await releaseVoiceCloneAfterDownload(record, targetProjectId);
        } catch (_error) {
          showToast("音频已保存，本次资源清理暂未确认；再次下载可重试清理。", "warning");
          return;
        }
      }
      showToast("音频已下载", "success");
      renderAll();
      });
    } finally {
      if (String(state.currentProjectId || "") === targetProjectId && voiceCloneBusyToken === busyToken) {
        state.voiceClone.busy = "";
        renderAll();
      }
    }
    return;
  }

  const recordDeleteButton = target.closest("[data-voice-record-delete]");
  if (recordDeleteButton) {
    const recordId = recordDeleteButton.dataset.voiceRecordDelete;
    if (!requireSecondClick(`delete-voice-record:${recordId}`, "再次点击删除这条生成音频")) return;
    await withUiTask(recordDeleteButton, "删除中...", async () => {
      await deleteVoiceCloneRecord(recordId);
      showToast("生成音频已删除", "success");
      renderAll();
    });
    return;
  }

  const voiceCloneTrimButton = target.closest("#voice-clone-trim-button");
  if (voiceCloneTrimButton) {
    await withUiTask(voiceCloneTrimButton, "裁剪中...", async () => {
      await trimVoiceCloneSampleDraft();
      showToast("已裁剪，上传时会使用裁剪后的 WAV", "success");
      renderAll();
    });
    return;
  }

  const voiceCloneTrimResetButton = target.closest("#voice-clone-trim-reset-button");
  if (voiceCloneTrimResetButton) {
    resetVoiceCloneTrimmedFile();
    showToast("已恢复为原始样本", "neutral");
    renderAll();
    return;
  }

  const voiceCloneUseSpeechButton = target.closest("#voice-clone-use-speech-window");
  if (voiceCloneUseSpeechButton) {
    applyRecommendedSpeechWindow();
    showToast("已定位到最长有声片段", "success");
    renderAll();
    return;
  }

  const pickXmlButton = target.closest("#pick-xml-repair-file-button");
  if (pickXmlButton) {
    await withUiTask(pickXmlButton, "选择中...", async () => {
      const result = await pickDesktopFiles("xml");
      if (result?.files?.length) {
        await loadXmlRepairFile(result.files[0]);
      } else {
        document.querySelector("#xml-repair-file-input")?.click();
      }
      renderAll();
    });
    return;
  }

  const pickXmlImagesButton = target.closest("#pick-xml-repair-images-button");
  if (pickXmlImagesButton) {
    await withUiTask(pickXmlImagesButton, "选择中...", async () => {
      const result = await pickDesktopFiles("xmlImages");
      if (result?.files?.length) {
        applyXmlRepairReplacementFiles(result.files);
      } else {
        document.querySelector("#xml-repair-images-input")?.click();
      }
      renderAll();
    });
    return;
  }

  const exportXmlButton = target.closest("#export-xml-repair-button");
  if (exportXmlButton) {
    await withUiTask(exportXmlButton, "导出中...", async () => {
      exportXmlRepairResult();
      renderAll();
    });
    return;
  }

  const checkClientUpdateButton = target.closest("#check-client-update-button");
  if (checkClientUpdateButton) {
    await checkClientUpdate({ silent: false });
    return;
  }

  const installClientUpdateButton = target.closest("#install-client-update-button, [data-client-update-action='install']");
  if (installClientUpdateButton) {
    await installClientUpdate();
    return;
  }

  const dismissClientUpdateButton = target.closest("#dismiss-client-update-button, [data-client-update-action='dismiss']");
  if (dismissClientUpdateButton) {
    if (state.clientUpdate.version) {
      state.clientUpdate.dismissedVersion = state.clientUpdate.version;
      localStorage.setItem("speaker.dismissedUpdateVersion", state.clientUpdate.version);
    }
    state.clientUpdate.noticeVisible = false;
    state.clientUpdate.message = "已设置稍后提醒。";
    renderAll();
    return;
  }

  const refreshLogsButton = target.closest("#refresh-client-logs-button");
  if (refreshLogsButton) {
    await withUiTask(refreshLogsButton, "读取中...", async () => {
      if (!window.SpeakerDesktopBridge?.isAvailable?.()) throw new Error("客户端日志需要在桌面客户端中读取");
      state.clientLogs = await window.SpeakerDesktopBridge.readClientLogs(500);
      renderAll();
    });
    return;
  }

  const downloadLogsButton = target.closest("#download-client-logs-button");
  if (downloadLogsButton) {
    await withUiTask(downloadLogsButton, "导出中...", async () => {
      await downloadTextFile(`speaker-client-log-${formatChinaFileTimestamp()}.log`, state.clientLogs.content || "");
    });
    return;
  }

  const clearLogsButton = target.closest("#clear-client-logs-button");
  if (clearLogsButton) {
    if (!requireSecondClick("clear-logs", "再次点击清空客户端日志")) return;
    await withUiTask(clearLogsButton, "清空中...", async () => {
      if (!window.SpeakerDesktopBridge?.isAvailable?.()) throw new Error("客户端日志需要在桌面客户端中清空");
      await window.SpeakerDesktopBridge.clearClientLogs();
      state.clientLogs = { path: "", content: "" };
      renderAll();
    });
  }
}

function handleDocumentInput(event) {
  if (event.target.matches?.("#local-speaker-name-input")) { state.localRecognition.speakerName = event.target.value; return; }
  const target = event.target;
  if (target.matches("#current-project-select")) {
    renderProjectSearchResults(target);
    return;
  }
  if (target.matches("[data-speaker-input]")) {
    syncSpeakerMappingDraft(target.dataset.speakerInput, target.value);
    return;
  }
  if (target.matches("[data-cue-speaker-input]")) {
    syncCueSpeakerDraft(target.dataset.cueIndex, target.value);
    return;
  }

  if (target.matches("#voiceprint-auto-learning-toggle")) {
    state.voiceprintAutoLearningEnabled = Boolean(target.checked);
    renderAll();
    return;
  }

  if (target.matches("[data-cue-text-input]")) {
    syncCueTextDraft(target.dataset.cueIndex, target.value);
    return;
  }

  if (target.matches("#cue-search-input")) {
    if (event.isComposing || state.cueSearchComposing) {
      return;
    }
    syncCueSearchInput(target);
    return;
  }

  if (target.matches("#review-script-text-input")) {
    state.scriptText = target.value;
    return;
  }

  if (target.matches("#lexicon-source-input")) {
    state.lexicon.pickedSource = target.value;
    return;
  }

  if (target.matches("#lexicon-paste-input")) {
    state.lexicon.pastedText = target.value;
    return;
  }

  if (target.matches("#voiceprint-search-input")) {
    const cursorPosition = target.selectionStart;
    state.voiceprintSearch = target.value;
    rerenderVoiceprintSearchInput(cursorPosition);
    return;
  }

  if (target.matches("#history-search-input")) {
    const cursorPosition = target.selectionStart;
    state.historySearch = target.value;
    renderAll();
    const nextInput = document.querySelector("#history-search-input");
    if (nextInput) {
      nextInput.focus();
      const nextCursor = Number.isFinite(cursorPosition) ? cursorPosition : nextInput.value.length;
      nextInput.setSelectionRange(nextCursor, nextCursor);
    }
    return;
  }

  if (target.matches("#voice-clone-profile-search-input, #voice-clone-sample-search-input")) {
    const key = target.id === "voice-clone-profile-search-input" ? "profileSearch" : "sampleSearch";
    const kind = key === "profileSearch" ? "profile" : "sample";
    state.voiceClone[key] = target.value;
    if (!event.isComposing && !state.voiceClone.searchComposing[kind]) {
      scheduleVoiceCloneSearch(kind);
    }
    return;
  }

  if (target.matches("#voice-clone-display-name-input")) {
    state.voiceClone.displayName = target.value;
    state.voiceClone.createDrawer.profileQuery = target.value;
    const genericNameHint = document.querySelector(".voice-generic-name-hint");
    if (genericNameHint) {
      genericNameHint.hidden = !["文本配音", "配音", "测试"].includes(
        String(target.value || "").normalize("NFKC").trim(),
      );
    }
    if (!event.isComposing && !state.voiceClone.searchComposing.drawer) {
      scheduleVoiceCloneSearch("drawer");
    }
    return;
  }

  if (target.matches("#voice-clone-target-model-input")) {
    state.voiceClone.targetModel = target.value;
    return;
  }

  if (target.matches("#voice-clone-preview-text")) {
    state.voiceClone.previewText = target.value;
    updateVoiceCloneGenerationControls();
    return;
  }

  if (target.matches("#voice-clone-instruction-input")) {
    state.voiceClone.voiceInstruction = target.value;
    updateVoiceCloneGenerationControls();
    return;
  }

  if (target.matches("#voice-clone-trim-start, #voice-clone-trim-end")) {
    updateVoiceCloneTrimDraft(target.id, target.value);
  }
}

async function handleDocumentChange(event) {
  const target = event.target;
  if (globalThis.SpeakerLocalRecognition?.handleChange(target)) return;
  if (rejectDisabledFileDropzone(target, event)) {
    target.value = "";
    return;
  }
  if (target.matches("#current-project-select")) {
    const projectQuery = String(target.value || "").trim().toLocaleLowerCase("zh-CN");
    const selectedProject = state.projects.find((project) => (
      String(project.project_id || "").toLocaleLowerCase("zh-CN") === projectQuery ||
      String(project.program_name || "").toLocaleLowerCase("zh-CN") === projectQuery
    ));
    if (!selectedProject) {
      target.value = "";
      showToast("请选择搜索结果中的项目", "warning");
      return;
    }
    const selectedProjectId = selectedProject.project_id;
    if (selectedProjectId === state.currentProjectId) {
      target.value = "";
      return;
    }
    const projectChanged = Boolean(state.currentProjectId && state.currentProjectId !== selectedProjectId);
    const clearedPendingUpload = projectChanged && Boolean(
      state.pickedMediaFile || state.pickedTaskScriptFiles.length || state.pickedScriptFile || state.scriptText || state.transcript.pickedFile ||
      state.pickedLowerThirdFiles.length || Object.values(state.pickedSubtitleBackplateFiles || {}).some(Boolean)
    );
    if (projectChanged) clearPendingTaskUploads();
    setCurrentProject(selectedProjectId);
    await withUiTask(null, "切换中...", async () => {
      await Promise.allSettled([refreshJobs(), refreshProjectScripts()]);
      if (state.currentProjectId !== selectedProjectId) return;
      if (CLONE_PAGE_IDS.has(state.activeStep)) {
        await refreshVoiceCloneData().catch(() => {});
      }
      if (state.activeStep === "lexicon") {
        state.lexicon.candidates = [];
        state.lexicon.terms = [];
        state.lexicon.bundlePreview = null;
        await refreshLexiconData().catch(() => {});
      }
      if (state.jobDetail && !isJobInCurrentProject(state.jobDetail)) {
        state.jobDetail = null;
        setCurrentJobId("", getWorkflowKindForStep(state.activeStep));
        state.activeStep = state.activeStep === "transcript" ? "transcript" : "upload";
      }
      if (TRANSCRIPT_PAGE_IDS.has(state.activeStep) || TASK_STEPS.some((step) => step.id === state.activeStep)) {
        await selectJobForStep(state.activeStep).catch(() => {});
      }
      renderAll();
      if (clearedPendingUpload) {
        showToast("项目已切换，待上传的音频和台本已清空，请在当前项目重新选择", "warning");
      }
    });
    return;
  }

  if (target.matches("#use-project-scripts-input")) {
    state.useProjectScripts = Boolean(target.checked);
    return;
  }

  if (target.matches("#lyrics-correction-mode-input")) {
    state.lyricsCorrectionMode = Boolean(target.checked);
    renderUploadStep();
    return;
  }

  if (target.matches("#video-file-input")) {
    const file = target.files?.[0] || null;
    try {
      assertRecognitionFileSize(file);
    } catch (error) {
      target.value = "";
      state.pickedMediaFile = null;
      state.pickedMediaSource = "";
      state.pickedMediaProjectId = "";
      const summary = document.querySelector("#media-picker-trigger span");
      if (summary) summary.textContent = "支持 wav、mp3、m4a、aac、aif、aiff；单文件最长 12 小时 / 2GB";
      showToast(normalizeError(error).message, "danger");
      return;
    }
    state.pickedMediaFile = file ? { name: file.name, path: "", browserFile: file } : null;
    state.pickedMediaSource = file ? "browser" : "";
    state.pickedMediaProjectId = file ? String(state.currentProjectId || "") : "";
    const summary = document.querySelector("#media-picker-trigger span");
    if (summary) summary.textContent = file?.name || "支持 wav、mp3、m4a、aac、aif、aiff；单文件最长 12 小时 / 2GB";
    return;
  }

  if (target.matches("#task-script-file-input")) {
    await setTaskScriptDraftFromFiles(target.files || [], "browser");
    return;
  }

  if (target.matches("#transcript-file-input")) {
    const file = target.files?.[0] || null;
    try {
      assertRecognitionFileSize(file);
    } catch (error) {
      target.value = "";
      state.transcript.pickedFile = null;
      state.transcript.pickedProjectId = "";
      state.transcript.pickedSource = "";
      const summary = document.querySelector("#transcript-picker-trigger span");
      if (summary) summary.textContent = "支持 wav、mp3、m4a、aac、aif、aiff；单文件最长 12 小时 / 2GB";
      showToast(normalizeError(error).message, "danger");
      return;
    }
    state.transcript.pickedFile = file ? { name: file.name, path: "", browserFile: file } : null;
    state.transcript.pickedProjectId = file ? String(state.currentProjectId || "") : "";
    state.transcript.pickedSource = file ? "browser" : "";
    const summary = document.querySelector("#transcript-picker-trigger span");
    if (summary) summary.textContent = file?.name || "支持 wav、mp3、m4a、aac、aif、aiff；单文件最长 12 小时 / 2GB";
    return;
  }

  if (target.matches("#review-script-file-input")) {
    await setScriptDraftFromFile(target.files?.[0] || null, "browser");
    return;
  }

  if (target.matches("#lexicon-script-input")) {
    await setLexiconDraftFromFile(target.files?.[0] || null, "browser");
    return;
  }

  if (target.matches("#lexicon-status-select")) {
    state.lexicon.selectedStatus = target.value || "candidate";
    await withUiTask(null, "刷新中...", async () => {
      await refreshLexiconData();
      renderAll();
    });
    return;
  }

  if (target.matches("#lower-third-input")) {
    const files = Array.from(target.files || []);
    state.pickedLowerThirdFiles = [];
    state.pickedLowerThirdSource = files.length ? "browser" : "";
    state.pickedLowerThirdProjectId = files.length ? String(state.currentProjectId || "") : "";
    state.pickedLowerThirdJobId = files.length ? String(state.currentJobId || "") : "";
    const summary = document.querySelector("#lower-third-picker-trigger span");
    if (summary) summary.textContent = files.length ? `${files.length} 张待上传` : "支持 png、jpg、webp，推荐透明 PNG";
    return;
  }

  if (target.matches("[data-subtitle-backplate-input-role], #subtitle-backplate-input")) {
    const role = normalizeSubtitleBackplateRole(target.dataset.subtitleBackplateInputRole || "with_speaker");
    const file = target.files?.[0] || null;
    state.pickedSubtitleBackplateFiles = {
      ...(state.pickedSubtitleBackplateFiles || {}),
      [role]: null,
    };
    state.pickedSubtitleBackplateSources = {
      ...(state.pickedSubtitleBackplateSources || {}),
      [role]: file ? "browser" : "",
    };
    state.pickedSubtitleBackplateProjectIds = {
      ...(state.pickedSubtitleBackplateProjectIds || {}),
      [role]: file ? String(state.currentProjectId || "") : "",
    };
    state.pickedSubtitleBackplateJobIds = {
      ...(state.pickedSubtitleBackplateJobIds || {}),
      [role]: file ? String(state.currentJobId || "") : "",
    };
    if (role === "with_speaker") {
      state.pickedSubtitleBackplateFile = null;
      state.pickedSubtitleBackplateSource = file ? "browser" : "";
    }
    const inputId = target.id || `subtitle-backplate-${role}-input`;
    const summary = document.querySelector(`[data-file-drop-target="${inputId}"] span`);
    if (summary) summary.textContent = file?.name || (role === "without_speaker" ? "用于只有唱词、没有人名条的字幕底板" : "用于带人名条的字幕底板");
    return;
  }

  if (target.matches("#voice-sample-input")) {
    await setVoiceSampleDraftFromFile(target.files?.[0] || null, "browser");
    return;
  }

  if (target.matches("#voice-clone-sample-input")) {
    await setVoiceCloneSampleDraftFromFile(target.files?.[0] || null);
    return;
  }

  if (target.matches("[data-speaker-input]")) {
    syncSpeakerMappingDraft(target.dataset.speakerInput, target.value);
    return;
  }

  if (target.matches("[data-cue-speaker-input]")) {
    syncCueSpeakerDraft(target.dataset.cueIndex, target.value);
    return;
  }

  if (target.matches("#voice-clone-sample-select")) {
    if (refuseExpiredVoiceAsset(state.voiceClone.samples.find((sample) => sample.id === target.value))) return;
    state.voiceClone.sampleId = target.value;
    const selectedSample = state.voiceClone.samples.find((sample) => sample.id === target.value);
    if (selectedSample?.display_name) state.voiceClone.displayName = selectedSample.display_name;
    renderAll();
    return;
  }

  if (target.matches("#voice-clone-display-name-input")) {
    state.voiceClone.displayName = target.value.trim();
    return;
  }

  if (target.matches("#voice-clone-profile-select")) {
    const profileId = target.value;
    await handleDocumentClick({ target: { matches: () => false, closest(selector) { return selector === "[data-voice-profile]" ? { dataset: { voiceProfile: profileId } } : null; } } });
    return;
  }

  if (target.matches("#voice-clone-model-select")) {
    if (!state.voiceClone.busy && selectVoiceCloneModel(target.value)) renderAll();
    return;
  }

  if (target.matches("#voice-clone-current-model-select")) {
    const id = target.value;
    const projectId = String(state.currentProjectId || "");
    const requestGeneration = state.voiceClone.requestGeneration;
    await withUiTask(null, "", async () => {
      try {
        const switching = switchVoiceCloneModel(id);
        updateVoiceCloneGenerationControls();
        await switching;
      } catch (error) {
        if (String(state.currentProjectId || "") === projectId && state.voiceClone.requestGeneration === requestGeneration) state.voiceClone.error = normalizeError(error).message;
        throw error;
      } finally { renderAll(); }
    });
    return;
  }

  if (target.matches("#voice-clone-provider-select")) {
    if (!isVoiceCloneServiceAvailable()) {
      showToast("声音服务未连接，暂时不能切换复刻模型", "warning");
      renderAll();
      return;
    }
    state.voiceClone.provider = target.value;
    applyVoiceCloneProviderDefaults(true);
    renderAll();
    return;
  }

  if (target.matches("#voice-clone-target-model-input")) {
    state.voiceClone.targetModel = target.value.trim();
    return;
  }

  if (target.matches("#voice-clone-instruction-input")) {
    state.voiceClone.voiceInstruction = target.value.trim();
    return;
  }

  if (target.matches("#voice-sample-person-select")) {
    state.voiceClone.personId = target.value;
    renderAll();
    return;
  }

  if (target.matches("#voiceprint-project-filter-select")) {
    state.voiceprintProjectFilter = target.value;
    state.voiceClone.personId = "";
    renderAll();
    return;
  }

  if (target.matches("#voiceprint-search-input")) {
    state.voiceprintSearch = target.value;
    renderAll();
    return;
  }

  if (target.matches("#xml-repair-file-input")) {
    const file = target.files?.[0] || null;
    if (file) {
      state.xmlRepair.xmlFile = { name: file.name, path: "" };
      state.xmlRepair.xmlText = await file.text();
      parseXmlRepairDocument();
      renderAll();
    }
    return;
  }

  if (target.matches("#xml-repair-images-input")) {
    const files = Array.from(target.files || []);
    applyXmlRepairReplacementFiles(files.map((file) => ({ name: file.name, path: "", browserFile: file })));
    renderAll();
  }
}

async function handleDocumentSubmit(event) {
  const form = event.target;
  if (form.matches("#create-project-form")) {
    event.preventDefault();
    const input = form.querySelector("#project-name-input");
    const name = input?.value.trim() || "";
    await withUiTask(form.querySelector("button"), "创建中...", async () => {
      if (!name) throw new Error("请输入项目名称");
      await createProject(name);
      if (input) input.value = "";
      showToast("项目已创建", "success");
      renderAll();
    });
    return;
  }

  if (form.matches("#video-job-form")) {
    event.preventDefault();
    const button = form.querySelector("#create-job-button");
    await withUiTask(button, "处理中...", async () => {
      const created = await createMediaJob();
      showToast(created._uiDetached ? "任务已创建在原项目，正在后台同步" : "任务已创建，开始同步", "success");
      renderAll();
      const completed = await syncJobUntilDone({
        jobId: created.id,
        projectId: created._uiProjectId,
        completedStep: "review",
      });
      showToast(completed._uiDetached ? "原项目任务已在后台完成" : "任务完成，可以开始校对", "success");
      renderAll();
    });
    return;
  }

  if (form.matches("#transcript-job-form")) {
    event.preventDefault();
    const button = form.querySelector("#create-transcript-button");
    await withUiTask(button, "处理中...", async () => {
      const created = await createTranscriptJob();
      showToast(created._uiDetached ? "扒词任务已创建在原项目，正在后台同步" : "扒词任务已创建，开始同步", "success");
      renderAll();
      const completed = await syncJobUntilDone({
        jobId: created.id,
        projectId: created._uiProjectId,
        completedStep: "transcript",
      });
      showToast(
        completed._uiDetached ? "原项目扒词任务已在后台完成" : "扒词完成，可以试听、修改说话人姓名并导出",
        "success",
      );
      renderAll();
    });
    return;
  }

  if (form.matches("#lower-third-form")) {
    event.preventDefault();
    const button = form.querySelector("#upload-lower-thirds-button");
    await withUiTask(button, "上传中...", async () => {
      await uploadLowerThirds();
      state.activeStep = "export";
      showToast("人名条已上传", "success");
      renderAll();
    });
    return;
  }

  if (form.matches("[data-subtitle-backplate-form-role], #subtitle-backplate-form")) {
    event.preventDefault();
    const role = normalizeSubtitleBackplateRole(form.dataset.subtitleBackplateFormRole || "with_speaker");
    const button = form.querySelector("[data-subtitle-backplate-upload-role], #upload-subtitle-backplate-button");
    await withUiTask(button, "上传中...", async () => {
      await uploadSubtitleBackplate(role);
      showToast("唱词条底图已上传", "success");
      renderAll();
    });
    return;
  }

  if (form.matches("#lexicon-import-form")) {
    event.preventDefault();
    const button = form.querySelector("#lexicon-import-button");
    await withUiTask(button, "导入中...", async () => {
      const imported = await importLexiconDocument();
      const count = Number(imported?.candidate_count || imported?.candidates?.length || 0);
      const input = form.querySelector("#lexicon-script-input");
      if (input) input.value = "";
      showToast(count ? `已导入 ${count} 条候选词` : "已导入，未发现新的候选词", count ? "success" : "neutral");
      renderAll();
    });
    return;
  }

  if (form.matches("#create-person-form")) {
    event.preventDefault();
    const input = form.querySelector("#person-name-input");
    const name = input?.value.trim() || "";
    await withUiTask(form.querySelector("button"), "添加中...", async () => {
      if (!name) throw new Error("请输入人物姓名");
      const projectId = resolveVoiceprintCreateProjectId();
      if (!projectId) throw new Error("请先进入人物所属项目");
      await requestV2(`/projects/${encodeURIComponent(projectId)}/voiceprints/people`, {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      if (input) input.value = "";
      await refreshPeople();
      showToast("人物已添加", "success");
      renderAll();
    });
    return;
  }

  if (form.matches("#upload-voice-sample-form")) {
    event.preventDefault();
    const personId = form.querySelector("#voice-sample-person-select")?.value || "";
    const file = form.querySelector("#voice-sample-input")?.files?.[0] || state.voiceSampleDraft.file || null;
    await withUiTask(form.querySelector("#upload-voice-sample-button"), "上传中...", async () => {
      await uploadVoiceSampleFile(personId, file);
      const input = form.querySelector("#voice-sample-input");
      if (input) input.value = "";
      clearVoiceSampleDraft();
      showToast("样本已上传", "success");
      renderAll();
    });
    return;
  }

  if (form.matches("#voice-clone-upload-sample-form")) {
    event.preventDefault();
    if (state.voiceClone.busy) return;
    if (!isVoiceCloneServiceAvailable()) {
      state.voiceClone.error = "声音服务未连接，暂时不能创建音色";
      renderAll();
      return;
    }
    const nameInput = document.querySelector("#voice-clone-display-name-input");
    const displayName = nameInput?.value.trim() || state.voiceClone.displayName;
    const draft = state.voiceClone.sampleDraft || {};
    const file = draft.trimmedFile || form.querySelector("#voice-clone-sample-input")?.files?.[0] || draft.file || null;
    state.voiceClone.displayName = displayName;
    state.voiceClone.addFlow = { ...state.voiceClone.addFlow, open: true, stage: "checking", fileLabel: file?.name || draft.fileName || "", error: "" };
    await runVoiceCloneCreation(form.querySelector("#voice-clone-upload-sample-button"), (shouldApply) => (
      uploadVoiceCloneSampleAndCreateProfile(displayName, file, { shouldApply })
    ));
    return;
  }
}

async function loadXmlRepairFile(fileMeta) {
  const text = await readClientTextFile(fileMeta);
  state.xmlRepair.xmlFile = fileMeta;
  state.xmlRepair.xmlText = text;
  parseXmlRepairDocument();
}

function parseXmlRepairDocument() {
  if (!window.SpeakerXmlRepair) throw new Error("XML 返修模块未载入");
  const parsed = window.SpeakerXmlRepair.parseXmlRepairDocument(state.xmlRepair.xmlFile, state.xmlRepair.xmlText);
  state.xmlRepair.assets = parsed.assets || [];
  state.xmlRepair.status = `发现 ${state.xmlRepair.assets.length} 个 PNG 资源`;
  state.xmlRepair.patchedXmlText = "";
}

function applyXmlRepairReplacementFiles(files) {
  if (!window.SpeakerXmlRepair) throw new Error("XML 返修模块未载入");
  if (!state.xmlRepair.assets.length) throw new Error("请先选择旧 XML");
  state.xmlRepair.replacementFiles = files || [];
  state.xmlRepair.assets = window.SpeakerXmlRepair.autoMatchAssets(state.xmlRepair.assets, state.xmlRepair.replacementFiles);
  const matchedCount = state.xmlRepair.assets.filter((asset) => asset.replacementPath).length;
  state.xmlRepair.status = `已匹配 ${matchedCount}/${state.xmlRepair.assets.length} 个 PNG`;
}

function exportXmlRepairResult() {
  if (!window.SpeakerXmlRepair) throw new Error("XML 返修模块未载入");
  const result = window.SpeakerXmlRepair.applyXmlReplacements(state.xmlRepair.xmlText, state.xmlRepair.assets);
  const sourcePath = state.xmlRepair.xmlFile?.path || state.xmlRepair.xmlFile?.name || "patched.xml";
  const patchedPath = window.SpeakerXmlRepair.buildPatchedXmlPath(sourcePath);
  state.xmlRepair.patchedXmlText = result.xmlText;
  state.xmlRepair.patchedPath = patchedPath;
  state.xmlRepair.status = `已替换 ${result.replacedCount} 处引用`;
  writeTextFile(patchedPath, result.xmlText);
}

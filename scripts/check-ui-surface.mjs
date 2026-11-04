#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

const files = {
  api: path.join(root, "src/frontend/js/api.js"),
  renderers: path.join(root, "src/frontend/js/renderers.js"),
  events: path.join(root, "src/frontend/js/events.js"),
  state: path.join(root, "src/frontend/js/state.js"),
  voiceCloneLibrary: path.join(root, "src/frontend/js/voice-clone-library.js"),
  voiceCloneStyles: path.join(root, "src/frontend/styles/v2-direction.css"),
  adminHtml: path.join(root, "backend/admin-ui/admin.html"),
  adminJs: path.join(root, "backend/admin-ui/admin.js"),
};

const source = Object.fromEntries(
  Object.entries(files).map(([key, filePath]) => [key, fs.readFileSync(filePath, "utf8")])
);

const requiredSnippets = [
  "const TASK_STEPS = [",
  "const APP_PAGES = [",
  "const NAV_GROUPS = [",
  "{ id: \"upload\", index: \"01\", title: \"生成字幕\"",
  "{ id: \"review\", index: \"02\", title: \"校对说话人\"",
  "{ id: \"lowerThird\", index: \"03\", title: \"匹配人名条\"",
  "{ id: \"export\", index: \"04\", title: \"导出\"",
  "{ id: \"voiceprints\", index: \"V1\", title: \"声纹库\"",
  "{ id: \"voiceCloning\", index: \"V2\", title: \"声音复刻\"",
  "{ id: \"cloneSamples\", index: \"V3\", title: \"复刻样本库\"",
  "{ id: \"voiceCloneRecords\", index: \"V4\", title: \"生成音频\"",
  "{ id: \"history\", index: \"H\", title: \"历史记录\"",
  "{ id: \"tools\", index: \"T\", title: \"工具与日志\"",
  "{ id: \"transcriptReview\", index: \"P2\", title: \"校对说话人\"",
  "id=\"current-project-select\"",
  "placeholder=\"搜索并切换项目\"",
  "id=\"project-search-results\"",
  "data-project-switch=",
  "function renderProjectSearchResults(input)",
  "focusedProjectOption.click()",
  "event.target.matches(\"#current-project-select\")",
  "class=\"rail-project-management\"",
  "class=\"rail-nav-label\">主要功能",
  "id=\"project-name-input\"",
  "id=\"purge-project-button\"",
  "async function purgeCurrentProject()",
  "id=\"refresh-data-button\"",
  "id=\"video-file-input\"",
  "id=\"create-job-button\"",
  "formData.append(\"job_kind\", \"transcription\")",
  "id=\"sync-job-button\"",
  "jobId: created.id,",
  "projectId: created._uiProjectId,",
  "completedStep: \"review\",",
  "completedStep: \"transcript\",",
  "id=\"transcript-file-input\"",
  "id=\"create-transcript-button\"",
  "formData.append(\"job_kind\", \"transcript\")",
  "function renderTranscriptPipeline(job)",
  "aria-label=\"扒词生成进度\"",
  "自动更新中",
  "状态返回后自动更新",
  "id=\"source-media-player\" controls",
  "transcript/download?format=docx",
  "transcript/download?format=srt",
  "transcript/download?format=txt",
  "id=\"review-script-picker-trigger\"",
  "id=\"review-script-text-input\"",
  "id=\"review-script-update-button\"",
  "data-apply-script-corrections",
  "id=\"speaker-save-button\"",
  "id=\"cue-search-input\"",
  "data-cue-search-delta",
  "data-cue-page-delta",
  "id=\"batch-delete-fillers-button\"",
  "id=\"subtitle-combined-save-button\"",
  "data-cue-text-delete",
  "id=\"lower-third-input\"",
  "id=\"upload-lower-thirds-button\"",
  "premiere-xml/download",
  "variant=full",
  "variant=clean",
  "id=\"voiceprint-project-filter-select\"",
  "id=\"voiceprint-search-input\"",
  "id=\"person-name-input\"",
  "resolveVoiceprintCreateProjectId",
  "requestV2(`/projects/${encodeURIComponent(projectId)}/voiceprints/people`",
  "/voiceprints/people/${encodeURIComponent(personId)}/samples`",
  "data-voiceprint-person-delete",
  "id=\"voice-sample-person-select\"",
  "renderAudioDropzone(\"voice-sample-input\"",
  "id=\"upload-voice-sample-button\"",
  "data-voiceprint-sample-delete",
  "id=\"voice-clone-model-select\"",
  "id=\"voice-clone-current-model-select\"",
  "id=\"voice-clone-display-name-input\"",
  "renderAudioDropzone(\"voice-clone-sample-input\"",
  "id=\"voice-clone-use-speech-window\"",
  "id=\"voice-clone-trim-start\"",
  "id=\"voice-clone-trim-end\"",
  "id=\"voice-clone-trim-button\"",
  "id=\"voice-clone-trim-reset-button\"",
  "id=\"voice-clone-upload-sample-button\"",
  "id=\"voice-clone-refresh-button\"",
  "id=\"voice-clone-create-button\"",
  "data-voice-clone-sample-delete",
  "data-voice-profile-delete",
  "data-voice-profile-source-preview",
  "data-voice-profile-source-api",
  "data-voice-profile-source-path",
  "voice-project-scope",
  "来源项目",
  "样本文件",
  "试听来源",
  "正在创建的音色",
  "建议使用“人物名 + 用途”，例如“王冰冰 新闻旁白”，以后更容易辨认。",
  "id=\"voice-clone-preview-text\"",
  "for=\"voice-clone-preview-text\"",
  "function renderVoiceIdentityList(groups",
  "data-voice-group-profiles",
  "function refreshVoiceCloneSamplePage",
  "/samples/page?",
  "id=\"voice-clone-new-button\"",
  "id=\"voice-clone-create-drawer\"",
  "function renderAddVoiceFlow(",
  "id=\"voice-clone-shared-sample-player\"",
  "aria-label=\"声音复刻样本播放器\"",
  "aria-label=\"删除样本：",
  "aria-label=\"删除音色版本：",
  ".voice-library-card:focus-within",
  ".voice-create-steps button:focus-visible",
  "创建失败",
  "已归档",
  "data-voice-clone-view",
  "id=\"voice-clone-load-more-button\"",
  "id=\"voice-clone-instruction-input\"",
  "data-voice-instruction-example",
  "id=\"voice-clone-preview-button\"",
  "data-voice-record-download",
  "data-voice-record-delete",
  "id=\"history-search-input\"",
  "id=\"refresh-jobs-button\"",
  "data-job-load",
  "data-job-abort",
  "data-job-delete",
  "id=\"pick-xml-repair-file-button\"",
  "id=\"pick-xml-repair-images-button\"",
  "id=\"export-xml-repair-button\"",
  "id=\"refresh-status-button\"",
  "id=\"refresh-client-logs-button\"",
  "id=\"download-client-logs-button\"",
  "id=\"clear-client-logs-button\"",
  "currentSubtitleJobId",
  "currentTranscriptJobId",
  "function isTranscriptJob",
  "async function selectJobForStep",
  "pickedLowerThirdProjectId",
  "pickedLowerThirdJobId",
  "项目或任务已切换，本次人名条上传已取消。",
  "pickedSubtitleBackplateProjectIds",
  "pickedSubtitleBackplateJobIds",
  "项目或任务已切换，本次唱词条底图上传已取消。",
  "id=\"load-project-subtitle-backplates-button\"",
  "function buildProjectVoiceClonePath",
  "buildProjectVoiceClonePath(targetProjectId, \"/samples\")",
  "state.voiceClone.requestGeneration",
  "resetVoiceCloneProjectState();",
  "state.voiceClone.sampleDraft = {",
  "项目或音色已切换，生成结果保留在原项目",
  "const isTargetContextActive = () => (",
  "loadJobRequestGeneration",
  "loadJobTargetId",
  "invalidateLoadJobRequests",
  "invalidateLoadRequests",
  "const requestContext = Object.freeze({",
  "if (!refreshed || !isTargetContextActive()) return undefined;",
  "if (detail === undefined) return null;",
  "if (!isLoadAttemptActive(candidateLoadGeneration)) return null;",
  "if (state.loadJobTargetId === jobId) invalidateLoadJobRequests();",
  "id=\"provider-cost-filter-form\"",
  "id=\"provider-cost-date-from-input\"",
  "id=\"provider-cost-date-to-input\"",
  "id=\"provider-cost-project-input\"",
  "id=\"provider-cost-job-input\"",
  "id=\"provider-cost-provider-input\"",
  "id=\"provider-cost-model-input\"",
  "id=\"token-trend-chart\"",
  "id=\"token-composition-chart\"",
  "id=\"project-token-chart\"",
  "id=\"export-provider-costs-button\"",
  "id=\"price-catalog-form\"",
  "id=\"price-effective-from-input\"",
  "id=\"price-unit-microunits-input\"",
  "data-admin-view=\"ledger\"",
  "data-admin-view=\"catalog\"",
  "data-admin-view=\"operations\"",
  "DeepSeek 按服务商实测 Token 统计",
  "项目用量分布",
  "/provider-costs?${buildProviderCostQuery()}",
  "/api/v1/admin/provider-costs/export.csv?${buildProviderCostQuery()}",
  "request(\"/provider-costs/prices\"",
  "measured_event_count",
  "unmetered_event_count",
  "catalog_estimated_amounts_by_currency",
];

if (source.renderers.includes('<select id="current-project-select"') || source.renderers.includes('<datalist id="project-options"')) {
  console.error("UI surface check failed. Project switching must use the searchable project picker.");
  process.exit(1);
}

const combined = Object.values(source).join("\n");
const missing = requiredSnippets.filter((snippet) => !combined.includes(snippet));

if (missing.length) {
  console.error("UI surface check failed. Missing snippets:");
  for (const snippet of missing) console.error(`- ${snippet}`);
  process.exit(1);
}

if (source.adminJs.includes("usage_payload")) {
  console.error("UI surface check failed. Admin provider cost UI must not access usage_payload.");
  process.exit(1);
}

for (const forbiddenSnippet of [
  "id=\"billing-config-form\"",
  "id=\"asr-price-input\"",
  "id=\"oss-cost-input\"",
  "total_asr_estimated_cost",
  "total_oss_estimated_cost",
  "total_estimated_cost",
  "asr_estimated_cost",
  "oss_estimated_cost",
  "data-admin-view=\"usage\"",
  "id=\"summary-stats\"",
  "id=\"project-profile-list\"",
  "request(\"/usage/summary",
  "request(\"/usage/daily",
  "request(\"/project-profiles",
]) {
  if (source.adminHtml.includes(forbiddenSnippet) || source.adminJs.includes(forbiddenSnippet)) {
    console.error(`UI surface check failed. Legacy admin estimate remains: ${forbiddenSnippet}`);
    process.exit(1);
  }
}

const hydrateAudioSource = source.api.slice(
  source.api.indexOf("function hydrateAudioPlayers()"),
  source.api.indexOf("\nasync function request(", source.api.indexOf("function hydrateAudioPlayers()")),
);
if (!hydrateAudioSource.includes("audio.src = buildApiUrl(path)")) {
  console.error("UI surface check failed. Audio players must stream directly from the backend URL.");
  process.exit(1);
}
for (const forbiddenSnippet of ["fetch(url)", "response.blob()", "URL.createObjectURL(blob)"]) {
  if (hydrateAudioSource.includes(forbiddenSnippet)) {
    console.error(`UI surface check failed. Audio hydration must not eagerly download blobs: ${forbiddenSnippet}`);
    process.exit(1);
  }
}
const voiceCloneInitialLoadSource = source.api.slice(
  source.api.indexOf("async function refreshVoiceCloneLibrary("),
  source.api.indexOf("\nasync function refreshVoiceCloneSamplePage("),
);
if (
  !voiceCloneInitialLoadSource.includes("/library?")
  || !voiceCloneInitialLoadSource.includes("/profiles/creating")
  || voiceCloneInitialLoadSource.includes('"/samples"')
  || voiceCloneInitialLoadSource.includes('"/profiles"')
) {
  console.error("UI surface check failed. Voice-clone initial load must use grouped library and creating-profile endpoints only.");
  process.exit(1);
}
const voiceCloneSampleLibrarySource = source.renderers.slice(
  source.renderers.indexOf("function renderVoiceCloneSampleLibrary("),
  source.renderers.indexOf("\nfunction getFilteredVoiceCloneProfiles("),
);
const sampleLibraryAudioCount = (
  voiceCloneSampleLibrarySource.match(/<audio /g) || []
).length;
if (
  !voiceCloneSampleLibrarySource.includes('id="voice-clone-shared-sample-player"')
  || sampleLibraryAudioCount !== 1
) {
  console.error("UI surface check failed. Voice-clone samples must use exactly one shared audio player.");
  process.exit(1);
}
const voiceCloneSourcePreviewSource = source.events.slice(
  source.events.indexOf('const voiceProfileSourcePreviewButton = target.closest("[data-voice-profile-source-preview]");'),
  source.events.indexOf('\n  const voiceCloneSampleSelectButton'),
);
if (
  !voiceCloneSourcePreviewSource.includes('sourceApi === "v1" ? buildApiUrl(sourcePath) : buildApiV2Url(sourcePath)')
  || !source.voiceCloneLibrary.includes('path: `/voiceprints/samples/${encodeURIComponent(voiceprintSampleId)}/audio`')
  || !source.voiceCloneLibrary.includes('path: `/projects/${encodeURIComponent(normalizedProjectId)}/voice-cloning/samples/${encodeURIComponent(cloneSampleId)}/audio`')
) {
  console.error("UI surface check failed. Source previews must route clone and voiceprint samples to their respective audio APIs.");
  process.exit(1);
}
const voiceClonePreviewRecoveryApiSource = source.api.slice(
  source.api.indexOf("async function createVoiceClonePreview()"),
  source.api.indexOf("\nasync function", source.api.indexOf("async function createVoiceClonePreview()") + 1),
);
const voiceClonePreviewRecoveryEventSource = source.events.slice(
  source.events.indexOf("async function generateVoiceCloneAudio("),
  source.events.indexOf("\nfunction closeVoiceCloneCreationDrawer("),
);
const unavailableProfileCode = "voice_clone_profile_unavailable";
const recoveryCodeIndex = voiceClonePreviewRecoveryApiSource.indexOf(
  'recoveredError.code = error.code',
);
const eventRecoveryIndex = voiceClonePreviewRecoveryEventSource.indexOf(
  '["voice_clone_profile_unavailable", "voice_asset_expired"].includes(error?.code)',
);
if (
  recoveryCodeIndex < 0
  || eventRecoveryIndex < 0
  || voiceClonePreviewRecoveryEventSource.includes('error?.message === "当前音色已不可用，请重新选择后再生成。"')
  || voiceClonePreviewRecoveryEventSource.indexOf("renderAll()", eventRecoveryIndex) < eventRecoveryIndex
  || voiceClonePreviewRecoveryEventSource.indexOf("throw error", eventRecoveryIndex) < eventRecoveryIndex
) {
  console.error(`UI surface check failed. ${unavailableProfileCode} recovery must preserve the business code and render the persistent result.`);
  process.exit(1);
}
const voiceCloneSearchHandlerSource = source.events.slice(
  source.events.indexOf('if (target.matches("#voice-clone-profile-search-input, #voice-clone-sample-search-input"))'),
  source.events.indexOf('\n  if (target.matches("#voice-clone-display-name-input"))'),
);
if (
  !voiceCloneSearchHandlerSource.includes("scheduleVoiceCloneSearch")
  || voiceCloneSearchHandlerSource.includes("renderAll()")
) {
  console.error("UI surface check failed. Voice-clone search must debounce without full-page rendering.");
  process.exit(1);
}
const voiceCloneRecordsSource = source.renderers.slice(
  source.renderers.indexOf("function renderVoiceCloneRecordsList()"),
  source.renderers.indexOf("function shouldShowVoiceRecordMessage("),
);
if (
  !/<audio controls preload="none"[^\n]*data-audio-src=/.test(voiceCloneRecordsSource)
  || /<audio controls preload="metadata"[^\n]*data-audio-src=/.test(voiceCloneRecordsSource)
) {
  console.error("UI surface check failed. Voice-clone synthesis history must not preload audio.");
  process.exit(1);
}

if (source.renderers.includes("进度 ${percent}%")) {
  console.error("UI surface check failed. User-facing sync UI must not display estimated percentages.");
  process.exit(1);
}

const transcriptUiSource = source.renderers.slice(
  source.renderers.indexOf("function renderTranscriptPipeline(job)"),
  source.renderers.indexOf("function renderTranscriptReviewStep()"),
);
for (const forbiddenSnippet of ["扒词正在处理", "同步到完成", "transcriptJob.processing_message"]) {
  if (transcriptUiSource.includes(forbiddenSnippet)) {
    console.error(`UI surface check failed. Transcript UI still exposes the legacy status: ${forbiddenSnippet}`);
    process.exit(1);
  }
}

function sliceSource(label, content, startSnippet, endSnippet) {
  const start = content.indexOf(startSnippet);
  const end = content.indexOf(endSnippet, start + startSnippet.length);
  if (start < 0 || end < 0) {
    console.error(`UI surface check failed. Could not isolate ${label}.`);
    process.exit(1);
  }
  return content.slice(start, end);
}

function requireOrdered(label, content, snippets) {
  let cursor = 0;
  for (const snippet of snippets) {
    const index = content.indexOf(snippet, cursor);
    if (index < 0) {
      console.error(`UI surface check failed. ${label} is missing ordered snippet: ${snippet}`);
      process.exit(1);
    }
    cursor = index + snippet.length;
  }
}

const loadJobSource = sliceSource("loadJob", source.api, "async function loadJob(", "\nasync function selectJobForStep(");
const firstLoadJobAwait = loadJobSource.indexOf("await ");
for (const snippet of [
  "const targetProjectId = getCurrentProjectId();",
  "const targetJobId = String(jobId || \"\").trim();",
  "const requestGeneration = state.loadJobRequestGeneration + 1;",
  "const requestContext = Object.freeze({",
]) {
  const index = loadJobSource.indexOf(snippet);
  if (index < 0 || index > firstLoadJobAwait) {
    console.error(`UI surface check failed. loadJob must freeze context before its first await: ${snippet}`);
    process.exit(1);
  }
}
requireOrdered("loadJob stale-result guard", loadJobSource, [
  "detail = decorateJobDetail(await request(",
  "if (!isTargetContextActive()) return undefined;",
  "state.jobDetail = detail;",
  "setCurrentJobId(nextJobId, detail.kind, false);",
  "const refreshed = await refreshJobDerived(nextJobId, requestContext);",
  "if (!refreshed || !isTargetContextActive()) return undefined;",
]);

const loadJobEventSource = sliceSource(
  "load-job click handler",
  source.events,
  "const loadJobButton = target.closest(\"[data-job-load]\");",
  "\n  const abortJobButton = target.closest(\"[data-job-abort]\");",
);
requireOrdered("load-job click stale-result guard", loadJobEventSource, [
  "const detail = await loadJob(loadJobButton.dataset.jobLoad);",
  "if (!detail) return;",
  "state.activeStep = deriveActiveStep();",
  "showToast(\"已载入任务\", \"success\");",
  "renderAll();",
]);

const pageMatches = [...source.renderers.matchAll(/\{\s*id:\s*"([^"]+)"\s*,\s*index:\s*"([^"]+)"\s*,\s*title:\s*"([^"]+)"/g)];
const pages = pageMatches.map((match) => `${match[2]} ${match[3]} (${match[1]})`);
console.log(`UI surface check passed: ${requiredSnippets.length} required snippets present.`);
console.log(`Detected page entries: ${pages.join(", ")}`);

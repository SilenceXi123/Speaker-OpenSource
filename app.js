window.SPEAKER_BUILD_CONFIG = {
  "version": "2.0.19",
  "buildMarker": "2026-11-04 / speaker-2.0.19-open-source-test",
  "updateChannel": "opensource-test",
  "apiBase": "http://127.0.0.1:8787/api/v1"
};
const APP_VERSION = window.SPEAKER_BUILD_CONFIG?.version || "0.0.0-dev";
const BUILD_MARKER = window.SPEAKER_BUILD_CONFIG?.buildMarker || "unbuilt-development";
const UPDATE_CHANNEL = window.SPEAKER_BUILD_CONFIG?.updateChannel || "development";
const BUILD_API_BASE = String(window.SPEAKER_BUILD_CONFIG?.apiBase || "").trim();
const APP_TIME_ZONE = "Asia/Shanghai";
const DESKTOP_DEFAULT_API_BASE = "http://127.0.0.1:8787/api/v1";
const DESKTOP_RUNTIME_HOSTS = new Set(["tauri.localhost", "ipc.localhost"]);
const CLIENT_BRIDGE_BASE = "http://127.0.0.1:4174";
const CUE_PREVIEW_PAGE_SIZE = 120;
const ASR_RECORDED_FILE_MAX_BYTES = 2 * 1024 * 1024 * 1024;
const VOICE_AUDIO_EXTENSIONS = new Set(["wav", "mp3", "m4a", "mp4", "aac", "ogg", "flac"]);
const VOICE_CLONE_PROVIDER_FALLBACKS = [
  {
    provider: "cosyvoice",
    label: "CosyVoice 3.5 Plus",
    target_model: "cosyvoice-v3.5-plus",
    sample_requirements: [
      "WAV 16bit / MP3 / M4A；建议导出 WAV",
      "推荐 10-20 秒，最长不超过 60 秒",
      "采样率不低于 16 kHz；建议 24 kHz 单声道",
      "至少 5 秒连续清晰朗读，无音乐、噪音或其他人声",
    ],
    notes: ["创建和合成必须使用同一个 target_model", "CosyVoice 3.5 Plus 支持 instruction 语气提示，可控制情绪、语速和角色感"],
  },
  {
    provider: "qwen",
    label: "Qwen3-TTS",
    target_model: "qwen3-tts-vc-2026-01-22",
    sample_requirements: [
      "WAV 16bit / MP3 / M4A；建议导出 24 kHz 单声道 WAV",
      "推荐 10-20 秒，最长不超过 60 秒",
      "采样率必须不低于 24 kHz，且应为单声道",
      "至少 3 秒连续清晰朗读，无音乐、噪音或其他人声",
    ],
    notes: ["创建和合成必须使用同一个 target_model", "Qwen3-TTS-VC 不支持语气提示"],
  },
];
const STORAGE_KEYS = {
  apiBase: "speaker.apiBase",
  currentProjectName: "speaker.currentProjectName",
  lastProjectId: "speaker.lastProjectId",
  jobId: "speaker.lastJobId",
  subtitleJobId: "speaker.lastSubtitleJobId",
  transcriptJobId: "speaker.lastTranscriptJobId",
};

function isDesktopRuntime() {
  const protocol = window.location.protocol || "";
  const hostname = window.location.hostname || "";
  return !protocol.startsWith("http") || DESKTOP_RUNTIME_HOSTS.has(hostname);
}

function deriveDefaultApiBase() {
  if (!isDesktopRuntime() && window.location.hostname) {
    return `${window.location.protocol}//${window.location.hostname}:8787/api/v1`;
  }
  return DESKTOP_DEFAULT_API_BASE;
}

const state = {
  apiBase: BUILD_API_BASE || localStorage.getItem(STORAGE_KEYS.apiBase) || deriveDefaultApiBase(),
  activeStep: "upload",
  currentProjectId: "",
  currentProjectName: "",
  lastProjectId: localStorage.getItem(STORAGE_KEYS.lastProjectId) || "",
  currentSubtitleJobId: localStorage.getItem(STORAGE_KEYS.subtitleJobId) || localStorage.getItem(STORAGE_KEYS.jobId) || "",
  currentTranscriptJobId: localStorage.getItem(STORAGE_KEYS.transcriptJobId) || "",
  currentJobId: localStorage.getItem(STORAGE_KEYS.subtitleJobId) || localStorage.getItem(STORAGE_KEYS.jobId) || "",
  loadJobRequestGeneration: 0,
  loadJobTargetId: "",
  projects: [],
  projectLoad: { status: "loading", error: "" },
  jobs: [],
  people: [],
  voiceprintProjectFilter: "__current__",
  voiceprintSearch: "",
  jobDetail: null,
  speakers: [],
  speakerMappings: {},
  memorySuggestions: [],
  voiceprintAutoLearningEnabled: true,
  cues: [],
  cuePage: 0,
  cueSearchQuery: "",
  cueSearchMatchIndex: 0,
  cueSearchComposing: false,
  cueSpeakerDrafts: {},
  cueTextDrafts: {},
  cueDeletedDrafts: {},
  subtitlePreview: "",
  cleanSubtitlePreview: "",
  pickedMediaFile: null,
  pickedMediaSource: "",
  pickedMediaProjectId: "",
  pickedTaskScriptFiles: [],
  pickedTaskScriptProjectId: "",
  lyricsCorrectionMode: false,
  projectScripts: [],
  useProjectScripts: true,
  transcript: {
    pickedFile: null,
    pickedSource: "",
    pickedProjectId: "",
  },
  pickedScriptFile: null,
  pickedScriptSource: "",
  scriptText: "",
  scriptAssistExpanded: false,
  historySearch: "",
  pickedLowerThirdFiles: [],
  pickedLowerThirdSource: "",
  pickedLowerThirdProjectId: "",
  pickedLowerThirdJobId: "",
  pickedSubtitleBackplateFile: null,
  pickedSubtitleBackplateSource: "",
  pickedSubtitleBackplateFiles: {
    with_speaker: null,
    without_speaker: null,
  },
  pickedSubtitleBackplateSources: {
    with_speaker: "",
    without_speaker: "",
  },
  pickedSubtitleBackplateProjectIds: {
    with_speaker: "",
    without_speaker: "",
  },
  pickedSubtitleBackplateJobIds: {
    with_speaker: "",
    without_speaker: "",
  },
  pickedVoiceSampleFile: null,
  pickedVoiceSampleSource: "",
  voiceSampleDraft: {
    file: null,
    fileName: "",
  },
  serviceStatus: { health: null, config: null, voiceprint: null, voiceClone: null },
  syncProgress: { active: false, attempt: 0, maxAttempts: 20, phase: "", message: "", progress: 0, pipeline: null },
  syncAbortRequested: false,
  busyLabels: new Map(),
  pendingDangerKey: "",
  toast: { visible: false, message: "", tone: "neutral", timer: null },
  voiceClone: {
    requestGeneration: 0,
    view: "generate",
    busy: "",
    error: "",
    pendingModelId: "",
    personId: "",
    sampleId: "",
    profileId: "",
    provider: "",
    targetModel: "",
    displayName: "",
    profileSearch: "",
    sampleSearch: "",
    profiles: [],
    samples: [],
    records: [],
    library: {
      groups: [],
      nextCursor: null,
      total: 0,
      availableGroupCount: 0,
      exceptionGroupCount: 0,
      reusableSampleCount: 0,
      creatingProfiles: [],
      loaded: false,
      loading: false,
      query: "",
      expandedGroupKey: "",
      profilesByGroup: {},
      samplesByGroup: {},
    },
    samplePage: {
      items: [],
      nextCursor: null,
      total: 0,
      loaded: false,
      loading: false,
      query: "",
      status: "",
    },
    createDrawer: {
      open: false,
      step: "existing_profile",
      profileQuery: "",
      sampleQuery: "",
    },
    addFlow: {
      open: false,
      stage: "pick",
      matchType: "",
      matchScore: null,
      matchedIdentity: null,
      sampleSlotsRemaining: null,
      error: "",
    },
    searchComposing: { profile: false, sample: false, drawer: false },
    sharedPlayer: {
      sampleId: "",
      label: "",
      url: "",
    },
    previewText: "你好，欢迎使用声音复刻功能。",
    voiceInstruction: "",
    audioBlobUrls: new Map(),
    sampleDraft: {
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
    },
  },
  xmlRepair: {
    xmlFile: null,
    xmlText: "",
    assets: [],
    replacementFiles: [],
    status: "",
    patchedXmlText: "",
    patchedPath: "",
  },
  clientUpdate: {
    checking: false,
    installing: false,
    available: false,
    version: "",
    notes: "",
    date: "",
    message: "",
    lastCheckedAt: "",
    noticeVisible: false,
    dismissedVersion: localStorage.getItem("speaker.dismissedUpdateVersion") || "",
  },
  lexicon: {
    terms: [],
    candidates: [],
    imports: [],
    bundlePreview: null,
    pickedFile: null,
    pickedSource: "",
    pastedText: "",
    loading: false,
    selectedStatus: "candidate",
    message: "",
  },
  clientLogs: { path: "", content: "" },
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function getVoiceCloneProviderOptions() {
  const options = state.serviceStatus.voiceClone?.providers;
  return Array.isArray(options) && options.length ? options : VOICE_CLONE_PROVIDER_FALLBACKS;
}

function getVoiceCloneProviderOption(provider = state.voiceClone.provider) {
  const normalized = String(provider || "").toLowerCase();
  return getVoiceCloneProviderOptions().find((option) => option.provider === normalized) || getVoiceCloneProviderOptions()[0];
}

function getVoiceCloneModelOptions() {
  const status = state.serviceStatus.voiceClone || {};
  if (Array.isArray(status.models)) return status.models.filter((option) => (
    option.provider && option.model && option.id === `${option.provider}:${option.model}`
  ));
  // Older servers expose only their configured provider/model pairs.
  const providers = Array.isArray(status.providers) ? status.providers : status.provider && status.target_model ? [status] : [];
  return providers.filter((option) => option.provider && option.target_model).map((option) => ({
    ...option,
    id: `${option.provider}:${option.target_model}`,
    model: option.target_model,
    available: status.available === true && option.available !== false,
    recommended: option.provider === status.provider && option.target_model === status.target_model,
    capabilities: option.capabilities || { instruction: option.provider === "cosyvoice" },
    constraints: option.constraints || {},
  }));
}

function getVoiceCloneModelOption(id = `${state.voiceClone.provider}:${state.voiceClone.targetModel}`) {
  return getVoiceCloneModelOptions().find((option) => option.id === id) || null;
}

function selectVoiceCloneModel(id) {
  const option = getVoiceCloneModelOption(id);
  if (!option) return false;
  state.voiceClone.provider = option.provider;
  state.voiceClone.targetModel = option.model;
  localStorage.setItem("speaker.voiceCloneModel", option.id);
  return true;
}

function isVoiceCloneServiceAvailable(profile = null) {
  const status = state.serviceStatus.voiceClone;
  if (!status || status.enabled === false) return false;
  // Catalog availability is per model; the top-level field describes only
  // the server's configured default. Older servers have no catalog.
  if (!Array.isArray(status.models) && status.available !== true) return false;
  return profile
    ? Boolean(getVoiceCloneModelOption(`${profile.provider}:${profile.target_model}`)?.available)
    : getVoiceCloneModelOptions().some((model) => model.available);
}

function applyVoiceCloneProviderDefaults(forceTargetModel = false) {
  const options = getVoiceCloneModelOptions();
  if (!forceTargetModel && getVoiceCloneModelOption()) return;
  const preferred = localStorage.getItem("speaker.voiceCloneModel");
  const selected = (forceTargetModel && options.find((option) => option.provider === state.voiceClone.provider))
    || options.find((option) => option.id === preferred && option.available)
    || options.find((option) => option.recommended && option.available)
    || options.find((option) => option.available)
    || options[0];
  if (selected) selectVoiceCloneModel(selected.id);
}

function getVoiceCloneInstructionWeight(value, model = getVoiceCloneModelOption()) {
  const hanWeight = Number(model?.constraints?.instruction_han_weight || 2);
  return Array.from(String(value || "")).reduce((total, char) => total + (/\p{Script=Han}/u.test(char) ? hanWeight : 1), 0);
}

function getVoiceAssetExpiresAt(asset) {
  return globalThis.SpeakerVoiceCloneLibrary.getAssetExpiresAt(asset);
}

function isVoiceAssetExpired(asset, now = Date.now()) {
  return globalThis.SpeakerVoiceCloneLibrary.isAssetExpired(asset, now);
}

function assertVoiceAssetAvailable(asset) {
  if (!isVoiceAssetExpired(asset)) return;
  const error = new Error("声音数据已到期，仅保留72小时。请重新上传或新建声音，当前文案已保留。");
  error.code = "voice_asset_expired";
  throw error;
}

function formatDate(value) {
  if (!value) return "-";
  const date = parseBackendDate(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("zh-CN", {
    timeZone: APP_TIME_ZONE,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function parseBackendDate(value) {
  if (value instanceof Date) return value;
  const raw = String(value || "").trim();
  if (!raw) return new Date(Number.NaN);
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw);
  const normalized = raw.includes("T") ? raw : raw.replace(" ", "T");
  return new Date(hasTimezone ? normalized : `${normalized}Z`);
}

function getChinaDateParts(date = new Date()) {
  const formatter = new Intl.DateTimeFormat("zh-CN", {
    timeZone: APP_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    hourCycle: "h23",
  });
  return formatter.formatToParts(date).reduce((parts, item) => {
    if (item.type !== "literal") parts[item.type] = item.value;
    return parts;
  }, {});
}

function formatChinaIsoTimestamp(date = new Date()) {
  const parts = getChinaDateParts(date);
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}+08:00`;
}

function formatChinaFileTimestamp(date = new Date()) {
  return formatChinaIsoTimestamp(date).replaceAll(":", "-");
}

function formatDuration(seconds) {
  const value = Number(seconds || 0);
  if (!Number.isFinite(value) || value <= 0) return "-";
  const total = Math.round(value);
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes < 60) return `${minutes}:${String(rest).padStart(2, "0")}`;
  const hours = Math.floor(minutes / 60);
  return `${hours}:${String(minutes % 60).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function formatMillis(ms) {
  const value = Math.max(0, Number(ms || 0));
  const total = Math.round(value / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function normalizeError(error) {
  if (!error) return new Error("发生未知错误");
  if (error instanceof Error) return error;
  if (typeof error.message === "string") return new Error(error.message);
  return new Error(String(error));
}

function setCurrentProject(projectId) {
  const previousProjectId = String(state.currentProjectId || "");
  const normalizedId = String(projectId || "").trim();
  const project = state.projects.find((item) => String(item.project_id || item.id || "") === normalizedId) || null;
  state.currentProjectId = project ? String(project.project_id || project.id) : "";
  state.currentProjectName = project ? String(project.program_name || project.name || "").trim() : "";
  if (state.currentProjectId !== previousProjectId) {
    globalThis.SpeakerLocalRecognition?.contextChanged();
    invalidateLoadJobRequests();
    state.voiceClone.requestGeneration += 1;
    resetVoiceCloneProjectState();
    state.projectScripts = [];
    state.useProjectScripts = true;
  }
  if (state.currentProjectId) {
    state.lastProjectId = state.currentProjectId;
    localStorage.setItem(STORAGE_KEYS.lastProjectId, state.currentProjectId);
    localStorage.setItem(STORAGE_KEYS.currentProjectName, state.currentProjectName);
  } else {
    localStorage.removeItem(STORAGE_KEYS.currentProjectName);
  }
}

function resetVoiceCloneProjectState() {
  const draftObjectUrl = String(state.voiceClone.sampleDraft?.objectUrl || "");
  const trimmedObjectUrl = String(state.voiceClone.sampleDraft?.trimmedObjectUrl || "");
  if (draftObjectUrl) URL.revokeObjectURL(draftObjectUrl);
  if (trimmedObjectUrl) URL.revokeObjectURL(trimmedObjectUrl);
  for (const objectUrl of state.voiceClone.audioBlobUrls.values()) {
    URL.revokeObjectURL(objectUrl);
  }
  state.voiceClone.audioBlobUrls.clear();
  state.voiceClone.busy = "";
  state.voiceClone.error = "";
  state.voiceClone.pendingModelId = "";
  state.voiceClone.personId = "";
  state.voiceClone.sampleId = "";
  state.voiceClone.profileId = "";
  state.voiceClone.displayName = "";
  state.voiceClone.profileSearch = "";
  state.voiceClone.sampleSearch = "";
  state.voiceClone.samples = [];
  state.voiceClone.profiles = [];
  state.voiceClone.records = [];
  state.voiceClone.library = {
    groups: [],
    nextCursor: null,
    total: 0,
    availableGroupCount: 0,
    exceptionGroupCount: 0,
    reusableSampleCount: 0,
    creatingProfiles: [],
    loaded: false,
    loading: false,
    query: "",
    expandedGroupKey: "",
    profilesByGroup: {},
    samplesByGroup: {},
  };
  state.voiceClone.samplePage = {
    items: [],
    nextCursor: null,
    total: 0,
    loaded: false,
    loading: false,
    query: "",
    status: "",
  };
  state.voiceClone.createDrawer = {
    open: false,
    step: "existing_profile",
    profileQuery: "",
    sampleQuery: "",
  };
  state.voiceClone.addFlow = {
    open: false,
    stage: "pick",
    matchType: "",
    matchScore: null,
    matchedIdentity: null,
    sampleSlotsRemaining: null,
    error: "",
  };
  state.voiceClone.sharedPlayer = {
    sampleId: "",
    label: "",
    url: "",
  };
  state.voiceClone.sampleDraft = {
    projectId: "",
    file: null,
    fileName: "",
    objectUrl: "",
    duration: 0,
    speechProfile: null,
    trimStart: 0,
    trimEnd: 0,
    trimmedFile: null,
    trimmedObjectUrl: "",
    trimmedDuration: 0,
  };
}

function invalidateLoadJobRequests() {
  state.loadJobRequestGeneration += 1;
  state.loadJobTargetId = "";
}

function normalizeJobKind(job = null) {
  return String(typeof job === "string" ? job : job?.kind || "transcription").trim().toLowerCase() === "transcript"
    ? "transcript"
    : "transcription";
}

function isTranscriptJob(job = state.jobDetail) {
  return normalizeJobKind(job) === "transcript";
}

function isSubtitleJob(job = state.jobDetail) {
  return normalizeJobKind(job) === "transcription";
}

function getWorkflowKindForStep(step = state.activeStep) {
  return step === "transcript" || step === "transcriptReview" ? "transcript" : "transcription";
}

function setCurrentJobId(jobId, kind = "transcription", invalidateLoadRequests = true) {
  const previousJobId = String(state.currentJobId || "");
  const nextJobId = String(jobId || "").trim();
  if (invalidateLoadRequests && nextJobId !== previousJobId) {
    invalidateLoadJobRequests();
  }
  if (nextJobId !== previousJobId) {
    state.pickedSubtitleBackplateFile = null;
    state.pickedSubtitleBackplateSource = "";
    state.pickedSubtitleBackplateFiles = { with_speaker: null, without_speaker: null };
    state.pickedSubtitleBackplateSources = { with_speaker: "", without_speaker: "" };
    state.pickedSubtitleBackplateProjectIds = { with_speaker: "", without_speaker: "" };
    state.pickedSubtitleBackplateJobIds = { with_speaker: "", without_speaker: "" };
  }
  state.currentJobId = nextJobId;
  const normalizedKind = normalizeJobKind(kind);
  if (state.currentJobId) {
    if (normalizedKind === "transcript") {
      state.currentTranscriptJobId = state.currentJobId;
      localStorage.setItem(STORAGE_KEYS.transcriptJobId, state.currentJobId);
    } else {
      state.currentSubtitleJobId = state.currentJobId;
      localStorage.setItem(STORAGE_KEYS.subtitleJobId, state.currentJobId);
      localStorage.setItem(STORAGE_KEYS.jobId, state.currentJobId);
    }
  } else {
    if (normalizedKind === "transcript") {
      state.currentTranscriptJobId = "";
      localStorage.removeItem(STORAGE_KEYS.transcriptJobId);
    } else {
      state.currentSubtitleJobId = "";
      localStorage.removeItem(STORAGE_KEYS.subtitleJobId);
      localStorage.removeItem(STORAGE_KEYS.jobId);
    }
  }
}

function setDisplayedJobId(jobId) {
  state.currentJobId = String(jobId || "").trim();
}

function showToast(message, tone = "neutral") {
  state.toast.message = String(message || "");
  state.toast.tone = tone;
  state.toast.visible = Boolean(state.toast.message);
  if (state.toast.timer) window.clearTimeout(state.toast.timer);
  state.toast.timer = window.setTimeout(() => {
    state.toast.visible = false;
    renderToast();
  }, tone === "danger" ? 5200 : 3600);
  renderToast();
}

function getRecognitionFileSizeBytes(fileOrMeta) {
  if (!fileOrMeta) return 0;
  const candidates = [
    fileOrMeta.size,
    fileOrMeta.sizeBytes,
    fileOrMeta.size_bytes,
    fileOrMeta.browserFile?.size,
  ];
  for (const candidate of candidates) {
    const size = Number(candidate || 0);
    if (Number.isFinite(size) && size > 0) return size;
  }
  return 0;
}

function formatFileSize(bytes) {
  const size = Number(bytes || 0);
  if (!Number.isFinite(size) || size <= 0) return "";
  const gb = size / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb.toFixed(gb >= 10 ? 1 : 2)}GB`;
  const mb = size / (1024 * 1024);
  return `${mb.toFixed(mb >= 10 ? 0 : 1)}MB`;
}

function assertRecognitionFileSize(fileOrMeta) {
  const size = getRecognitionFileSizeBytes(fileOrMeta);
  if (size > ASR_RECORDED_FILE_MAX_BYTES) {
    const actual = formatFileSize(size);
    throw new Error(`当前音频文件${actual ? `约 ${actual}，` : ""}超过 2GB 上限。请先压缩为 16kHz 单声道 WAV，或拆分后再上传。`);
  }
}

function setSyncProgress(nextProgress) {
  state.syncProgress = { ...state.syncProgress, ...nextProgress };
  renderSyncOverlay();
}

function resetSyncProgress() {
  state.syncProgress = { active: false, attempt: 0, maxAttempts: 20, phase: "", message: "", progress: 0, pipeline: null };
  renderSyncOverlay();
}

function requestSyncAbort() {
  state.syncAbortRequested = true;
  resetSyncProgress();
}

function isCompletedJob(job = state.jobDetail) {
  return ["completed", "completed_with_warnings"].includes(String(job?.status || "").toLowerCase());
}

function isProcessingJob(job = state.jobDetail) {
  return ["pending", "running", "processing", "finalizing", "subtitle_enhancing"].includes(String(job?.status || "").toLowerCase());
}

function guessMimeType(fileName = "") {
  const lower = String(fileName).toLowerCase();
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".wav")) return "audio/wav";
  if (lower.endsWith(".mp3")) return "audio/mpeg";
  if (lower.endsWith(".m4a")) return "audio/mp4";
  if (lower.endsWith(".mp4")) return "video/mp4";
  if (lower.endsWith(".mov")) return "video/quicktime";
  if (lower.endsWith(".xml")) return "application/xml";
  return "application/octet-stream";
}

function stripFileExtension(fileName = "") {
  return String(fileName || "")
    .split(/[\\/]/)
    .pop()
    .replace(/\.[^.]+$/, "")
    .trim();
}

function sanitizeDownloadStem(value = "", fallback = "speaker-output") {
  const normalized = String(value || "")
    .replace(/[\\/:*?"<>|\r\n\t]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 48);
  return normalized || fallback;
}

function resolveJobSourceStem(job = {}) {
  const candidates = [
    job.video_source_export_path,
    job.source_export_path,
    job.video_source_local_path,
    job.source_local_path,
    state.pickedMediaFile?.name,
    job.program_name,
    job.id,
  ];
  for (const candidate of candidates) {
    const stem = sanitizeDownloadStem(stripFileExtension(candidate || ""), "");
    if (stem) return stem;
  }
  return "speaker-output";
}

function resolveCurrentJobSourceStem() {
  return resolveJobSourceStem(state.jobDetail || {});
}

function buildVoiceRecordFileName(record) {
  const text = String(record?.input_text || "")
    .replace(/[，。！？、；：“”‘’（）()\[\]【】《》〈〉…,.!?;:\s]+/g, "")
    .trim();
  const stem = sanitizeDownloadStem(text || record?.id || "voice-audio", "voice-audio");
  return `${stem}.${resolveVoiceRecordAudioExtension(record)}`;
}

function resolveVoiceRecordAudioExtension(record = {}) {
  const candidates = [record.output_audio_local_path, record.output_audio_public_url];
  for (const value of candidates) {
    const path = String(value || "").split(/[?#]/, 1)[0].toLowerCase();
    const match = path.match(/\.([a-z0-9]+)$/);
    if (match && VOICE_AUDIO_EXTENSIONS.has(match[1])) return match[1];
  }
  return "mp3";
}

function getCueSearchMatches(query = state.cueSearchQuery) {
  const normalizedQuery = String(query || "").trim().toLowerCase();
  if (!normalizedQuery) return [];
  return (state.cues || [])
    .map((cue, arrayIndex) => ({ cue, arrayIndex }))
    .filter(({ cue }) => {
      const haystack = [
        cue.clean_text,
        cue.full_text,
        cue.speaker_name,
        cue.speaker_key,
        state.cueSpeakerDrafts?.[String(cue.index || "")],
        state.cueTextDrafts?.[String(cue.index || "")],
      ].join(" ").toLowerCase();
      return haystack.includes(normalizedQuery);
    });
}


(function attachVoiceCloneLibrary(globalObject) {
  const CREATION_FLOW = Object.freeze([
    "existing_profile",
    "existing_sample",
    "upload",
  ]);
  const STATUS_LABELS = Object.freeze({
    ready: "可用",
    draft: "待创建",
    creating: "创建中",
    failed: "创建失败",
    deleted: "已归档",
    warning: "需检查",
    rejected: "不可用",
    unknown: "未检查",
  });
  const PROJECT_SCOPE_LABELS = Object.freeze({
    current_project: "当前项目",
    used_in_current_project: "本项目用过",
    shared: "共享音色",
  });
  const UNAVAILABLE_PROFILE_MESSAGE = "该音色已失效并被清理，请重新选择或上传";

  function translateStatus(status) {
    const normalized = String(status || "").trim().toLowerCase();
    return STATUS_LABELS[normalized] || "状态未知";
  }

  function projectScopeLabel(scope) {
    return PROJECT_SCOPE_LABELS[String(scope || "").trim()] || "共享音色";
  }

  function isSelectableProfile(profile = {}) {
    return Boolean(
      profile?.selectable
      && !isAssetExpired(profile)
      && profile.source_sample_ready
      && profile.status === "ready"
      && profile.voice_id,
    );
  }

  function getAssetExpiresAt(asset = {}) {
    const value = asset?.expires_at || asset?.created_at;
    if (!value) return null;
    // Use the application's UTC-aware date parser; keep the library usable alone.
    const raw = String(value).trim().replace(" ", "T");
    const date = typeof globalObject.parseBackendDate === "function"
      ? globalObject.parseBackendDate(value)
      : new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw) ? raw : `${raw}Z`);
    if (!Number.isFinite(date.getTime())) return null;
    return new Date(date.getTime() + (asset.expires_at ? 0 : 72 * 60 * 60 * 1000));
  }

  function isAssetExpired(asset, now = Date.now()) {
    const expiry = getAssetExpiresAt(asset);
    return Boolean(expiry && expiry.getTime() <= now);
  }

  function getSourcePreview(profile = {}, projectId = "") {
    const cloneSampleId = String(profile.source_clone_sample_id || "").trim();
    const normalizedProjectId = String(projectId || "").trim();
    if (cloneSampleId) {
      if (!normalizedProjectId) return null;
      return {
        sampleId: cloneSampleId,
        apiVersion: "v2",
        path: `/projects/${encodeURIComponent(normalizedProjectId)}/voice-cloning/samples/${encodeURIComponent(cloneSampleId)}/audio`,
      };
    }
    const voiceprintSampleId = String(profile.source_sample_id || "").trim();
    return voiceprintSampleId ? {
      sampleId: voiceprintSampleId,
      apiVersion: "v1",
      path: `/voiceprints/samples/${encodeURIComponent(voiceprintSampleId)}/audio`,
    } : null;
  }

  async function recoverUnavailableProfile(clear, refresh) {
    clear();
    try {
      await refresh();
    } catch (_error) {
      // The product message remains actionable even if the refresh is unavailable.
    }
    return UNAVAILABLE_PROFILE_MESSAGE;
  }

  function normalizeIdentityAliases(aliases) {
    return Array.isArray(aliases)
      ? aliases.map((alias) => String(alias || "").trim()).filter(Boolean)
      : [];
  }

  function normalizeProfile(raw = {}) {
    return {
      id: String(raw.id || ""),
      group_key: String(raw.group_key || "").trim(),
      identity_id: String(raw.identity_id || "").trim() || null,
      identity_display_name: String(raw.identity_display_name || "").trim() || null,
      identity_aliases: normalizeIdentityAliases(raw.identity_aliases),
      voice_name: String(raw.voice_name || "").trim() || "未命名音色",
      provider: String(raw.provider || ""),
      target_model: String(raw.target_model || ""),
      status: String(raw.status || "unknown").toLowerCase(),
      voice_id: raw.voice_id || null,
      provider_message: raw.provider_message || null,
      selectable: Boolean(raw.selectable),
      project_scope: String(raw.project_scope || "shared"),
      project_id: raw.project_id || null,
      source_sample_id: raw.source_sample_id || null,
      source_clone_sample_id: raw.source_clone_sample_id || null,
      source_display_name: raw.source_display_name || null,
      source_project_name: raw.source_project_name || null,
      source_original_filename: raw.source_original_filename || null,
      source_sample_ready: Boolean(raw.source_sample_ready),
      created_at: raw.created_at || null,
      expires_at: raw.expires_at || null,
      updated_at: raw.updated_at || null,
      last_used_at: raw.last_used_at || null,
    };
  }

  function normalizeSample(raw = {}) {
    return {
      id: String(raw.id || ""),
      display_name: String(raw.display_name || "").trim(),
      project_id: raw.project_id || null,
      program_key: raw.program_key || null,
      program_name: raw.program_name || null,
      original_filename: String(raw.original_filename || "").trim(),
      content_sha256: raw.content_sha256 || null,
      file_size_bytes: raw.file_size_bytes ?? null,
      duration_ms: raw.duration_ms ?? null,
      sample_rate_hz: raw.sample_rate_hz ?? null,
      channel_count: raw.channel_count ?? null,
      clone_readiness_status: String(raw.clone_readiness_status || "unknown").toLowerCase(),
      clone_readiness_message: raw.clone_readiness_message || null,
      audio_public_url: raw.audio_public_url || null,
      created_at: raw.created_at || null,
      expires_at: raw.expires_at || null,
      last_used_at: raw.last_used_at || null,
      reused_existing: Boolean(raw.reused_existing),
      identity_id: String(raw.identity_id || "").trim() || null,
      identity_display_name: String(raw.identity_display_name || "").trim() || null,
      identity_aliases: normalizeIdentityAliases(raw.identity_aliases),
      match_type: String(raw.match_type || ""),
      match_score: raw.match_score ?? null,
      sample_slots_remaining: raw.sample_slots_remaining ?? null,
      message: raw.message || null,
      in_use: Boolean(raw.in_use),
      profile_count: Number(raw.profile_count || 0),
    };
  }

  function normalizeGroup(raw = {}) {
    const recommendedProfile = raw.recommended_profile ? normalizeProfile(raw.recommended_profile) : null;
    const groupKey = String(raw.group_key || recommendedProfile?.voice_name || "");
    if (recommendedProfile) recommendedProfile.group_key = recommendedProfile.group_key || groupKey;
    return {
      group_key: groupKey,
      expires_at: raw.expires_at || null,
      identity_id: String(raw.identity_id || "").trim() || null,
      identity_display_name: String(raw.identity_display_name || "").trim() || null,
      identity_aliases: normalizeIdentityAliases(raw.identity_aliases),
      display_name: String(raw.display_name || recommendedProfile?.voice_name || "").trim() || "未命名音色",
      version_count: Math.max(0, Number(raw.version_count ?? (recommendedProfile ? 1 : 0))),
      health_status: String(raw.health_status || recommendedProfile?.status || "draft").toLowerCase(),
      status_label: translateStatus(raw.health_status || recommendedProfile?.status || "draft"),
      recommended_profile: recommendedProfile,
    };
  }

  function groupMatchesQuery(group = {}, query = "") {
    const normalizedQuery = String(query || "").trim().toLocaleLowerCase("zh-CN");
    if (!normalizedQuery) return true;
    const profile = group.recommended_profile || {};
    return [
      group.display_name,
      group.health_status,
      profile.provider,
      profile.target_model,
      profile.source_display_name,
      profile.source_project_name,
      profile.source_original_filename,
    ].join(" ").toLocaleLowerCase("zh-CN").includes(normalizedQuery);
  }

  function resolveLibraryGroups(serverGroups, legacyProfiles, query = "") {
    if (Array.isArray(serverGroups)) return serverGroups;
    return groupProfiles(legacyProfiles).filter((group) => groupMatchesQuery(group, query));
  }

  function reconcileLibrarySelection(groups, {
    selectedProfileId = "",
    selectedProfile = null,
    preserveSelectedProfile = false,
    allowAutoSelect = true,
  } = {}) {
    const normalizedSelectedId = String(selectedProfileId || "");
    const libraryGroups = Array.isArray(groups) ? groups : [];
    const selectedIdentityId = String(selectedProfile?.identity_id || "").trim();
    const selectedGroupKey = String(selectedProfile?.group_key || "").trim();
    const profiles = libraryGroups
      .map((group) => group.recommended_profile)
      .filter(isSelectableProfile);
    if (
      (preserveSelectedProfile || (
        selectedIdentityId
        && libraryGroups.some((group) => group.identity_id === selectedIdentityId)
      ) || (
        selectedGroupKey
        && libraryGroups.some((group) => group.group_key === selectedGroupKey)
      ))
      && selectedProfile?.id === normalizedSelectedId
      && isSelectableProfile(selectedProfile)
      && !profiles.some((profile) => profile.id === normalizedSelectedId)
    ) {
      profiles.push(selectedProfile);
    }
    const profileId = profiles.some((profile) => profile.id === normalizedSelectedId)
      ? normalizedSelectedId
      : (!normalizedSelectedId && allowAutoSelect ? profiles[0]?.id || "" : "");
    return { profiles, profileId };
  }

  function limitGroups(groups, limit = 30) {
    if (!Array.isArray(groups)) return [];
    const safeLimit = Math.max(0, Number.parseInt(limit, 10) || 0);
    return groups.slice(0, safeLimit);
  }

  function profileRank(profile) {
    const readyScore = isSelectableProfile(profile) ? 1 : 0;
    const timestamp = Date.parse(
      profile.last_used_at
      || profile.updated_at
      || profile.created_at
      || "",
    );
    return [readyScore, Number.isFinite(timestamp) ? timestamp : 0, profile.id];
  }

  function compareRank(left, right) {
    for (let index = 0; index < left.length; index += 1) {
      if (left[index] === right[index]) continue;
      return left[index] > right[index] ? 1 : -1;
    }
    return 0;
  }

  function groupProfiles(profiles) {
    const grouped = new Map();
    for (const rawProfile of Array.isArray(profiles) ? profiles : []) {
      const profile = normalizeProfile(rawProfile);
      const groupKey = profile.voice_name
        .normalize("NFKC")
        .trim()
        .replace(/\s+/g, " ")
        .toLocaleLowerCase("zh-CN");
      if (!groupKey) continue;
      const existing = grouped.get(groupKey) || [];
      existing.push(profile);
      grouped.set(groupKey, existing);
    }
    return [...grouped.entries()].flatMap(([groupKey, members]) => {
      const selectableMembers = members.filter(isSelectableProfile);
      if (!selectableMembers.length) return [];
      const recommended = selectableMembers.reduce((best, candidate) => (
        compareRank(profileRank(candidate), profileRank(best)) > 0
          ? candidate
          : best
      ));
      return normalizeGroup({
        group_key: groupKey,
        display_name: recommended.voice_name,
        version_count: members.length,
        health_status: recommended.status,
        recommended_profile: recommended,
      });
    });
  }

  function updateDrawerState(current = {}, patch = {}) {
    const requestedStep = patch.step ?? current.step ?? CREATION_FLOW[0];
    return {
      open: Boolean(patch.open ?? current.open ?? false),
      step: CREATION_FLOW.includes(requestedStep)
        ? requestedStep
        : CREATION_FLOW[0],
      profileQuery: String(patch.profileQuery ?? current.profileQuery ?? ""),
      sampleQuery: String(patch.sampleQuery ?? current.sampleQuery ?? ""),
    };
  }

  globalObject.SpeakerVoiceCloneLibrary = Object.freeze({
    CREATION_FLOW,
    getAssetExpiresAt,
    isAssetExpired,
    getSourcePreview,
    groupMatchesQuery,
    groupProfiles,
    isSelectableProfile,
    limitGroups,
    normalizeGroup,
    normalizeProfile,
    normalizeSample,
    projectScopeLabel,
    reconcileLibrarySelection,
    recoverUnavailableProfile,
    resolveLibraryGroups,
    translateStatus,
    updateDrawerState,
  });
}(globalThis));


(function attachSubtitleQuality(globalObject) {
  const ISSUE_LABELS = {
    invalid_timing: "字幕时间范围无效", overlapping_time: "相邻字幕时间重叠",
    isolated_single_cjk_cue: "字幕被切成孤立单字", overlong_cjk_cue: "字幕超过建议字数",
    protected_term_split: "专名或固定词被拆开", lexical_atom_split: "完整词语被拆开",
    lexicon_hard_term_split: "项目保护词被拆开", lexicon_strong_term_split: "项目重点词被拆开",
    cross_speaker_cue: "同一条字幕包含不同人物", speaker_evidence_mismatch: "说话人与来源不一致",
    token_loss: "原始识别文字有遗漏", token_duplicate: "原始识别文字重复使用",
    token_reorder: "文字顺序与原始识别不一致", unauthorized_text_insert: "出现缺少来源依据的文字",
    cumulative_timing_drift: "字幕时间与来源存在偏移", display_fact_mismatch: "显示文字与来源不一致",
  };
  const REPAIR_LABELS = { inline_repetition: "整理句内重复", adjacent_echo: "整理相邻重复",
    project_lexicon: "项目词库修正", script_roster: "台本人名修正" };
  const list = value => Array.isArray(value) ? value.filter(item => item && typeof item === "object") : [];
  const millis = value => value !== null && value !== undefined && String(value).trim() !== "" && Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : null;
  const total = (value, items) => Math.max(items.length, Number.isFinite(Number(value)) ? Number(value) : 0);

  function historyRow(item, source) {
    const startMs = millis(item.start_ms), endMs = millis(item.end_ms);
    return {
      before: String(item.source_text ?? item.before ?? ""), after: String(item.replacement_text ?? item.after ?? ""),
      reason: source === "evidence" ? "依据原始识别与台本证据修正" : (Array.isArray(item.reasons) ? item.reasons : [])
        .map(reason => REPAIR_LABELS[reason] || (String(reason).startsWith("bilingual_") ? "双语表达整理" : "广播文本整理")).join("、") || "广播文本整理",
      startMs: startMs !== null && endMs !== null && endMs > startMs ? startMs : null,
    };
  }

  function build({ detail, cues = [], projectId = "" }) {
    const artifacts = detail?.pipeline_artifacts || {};
    const scoped = Boolean(detail?.id) && (!detail.project_id || detail.project_id === projectId)
      && (!artifacts.source_revision?.job_id || artifacts.source_revision.job_id === detail.id);
    const report = artifacts.cue_validation_report;
    const snapshot = artifacts.subtitle_cues_snapshot;
    const snapshotItems = list(snapshot?.items);
    const savedCues = new Map(list(cues).map(cue => [String(cue.index), cue]));
    const matches = Array.isArray(snapshot?.items) && Number(snapshot.count) === cues.length
      && (snapshot.truncated ? snapshotItems.length > 0 && snapshotItems.length <= cues.length : snapshotItems.length === cues.length)
      && snapshotItems.every(cue => {
        const current = savedCues.get(String(cue.index));
        return current && ["clean_text", "start_ms", "end_ms", "speaker_key", "speaker_name"]
          .every(key => String(current[key] ?? "") === String(cue[key] ?? ""));
      });
    const currentScope = (!report?.scope || report.scope === "current_timeline")
      && (!report?.timeline_revision || report.timeline_revision === artifacts.timeline_revision);
    const status = !scoped ? "stale" : !Array.isArray(report?.issues) ? "missing" : !matches || !currentScope ? "stale" : "ready";
    const issues = status === "ready" ? list(report.issues).map(issue => {
      const indices = [issue.index, issue.left_index, issue.right_index].filter(value => value !== null && value !== undefined);
      const involved = [...new Set(indices.map(String))].map(index => savedCues.get(index)).filter(Boolean);
      const starts = involved.map(cue => millis(cue.start_ms)).filter(value => value !== null);
      return { message: ISSUE_LABELS[issue.code] || String(issue.message || "字幕存在需要复核的问题"),
        severity: issue.severity === "P0" ? "导出前需处理" : "建议复核",
        text: involved.map(cue => cue.clean_text || "").join(" / ") || String(issue.term || ""),
        startMs: starts.length ? Math.min(...starts) : null, cueIndex: involved[0]?.index };
    }) : [];
    const evidence = scoped ? artifacts.evidence_subtitle_v2 : null;
    const broadcast = scoped ? artifacts.dialogue_broadcast_normalization : null;
    const corrections = list(evidence?.corrections), changes = list(broadcast?.changes);
    const rows = [...corrections.map(item => historyRow(item, "evidence")), ...changes.map(item => historyRow(item, "broadcast"))];
    const retained = [...list(evidence?.rejected_corrections), ...list(broadcast?.skipped_changes)].map(item => ({
      ...historyRow(item, "evidence"), reason: item.reason === "numeric_fact_change" ? "数字事实可能变化，已保留原文"
        : item.reason === "speaker_not_confirmed_same" ? "人物身份未确认相同，已保留原文" : "未自动修改，请试听复核",
    }));
    return {
      jobId: detail?.id || "", projectId,
      current: { status, issues, count: total(report?.issue_count, issues),
        truncated: Boolean(report?.truncated || total(report?.issue_count, issues) > issues.length),
        partialSnapshot: Boolean(snapshot?.truncated) },
      history: { rows, retained, truncated: Boolean(evidence?.truncated || broadcast?.truncated),
        generationIssueCount: Array.isArray(evidence?.validation?.issues) ? total(evidence.validation.issue_count, evidence.validation.issues) : null },
    };
  }

  function playbackButton(row, report) {
    if (row.startMs === null) return '<small class="subtitle-quality-location">无可靠位置，不能自动定位</small>';
    return `<button class="quiet-button small" type="button" data-subtitle-quality-seek data-cue-start="${row.startMs}" data-qc-job-id="${escapeHtml(report.jobId)}" data-qc-project-id="${escapeHtml(report.projectId)}"${row.cueIndex !== undefined ? ` data-qc-cue-index="${escapeHtml(row.cueIndex)}"` : ""}>${escapeHtml(formatMillis(row.startMs))} · 试听定位</button>`;
  }

  function render(input) {
    const report = build(input), current = report.current, history = report.history;
    const summary = current.status === "missing" ? "无检查结果" : current.status === "stale" ? "等待当前字幕的检查结果"
      : current.truncated ? `${current.count} 项待复核 · 仅展示部分记录` : current.issues.length ? `${current.issues.length} 项待复核` : "未发现结构异常";
    const currentNotice = current.status === "missing" ? "此任务没有检查结果，不能据此判断字幕是否存在问题。"
      : current.status === "stale" ? "报告与当前任务或字幕快照不一致，请保存或重新同步后查看。"
        : current.truncated ? `仅展示部分记录：已载入 ${current.issues.length} 项，报告共 ${current.count} 项。`
          : current.issues.length ? "以下问题来自当前已保存字幕的检查结果。" : "未发现结构异常；人名、数字及实际听写内容仍需核对原音。";
    return `<details class="subtitle-quality-panel"><summary><strong>字幕质检</strong><span>${escapeHtml(summary)}</span></summary>
      <div class="subtitle-quality-body"><section><h4>当前已保存字幕 · 待复核</h4><p>${escapeHtml(currentNotice)}</p>
      <small>未保存的修改尚未重新检查，请先保存字幕。</small>
      ${current.partialSnapshot && current.status === "ready" ? '<p>字幕快照仅包含部分记录，未逐条核对完整快照范围。</p>' : ""}
      ${current.issues.length ? `<ul class="subtitle-quality-list">${current.issues.map(row => `<li><strong>${escapeHtml(row.severity)} · ${escapeHtml(row.message)}</strong>${row.text ? `<p>${escapeHtml(row.text)}</p>` : ""}${playbackButton(row, report)}</li>`).join("")}</ul>` : ""}</section>
      <section><h4>生成修复历史</h4><p>以下为生成时的调整，不代表当前字幕仍是这些文字。</p>
      ${history.generationIssueCount !== null ? `<small>生成时检查（历史）：${history.generationIssueCount} 项提示，不作为当前检查结果。</small>` : ""}
      ${history.truncated ? '<p>仅展示部分记录，生成历史已截断。</p>' : ""}
      ${history.rows.length ? `<ul class="subtitle-quality-list">${history.rows.map(row => `<li><strong>${escapeHtml(row.reason)}</strong><div class="subtitle-quality-change"><div><small>调整前</small><p>${escapeHtml(row.before || "（空）")}</p></div><div><small>调整后</small><p>${escapeHtml(row.after || "（已移除）")}</p></div></div>${playbackButton(row, report)}</li>`).join("")}</ul>` : '<p>无生成修复记录。</p>'}
      ${history.retained.length ? `<h4>生成时保留原文 · 建议人工复核</h4><p>这些候选未自动采用；后续人工修改请以当前字幕为准。</p><ul class="subtitle-quality-list">${history.retained.map(row => `<li><strong>${escapeHtml(row.reason)}</strong><div class="subtitle-quality-change"><div><small>当时保留的原文</small><p>${escapeHtml(row.before || "（空）")}</p></div><div><small>未采用的候选</small><p>${escapeHtml(row.after || "（移除文字）")}</p></div></div>${playbackButton(row, report)}</li>`).join("")}</ul>` : ""}</section></div></details>`;
  }

  function isCurrentTarget(dataset, { detail, jobId, projectId }) {
    return Boolean(jobId && projectId && detail?.id === jobId && dataset.qcJobId === jobId
      && dataset.qcProjectId === projectId && (!detail.project_id || detail.project_id === projectId)
      && millis(dataset.cueStart) !== null);
  }

  globalObject.SpeakerSubtitleQuality = Object.freeze({ build, render, isCurrentTarget });
}(globalThis));


(function attachLocalRecognition(globalObject) {
  const local = state.localRecognition ||= { mode: "cloud", offline: false, capability: null, busy: "", error: "", run: 0, context: 0, sourceUrls: new Map() };
  let connectionProbe = null;
  let recoveryTimer = null;
  const bridge = () => window.SpeakerDesktopBridge;
  const available = () => Boolean(bridge()?.isAvailable?.());
  const isLocalId = id => String(id || "").startsWith("local-");
  const project = id => state.projects.find(item => String(item.project_id || item.id) === String(id));
  const wantsLocal = () => local.mode === "apple" || local.offline || Boolean(project(state.currentProjectId)?.local_only);
  const native = (operation, payload = {}, scope = state.apiBase) => {
    if (!available() || !bridge().offlineSubtitles) throw new Error("本地字幕只在新版桌面客户端中可用");
    return bridge().offlineSubtitles(scope, operation, payload);
  };
  const value = (selector, fallback = "") => document.querySelector(selector)?.value || fallback;
  const normalizedProjects = items => (Array.isArray(items) ? items : []).map(item => ({ ...item,
    id: item.id || item.project_id, project_id: item.id || item.project_id,
    name: item.name || item.program_name, program_name: item.name || item.program_name,
    job_count: Number(item.job_count || 0),
  }));

  async function detect() {
    if (!available()) {
      local.capability = { available: false, reason: "Apple 本机识别仅支持桌面客户端，浏览器请使用云端识别。" };
      return local.capability;
    }
    try {
      const [speech, core] = await Promise.all([bridge().detectLocalSpeech(), native("capability")]);
      local.capability = { ...speech, available: Boolean(speech.available && core.available), download_available: Boolean(speech.download_available && core.available), reason: core.available ? speech.reason : core.reason || "本地字幕工具尚未就绪" };
    }
    catch (error) { local.capability = { available: false, reason: normalizeError(error).message }; }
    return local.capability;
  }
  async function initialize() {
    local.offline = typeof navigator !== "undefined" && navigator.onLine === false;
    window.addEventListener("offline", async () => {
      await probeConnection();
      if (local.offline) await refreshOffline().catch(() => {});
    });
    window.addEventListener("online", () => { reconnect().catch(() => {}); });
    if (recoveryTimer === null) recoveryTimer = window.setInterval(() => local.offline ? reconnect().catch(() => {}) : undefined, 15000);
    await Promise.all([detect(), probeConnection()]);
  }
  async function connectionFailed(failedUrl = "") {
    if (failedUrl && !/\/health(?:\?|$)/.test(failedUrl)) {
      await probeConnection();
      return;
    }
    local.offline = true;
    renderConnection();
  }
  function connectionSucceeded() { local.offline = false; renderConnection(); }
  function probeConnection() {
    const scope = state.apiBase;
    if (connectionProbe?.scope === scope) return connectionProbe.promise;
    const probe = { scope, promise: null };
    probe.promise = request("/health", { timeoutMs: 5000 }).then(() => true, () => false).finally(() => {
      if (connectionProbe === probe) connectionProbe = null;
    });
    connectionProbe = probe;
    return probe.promise;
  }
  async function reconnect() {
    const scope = state.apiBase;
    if (!await probeConnection() || state.apiBase !== scope) return;
    await Promise.allSettled([refreshProjects(), refreshJobs(), refreshServiceStatus()]);
    renderAll();
  }
  async function refreshOffline() {
    if (!available()) { renderAll(); return; }
    await Promise.allSettled([loadCachedProjects(), refreshJobs()]);
    renderAll();
  }
  async function cacheProjects(items) {
    if (!available()) return items;
    const scope = state.apiBase;
    const previous = normalizedProjects(await native("projects", {}, scope));
    const merged = [...normalizedProjects(items), ...previous.filter(item => item.local_only && !items.some(server => String(server.id || server.project_id) === item.id))];
    await native("save_projects", { projects: merged }, scope);
    return merged;
  }
  async function loadCachedProjects() {
    const scope = state.apiBase;
    const projects = normalizedProjects(await native("projects", {}, scope));
    if (state.apiBase !== scope) return [];
    state.projects = projects;
    state.projectLoad = { status: "ready", error: "" };
    return projects;
  }
  async function createProject(name) {
    const scope = state.apiBase;
    const projects = normalizedProjects(await native("projects", {}, scope));
    const created = { id: crypto.randomUUID(), name: String(name).trim(), local_only: true };
    if (!created.name) throw new Error("请输入项目名称");
    const merged = normalizedProjects([...projects, created]);
    await native("save_projects", { projects: merged }, scope);
    if (scope !== state.apiBase) return;
    state.projects = merged; state.projectLoad = { status: "ready", error: "" };
    setCurrentProject(created.id);
  }
  async function mergeJobs(items = []) {
    if (!available()) return items;
    const jobs = await native("list");
    return [...(Array.isArray(jobs) ? jobs : []), ...items].sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  }
  async function getJob(id, projectId = state.currentProjectId, scope = state.apiBase) {
    const job = await native("get", { id, project_id: projectId }, scope);
    if (String(job.project_id) !== String(projectId) || job.id !== id) throw new Error("本地任务不属于当前项目");
    const path = await bridge().offlineSourcePath(scope, id, projectId).catch(() => null);
    const url = path ? window.__TAURI__?.core?.convertFileSrc?.(typeof path === "string" ? path : path?.path) : "";
    if (url) local.sourceUrls.set(`${scope}\n${projectId}\n${id}`, url);
    return job;
  }
  function sourceUrl(detail) {
    return local.sourceUrls.get(`${state.apiBase}\n${state.currentProjectId}\n${detail?.id}`) || "";
  }
  function routeRequest(path, options = {}) {
    const match = /^\/transcriptions\/jobs\/(local-[^/?]+)(\/[^?]*)?(?:\?(.*))?$/.exec(path);
    if (!match) return null;
    return (async () => {
      const id = decodeURIComponent(match[1]), suffix = match[2] || "", scope = state.apiBase, projectId = state.currentProjectId;
      const context = local.context;
      const requireCurrent = () => { if (scope !== state.apiBase || projectId !== state.currentProjectId || context !== local.context) throw new Error("项目或服务器已变化，请在原项目重新打开本机任务"); };
      const method = options.method || "GET", payload = { id, project_id: projectId };
      const job = await getJob(id, projectId, scope);
      requireCurrent();
      if (!suffix && method === "DELETE") return native("delete", payload, scope);
      if ((!suffix || suffix === "/sync") && ["GET", "POST"].includes(method)) return job;
      const params = new URLSearchParams(match[3] || "");
      const preview = result => ({ cues: result.local_cues || [], content: params.get("variant") === "clean" ? result.local_clean_srt : result.local_full_srt });
      if (suffix === "/speakers" && method === "GET") return job.local_speakers || { speakers: [], mappings: {}, memory_suggestions: [] };
      if (suffix === "/subtitles/preview") return preview(job);
      const action = { "/speakers": "speakers", "/subtitles/speakers": "subtitle_speakers", "/subtitles/text": "subtitle_text", "/subtitles/text/delete-fillers": "delete_fillers" }[suffix];
      if (action && ["PUT", "POST"].includes(method)) {
        const body = options.body ? JSON.parse(options.body) : {};
        const updated = await native("edit", { ...payload, action, changes: body.mappings || body.cues || [] }, scope);
        requireCurrent();
        return action === "speakers" ? updated.local_speakers : { ...preview(updated), deleted_count: Math.max(0, (job.local_cues || []).length - (updated.local_cues || []).length) };
      }
      throw new Error("此功能需要联网并先同步服务器校对，本机字幕原稿会保留");
    })();
  }
  async function download(path) {
    const match = /^\/transcriptions\/jobs\/(local-[^/?]+)\/(subtitles|premiere-xml)\/download(?:\?(.*))?$/.exec(path);
    if (!match) throw new Error("本机任务当前支持 SRT、VTT 和 XML 导出；其他格式需要联网同步");
    const params = new URLSearchParams(match[3] || "");
    const result = await native("export", { id: decodeURIComponent(match[1]), project_id: state.currentProjectId,
      format: match[2] === "premiere-xml" ? "xml" : params.get("format") || "srt", variant: params.get("variant") || "full" });
    const blob = decodeBase64ToBlob(result.content_base64, result.mime_type);
    const saved = await saveBlobWithDesktopDialog(blob, result.filename);
    if (saved === null) downloadBlob(blob, result.filename);
    return saved !== false;
  }
  async function create() {
    if (!available() || !local.capability?.available) throw new Error(local.capability?.reason || "Apple 本机识别尚未就绪，请先检测设备与模型");
    if (local.busy) throw new Error("本机识别正在进行，请等待或取消");
    const file = state.pickedMediaFile;
    if (!file?.path) throw new Error("请在桌面客户端重新选择本地音频文件");
    const projectId = state.currentProjectId, scope = state.apiBase, context = local.context;
    if (!projectId || (state.pickedMediaProjectId && state.pickedMediaProjectId !== projectId)) throw new Error("项目已变化，请在当前项目重新选择音频");
    assertRecognitionFileSize(file);
    const run = ++local.run;
    const job = { id: `local-${crypto.randomUUID()}`, project_id: projectId, program_name: state.currentProjectName,
      kind: "transcription", original_filename: file.name, source_local_path: file.path, source_export_path: file.path, client_source_path: file.path,
      speaker_name: String(value("#local-speaker-name-input")).trim(), export_frame_rate: value("#export-frame-rate-select", "25"),
      subtitle_length_mode: value("#subtitle-length-mode-select", "smart"), subtitle_max_chars: Number(value("#subtitle-max-chars-input", "14")),
      use_project_scripts: state.useProjectScripts, lyrics_correction_mode: state.lyricsCorrectionMode };
    const scriptFiles = [...(state.pickedTaskScriptFiles || [])];
    const initiallyOnline = !local.offline && !project(projectId)?.local_only;
    local.busy = "正在本机识别，完成后保存本地字幕…"; local.error = ""; renderAll();
    try {
      const recognition = await bridge().transcribeLocalSpeech(file.path);
      if (run !== local.run) throw new Error("本机识别已取消");
      local.busy = "正在整理本地字幕与规则质检…"; renderAll();
      let saved = await native("build", { job, recognition }, scope);
      if (run !== local.run) throw new Error("本机识别已取消；已保存的原稿可在后台任务查看");
      if (initiallyOnline && !local.offline && scope === state.apiBase) {
        local.busy = "正在上传已识别素材，交服务器保存与 DeepSeek 校对…"; renderAll();
        try { saved = await sync(saved, projectId, { scope, scriptFiles }); }
        catch (error) { local.error = `已保留本机原稿；服务器同步未完成：${normalizeError(error).message}`; }
      }
      const detached = state.currentProjectId !== projectId || state.apiBase !== scope || local.context !== context;
      if (!detached) {
        state.jobDetail = decorateJobDetail(saved); setCurrentJobId(saved.id, "transcription", false);
        state.pickedMediaFile = null; state.pickedMediaProjectId = ""; state.pickedMediaSource = "";
        state.pickedTaskScriptFiles = []; state.pickedTaskScriptProjectId = "";
      }
      if (scope === state.apiBase) await refreshJobs();
      return { ...saved, _uiProjectId: projectId, _uiDetached: detached };
    } finally { if (run === local.run) { local.busy = ""; renderAll(); } }
  }
  async function cancel() { ++local.run; local.busy = ""; await Promise.allSettled([bridge()?.cancelLocalSpeech?.(), native("cancel")]); local.error = "已取消本机识别"; renderAll(); }
  async function sync(job = state.jobDetail, targetProjectId = "", { scope = state.apiBase, scriptFiles = [] } = {}) {
    if (local.offline) throw new Error("请恢复联网后同步服务器校对");
    const target = project(targetProjectId || job?.project_id);
    if (!target || target.local_only) throw new Error("请明确选择服务器项目后同步，本机项目不会按名称自动合并");
    const projectId = String(target.id || target.project_id);
    const pathResult = await bridge().offlineSourcePath(scope, job.id, job.project_id);
    const path = typeof pathResult === "string" ? pathResult : pathResult?.path;
    const blob = await loadClientBridgeFile({ path, name: job.original_filename });
    const form = new FormData();
    form.append("file", new File([blob], job.original_filename || "local-audio.wav", { type: blob.type }));
    form.append("recognition", JSON.stringify(job.local_recognition));
    form.append("client_job_id", job.id.replace(/^local-/, ""));
    form.append("client_source_path", path);
    form.append("job_kind", "transcription");
    const profile = job.processing_metrics?.subtitle_length_profile || {};
    const settings = { ...job, speaker_name: job.speaker_name ?? job.raw_payload?._apple_speech?.speaker_name,
      subtitle_length_mode: job.subtitle_length_mode ?? profile.mode, subtitle_max_chars: job.subtitle_max_chars ?? profile.max_chars };
    for (const key of ["speaker_name", "export_frame_rate", "subtitle_length_mode", "subtitle_max_chars", "lyrics_correction_mode"]) if (settings[key] !== undefined) form.append(key, String(settings[key]));
    form.append("use_project_scripts", job.use_project_scripts === false ? "false" : "true");
    for (const file of scriptFiles) {
      const content = file.browserFile || (file.path ? await loadClientBridgeFile(file) : null);
      if (content) form.append("script_files", new File([content], file.name, { type: content.type }));
    }
    if (scope !== state.apiBase) throw new Error("服务器已切换，本机稿已保留，请在原服务器配置下同步");
    const response = await requestV2(`/projects/${encodeURIComponent(projectId)}/transcriptions/local-jobs`, { method: "POST", body: form });
    const server = response.job || response;
    await native("mark_synced", { id: job.id, project_id: job.project_id, server_job_id: server.id, server_project_id: projectId }, scope);
    return server;
  }
  function renderControls() {
    const selected = wantsLocal(), capability = local.capability;
    return `<section class="local-recognition-controls"><label><strong>识别方式</strong><select id="recognition-engine-select" ${local.busy ? "disabled" : ""}>
      <option value="cloud" ${selected ? "" : "selected"} ${local.offline || project(state.currentProjectId)?.local_only ? "disabled" : ""}>云端 Fun-ASR · 区分说话人</option>
      <option value="apple" ${selected ? "selected" : ""} ${available() ? "" : "disabled"}>Apple 本机识别</option></select></label>
      <p>${escapeHtml(capability?.available ? "本机中文模型已就绪。Apple 不会自动区分多人，请使用单人音频或已分好人物的独立音轨。" : capability?.reason || "正在检测本机与中文模型…")}</p>
      <button type="button" class="quiet-button small" data-local-action="detect" ${local.busy ? "disabled" : ""}>重新检测</button>
      ${capability?.download_available ? `<button type="button" class="secondary-button small" data-local-action="prepare" ${local.busy || local.offline ? "disabled" : ""}>联网下载中文模型</button>` : ""}
      ${selected ? `<label><span>本音频人物姓名（可选）</span><input id="local-speaker-name-input" value="${escapeHtml(local.speakerName || "")}" placeholder="未填写则显示未区分说话人" /></label><p>${local.offline || project(state.currentProjectId)?.local_only ? "素材与字幕仅保存本机；规则质检尚未经过云端 AI 校对，离线不使用项目台本或热词。" : "音频在本机识别后，素材与识别结果会上传当前项目，由服务器保存并交 DeepSeek 校对；本机原稿保留。"}</p>` : ""}
      ${local.busy ? `<p role="status">${escapeHtml(local.busy)}</p>${/正在本机识别|正在整理本地/.test(local.busy) ? '<button type="button" class="secondary-button small" data-local-action="cancel">取消本机识别</button>' : ""}` : ""}
      ${local.error ? `<p class="local-recognition-error" role="alert">${escapeHtml(local.error)}</p>` : ""}</section>`;
  }
  function renderBanner() {
    const job = state.jobDetail, localJob = isLocalId(job?.id), localProject = project(state.currentProjectId)?.local_only;
    const offline = local.offline ? `<div class="local-mode-banner" role="status"><strong>离线模式</strong><span>服务器暂不可达。素材、识别和字幕编辑仅在本机；未进行云端 AI 校对。声音库、云端台本及其他服务器功能暂不可用。</span><button type="button" class="secondary-button small" data-local-action="reconnect">重试连接</button></div>` : "";
    const projectNotice = localProject ? '<p class="local-mode-banner"><strong>仅本机项目</strong><span>联网同步时需明确选择服务器项目，不会按名称自动合并。</span></p>' : "";
    if (!localJob) return offline + projectNotice;
    const targets = state.projects.filter(item => !item.local_only);
    return offline + projectNotice + `<div class="local-mode-banner local-job-banner"><strong>本机字幕原稿 · 未经云端 AI 校对</strong><span>来源：Apple 本机识别。当前为本地规则处理与质检；同步会上传音频并基于原始识别创建服务器校对版，不包含此稿后续的人工改字。本机原稿及修改继续保留。</span>
      ${job.server_job_id ? `<span>已生成服务器校对版，本机原稿继续保留。</span>` : ""}
      ${job.server_project_id ? `<span>服务器项目：${escapeHtml(project(job.server_project_id)?.name || project(job.server_project_id)?.program_name || job.server_project_id)}</span>` : localProject ? `<label>同步到服务器项目<select id="local-sync-project-select"><option value="">请选择明确归属</option>${targets.map(item => `<option value="${escapeHtml(item.id || item.project_id)}">${escapeHtml(item.name || item.program_name)}</option>`).join("")}</select></label>` : ""}
      <button type="button" class="primary-button small" data-local-action="sync" ${local.offline || local.busy ? "disabled" : ""}>${job.server_job_id ? "打开服务器校对版" : "同步服务器校对"}</button>
      ${local.error ? `<span role="alert">${escapeHtml(local.error)}</span>` : ""}</div>`;
  }
  function renderConnection() {
    if (typeof elements === "undefined" || !elements.simpleWorkspace?.querySelector) return;
    elements.simpleWorkspace.querySelector("#local-mode-notices")?.remove();
    const banner = renderBanner();
    if (banner) elements.simpleWorkspace.insertAdjacentHTML("afterbegin", `<div id="local-mode-notices">${banner}</div>`);
  }
  async function handleClick(target) {
    const button = target.closest?.("[data-local-action]");
    if (!button) return false;
    const action = button.dataset.localAction;
    await withUiTask(button, "处理中…", async () => {
      local.error = "";
      try {
        if (action === "detect") await detect();
        else if (action === "prepare") { if (local.offline) throw new Error("准备中文模型需要联网，请先恢复连接"); local.busy = "正在联网下载中文模型…"; renderAll(); try { await bridge().prepareLocalSpeech(); await detect(); } finally { local.busy = ""; } }
        else if (action === "cancel") await cancel();
        else if (action === "reconnect") await reconnect();
        else if (action === "sync") {
          const original = state.jobDetail, projectId = state.currentProjectId, context = local.context;
          const targetId = original.server_project_id || value("#local-sync-project-select", original.project_id);
          if (!project(targetId) || project(targetId).local_only) throw new Error("请明确选择服务器项目后同步或打开校对版");
          local.busy = "正在同步服务器校对…"; renderAll();
          try {
            const server = original.server_job_id ? { id: original.server_job_id, project_id: targetId } : await sync(original, targetId);
            await refreshJobs();
            if (state.currentProjectId === projectId && local.context === context) {
              setCurrentProject(server.project_id || targetId); await loadJob(server.id); state.activeStep = "review";
            }
          } finally { local.busy = ""; }
        }
      } catch (error) { local.error = normalizeError(error).message; throw error; }
      finally { renderAll(); }
    });
    return true;
  }
  function handleChange(target) {
    if (target.matches?.("#recognition-engine-select")) { local.mode = target.value === "apple" ? "apple" : "cloud"; local.error = ""; renderAll(); return true; }
    return false;
  }
  globalObject.SpeakerLocalRecognition = Object.freeze({ initialize, detect, wantsLocal, isLocalId, connectionFailed, connectionSucceeded,
    cacheProjects, loadCachedProjects, createProject, mergeJobs, getJob, sourceUrl, request: routeRequest, download, create, cancel, sync,
    renderControls, renderBanner, renderConnection, handleClick, handleChange, contextChanged: () => { local.context += 1; } });
}(globalThis));


function buildApiUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  return `${state.apiBase}${path}`;
}

function buildApiV2Url(path) {
  if (/^https?:\/\//i.test(path)) return path;
  const base = String(state.apiBase || "").replace(/\/api\/v1\/?$/i, "/api/v2");
  return `${base}${path}`;
}

function buildProjectVoiceClonePath(projectId, suffix = "") {
  const normalizedProjectId = String(projectId || "").trim();
  if (!normalizedProjectId) throw new Error("请先选择项目，再使用声音复刻");
  return `/projects/${encodeURIComponent(normalizedProjectId)}/voice-cloning${suffix}`;
}

function getCurrentProjectId() {
  return String(state.currentProjectId || "").trim();
}

function getCurrentSubtitleJobId() {
  if (!state.jobDetail || !isSubtitleJob(state.jobDetail)) return "";
  return String(state.jobDetail.id || state.currentJobId || "");
}

function hydrateAudioPlayers() {
  const audioElements = Array.from(document.querySelectorAll("audio[data-audio-src]"));
  audioElements.forEach((audio) => {
    const path = audio.dataset.audioSrc || "";
    if (!path || audio.dataset.audioReady === "1") return;
    if (isVoiceAssetExpired({ expires_at: audio.dataset.voiceExpiresAt })) return;
    audio.src = buildApiUrl(path);
    audio.dataset.audioReady = "1";
  });
}

async function request(path, options = {}) {
  const localRequest = globalThis.SpeakerLocalRecognition?.request(path, options);
  if (localRequest) return localRequest;
  return requestUrl(buildApiUrl(path), options);
}

async function requestV2(path, options = {}) {
  return requestUrl(buildApiV2Url(path), options);
}

async function requestUrl(url, options = {}) {
  if (state.localRecognition?.offline && !/\/health(?:\?|$)/.test(url)) throw new Error("当前为离线模式，此功能需要恢复服务器连接");
  const headers = new Headers(options.headers || {});
  const hasBody = options.body !== undefined && options.body !== null;
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  if (hasBody && !isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const timeoutMs = Number(options.timeoutMs ?? (String(options.method || "GET").toUpperCase() === "GET" ? 15000 : 0));
  const controller = timeoutMs > 0 && typeof AbortController !== "undefined" ? new AbortController() : null;
  const timeoutId = controller
    ? window.setTimeout(() => controller.abort(), timeoutMs)
    : null;
  const { timeoutMs: _timeoutMs, ...fetchOptions } = options;
  let response;
  let payload;
  try {
    response = await fetch(url, { ...fetchOptions, headers, signal: controller?.signal || options.signal });
    const isJson = (response.headers.get("content-type") || "").includes("application/json");
    try {
      payload = isJson ? await response.json() : await response.text();
    } catch (error) {
      if (controller?.signal.aborted) throw error;
      payload = isJson ? null : "";
    }
  } catch (error) {
    if (url.startsWith(String(state.apiBase).replace(/\/api\/v[12]\/?$/, "/api/"))) await globalThis.SpeakerLocalRecognition?.connectionFailed(url);
    if (controller?.signal.aborted) {
      throw new Error(`连接后端超时：${Math.round(timeoutMs / 1000)} 秒内没有响应`);
    }
    throw new Error(`连接后端失败：${normalizeError(error).message}`);
  } finally {
    if (timeoutId) window.clearTimeout(timeoutId);
  }
  if (!response.ok) {
    const detail = payload?.detail;
    const detailMessage = Array.isArray(detail)
      ? detail.map((item) => item?.message || item?.msg || item).filter(Boolean).join("；")
      : detail && typeof detail === "object" ? detail.message : detail;
    const message = detailMessage || payload?.message || (typeof payload === "string" ? payload : "") || `请求失败：${response.status}`;
    const error = new Error(String(message));
    error.status = response.status;
    error.code = typeof detail === "object" ? String(detail.code || "") : "";
    error.detail = typeof detail === "object" ? detail : null;
    throw error;
  }
  if (/\/health(?:\?|$)/.test(url) && url.startsWith(state.apiBase)) globalThis.SpeakerLocalRecognition?.connectionSucceeded();
  return payload;
}

function decodeBase64ToBlob(base64Value, mimeType = "application/octet-stream") {
  const binary = window.atob(base64Value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new Blob([bytes], { type: mimeType });
}

async function requestClientBridge(path, options = {}) {
  const response = await fetch(`${CLIENT_BRIDGE_BASE}${path}`, options);
  const contentType = response.headers.get("content-type") || "";
  if (!response.ok) {
    const payload = contentType.includes("application/json") ? await response.json().catch(() => null) : await response.text();
    throw new Error(payload?.detail || payload?.message || payload || "客户端桥接请求失败");
  }
  return contentType.includes("application/json") ? response.json() : response.blob();
}

async function loadClientBridgeFile(fileMeta) {
  const path = String(fileMeta?.path || "");
  const name = String(fileMeta?.name || path.split(/[\\/]/).pop() || "local-file");
  if (!path) throw new Error("缺少本地文件路径");
  if (window.SpeakerDesktopBridge?.isAvailable?.()) {
    const base64 = await window.SpeakerDesktopBridge.readFile(path);
    return decodeBase64ToBlob(base64, guessMimeType(name));
  }
  return requestClientBridge(`/read-file?path=${encodeURIComponent(path)}`);
}

async function readClientTextFile(fileMeta) {
  const path = String(fileMeta?.path || "");
  if (!path) throw new Error("缺少本地文件路径");
  if (window.SpeakerDesktopBridge?.isAvailable?.()) {
    return window.SpeakerDesktopBridge.readTextFile(path);
  }
  const blob = await requestClientBridge(`/read-file?path=${encodeURIComponent(path)}`);
  return blob.text();
}

async function pickDesktopFiles(kind) {
  if (window.SpeakerDesktopBridge?.isAvailable?.()) {
    if (kind === "media") return window.SpeakerDesktopBridge.pickMedia();
    if (kind === "lowerThirds") return window.SpeakerDesktopBridge.pickLowerThirds();
    if (kind === "xml") return window.SpeakerDesktopBridge.pickXmlFile();
    if (kind === "xmlImages") return window.SpeakerDesktopBridge.pickXmlRepairImages();
  }
  if (kind === "media") return requestClientBridge("/pick-media", { method: "POST" }).catch(() => null);
  if (kind === "lowerThirds") return requestClientBridge("/pick-lower-thirds", { method: "POST" }).catch(() => null);
  return null;
}

function getFilenameFromDisposition(disposition, fallbackName) {
  const raw = String(disposition || "");
  const encodedMatch = raw.match(/filename\*=UTF-8''([^;]+)/i);
  if (encodedMatch) return decodeURIComponent(encodedMatch[1]);
  const match = raw.match(/filename="?([^"]+)"?/i);
  return match?.[1] || fallbackName;
}

function downloadBlob(blob, fallbackName) {
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1200);
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const value = String(reader.result || "");
      resolve(value.includes(",") ? value.split(",", 2)[1] : value);
    };
    reader.onerror = () => reject(reader.error || new Error("读取下载内容失败"));
    reader.readAsDataURL(blob);
  });
}

async function saveBlobWithDesktopDialog(blob, fileName) {
  if (!window.SpeakerDesktopBridge?.isAvailable?.()) return null;
  if (typeof window.SpeakerDesktopBridge.pickSaveFile !== "function") return null;
  if (typeof window.SpeakerDesktopBridge.writeFile !== "function") return null;
  const savePath = await window.SpeakerDesktopBridge.pickSaveFile(fileName);
  if (!savePath) {
    showToast("已取消保存", "neutral");
    return false;
  }
  const base64 = await blobToBase64(blob);
  await window.SpeakerDesktopBridge.writeFile(savePath, base64);
  showToast(`已保存到 ${savePath}`, "success");
  return true;
}

async function buildDownloadErrorMessage(response) {
  const fallback = `下载失败：${response.status}`;
  let bodyText = "";
  try {
    bodyText = await response.text();
  } catch (_error) {
    return fallback;
  }
  const trimmed = bodyText.trim();
  if (!trimmed) return fallback;
  try {
    const payload = JSON.parse(trimmed);
    const detail = payload?.detail || payload?.message || payload?.error;
    if (Array.isArray(detail)) return `${fallback}：${detail.map((item) => item?.msg || item).join("；")}`;
    if (detail) return `${fallback}：${String(detail)}`;
  } catch (_error) {
    // Non-JSON error bodies are still useful when the server includes a text reason.
  }
  return `${fallback}：${trimmed.slice(0, 300)}`;
}

async function downloadBinaryFile(path, fallbackName) {
  if (/\/transcriptions\/jobs\/local-/.test(path)) return globalThis.SpeakerLocalRecognition.download(path);
  if (state.localRecognition?.offline) throw new Error("离线时只能导出已保存的本机任务");
  const response = await fetch(buildApiUrl(path));
  if (!response.ok) throw new Error(await buildDownloadErrorMessage(response));
  const blob = await response.blob();
  const fileName = getFilenameFromDisposition(response.headers.get("content-disposition"), fallbackName);
  const dialogResult = await saveBlobWithDesktopDialog(blob, fileName);
  if (dialogResult === true) return true;
  if (dialogResult === false) return false;
  downloadBlob(blob, fileName);
  showToast(`已开始下载 ${fileName}`, "success");
  return true;
}

async function downloadTextFile(fileName, text) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const dialogResult = await saveBlobWithDesktopDialog(blob, fileName);
  if (dialogResult === true || dialogResult === false) return;
  downloadBlob(blob, fileName);
  showToast(`已开始下载 ${fileName}`, "success");
}

async function writeTextFile(path, contents) {
  if (window.SpeakerDesktopBridge?.isAvailable?.()) {
    await window.SpeakerDesktopBridge.writeTextFile(path, contents);
    showToast(`已导出到 ${path}`, "success");
    return;
  }
  await downloadTextFile(path.split(/[\\/]/).pop() || "speaker-output.xml", contents);
}

async function recordClientLog(type, message, context = {}) {
  const entry = JSON.stringify({
    timestamp: formatChinaIsoTimestamp(),
    type,
    message,
    context,
    build_marker: BUILD_MARKER,
    api_base: state.apiBase,
    job_id: state.currentJobId || undefined,
  });
  console.info(`[${type}] ${message}`, context);
  if (window.SpeakerDesktopBridge?.isAvailable?.()) {
    await window.SpeakerDesktopBridge.appendClientLog(entry).catch(() => {});
  }
}

async function checkClientUpdate({ silent = false } = {}) {
  if (state.localRecognition?.offline) { if (!silent) throw new Error("离线时无法检查客户端更新"); return null; }
  if (!window.SpeakerDesktopBridge?.isAvailable?.()) {
    if (!silent) showToast("当前不是桌面客户端环境，无法检查更新。", "neutral");
    return null;
  }
  state.clientUpdate.checking = true;
  state.clientUpdate.message = "正在检查更新...";
  renderAll();
  try {
    const result = await window.SpeakerDesktopBridge.checkClientUpdate(state.apiBase, UPDATE_CHANNEL);
    state.clientUpdate.available = Boolean(result?.available);
    state.clientUpdate.version = result?.version || "";
    state.clientUpdate.notes = result?.notes || "";
    state.clientUpdate.date = result?.date || "";
    state.clientUpdate.lastCheckedAt = formatChinaIsoTimestamp();
    state.clientUpdate.message = state.clientUpdate.available ? "发现新版本。" : "当前已经是最新版本。";
    state.clientUpdate.noticeVisible = Boolean(
      state.clientUpdate.available
        && state.clientUpdate.version
        && (!silent || state.clientUpdate.dismissedVersion !== state.clientUpdate.version)
    );
    await recordClientLog("client-update", state.clientUpdate.message, {
      current_version: APP_VERSION,
      update_channel: UPDATE_CHANNEL,
      next_version: state.clientUpdate.version || undefined,
    });
    if (!state.clientUpdate.available && !silent) {
      showToast(state.clientUpdate.message, state.clientUpdate.available ? "success" : "neutral");
    }
    renderClientUpdateNotice();
    return result;
  } catch (error) {
    state.clientUpdate.message = normalizeError(error).message;
    await recordClientLog("client-update-error", "检查客户端更新失败", {
      error: state.clientUpdate.message,
      current_version: APP_VERSION,
      update_channel: UPDATE_CHANNEL,
    });
    if (!silent) showToast(`检查更新失败：${state.clientUpdate.message}`, "error");
    return null;
  } finally {
    state.clientUpdate.checking = false;
    renderAll();
  }
}

async function installClientUpdate() {
  if (!window.SpeakerDesktopBridge?.isAvailable?.()) {
    showToast("当前不是桌面客户端环境，无法安装更新。", "error");
    return;
  }
  state.clientUpdate.installing = true;
  state.clientUpdate.message = "正在下载并安装更新，完成后会自动重启。";
  renderAll();
  try {
    await recordClientLog("client-update", "开始安装客户端更新", {
      current_version: APP_VERSION,
      update_channel: UPDATE_CHANNEL,
      next_version: state.clientUpdate.version || undefined,
    });
    const result = await window.SpeakerDesktopBridge.installClientUpdate(state.apiBase, UPDATE_CHANNEL);
    state.clientUpdate.installing = false;
    if (!result?.installed) {
      state.clientUpdate.available = false;
      state.clientUpdate.notes = "";
      state.clientUpdate.date = "";
    }
    state.clientUpdate.version = result?.version || (result?.installed ? state.clientUpdate.version : "");
    state.clientUpdate.message = result?.installed
      ? "更新已安装，请重启客户端。"
      : "未发现可安装的新版本。";
    await recordClientLog("client-update", state.clientUpdate.message, {
      current_version: APP_VERSION,
      update_channel: UPDATE_CHANNEL,
      next_version: state.clientUpdate.version || undefined,
      installed: Boolean(result?.installed),
    });
    showToast(state.clientUpdate.message, result?.installed ? "success" : "neutral");
    renderAll();
  } catch (error) {
    state.clientUpdate.installing = false;
    state.clientUpdate.message = normalizeError(error).message;
    await recordClientLog("client-update-error", "安装客户端更新失败", {
      error: state.clientUpdate.message,
      current_version: APP_VERSION,
      update_channel: UPDATE_CHANNEL,
    });
    showToast(`安装更新失败：${state.clientUpdate.message}`, "error");
    renderAll();
  }
}

async function withUiTask(button, busyText, task) {
  const target = button || null;
  const originalText = target?.textContent || "";
  try {
    if (target) {
      target.disabled = true;
      target.textContent = busyText || "处理中...";
    }
    await task();
  } catch (error) {
    const normalized = normalizeError(error);
    await recordClientLog("error", normalized.message);
    showToast(normalized.message, "danger");
  } finally {
    if (target) {
      target.disabled = false;
      target.textContent = originalText;
    }
  }
}

function decorateJobDetail(detail) {
  if (!detail) return null;
  return {
    ...detail,
    kind: normalizeJobKind(detail),
    normalized_segments: Array.isArray(detail.normalized_segments) ? detail.normalized_segments : [],
    lower_third_assets: Array.isArray(detail.lower_third_assets) ? detail.lower_third_assets : [],
    matched_lower_thirds: detail.matched_lower_thirds || {},
    subtitle_backplate_asset: detail.subtitle_backplate_asset || null,
    speaker_mappings: detail.speaker_mappings || {},
  };
}

async function refreshProjects() {
  const localScope = state.apiBase;
  state.projectLoad = { status: "loading", error: "" };
  try {
    const response = await requestV2("/projects", { timeoutMs: 30000 });
    if (state.apiBase !== localScope) return state.projects;
    state.projects = (Array.isArray(response) ? response : []).map((project) => ({
      ...project,
      project_id: project.id,
      program_name: project.name,
      program_key: project.legacy_program_key || project.name,
      person_count: Number(project.person_count || 0),
      sample_count: Number(project.sample_count || 0),
      asset_count: Number(project.asset_count || 0),
      job_count: Number(project.job_count || 0),
    }));
    if (globalThis.SpeakerLocalRecognition) {
      const cached = await globalThis.SpeakerLocalRecognition.cacheProjects(state.projects).catch(() => state.projects);
      if (state.apiBase !== localScope) return state.projects;
      state.projects = cached;
    }
    state.projectLoad = { status: "ready", error: "" };
    if (state.currentProjectId && !state.projects.some((project) => project.project_id === state.currentProjectId)) {
      setCurrentProject("");
    }
    return state.projects;
  } catch (error) {
    if (state.apiBase !== localScope) return state.projects;
    if (state.localRecognition?.offline && window.SpeakerDesktopBridge?.isAvailable?.()) return globalThis.SpeakerLocalRecognition.loadCachedProjects();
    state.projectLoad = { status: "error", error: normalizeError(error).message };
    throw error;
  }
}

async function refreshPeople() {
  const response = await request("/voiceprints/people");
  state.people = Array.isArray(response) ? response : [];
  return state.people;
}

async function refreshJobs() {
  const localScope = state.apiBase;
  if (state.localRecognition?.offline) {
    const cached = await globalThis.SpeakerLocalRecognition.mergeJobs([]);
    if (state.apiBase === localScope) state.jobs = cached;
    return state.jobs;
  }
  let response;
  try {
    response = await request("/transcriptions/jobs/summaries", { timeoutMs: 30000 });
  } catch (error) {
    if (state.apiBase !== localScope) return state.jobs;
    if (state.localRecognition?.offline && window.SpeakerDesktopBridge?.isAvailable?.()) {
      const cached = await globalThis.SpeakerLocalRecognition.mergeJobs([]);
      if (state.apiBase === localScope) state.jobs = cached;
      return state.jobs;
    }
    const legacyRouteConflict = error?.status === 422
      && Array.isArray(error.detail)
      && error.detail.some((item) => item?.type === "uuid_parsing" && item?.input === "summaries");
    if (error?.status !== 404 && !legacyRouteConflict) throw error;
    response = await request("/transcriptions/jobs", { timeoutMs: 30000 });
  }
  if (state.apiBase !== localScope) return state.jobs;
  let jobs = Array.isArray(response) ? response : [];
  if (globalThis.SpeakerLocalRecognition) jobs = await globalThis.SpeakerLocalRecognition.mergeJobs(jobs).catch(() => jobs);
  if (state.apiBase === localScope) state.jobs = jobs;
  return state.jobs;
}

async function refreshProjectScripts() {
  const projectId = getCurrentProjectId();
  if (!projectId) {
    state.projectScripts = [];
    return state.projectScripts;
  }
  const response = await request(`/lexicon/project-scripts?project_id=${encodeURIComponent(projectId)}`);
  state.projectScripts = response?.items || [];
  return state.projectScripts;
}

async function refreshServiceStatus() {
  const [health, config, voiceprint, voiceClone] = await Promise.allSettled([
    request("/health"),
    request("/config/status"),
    request("/voiceprints/provider-status"),
    request("/voice-cloning/provider-status"),
  ]);
  state.serviceStatus = {
    health: health.status === "fulfilled" ? health.value : null,
    config: config.status === "fulfilled" ? config.value : null,
    voiceprint: voiceprint.status === "fulfilled" ? voiceprint.value : null,
    voiceClone: voiceClone.status === "fulfilled" ? voiceClone.value : null,
  };
  applyVoiceCloneProviderDefaults();
}

async function refreshLexiconData() {
  const projectId = getCurrentProjectId();
  if (!projectId) {
    state.lexicon.terms = [];
    state.lexicon.candidates = [];
    state.lexicon.bundlePreview = null;
    state.lexicon.message = "请先选择或新建项目。";
    return state.lexicon;
  }
  state.lexicon.loading = true;
  state.lexicon.message = "";
  try {
    const status = state.lexicon.selectedStatus || "candidate";
    const [termsResult, candidatesResult, previewResult, scriptsResult] = await Promise.allSettled([
      request(`/lexicon/terms?project_id=${encodeURIComponent(projectId)}&status=active`),
      request(`/lexicon/candidates?project_id=${encodeURIComponent(projectId)}&status=${encodeURIComponent(status)}`),
      request("/lexicon/runtime-bundle/preview", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, temporary_terms: [] }),
      }),
      request(`/lexicon/project-scripts?project_id=${encodeURIComponent(projectId)}`),
    ]);
    if (termsResult.status === "fulfilled") {
      state.lexicon.terms = termsResult.value?.items || [];
    } else {
      state.lexicon.terms = [];
      state.lexicon.message = normalizeError(termsResult.reason).message;
    }
    if (candidatesResult.status === "fulfilled") {
      state.lexicon.candidates = candidatesResult.value?.items || [];
    } else {
      state.lexicon.candidates = [];
      state.lexicon.message = normalizeError(candidatesResult.reason).message;
    }
    if (previewResult.status === "fulfilled") {
      state.lexicon.bundlePreview = previewResult.value || null;
    } else {
      state.lexicon.bundlePreview = null;
      state.lexicon.message = normalizeError(previewResult.reason).message;
    }
    state.projectScripts = scriptsResult.status === "fulfilled" ? scriptsResult.value?.items || [] : [];
    return state.lexicon;
  } finally {
    state.lexicon.loading = false;
  }
}

async function importLexiconDocument(options = {}) {
  const projectId = getCurrentProjectId();
  if (!projectId) throw new Error("请先选择或新建项目");
  const sourceName = String(options.sourceName ?? state.lexicon.pickedSource ?? "").trim();
  const text = String(options.text ?? state.lexicon.pastedText ?? "").trim();
  const pickedFile = options.file ?? state.lexicon.pickedFile;
  if (!text && !pickedFile) throw new Error("请先上传或粘贴台本/术语文本");
  const formData = new FormData();
  formData.append("project_id", projectId);
  formData.append("source_name", sourceName || pickedFile?.name || "粘贴文本");
  if (text) formData.append("text", text);
  if (pickedFile?.browserFile) {
    formData.append("script_file", pickedFile.browserFile);
  } else if (pickedFile?.path) {
    const blob = await loadClientBridgeFile(pickedFile);
    formData.append("script_file", new File([blob], pickedFile.name, { type: blob.type || guessMimeType(pickedFile.name) }));
  }
  const imported = await request("/lexicon/imports", { method: "POST", body: formData });
  state.lexicon.imports = [imported, ...state.lexicon.imports].slice(0, 6);
  state.lexicon.pickedFile = null;
  state.lexicon.pickedSource = "";
  state.lexicon.pastedText = "";
  await refreshLexiconData();
  return imported;
}

async function promoteLexiconCandidate(candidateId, protectionLevel = "strong") {
  const promoted = await request(`/lexicon/candidates/${encodeURIComponent(candidateId)}/promote`, {
    method: "POST",
    body: JSON.stringify({ reviewed_by: "client", protection_level: protectionLevel }),
  });
  if (promoted?.id && protectionLevel && promoted.protection_level !== protectionLevel) {
    await request(`/lexicon/terms/${encodeURIComponent(promoted.id)}`, {
      method: "PATCH",
      body: JSON.stringify({ protection_level: protectionLevel }),
    }).catch(() => promoted);
  }
  await refreshLexiconData();
  return promoted;
}

async function rejectLexiconCandidate(candidateId) {
  const rejected = await request(`/lexicon/candidates/${encodeURIComponent(candidateId)}/reject`, {
    method: "POST",
    body: JSON.stringify({ reviewed_by: "client" }),
  });
  await refreshLexiconData();
  return rejected;
}

async function disableLexiconTermForProject(termId) {
  const projectId = getCurrentProjectId();
  if (!projectId) throw new Error("请先选择或新建项目");
  const disabled = await request(`/lexicon/terms/${encodeURIComponent(termId)}/disable-for-project`, {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, reason: "client disabled", created_by: "client" }),
  });
  await refreshLexiconData();
  return disabled;
}

async function createLexiconTermFromText(displayText, protectionLevel = "strong") {
  const projectId = getCurrentProjectId();
  if (!projectId) throw new Error("请先选择或新建项目");
  const text = String(displayText || "").trim();
  if (!text) throw new Error("词条不能为空");
  const term = await request("/lexicon/terms", {
    method: "POST",
    body: JSON.stringify({
      project_id: projectId,
      scope_type: "project",
      display_text: text,
      term_type: "term",
      protection_level: protectionLevel,
      source_type: "manual",
      created_by: "client",
    }),
  });
  await refreshLexiconData().catch(() => {});
  return term;
}

async function rebuildJobRuntimeLexiconBundle() {
  const projectId = getCurrentProjectId();
  const jobId = getCurrentSubtitleJobId();
  if (!projectId) throw new Error("请先选择或新建项目");
  if (!jobId) throw new Error("请先载入当前字幕任务");
  const bundle = await request(`/lexicon/jobs/${encodeURIComponent(jobId)}/runtime-bundle/rebuild`, {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, temporary_terms: [] }),
  });
  if (state.currentJobId === jobId) {
    await loadJob(jobId, true).catch(() => {});
  }
  await refreshLexiconData();
  return bundle;
}

async function refreshJobDerived(jobId = state.currentJobId, requestContext = null) {
  const targetJobId = String(requestContext?.jobId || jobId || "");
  const targetProjectId = String(requestContext?.projectId ?? getCurrentProjectId());
  const requestGeneration = Number(requestContext?.generation ?? state.loadJobRequestGeneration);
  const isTargetContextActive = () => (
    String(state.currentJobId || "") === targetJobId &&
    getCurrentProjectId() === targetProjectId &&
    state.loadJobRequestGeneration === requestGeneration
  );
  if (!isTargetContextActive()) return false;
  if (!targetJobId || !isCompletedJob()) {
    state.speakers = [];
    state.speakerMappings = {};
    state.memorySuggestions = [];
    state.cues = [];
    state.cuePage = 0;
    state.cueSpeakerDrafts = {};
    state.cueTextDrafts = {};
    state.cueDeletedDrafts = {};
    state.subtitlePreview = "";
    state.cleanSubtitlePreview = "";
    return true;
  }
  const [speakerResult, fullResult, cleanResult] = await Promise.allSettled([
    request(`/transcriptions/jobs/${encodeURIComponent(targetJobId)}/speakers`),
    request(`/transcriptions/jobs/${encodeURIComponent(targetJobId)}/subtitles/preview?format=srt&variant=full`),
    request(`/transcriptions/jobs/${encodeURIComponent(targetJobId)}/subtitles/preview?format=srt&variant=clean`),
  ]);
  if (!isTargetContextActive()) return false;
  if (speakerResult.status === "fulfilled") {
    state.speakers = speakerResult.value.speakers || [];
    state.speakerMappings = speakerResult.value.mappings || {};
    state.memorySuggestions = speakerResult.value.memory_suggestions || [];
  }
  if (fullResult.status === "fulfilled") {
    state.cues = fullResult.value.cues || [];
    state.cuePage = Math.min(state.cuePage || 0, Math.max(0, Math.ceil(state.cues.length / CUE_PREVIEW_PAGE_SIZE) - 1));
    state.cueTextDrafts = {};
    state.cueDeletedDrafts = {};
    state.subtitlePreview = fullResult.value.content || "";
  }
  if (cleanResult.status === "fulfilled") {
    state.cleanSubtitlePreview = cleanResult.value.content || "";
  }
  return true;
}

function isJobInCurrentProject(job) {
  const currentProjectId = getCurrentProjectId();
  if (!currentProjectId) return false;
  if (job?.project_id) return String(job.project_id) === currentProjectId;
  return String(job?.program_name || "").trim() === String(state.currentProjectName || "").trim();
}

function findLatestProjectJob(kind = getWorkflowKindForStep(state.activeStep)) {
  if (!getCurrentProjectId()) return null;
  return (state.jobs || []).find((job) => normalizeJobKind(job) === kind && isJobInCurrentProject(job)) || null;
}

function resetDisplayedJobState(kind = getWorkflowKindForStep(state.activeStep), persist = true) {
  invalidateLoadJobRequests();
  state.jobDetail = null;
  if (persist) setCurrentJobId("", kind, false);
  else setDisplayedJobId("");
  state.speakers = [];
  state.speakerMappings = {};
  state.memorySuggestions = [];
  state.cues = [];
  state.cuePage = 0;
  state.cueSpeakerDrafts = {};
  state.cueTextDrafts = {};
  state.cueDeletedDrafts = {};
  state.subtitlePreview = "";
  state.cleanSubtitlePreview = "";
}

async function loadJob(jobId, options = true) {
  const targetProjectId = getCurrentProjectId();
  const targetJobId = String(jobId || "").trim();
  if (!targetJobId) return null;
  const requestGeneration = state.loadJobRequestGeneration + 1;
  state.loadJobRequestGeneration = requestGeneration;
  state.loadJobTargetId = targetJobId;
  const requestContext = Object.freeze({
    projectId: targetProjectId,
    jobId: targetJobId,
    generation: requestGeneration,
  });
  const isTargetContextActive = () => (
    state.loadJobRequestGeneration === requestGeneration && getCurrentProjectId() === targetProjectId
  );
  const finishRequest = () => {
    if (isTargetContextActive() && state.loadJobTargetId === targetJobId) {
      state.loadJobTargetId = "";
    }
  };
  const withDerived = typeof options === "boolean" ? options : options?.withDerived !== false;
  const syncProjectFromJob = typeof options === "boolean" ? true : options?.syncProjectFromJob !== false;
  let detail;
  try {
    detail = decorateJobDetail(await request(`/transcriptions/jobs/${encodeURIComponent(targetJobId)}`));
  } catch (error) {
    if (isTargetContextActive()) {
      finishRequest();
      throw error;
    }
    return undefined;
  }
  if (!isTargetContextActive()) return undefined;
  const nextJobId = String(detail?.id || targetJobId);
  const detailProjectId = String(detail?.project_id || "");
  if (nextJobId !== targetJobId || (targetProjectId && detailProjectId && detailProjectId !== targetProjectId)) {
    finishRequest();
    return null;
  }
  if (state.currentJobId !== nextJobId) {
    state.cuePage = 0;
    state.cueSpeakerDrafts = {};
    state.cueTextDrafts = {};
    state.cueDeletedDrafts = {};
  }
  state.jobDetail = detail;
  setCurrentJobId(nextJobId, detail.kind, false);
  if (withDerived) {
    const refreshed = await refreshJobDerived(nextJobId, requestContext);
    if (!refreshed || !isTargetContextActive()) return undefined;
  }
  finishRequest();
  return detail;
}

async function selectJobForStep(step = state.activeStep) {
  const workflowKind = getWorkflowKindForStep(step);
  const targetProjectId = getCurrentProjectId();
  const isLoadAttemptActive = (generation) => (
    state.loadJobRequestGeneration === generation && getCurrentProjectId() === targetProjectId
  );
  if (!getCurrentProjectId()) {
    resetDisplayedJobState(workflowKind);
    return null;
  }
  const currentMatches = workflowKind === "transcript" ? isTranscriptJob(state.jobDetail) : isSubtitleJob(state.jobDetail);
  if (state.jobDetail && currentMatches && isJobInCurrentProject(state.jobDetail)) return state.jobDetail;
  if (state.jobDetail && !isJobInCurrentProject(state.jobDetail)) {
    resetDisplayedJobState(workflowKind);
  }

  const candidateJobId = workflowKind === "transcript" ? state.currentTranscriptJobId : state.currentSubtitleJobId;
  if (candidateJobId) {
    let candidateLoadGeneration = state.loadJobRequestGeneration;
    try {
      const loadPromise = loadJob(candidateJobId, { withDerived: false, syncProjectFromJob: false });
      candidateLoadGeneration = state.loadJobRequestGeneration;
      const detail = await loadPromise;
      if (detail === undefined) return null;
      if (detail) {
        const loadedMatches = workflowKind === "transcript" ? isTranscriptJob(detail) : isSubtitleJob(detail);
        if (loadedMatches && isJobInCurrentProject(detail)) {
          const refreshed = await refreshJobDerived(detail.id);
          if (!refreshed) return null;
          return detail;
        }
      }
      setCurrentJobId("", workflowKind);
    } catch (error) {
      await recordClientLog("load-job", normalizeError(error).message);
      if (!isLoadAttemptActive(candidateLoadGeneration)) return null;
      setCurrentJobId("", workflowKind);
    }
  }

  const projectJob = findLatestProjectJob(workflowKind);
  if (projectJob?.id) {
    let projectLoadGeneration = state.loadJobRequestGeneration;
    try {
      const loadPromise = loadJob(projectJob.id, { withDerived: true, syncProjectFromJob: false });
      projectLoadGeneration = state.loadJobRequestGeneration;
      const detail = await loadPromise;
      if (detail === undefined) return null;
      if (detail && isJobInCurrentProject(detail)) return detail;
    } catch (error) {
      await recordClientLog("load-project-job", normalizeError(error).message);
      if (!isLoadAttemptActive(projectLoadGeneration)) return null;
    }
  }

  resetDisplayedJobState(workflowKind);
  return null;
}

async function initializeAppData() {
  const projectsTask = refreshProjects();
  const backgroundTasks = [refreshJobs(), refreshPeople(), refreshServiceStatus()];
  await Promise.allSettled([projectsTask]);
  renderAll();
  await Promise.allSettled(backgroundTasks);
  await refreshProjectScripts().catch(() => {});
  if (state.currentProjectId && state.currentJobId) {
    try {
      const detail = await loadJob(state.currentJobId, { withDerived: true, syncProjectFromJob: !state.currentProjectName });
      if (!detail) return;
      if (!isJobInCurrentProject(detail)) {
        resetDisplayedJobState(normalizeJobKind(detail));
      }
    } catch (error) {
      recordClientLog("load-job", normalizeError(error).message);
      setCurrentJobId("");
      state.jobDetail = null;
    }
  }
}

async function createProject(programName) {
  if (state.localRecognition?.offline) return globalThis.SpeakerLocalRecognition.createProject(programName);
  const created = await requestV2("/projects", {
    method: "POST",
    body: JSON.stringify({ name: programName }),
  });
  await refreshProjects();
  setCurrentProject(created.id);
  await refreshProjectScripts().catch(() => {});
}

async function createMediaJob() {
  if (globalThis.SpeakerLocalRecognition?.wantsLocal()) return globalThis.SpeakerLocalRecognition.create();
  const projectId = getCurrentProjectId();
  if (!projectId || !state.currentProjectName) throw new Error("请先进入项目，再创建任务");
  const input = document.querySelector("#video-file-input");
  const selectedFile = input?.files?.[0] || null;
  const pickedFile = state.pickedMediaFile;
  if (!selectedFile && !pickedFile) throw new Error("请先选择音频文件");
  if (state.pickedMediaProjectId && state.pickedMediaProjectId !== projectId) {
    throw new Error("项目已变化，请在当前项目重新选择音频");
  }
  if (state.pickedTaskScriptProjectId && state.pickedTaskScriptProjectId !== projectId) {
    throw new Error("项目已变化，请在当前项目重新选择台本");
  }
  assertRecognitionFileSize(selectedFile || pickedFile);
  const formData = new FormData();
  if (selectedFile) {
    formData.append("file", selectedFile);
  } else if (pickedFile?.browserFile) {
    formData.append("file", pickedFile.browserFile);
  } else {
    const blob = await loadClientBridgeFile(pickedFile);
    formData.append("file", new File([blob], pickedFile.name, { type: blob.type || guessMimeType(pickedFile.name) }));
    formData.append("client_source_path", pickedFile.path);
  }
  const speakerCount = document.querySelector("#speaker-count-input")?.value.trim() || "";
  const languageHints = document.querySelector("#language-hints-input")?.value.trim() || "";
  const vocabularyId = document.querySelector("#vocabulary-id-input")?.value.trim() || "";
  const frameRate = document.querySelector("#export-frame-rate-select")?.value || "25";
  const subtitleLengthMode = document.querySelector("#subtitle-length-mode-select")?.value || "smart";
  const subtitleMaxChars = document.querySelector("#subtitle-max-chars-input")?.value || "14";
  if (languageHints) formData.append("language_hints", languageHints);
  if (speakerCount) formData.append("speaker_count", speakerCount);
  if (vocabularyId) formData.append("vocabulary_id", vocabularyId);
  formData.append("use_project_scripts", state.useProjectScripts ? "true" : "false");
  formData.append("lyrics_correction_mode", state.lyricsCorrectionMode ? "true" : "false");
  for (const scriptFile of state.pickedTaskScriptFiles || []) {
    if (scriptFile?.browserFile) {
      formData.append("script_files", scriptFile.browserFile);
    } else if (scriptFile?.path) {
      const blob = await loadClientBridgeFile(scriptFile);
      formData.append("script_files", new File([blob], scriptFile.name, { type: blob.type || guessMimeType(scriptFile.name) }));
    }
  }
  formData.append("job_kind", "transcription");
  formData.append("export_frame_rate", frameRate);
  formData.append("subtitle_strategy", "evidence");
  formData.append("subtitle_length_mode", subtitleLengthMode);
  if (subtitleLengthMode === "strict") {
    formData.append("subtitle_max_chars", subtitleMaxChars);
  }
  const response = await requestV2(`/projects/${encodeURIComponent(projectId)}/transcriptions/media-jobs`, { method: "POST", body: formData });
  const detail = decorateJobDetail(response.job || response);
  const detached = getCurrentProjectId() !== projectId;
  if (!detached) {
    state.jobDetail = detail;
    setCurrentJobId(detail.id, detail.kind);
    state.cueSpeakerDrafts = {};
    state.cueTextDrafts = {};
    state.cueDeletedDrafts = {};
    state.pickedMediaFile = null;
    state.pickedMediaSource = "";
    state.pickedMediaProjectId = "";
    state.pickedTaskScriptFiles = [];
    state.pickedTaskScriptProjectId = "";
    state.pickedScriptFile = null;
    state.pickedScriptSource = "";
    state.scriptText = "";
  }
  await Promise.all([refreshJobs(), refreshProjectScripts().catch(() => [])]);
  return { ...detail, _uiProjectId: projectId, _uiDetached: detached };
}

async function createTranscriptJob() {
  const projectId = getCurrentProjectId();
  if (!projectId || !state.currentProjectName) throw new Error("请先进入项目，再生成扒词");
  const input = document.querySelector("#transcript-file-input");
  const selectedFile = input?.files?.[0] || null;
  const pickedFile = state.transcript.pickedFile;
  if (!selectedFile && !pickedFile) throw new Error("请先选择音频文件");
  if (state.transcript.pickedProjectId && state.transcript.pickedProjectId !== projectId) {
    throw new Error("项目已变化，请在当前项目重新选择音频");
  }
  assertRecognitionFileSize(selectedFile || pickedFile);
  const formData = new FormData();
  if (selectedFile) {
    formData.append("file", selectedFile);
  } else if (pickedFile?.browserFile) {
    formData.append("file", pickedFile.browserFile);
  } else {
    const blob = await loadClientBridgeFile(pickedFile);
    formData.append("file", new File([blob], pickedFile.name, { type: blob.type || guessMimeType(pickedFile.name) }));
    formData.append("client_source_path", pickedFile.path);
  }
  const speakerCount = document.querySelector("#transcript-speaker-count-input")?.value.trim() || "";
  const languageHints = document.querySelector("#transcript-language-hints-input")?.value.trim() || "";
  const vocabularyId = document.querySelector("#transcript-vocabulary-id-input")?.value.trim() || "";
  if (languageHints) formData.append("language_hints", languageHints);
  if (speakerCount) formData.append("speaker_count", speakerCount);
  if (vocabularyId) formData.append("vocabulary_id", vocabularyId);
  formData.append("job_kind", "transcript");
  formData.append("export_frame_rate", "25");
  formData.append("subtitle_strategy", "standard");
  formData.append("remove_standalone_fillers", "false");
  const response = await requestV2(`/projects/${encodeURIComponent(projectId)}/transcriptions/media-jobs`, { method: "POST", body: formData });
  const detail = decorateJobDetail(response.job || response);
  const detached = getCurrentProjectId() !== projectId;
  if (!detached) {
    state.jobDetail = detail;
    setCurrentJobId(detail.id, detail.kind);
    state.cueSpeakerDrafts = {};
    state.cueTextDrafts = {};
    state.cueDeletedDrafts = {};
    state.transcript.pickedFile = null;
    state.transcript.pickedSource = "";
    state.transcript.pickedProjectId = "";
  }
  await refreshJobs();
  return { ...detail, _uiProjectId: projectId, _uiDetached: detached };
}

function friendlySyncMessage(detail, status, fallback = "") {
  const raw = String(detail?.processing_message || fallback || "").trim();
  if (status === "subtitle_enhancing") {
    if (/批失败|DeepSeek|字幕增强/.test(raw)) {
      return "字幕优化正在后台处理，部分批次响应较慢时会自动保留已成功结果。";
    }
    return raw || "正在优化字幕断句和唱词。";
  }
  if (status === "finalizing") return raw || "正在整理转写结果。";
  if (["pending", "running", "processing"].includes(status)) return raw || "正在转写音频并识别说话人。";
  return raw || fallback || "正在同步任务。";
}

let activeJobRefreshInFlight = false;

async function refreshActiveProcessingJob() {
  const targetJobId = String(state.currentJobId || "").trim();
  const targetProjectId = String(getCurrentProjectId() || "").trim();
  if (activeJobRefreshInFlight || !targetJobId || !isProcessingJob()) return false;
  activeJobRefreshInFlight = true;
  try {
    const detail = decorateJobDetail(await request(
      `/transcriptions/jobs/${encodeURIComponent(targetJobId)}`,
      { timeoutMs: 10000 },
    ));
    if (
      String(state.currentJobId || "") !== targetJobId
      || String(getCurrentProjectId() || "") !== targetProjectId
    ) return false;
    state.jobDetail = detail;
    let pipeline = null;
    if (
      targetProjectId
      && normalizeJobKind(detail) === "transcription"
      && String(detail.subtitle_strategy || "").toLowerCase() === "evidence"
    ) {
      pipeline = await requestV2(
        `/projects/${encodeURIComponent(targetProjectId)}/transcriptions/jobs/${encodeURIComponent(targetJobId)}/progress`,
        { timeoutMs: 10000 },
      ).catch(() => null);
      if (pipeline) state.syncProgress = { ...state.syncProgress, pipeline };
    }
    if (isCompletedJob(detail)) {
      await refreshJobs();
      await refreshJobDerived(targetJobId);
      if (
        String(state.currentJobId || "") === targetJobId
        && String(getCurrentProjectId() || "") === targetProjectId
      ) {
        state.activeStep = deriveActiveStep();
      }
    }
    renderAll();
    return true;
  } catch (error) {
    await recordClientLog("job-status-refresh", normalizeError(error).message).catch(() => {});
    return false;
  } finally {
    activeJobRefreshInFlight = false;
  }
}

async function syncJobUntilDone(options = {}) {
  const targetJobId = String(options.jobId || state.currentJobId || "").trim();
  const targetProjectId = String(options.projectId || state.currentProjectId || "").trim();
  if (!targetJobId) throw new Error("请先创建或载入任务");
  const completedStep = options.completedStep || "review";
  if (globalThis.SpeakerLocalRecognition?.isLocalId(targetJobId)) {
    const detached = getCurrentProjectId() !== targetProjectId || String(state.currentJobId || "") !== targetJobId;
    if (detached) return { id: targetJobId, _uiDetached: true };
    const detail = await loadJob(targetJobId);
    if (detail) state.activeStep = completedStep;
    return { ...detail, _uiDetached: !detail };
  }
  const isTargetContextActive = () => (
    getCurrentProjectId() === targetProjectId && String(state.currentJobId || "") === targetJobId
  );
  const softMaxAttempts = 60;
  const hardMaxAttempts = 720;
  const maxConsecutiveNetworkFailures = 12;
  let consecutiveNetworkFailures = 0;
  state.syncAbortRequested = false;
  setSyncProgress({
    active: true,
    attempt: 0,
    maxAttempts: softMaxAttempts,
    phase: "polling",
    message: "正在同步任务，长音频可能需要较久，但任务会持续等待。",
    progress: 2,
    pipeline: null,
  });
  try {
    for (let attempt = 1; attempt <= hardMaxAttempts; attempt += 1) {
      if (state.syncAbortRequested) {
        resetSyncProgress();
        throw new Error("同步已中止");
      }
      const exceededSoftLimit = attempt > softMaxAttempts;
      setSyncProgress({
        attempt,
        maxAttempts: exceededSoftLimit ? attempt : softMaxAttempts,
        phase: "polling",
        message: exceededSoftLimit
          ? "长音频仍在后台处理，客户端会继续等待。"
          : "正在确认转写和字幕优化进度。",
        progress: Math.max(4, Number(state.syncProgress.progress || 0)),
      });
      let detail;
      try {
        detail = decorateJobDetail(await request(`/transcriptions/jobs/${encodeURIComponent(targetJobId)}/sync`, { method: "POST", timeoutMs: 20000 }));
        consecutiveNetworkFailures = 0;
      } catch (error) {
        consecutiveNetworkFailures += 1;
        const message = normalizeError(error).message;
        setSyncProgress({
          phase: "retrying",
          retryAttempt: consecutiveNetworkFailures,
          maxRetries: maxConsecutiveNetworkFailures,
          message: consecutiveNetworkFailures <= 2
            ? "后端响应较慢，正在保持后台同步。"
            : `后端暂时不可达，正在自动重试 ${consecutiveNetworkFailures}/${maxConsecutiveNetworkFailures}：${message}`,
          progress: Math.max(4, Number(state.syncProgress.progress || 0)),
        });
        renderAll();
        if (consecutiveNetworkFailures >= maxConsecutiveNetworkFailures) {
          throw new Error(`连续 ${maxConsecutiveNetworkFailures} 次连接后端失败，请确认后端服务是否已重启成功`);
        }
        await new Promise((resolve) => window.setTimeout(resolve, Math.min(15000, 3000 + consecutiveNetworkFailures * 1000)));
        continue;
      }
      if (isTargetContextActive()) state.jobDetail = detail;
      const status = String(detail.status || "").toLowerCase();
      const progress = Number(detail.processing_progress || state.syncProgress.progress || 8);
      let pipeline = state.syncProgress.pipeline || null;
      if (
        targetProjectId
        && normalizeJobKind(detail) === "transcription"
        && String(detail.subtitle_strategy || "").toLowerCase() === "evidence"
      ) {
        try {
          pipeline = await requestV2(
            `/projects/${encodeURIComponent(targetProjectId)}/transcriptions/jobs/${encodeURIComponent(targetJobId)}/progress`,
            { timeoutMs: 10000 },
          );
        } catch (_error) {
          pipeline = null;
        }
      }
      if (["pending", "running", "processing"].includes(status)) {
        setSyncProgress({
          phase: "transcribing",
          message: friendlySyncMessage(detail, status),
          progress,
          pipeline,
        });
      }
      if (["finalizing", "subtitle_enhancing"].includes(status)) {
        setSyncProgress({
          phase: "finalizing",
          message: friendlySyncMessage(detail, status),
          progress,
          pipeline,
        });
      }
      renderAll();
      if (["completed", "completed_with_warnings", "failed", "aborted"].includes(status)) {
        if (status === "completed" || status === "completed_with_warnings") {
          setSyncProgress({
            phase: "completed",
            message: status === "completed_with_warnings"
              ? (detail.processing_message || "任务完成，部分字幕增强已回退，可以继续校对。")
              : "任务完成，正在整理试听与字幕预览。",
            progress: 100,
            pipeline,
          });
          await refreshJobs();
          if (isTargetContextActive()) {
            const refreshed = await refreshJobDerived(detail.id);
            if (refreshed && isTargetContextActive()) state.activeStep = completedStep;
          }
          window.setTimeout(resetSyncProgress, 1200);
          return { ...detail, _uiDetached: !isTargetContextActive() };
        }
        setSyncProgress({
          phase: status,
          message: detail.error_message || detail.processing_message || "任务未能完成，请到“工具与日志”查看详情。",
          progress: 100,
        });
        window.setTimeout(resetSyncProgress, 1800);
        throw new Error(detail.error_message || "任务未能完成");
      }
      await new Promise((resolve) => window.setTimeout(resolve, exceededSoftLimit ? 5000 : 3000));
    }
    throw new Error("同步等待超过安全上限，请稍后重新载入任务继续同步");
  } catch (error) {
    if (!state.syncAbortRequested) {
      setSyncProgress({
        phase: "failed",
        message: normalizeError(error).message || "同步失败",
        progress: Math.max(100, Number(state.syncProgress.progress || 0)),
      });
      window.setTimeout(resetSyncProgress, 1800);
    }
    throw error;
  }
}

async function saveSpeakerMappings() {
  if (!state.currentJobId) throw new Error("请先载入任务");
  const mappingMap = new Map();
  const collectMapping = (speakerKey, value) => {
    const name = String(value || "").trim();
    if (speakerKey && name) mappingMap.set(speakerKey, name);
  };
  Object.entries(state.speakerMappings || {}).forEach(([speakerKey, speakerName]) => {
    collectMapping(speakerKey, speakerName);
  });
  document.querySelectorAll("[data-speaker-input]").forEach((input) => {
    collectMapping(input.dataset.speakerInput, input.value);
  });
  const mappings = Array.from(mappingMap, ([speaker_key, speaker_name]) => ({ speaker_key, speaker_name }));
  if (!mappings.length) throw new Error("请至少填写一个说话人姓名");
  const shouldLearnVoiceprints = state.voiceprintAutoLearningEnabled && isSubtitleJob(state.jobDetail);
  const learnQuery = shouldLearnVoiceprints ? "?learn_voiceprints=true" : "";
  const response = await request(`/transcriptions/jobs/${encodeURIComponent(state.currentJobId)}/speakers${learnQuery}`, {
    method: "PUT",
    body: JSON.stringify({ mappings }),
  });
  state.speakers = response.speakers || [];
  state.speakerMappings = response.mappings || {};
  state.memorySuggestions = response.memory_suggestions || [];
  state.cueSpeakerDrafts = {};
  await refreshJobDerived(state.currentJobId);
  return response.voiceprint_learning || null;
}

async function saveSubtitleSpeakerOverrides() {
  if (!state.currentJobId) throw new Error("请先载入任务");
  const cueMap = new Map();
  Object.entries(state.cueSpeakerDrafts || {}).forEach(([cueIndex, speakerName]) => {
    const index = Number(cueIndex);
    const name = String(speakerName || "").trim();
    if (Number.isFinite(index) && index > 0 && name) cueMap.set(index, name);
  });
  document.querySelectorAll("[data-cue-speaker-input]").forEach((input) => {
    const index = Number(input.dataset.cueIndex || 0);
    const name = String(input.value || "").trim();
    if (Number.isFinite(index) && index > 0 && name) cueMap.set(index, name);
  });
  const cues = Array.from(cueMap, ([index, speaker_name]) => ({ index, speaker_name }));
  if (!cues.length) throw new Error("请至少填写一条字幕行人名");
  const shouldLearnVoiceprints = state.voiceprintAutoLearningEnabled && isSubtitleJob(state.jobDetail);
  const learnQuery = shouldLearnVoiceprints ? "?learn_voiceprints=true" : "";
  const response = await request(`/transcriptions/jobs/${encodeURIComponent(state.currentJobId)}/subtitles/speakers${learnQuery}`, {
    method: "PUT",
    body: JSON.stringify({ cues }),
  });
  state.cues = response.cues || [];
  state.subtitlePreview = response.content || "";
  state.cueSpeakerDrafts = {};
  const cleanResult = await request(`/transcriptions/jobs/${encodeURIComponent(state.currentJobId)}/subtitles/preview?format=srt&variant=clean`).catch(() => null);
  state.cleanSubtitlePreview = cleanResult?.content || state.cleanSubtitlePreview;
  await loadJob(state.currentJobId, false).catch(() => {});
  return response.voiceprint_learning || null;
}

function normalizeSubtitleTextForFillerCheck(text) {
  return String(text || "")
    .trim()
    .replace(/[，。、！？；：、“”‘’（）()\[\]【】《》〈〉…,.!?;:·\s]/g, "")
    .replace(/[—－–-]{2,}/g, "");
}

const FILLER_EDGE_CHARS = "嗯啊哦哎唉呃额呀哇呢嘛";
const FILLER_STANDALONE = new Set(["嗯", "啊", "哦", "哎", "唉", "呃", "额", "呀", "哇", "呢", "嘛", "哈", "哈哈", "呵", "呵呵", "嘿", "嘿嘿"]);
const FILLER_LAUGHTER = new Set(["哈", "哈哈", "呵", "呵呵", "嘿", "嘿嘿"]);
const FILLER_DISCOURSE_PREFIXES = ["其实", "就是", "那个", "这个", "然后", "那么", "所以", "但是", "不过", "还有", "而且"];
const FILLER_DISCOURSE_ONLY = new Set([
  "其实",
  "就是",
  "那个",
  "这个",
  "然后",
  "那么",
  "所以",
  "但是",
  "不过",
  "还有",
  "而且",
  "对吧",
  "是吧",
  "好吧",
  "对不对",
  "是不是",
  "你知道吧",
  "你懂吧",
]);
const FILLER_DISCOURSE_OPTIONAL_PARTICLE_PREFIXES = ["其实", "就是", "然后", "那么"];
const FILLER_DISCOURSE_REQUIRED_PARTICLE_PREFIXES = ["那个", "这个", "所以", "但是", "不过", "还有", "而且"];
const FILLER_TAIL_PHRASES = ["你知道吧", "你懂吧", "对不对", "是不是", "对吧", "是吧", "好吧"];

function isParticleOnlyFillerText(value) {
  return value.length >= 1 && value.length <= 8 && Array.from(value).every((char) => FILLER_EDGE_CHARS.includes(char));
}

function isDiscourseOnlyFillerText(value) {
  if (FILLER_DISCOURSE_ONLY.has(value)) return true;
  return FILLER_DISCOURSE_PREFIXES.some((prefix) => {
    if (!value.startsWith(prefix)) return false;
    const suffix = value.slice(prefix.length);
    return suffix.length > 0 && Array.from(suffix).every((char) => FILLER_EDGE_CHARS.includes(char));
  });
}

function isStandaloneFillerCueText(text, startMs = null, endMs = null) {
  const start = Number(startMs);
  const end = Number(endMs);
  const value = normalizeSubtitleTextForFillerCheck(text);
  if (!value) return false;
  const isStandalone = FILLER_STANDALONE.has(value)
    || isDiscourseOnlyFillerText(value)
    || isParticleOnlyFillerText(value)
    || (value.length <= 3 && Array.from(value).every((char) => `${FILLER_EDGE_CHARS}哈呵嘿`.includes(char)));
  if (!isStandalone) return false;
  const duration = Number.isFinite(start) && Number.isFinite(end) ? Math.max(end - start, 0) : 0;
  const isLaughter = FILLER_LAUGHTER.has(value) || Array.from(value).every((char) => "哈呵嘿".includes(char));
  if (!duration) return true;
  return duration <= (isLaughter ? 1200 : 5000);
}

function stripEdgeAndDiscourseFillersForCheck(text) {
  const value = normalizeSubtitleTextForFillerCheck(text);
  if (value.length <= 1) return value;
  let left = 0;
  let right = value.length;
  let changed = true;
  while (changed) {
    changed = false;
    while (right - left > 1 && FILLER_EDGE_CHARS.includes(value[left])) {
      left += 1;
      changed = true;
    }
    while (right - left > 1 && FILLER_EDGE_CHARS.includes(value[right - 1])) {
      right -= 1;
      changed = true;
    }
    const windowText = value.slice(left, right);
    for (const prefix of FILLER_DISCOURSE_OPTIONAL_PARTICLE_PREFIXES) {
      const removed = leadingDiscourseFillerLength(windowText, prefix, false);
      if (removed) {
        left += removed;
        changed = true;
        break;
      }
    }
    if (changed) continue;
    for (const prefix of FILLER_DISCOURSE_REQUIRED_PARTICLE_PREFIXES) {
      const removed = leadingDiscourseFillerLength(windowText, prefix, true);
      if (removed) {
        left += removed;
        changed = true;
        break;
      }
    }
    if (changed) continue;
    for (const phrase of FILLER_TAIL_PHRASES) {
      if (right - left > phrase.length && value.slice(left, right).endsWith(phrase)) {
        right -= phrase.length;
        changed = true;
        break;
      }
    }
  }
  return value.slice(left, right);
}

function leadingDiscourseFillerLength(text, prefix, requireParticle) {
  if (!text.startsWith(prefix)) return 0;
  const prefixEnd = prefix.length;
  if (text.length <= prefixEnd + 1) return 0;
  if (FILLER_EDGE_CHARS.includes(text[prefixEnd])) return prefixEnd + 1;
  if (requireParticle) return 0;
  return text.length >= prefixEnd + 4 ? prefixEnd : 0;
}

function hasFillerCleanupCandidate(text, startMs = null, endMs = null) {
  if (isStandaloneFillerCueText(text, startMs, endMs)) return true;
  const value = normalizeSubtitleTextForFillerCheck(text);
  return value.length > 1 && stripEdgeAndDiscourseFillersForCheck(value) !== value;
}

function getStandaloneFillerCueCount() {
  return (state.cues || []).filter((cue) => (
    hasFillerCleanupCandidate(cue.clean_text || "", cue.start_ms, cue.end_ms)
  )).length;
}

function buildSubtitleTextOverridePayload(overrides = null) {
  if (Array.isArray(overrides)) return overrides;
  const cueByIndex = new Map((state.cues || []).map((cue) => [String(cue.index || ""), cue]));
  const payload = new Map();
  Object.entries(state.cueDeletedDrafts || {}).forEach(([cueIndex, deleted]) => {
    if (deleted) payload.set(String(cueIndex), { index: Number(cueIndex), delete: true });
  });
  Object.entries(state.cueTextDrafts || {}).forEach(([cueIndex, text]) => {
    if (state.cueDeletedDrafts?.[String(cueIndex)]) return;
    const index = Number(cueIndex);
    const originalText = String(cueByIndex.get(String(cueIndex))?.clean_text || "");
    const cleanText = String(text || "").trim();
    if (!Number.isFinite(index) || index <= 0 || cleanText === originalText) return;
    payload.set(String(cueIndex), cleanText ? { index, clean_text: cleanText } : { index, delete: true });
  });
  return Array.from(payload.values());
}

async function saveSubtitleTextOverrides(overrides = null) {
  if (!state.currentJobId) throw new Error("请先载入任务");
  const cues = buildSubtitleTextOverridePayload(overrides);
  if (!cues.length) throw new Error("没有需要保存的唱词修改");
  const response = await request(`/transcriptions/jobs/${encodeURIComponent(state.currentJobId)}/subtitles/text`, {
    method: "PUT",
    body: JSON.stringify({ cues }),
  });
  state.cues = response.cues || [];
  state.subtitlePreview = response.content || "";
  state.cueTextDrafts = {};
  state.cueDeletedDrafts = {};
  const cleanResult = await request(`/transcriptions/jobs/${encodeURIComponent(state.currentJobId)}/subtitles/preview?format=srt&variant=clean`).catch(() => null);
  state.cleanSubtitlePreview = cleanResult?.content || state.cleanSubtitlePreview;
  await loadJob(state.currentJobId, false).catch(() => {});
  return cues.length;
}

async function saveCombinedCueEdits() {
  if (!state.currentJobId) throw new Error("请先载入任务");
  const textChanges = buildSubtitleTextOverridePayload();
  const voiceprintLearning = await saveSubtitleSpeakerOverrides();
  let savedTextCount = 0;
  if (textChanges.length) {
    savedTextCount = await saveSubtitleTextOverrides(textChanges);
  }
  return { savedTextCount, voiceprintLearning };
}

async function deleteStandaloneFillerCues() {
  if (!state.currentJobId) throw new Error("请先载入任务");
  const response = await request(`/transcriptions/jobs/${encodeURIComponent(state.currentJobId)}/subtitles/text/delete-fillers`, {
    method: "POST",
  });
  const deletedCount = Number(response.deleted_count || 0);
  state.cues = response.cues || [];
  state.subtitlePreview = response.content || "";
  state.cueTextDrafts = {};
  state.cueDeletedDrafts = {};
  const cleanResult = await request(`/transcriptions/jobs/${encodeURIComponent(state.currentJobId)}/subtitles/preview?format=srt&variant=clean`).catch(() => null);
  state.cleanSubtitlePreview = cleanResult?.content || state.cleanSubtitlePreview;
  await loadJob(state.currentJobId, false).catch(() => {});
  if (!deletedCount) throw new Error("当前字幕里没有可清理的语气词");
  return deletedCount;
}

async function applyScriptContextCorrections() {
  if (!state.currentJobId) throw new Error("请先载入任务");
  const response = await request(`/transcriptions/jobs/${encodeURIComponent(state.currentJobId)}/subtitles/text/apply-script-context`, {
    method: "POST",
  });
  const updatedCount = Number(response.updated_count || 0);
  state.cues = response.cues || [];
  state.subtitlePreview = response.content || "";
  state.cueTextDrafts = {};
  state.cueDeletedDrafts = {};
  const cleanResult = await request(`/transcriptions/jobs/${encodeURIComponent(state.currentJobId)}/subtitles/preview?format=srt&variant=clean`).catch(() => null);
  state.cleanSubtitlePreview = cleanResult?.content || state.cleanSubtitlePreview;
  await loadJob(state.currentJobId, false).catch(() => {});
  return updatedCount;
}

async function updateCurrentJobScriptContext() {
  if (!state.currentJobId) throw new Error("请先载入任务");
  const scriptText = state.scriptText || document.querySelector("#review-script-text-input")?.value.trim() || "";
  const scriptFile = state.pickedScriptFile;
  if (!scriptText && !scriptFile) throw new Error("请先上传或粘贴台本/唱词");
  const formData = new FormData();
  if (scriptText) formData.append("script_text", scriptText);
  if (scriptFile?.browserFile) {
    formData.append("script_file", scriptFile.browserFile);
  } else if (scriptFile?.path) {
    const blob = await loadClientBridgeFile(scriptFile);
    formData.append("script_file", new File([blob], scriptFile.name, { type: blob.type || guessMimeType(scriptFile.name) }));
  }
  const detail = decorateJobDetail(await request(`/transcriptions/jobs/${encodeURIComponent(state.currentJobId)}/script-context`, {
    method: "POST",
    body: formData,
  }));
  state.jobDetail = detail;
  state.pickedScriptFile = null;
  state.pickedScriptSource = "";
  state.scriptText = "";
  await refreshProjectScripts().catch(() => {});
  return detail.script_context || null;
}

async function uploadLowerThirds() {
  if (!state.currentJobId) throw new Error("请先创建或载入任务");
  const targetJobId = String(state.currentJobId || "");
  const targetProjectId = getCurrentProjectId();
  const input = document.querySelector("#lower-third-input");
  const inputFiles = Array.from(input?.files || []);
  const pickedFiles = state.pickedLowerThirdFiles || [];
  if (!inputFiles.length && !pickedFiles.length) throw new Error("请至少选择一张人名条图片");
  if (
    pickedFiles.length &&
    (state.pickedLowerThirdProjectId !== targetProjectId || state.pickedLowerThirdJobId !== targetJobId)
  ) {
    state.pickedLowerThirdFiles = [];
    state.pickedLowerThirdSource = "";
    state.pickedLowerThirdProjectId = "";
    state.pickedLowerThirdJobId = "";
    throw new Error("项目或任务已切换，请为当前任务重新选择人名条图片。");
  }
  if (window.SpeakerDesktopBridge?.isAvailable?.() && inputFiles.length) {
    throw new Error("为避免 XML 人名条脱机，请使用“选择人名条图片”按钮或直接拖到按钮区域，不要使用系统浏览器文件框。");
  }
  if (window.SpeakerDesktopBridge?.isAvailable?.() && pickedFiles.some((item) => !item?.path)) {
    throw new Error("人名条缺少本机路径，请重新点击选择图片。");
  }
  const formData = new FormData();
  const clientPathPayload = [];
  inputFiles.forEach((file) => {
    formData.append("files", file);
    clientPathPayload.push({ file_name: file.name, path: "" });
  });
  for (const item of pickedFiles) {
    const blob = await loadClientBridgeFile(item);
    if (getCurrentProjectId() !== targetProjectId || String(state.currentJobId || "") !== targetJobId) {
      throw new Error("项目或任务已切换，本次人名条上传已取消。");
    }
    formData.append("files", new File([blob], item.name, { type: blob.type || guessMimeType(item.name) }));
    clientPathPayload.push({ file_name: item.name, path: item.path });
  }
  if (clientPathPayload.some((item) => item.path)) {
    formData.append("client_paths", JSON.stringify(clientPathPayload));
  }
  if (getCurrentProjectId() !== targetProjectId || String(state.currentJobId || "") !== targetJobId) {
    throw new Error("项目或任务已切换，本次人名条上传已取消。");
  }
  await request(`/transcriptions/jobs/${encodeURIComponent(targetJobId)}/lower-thirds`, { method: "POST", body: formData });
  state.pickedLowerThirdFiles = [];
  state.pickedLowerThirdSource = "";
  state.pickedLowerThirdProjectId = "";
  state.pickedLowerThirdJobId = "";
  if (input) input.value = "";
  if (getCurrentProjectId() === targetProjectId && String(state.currentJobId || "") === targetJobId) {
    await loadJob(targetJobId);
  }
}

async function loadProjectLowerThirds() {
  if (!state.currentJobId) throw new Error("请先创建或载入任务");
  const targetJobId = String(state.currentJobId || "");
  const targetProjectId = getCurrentProjectId();
  await request(`/transcriptions/jobs/${encodeURIComponent(targetJobId)}/lower-thirds/from-project-library`, { method: "POST" });
  if (getCurrentProjectId() === targetProjectId && String(state.currentJobId || "") === targetJobId) {
    await loadJob(targetJobId);
  }
}

function normalizeSubtitleBackplateRole(role) {
  return role === "without_speaker" ? "without_speaker" : "with_speaker";
}

function getSubtitleBackplateInput(role) {
  return document.querySelector(`#subtitle-backplate-${normalizeSubtitleBackplateRole(role)}-input`);
}

function getPickedSubtitleBackplateFile(role) {
  const normalizedRole = normalizeSubtitleBackplateRole(role);
  return state.pickedSubtitleBackplateFiles?.[normalizedRole] || (normalizedRole === "with_speaker" ? state.pickedSubtitleBackplateFile : null) || null;
}

function clearPickedSubtitleBackplateFile(role) {
  const normalizedRole = normalizeSubtitleBackplateRole(role);
  state.pickedSubtitleBackplateFiles = {
    ...(state.pickedSubtitleBackplateFiles || {}),
    [normalizedRole]: null,
  };
  state.pickedSubtitleBackplateSources = {
    ...(state.pickedSubtitleBackplateSources || {}),
    [normalizedRole]: "",
  };
  state.pickedSubtitleBackplateProjectIds = {
    ...(state.pickedSubtitleBackplateProjectIds || {}),
    [normalizedRole]: "",
  };
  state.pickedSubtitleBackplateJobIds = {
    ...(state.pickedSubtitleBackplateJobIds || {}),
    [normalizedRole]: "",
  };
  if (normalizedRole === "with_speaker") {
    state.pickedSubtitleBackplateFile = null;
    state.pickedSubtitleBackplateSource = "";
  }
  const input = getSubtitleBackplateInput(normalizedRole);
  if (input) input.value = "";
}

async function uploadSubtitleBackplate(role = "with_speaker") {
  if (!state.currentJobId) throw new Error("请先创建或载入任务");
  const targetProjectId = getCurrentProjectId();
  const targetJobId = String(state.currentJobId || "");
  if (!targetProjectId) throw new Error("请先选择项目，再上传唱词条底图");
  const normalizedRole = normalizeSubtitleBackplateRole(role);
  const input = getSubtitleBackplateInput(normalizedRole);
  const inputFile = input?.files?.[0] || null;
  const pickedFile = getPickedSubtitleBackplateFile(normalizedRole);
  const fileMeta = pickedFile || (inputFile ? { name: inputFile.name, path: "", browserFile: inputFile } : null);
  if (!fileMeta) throw new Error("请先选择唱词条底图");
  const pickedProjectId = String(state.pickedSubtitleBackplateProjectIds?.[normalizedRole] || "");
  const pickedJobId = String(state.pickedSubtitleBackplateJobIds?.[normalizedRole] || "");
  if (pickedProjectId !== targetProjectId || pickedJobId !== targetJobId) {
    clearPickedSubtitleBackplateFile(normalizedRole);
    throw new Error("项目或任务已切换，请为当前任务重新选择唱词条底图。");
  }
  if (window.SpeakerDesktopBridge?.isAvailable?.() && !fileMeta.path) {
    throw new Error("为避免 XML 唱词条底图脱机，请使用“选择唱词条底图”按钮或直接拖到按钮区域。");
  }
  const formData = new FormData();
  if (fileMeta.path) {
    const blob = await loadClientBridgeFile(fileMeta);
    if (getCurrentProjectId() !== targetProjectId || String(state.currentJobId || "") !== targetJobId) {
      throw new Error("项目或任务已切换，本次唱词条底图上传已取消。");
    }
    formData.append("file", new File([blob], fileMeta.name, { type: blob.type || guessMimeType(fileMeta.name) }));
    formData.append("client_path", fileMeta.path);
  } else {
    formData.append("file", fileMeta.browserFile || inputFile);
  }
  formData.append("role", normalizedRole);
  await requestV2(
    `/projects/${encodeURIComponent(targetProjectId)}/transcriptions/jobs/${encodeURIComponent(targetJobId)}/subtitle-backplate`,
    { method: "POST", body: formData },
  );
  if (getCurrentProjectId() !== targetProjectId || String(state.currentJobId || "") !== targetJobId) {
    throw new Error("项目或任务已切换，底图已保存到原项目；当前页面不会载入它。");
  }
  clearPickedSubtitleBackplateFile(normalizedRole);
  await loadJob(targetJobId);
}

async function deleteSubtitleBackplate(role = "with_speaker") {
  if (!state.currentJobId) throw new Error("请先创建或载入任务");
  const targetProjectId = getCurrentProjectId();
  const targetJobId = String(state.currentJobId || "");
  if (!targetProjectId) throw new Error("请先选择项目，再删除唱词条底图");
  const normalizedRole = normalizeSubtitleBackplateRole(role);
  await requestV2(
    `/projects/${encodeURIComponent(targetProjectId)}/transcriptions/jobs/${encodeURIComponent(targetJobId)}/subtitle-backplate?role=${encodeURIComponent(normalizedRole)}`,
    { method: "DELETE" },
  );
  if (getCurrentProjectId() !== targetProjectId || String(state.currentJobId || "") !== targetJobId) {
    throw new Error("项目或任务已切换，原任务底图已删除；当前页面保持不变。");
  }
  clearPickedSubtitleBackplateFile(normalizedRole);
  await loadJob(targetJobId);
}

async function loadProjectSubtitleBackplates() {
  const targetProjectId = getCurrentProjectId();
  const targetJobId = String(state.currentJobId || "");
  if (!targetProjectId || !targetJobId) throw new Error("请先创建或载入当前项目的任务");
  await requestV2(
    `/projects/${encodeURIComponent(targetProjectId)}/transcriptions/jobs/${encodeURIComponent(targetJobId)}/subtitle-backplate/from-project-library`,
    { method: "POST" },
  );
  if (getCurrentProjectId() === targetProjectId && String(state.currentJobId || "") === targetJobId) {
    await loadJob(targetJobId);
  }
}

async function refreshVoiceCloneLibrary({ reset = true, allowAutoSelect = true } = {}) {
  const targetProjectId = getCurrentProjectId();
  if (!targetProjectId) {
    state.voiceClone.library.groups = [];
    state.voiceClone.library.nextCursor = null;
    state.voiceClone.library.total = 0;
    state.voiceClone.library.creatingProfiles = [];
    state.voiceClone.library.loaded = false;
    state.voiceClone.profiles = [];
    state.voiceClone.records = [];
    return false;
  }
  const requestGeneration = ++state.voiceClone.requestGeneration;
  const libraryState = state.voiceClone.library;
  const cursor = reset ? null : libraryState.nextCursor;
  const query = String(libraryState.query || state.voiceClone.profileSearch || "").trim();
  const params = new URLSearchParams({ limit: "30" });
  if (query) params.set("q", query);
  if (cursor) params.set("cursor", cursor);
  libraryState.loading = true;
  let response;
  let creatingProfiles;
  try {
    [response, creatingProfiles] = await Promise.all([
      requestV2(buildProjectVoiceClonePath(targetProjectId, `/library?${params.toString()}`)),
      requestV2(buildProjectVoiceClonePath(targetProjectId, "/profiles/creating")),
    ]);
  } finally {
    if (getCurrentProjectId() === targetProjectId) libraryState.loading = false;
  }
  if (getCurrentProjectId() !== targetProjectId || state.voiceClone.requestGeneration !== requestGeneration) {
    return false;
  }
  const normalizeGroup = globalThis.SpeakerVoiceCloneLibrary?.normalizeGroup
    || ((group) => group);
  const nextGroups = Array.isArray(response?.groups)
    ? response.groups.map(normalizeGroup)
    : [];
  libraryState.groups = reset
    ? nextGroups
    : [...libraryState.groups, ...nextGroups];
  libraryState.nextCursor = response?.next_cursor || null;
  libraryState.total = Number(response?.total || libraryState.groups.length);
  libraryState.availableGroupCount = Number(response?.available_group_count || 0);
  libraryState.exceptionGroupCount = Number(response?.exception_group_count || 0);
  libraryState.reusableSampleCount = Number(response?.reusable_sample_count || 0);
  libraryState.creatingProfiles = Array.isArray(creatingProfiles) ? creatingProfiles : [];
  libraryState.loaded = true;
  libraryState.query = query;
  const selectedProfileId = state.voiceClone.profileId;
  const selectedProfile = state.voiceClone.profiles.find(
    (profile) => profile.id === selectedProfileId,
  ) || null;
  const reconcileLibrarySelection = globalThis.SpeakerVoiceCloneLibrary?.reconcileLibrarySelection
    || (() => ({ profiles: [], profileId: "" }));
  const reconciled = reconcileLibrarySelection(
    libraryState.groups,
    {
      selectedProfileId,
      selectedProfile,
      preserveSelectedProfile: Boolean(query || libraryState.nextCursor),
      allowAutoSelect,
    },
  );
  state.voiceClone.profiles = reconciled.profiles;
  state.voiceClone.profileId = reconciled.profileId;
  const expandedGroupKey = libraryState.expandedGroupKey;
  const shouldApplyDetails = () => state.voiceClone.library.expandedGroupKey === expandedGroupKey;
  await Promise.all([
    refreshVoiceCloneRecords({ projectId: targetProjectId, requestGeneration }),
    ...(expandedGroupKey ? [
      loadVoiceCloneGroupProfiles(expandedGroupKey, { shouldApply: shouldApplyDetails, expand: false }),
      loadVoiceCloneGroupSamples(expandedGroupKey, { shouldApply: shouldApplyDetails }),
    ] : []),
  ]);
  return getCurrentProjectId() === targetProjectId && state.voiceClone.requestGeneration === requestGeneration;
}

async function refreshVoiceCloneSamplePage({ reset = true } = {}) {
  const targetProjectId = getCurrentProjectId();
  const samplePage = state.voiceClone.samplePage;
  if (!targetProjectId) {
    samplePage.items = [];
    samplePage.nextCursor = null;
    samplePage.total = 0;
    samplePage.loaded = false;
    state.voiceClone.samples = [];
    return false;
  }
  const requestGeneration = state.voiceClone.requestGeneration;
  const params = new URLSearchParams({ limit: "30" });
  const query = String(samplePage.query || state.voiceClone.sampleSearch || "").trim();
  if (query) params.set("q", query);
  if (samplePage.status) params.set("status", samplePage.status);
  if (!reset && samplePage.nextCursor) params.set("cursor", samplePage.nextCursor);
  samplePage.loading = true;
  let response;
  try {
    response = await requestV2(
      buildProjectVoiceClonePath(targetProjectId, `/samples/page?${params.toString()}`),
    );
  } finally {
    if (getCurrentProjectId() === targetProjectId) samplePage.loading = false;
  }
  if (
    getCurrentProjectId() !== targetProjectId
    || state.voiceClone.requestGeneration !== requestGeneration
  ) {
    return false;
  }
  const items = Array.isArray(response?.items) ? response.items : [];
  samplePage.items = reset ? items : [...samplePage.items, ...items];
  samplePage.nextCursor = response?.next_cursor || null;
  samplePage.total = Number(response?.total || samplePage.items.length);
  samplePage.loaded = true;
  samplePage.query = query;
  state.voiceClone.samples = samplePage.items;
  if (
    state.voiceClone.sampleId
    && !state.voiceClone.samples.some((sample) => sample.id === state.voiceClone.sampleId)
  ) {
    state.voiceClone.sampleId = "";
  }
  return true;
}

async function loadVoiceCloneGroupProfiles(groupKey, { shouldApply = null, expand = true } = {}) {
  const targetProjectId = getCurrentProjectId();
  const normalizedGroupKey = String(groupKey || "").trim();
  if (!targetProjectId || !normalizedGroupKey) return [];
  const requestGeneration = state.voiceClone.requestGeneration;
  const profiles = await requestV2(
    buildProjectVoiceClonePath(
      targetProjectId,
      `/library/${encodeURIComponent(normalizedGroupKey)}/profiles`,
    ),
  );
  if (
    getCurrentProjectId() !== targetProjectId
    || state.voiceClone.requestGeneration !== requestGeneration
    || (typeof shouldApply === "function" && !shouldApply())
  ) {
    return [];
  }
  const normalizeProfile = globalThis.SpeakerVoiceCloneLibrary?.normalizeProfile || ((profile) => profile);
  const items = Array.isArray(profiles)
    ? profiles.map((profile) => normalizeProfile({
      ...profile,
      group_key: normalizedGroupKey,
    }))
    : [];
  state.voiceClone.library.profilesByGroup[normalizedGroupKey] = items;
  if (expand) state.voiceClone.library.expandedGroupKey = normalizedGroupKey;
  return items;
}

async function loadVoiceCloneGroupSamples(groupKey, { shouldApply = null } = {}) {
  const projectId = getCurrentProjectId();
  const requestGeneration = state.voiceClone.requestGeneration;
  const key = String(groupKey || "").trim();
  if (!projectId || !key) return [];
  const response = await requestV2(buildProjectVoiceClonePath(projectId, `/library/${encodeURIComponent(key)}/samples`));
  if (getCurrentProjectId() !== projectId || state.voiceClone.requestGeneration !== requestGeneration
    || (typeof shouldApply === "function" && !shouldApply())) return [];
  const normalizeSample = globalThis.SpeakerVoiceCloneLibrary?.normalizeSample || ((item) => item);
  const samples = Array.isArray(response) ? response.map(normalizeSample) : [];
  state.voiceClone.library.samplesByGroup = { ...state.voiceClone.library.samplesByGroup, [key]: samples };
  state.voiceClone.samples = [...new Map([...state.voiceClone.samples, ...samples].map((sample) => [sample.id, sample])).values()];
  return samples;
}

async function refreshVoiceCloneData() {
  if (!await refreshVoiceCloneLibrary({ reset: true })) return false;
  const projectId = getCurrentProjectId();
  const requestGeneration = state.voiceClone.requestGeneration;
  const creating = state.voiceClone.library.creatingProfiles;
  if (!creating.length) return true;
  const results = await Promise.allSettled(creating.map((profile) => requestV2(
    buildProjectVoiceClonePath(projectId, `/profiles/${encodeURIComponent(profile.id)}`),
  )));
  if (getCurrentProjectId() !== projectId || state.voiceClone.requestGeneration !== requestGeneration) return false;
  if (results.some((result) => result.status === "fulfilled" && result.value?.status !== "creating")) {
    return refreshVoiceCloneLibrary({ reset: true });
  }
  return true;
}

async function refreshVoiceCloneRecords({
  projectId = getCurrentProjectId(),
  requestGeneration = state.voiceClone.requestGeneration,
} = {}) {
  const targetProjectId = String(projectId || "");
  if (!targetProjectId) {
    state.voiceClone.records = [];
    return false;
  }
  const records = await requestV2(
    buildProjectVoiceClonePath(targetProjectId, "/synthesis-records"),
  );
  if (
    getCurrentProjectId() !== targetProjectId
    || (requestGeneration !== null && state.voiceClone.requestGeneration !== requestGeneration)
  ) {
    return false;
  }
  state.voiceClone.records = Array.isArray(records) ? records : [];
  return true;
}

async function uploadVoiceSampleFile(personId, file) {
  if (!personId || !file) throw new Error("请选择人物和样本文件");
  const formData = new FormData();
  formData.append("file", file);
  const person = state.people.find((item) => item.id === personId) || null;
  const personProjectId = String(
    person?.project_id
      || state.projects.find((project) => project.program_name === person?.program_name)?.project_id
      || "",
  ).trim();
  const sample = personProjectId
    ? await requestV2(
      `/projects/${encodeURIComponent(personProjectId)}/voiceprints/people/${encodeURIComponent(personId)}/samples`,
      { method: "POST", body: formData },
    )
    : await request(`/voiceprints/people/${encodeURIComponent(personId)}/samples`, { method: "POST", body: formData });
  state.voiceClone.personId = personId;
  await refreshPeople();
  return sample;
}

async function uploadVoiceCloneSampleFile(displayName, file, { shouldApply = null } = {}) {
  const targetProjectId = getCurrentProjectId();
  const name = String(displayName || "").trim();
  if (!name) throw new Error("请先填写音色名称");
  if (!targetProjectId) throw new Error("请先选择项目，再使用声音复刻");
  if (!file) throw new Error("请选择声音复刻样本文件");
  const draftProjectId = String(state.voiceClone.sampleDraft?.projectId || "");
  if (draftProjectId && draftProjectId !== targetProjectId) {
    clearVoiceCloneSampleDraft();
    throw new Error("项目已切换，请为当前项目重新选择声音复刻样本。");
  }
  const requestGeneration = state.voiceClone.requestGeneration;
  const isCurrent = () => getCurrentProjectId() === targetProjectId
    && state.voiceClone.requestGeneration === requestGeneration
    && (typeof shouldApply !== "function" || shouldApply());
  const cached = state.voiceClone.addFlow?.uploadedFile === file ? state.voiceClone.addFlow.uploadedSample : null;
  let sample = isVoiceAssetExpired(cached) ? null : cached;
  if (!sample) {
    state.voiceClone.sampleId = "";
    state.voiceClone.addFlow = { ...state.voiceClone.addFlow, uploadedSample: null, uploadedFile: null, sourceAsset: null, createdProfileId: "", sourceSampleId: "", existingProfiles: [], existingSamples: [], matchedIdentity: null };
    const formData = new FormData();
    formData.append("display_name", name);
    formData.append("file", file);
    sample = await requestV2(buildProjectVoiceClonePath(targetProjectId, "/samples"), { method: "POST", body: formData });
  }
  if (!isCurrent()) return sample;
  const normalizeSample = globalThis.SpeakerVoiceCloneLibrary?.normalizeSample || ((item) => item || {});
  const normalizedSample = normalizeSample(sample);
  const identityId = String(normalizedSample.identity_id || "").trim();
  const matchType = String(sample?.match_type || "");
  const sampleSlotsRemaining = sample?.sample_slots_remaining ?? null;
  state.voiceClone.addFlow = {
    ...state.voiceClone.addFlow,
    stage: matchType === "new_identity" ? "new_identity" : "matched",
    uploadedSample: sample,
    uploadedFile: file,
    matchType,
    matchScore: sample?.match_score ?? null,
    matchedIdentity: identityId ? {
      id: identityId,
      display_name: normalizedSample.identity_display_name || null,
      aliases: Array.isArray(normalizedSample.identity_aliases) ? normalizedSample.identity_aliases : [],
    } : null,
    sampleSlotsRemaining,
    existingSamples: normalizedSample.id ? [normalizedSample] : [],
    error: "",
  };
  state.voiceClone.samples = [normalizedSample, ...state.voiceClone.samples.filter((item) => item.id !== normalizedSample.id)];
  state.voiceClone.displayName = name;
  state.voiceClone.sampleId = normalizedSample.id || state.voiceClone.sampleId;
  return sample;
}

async function uploadVoiceCloneSampleAndCreateProfile(displayName, file, { shouldApply = null } = {}) {
  const targetProjectId = getCurrentProjectId();
  const requestGeneration = state.voiceClone.requestGeneration;
  const isCurrent = () => getCurrentProjectId() === targetProjectId
    && state.voiceClone.requestGeneration === requestGeneration
    && (typeof shouldApply !== "function" || shouldApply());
  const sample = await uploadVoiceCloneSampleFile(displayName, file, { shouldApply: isCurrent });
  if (!isCurrent()) return null;
  if (!state.serviceStatus.voiceClone?.discard_voice_after_download && sample.match_type !== "new_identity" && sample.identity_id) {
    const profiles = await loadVoiceCloneGroupProfiles(sample.identity_id, { shouldApply: isCurrent, expand: false });
    if (!isCurrent()) return null;
    state.voiceClone.addFlow.existingProfiles = profiles;
    if (profiles.some((profile) => globalThis.SpeakerVoiceCloneLibrary?.isSelectableProfile(profile))) {
      state.voiceClone.addFlow.stage = "matched";
      return null;
    }
  }
  return createVoiceCloneProfile({ shouldApply: isCurrent });
}

async function deleteVoiceCloneSample(sampleId) {
  if (!sampleId) throw new Error("缺少声音复刻样本 ID");
  const targetProjectId = getCurrentProjectId();
  await requestV2(buildProjectVoiceClonePath(targetProjectId, `/samples/${encodeURIComponent(sampleId)}`), { method: "DELETE" });
  if (getCurrentProjectId() !== targetProjectId) {
    throw new Error("项目已切换，样本已从原项目删除；当前页面保持不变。");
  }
  if (state.voiceClone.sampleId === sampleId) state.voiceClone.sampleId = "";
  state.voiceClone.samples = state.voiceClone.samples.filter((sample) => sample.id !== sampleId);
  if (state.voiceClone.sharedPlayer.sampleId === sampleId) state.voiceClone.sharedPlayer = { sampleId: "", label: "", url: "" };
  state.voiceClone.samplePage.items = state.voiceClone.samplePage.items.filter((sample) => sample.id !== sampleId);
  for (const [key, samples] of Object.entries(state.voiceClone.library.samplesByGroup || {})) {
    state.voiceClone.library.samplesByGroup[key] = samples.filter((sample) => sample.id !== sampleId);
  }
  state.voiceClone.addFlow.existingSamples = (state.voiceClone.addFlow.existingSamples || []).filter((sample) => sample.id !== sampleId);
  if (state.voiceClone.addFlow.uploadedSample?.id === sampleId) {
    state.voiceClone.addFlow.uploadedSample = null;
    state.voiceClone.addFlow.uploadedFile = null;
  }
  await refreshVoiceCloneData();
}

async function deleteVoiceCloneProfile(profileId) {
  if (!profileId) throw new Error("缺少音色 ID");
  const targetProjectId = getCurrentProjectId();
  await requestV2(buildProjectVoiceClonePath(targetProjectId, `/profiles/${encodeURIComponent(profileId)}`), { method: "DELETE" });
  if (getCurrentProjectId() !== targetProjectId) {
    throw new Error("项目已切换，音色已从原项目删除；当前页面保持不变。");
  }
  if (state.voiceClone.profileId === profileId) {
    state.voiceClone.profileId = "";
  }
  await refreshVoiceCloneLibrary({ reset: true, allowAutoSelect: false });
}

async function deleteVoiceCloneRecord(recordId, projectId = getCurrentProjectId()) {
  if (!recordId) throw new Error("缺少生成记录 ID");
  const targetProjectId = String(projectId || "");
  await requestV2(buildProjectVoiceClonePath(targetProjectId, `/synthesis-records/${encodeURIComponent(recordId)}`), { method: "DELETE" });
  if (getCurrentProjectId() !== targetProjectId) return false;
  state.voiceClone.records = state.voiceClone.records.filter((record) => record.id !== recordId);
  return true;
}

async function deleteVoiceSample(sampleId) {
  if (!sampleId) throw new Error("缺少样本 ID");
  await request(`/voiceprints/samples/${encodeURIComponent(sampleId)}`, { method: "DELETE" });
  if (state.voiceClone.sampleId === sampleId) state.voiceClone.sampleId = "";
  await refreshPeople();
  await refreshVoiceCloneData().catch(() => {});
}

async function confirmVoiceSample(sampleId) {
  if (!sampleId) throw new Error("缺少样本 ID");
  assertVoiceAssetAvailable(state.people.flatMap((person) => person.samples || []).find((sample) => sample.id === sampleId));
  await request(`/voiceprints/samples/${encodeURIComponent(sampleId)}/confirm`, { method: "POST" });
  await refreshPeople();
  await refreshVoiceCloneData().catch(() => {});
}

async function deleteVoicePerson(personId) {
  if (!personId) throw new Error("缺少人物 ID");
  await request(`/voiceprints/people/${encodeURIComponent(personId)}`, { method: "DELETE" });
  if (state.voiceClone.personId === personId) {
    state.voiceClone.personId = "";
    state.voiceClone.sampleId = "";
    state.voiceClone.profileId = "";
  }
  await refreshPeople();
  await refreshProjects();
  await refreshVoiceCloneData().catch(() => {});
}

async function purgeCurrentProject() {
  const projectId = getCurrentProjectId();
  if (!projectId) throw new Error("请先进入一个有效项目");
  await requestV2(`/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
  setCurrentProject("");
  resetDisplayedJobState();
  await Promise.allSettled([refreshProjects(), refreshJobs(), refreshPeople()]);
  await refreshVoiceCloneData().catch(() => {});
}

async function waitForVoiceCloneProfile(profile, projectId, shouldApply) {
  const deadline = Date.now() + 30000;
  while (profile.status === "creating" && shouldApply()) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    let deadlineTimer;
    try {
      const updated = await Promise.race([
        requestV2(buildProjectVoiceClonePath(projectId, `/profiles/${encodeURIComponent(profile.id)}`), { timeoutMs: Math.min(15000, remaining) }),
        new Promise((resolve) => { deadlineTimer = window.setTimeout(() => resolve(null), remaining); }),
      ]);
      if (!updated || !shouldApply()) break;
      profile = updated;
    } catch (error) {
      if (Date.now() >= deadline) break;
      throw error;
    } finally {
      window.clearTimeout(deadlineTimer);
    }
    if (profile.status !== "creating" || !shouldApply()) break;
    const delay = Math.min(1000, deadline - Date.now());
    if (delay > 0) await new Promise((resolve) => window.setTimeout(resolve, delay));
  }
  return profile;
}

async function createVoiceCloneProfile({ shouldApply = null, sourceSampleId = "" } = {}) {
  const targetProjectId = getCurrentProjectId();
  const requestGeneration = state.voiceClone.requestGeneration;
  const isCurrent = () => getCurrentProjectId() === targetProjectId
    && state.voiceClone.requestGeneration === requestGeneration
    && (typeof shouldApply !== "function" || shouldApply());
  if (!targetProjectId) throw new Error("请先选择项目，再创建音色");
  if (!state.voiceClone.sampleId) throw new Error("请先上传或选择声音复刻样本");
  applyVoiceCloneProviderDefaults();
  if (!isVoiceCloneServiceAvailable({ provider: state.voiceClone.provider, target_model: state.voiceClone.targetModel })) throw new Error("所选模型暂不可用，请检查声音服务或选择其他模型");
  const selectedSample = state.voiceClone.samples.find((item) => item.id === state.voiceClone.sampleId);
  assertVoiceAssetAvailable(state.voiceClone.addFlow?.sourceAsset?.id === state.voiceClone.sampleId ? state.voiceClone.addFlow.sourceAsset : null);
  assertVoiceAssetAvailable(selectedSample || state.people.flatMap((person) => person.samples || []).find((item) => item.id === sourceSampleId));
  const nameInput = document.querySelector("#voice-clone-display-name-input");
  const displayName = nameInput?.value.trim() || state.voiceClone.displayName || selectedSample?.display_name || "";
  if (!displayName) throw new Error("请先填写音色名称");
  const payload = {
    project_id: targetProjectId,
    ...(sourceSampleId ? { sample_id: sourceSampleId } : { clone_sample_id: state.voiceClone.sampleId }),
    voice_name: displayName,
    provider: state.voiceClone.provider,
    target_model: state.voiceClone.targetModel,
    language_hints: ["zh"],
  };
  const previous = state.voiceClone.library.creatingProfiles.find((item) => (
    (sourceSampleId ? item.source_sample_id === sourceSampleId : item.source_clone_sample_id === payload.clone_sample_id)
    && item.provider === payload.provider && item.target_model === payload.target_model
  ));
  assertVoiceAssetAvailable(previous);
  let profile = previous || await requestV2(buildProjectVoiceClonePath(targetProjectId, "/profiles"), { method: "POST", body: JSON.stringify(payload) });
  if (!isCurrent()) return profile;
  state.voiceClone.addFlow = { ...state.voiceClone.addFlow, stage: "creating", createdProfileId: profile.id };
  if (profile.status === "creating") {
    state.voiceClone.library.creatingProfiles = [profile, ...state.voiceClone.library.creatingProfiles.filter((item) => item.id !== profile.id)];
    renderAll();
    if (!profile.voice_id && String(profile.provider_message || "").includes("未确认")) {
      // A lost create response has no cloud ID to poll. An explicit retry can
      // inspect the saved row once, but must not submit another create.
      if (previous) profile = await requestV2(buildProjectVoiceClonePath(targetProjectId, `/profiles/${encodeURIComponent(profile.id)}`), { timeoutMs: 15000 });
    } else {
      profile = await waitForVoiceCloneProfile(profile, targetProjectId, isCurrent);
    }
  }
  if (!isCurrent()) return profile;
  assertVoiceAssetAvailable(profile);
  state.voiceClone.library.creatingProfiles = state.voiceClone.library.creatingProfiles.filter((item) => item.id !== profile.id);
  if (profile.status === "creating") {
    state.voiceClone.library.creatingProfiles.unshift(profile);
    state.voiceClone.addFlow.error = profile.provider_message || "云端仍在创建，稍后点击重试检查进度；无需重新上传。";
    return profile;
  }
  if (profile.status !== "ready") throw new Error(profile.provider_message || "音色创建失败，请重试");
  const normalizeProfile = globalThis.SpeakerVoiceCloneLibrary?.normalizeProfile || ((item) => item);
  const normalized = normalizeProfile(profile);
  state.voiceClone.profiles = [normalized, ...state.voiceClone.profiles.filter((item) => item.id !== profile.id)];
  state.voiceClone.profileId = profile.id;
  state.voiceClone.pendingModelId = "";
  state.voiceClone.error = "";
  return profile;
}

async function switchVoiceCloneModel(id) {
  const model = getVoiceCloneModelOption(id);
  if (!model || !isVoiceCloneServiceAvailable({ provider: model.provider, target_model: model.model })) throw new Error("所选模型暂不可用");
  if (state.voiceClone.busy) return;
  const profile = state.voiceClone.profiles.find((item) => item.id === state.voiceClone.profileId);
  if (!profile) { selectVoiceCloneModel(id); return; }
  assertVoiceAssetAvailable(profile);
  const projectId = getCurrentProjectId();
  const requestGeneration = state.voiceClone.requestGeneration;
  state.voiceClone.pendingModelId = id;
  const isCurrent = () => getCurrentProjectId() === projectId
    && state.voiceClone.requestGeneration === requestGeneration
    && state.voiceClone.profileId === profile.id && state.voiceClone.pendingModelId === id;
  if (id === `${profile.provider}:${profile.target_model}`) { state.voiceClone.pendingModelId = ""; return; }
  const groupKey = profile.group_key || profile.identity_id;
  const versions = groupKey ? await loadVoiceCloneGroupProfiles(groupKey, { shouldApply: isCurrent, expand: false }) : [];
  if (!isCurrent()) return;
  const existing = versions.find((version) => version.provider === model.provider && version.target_model === model.model
    && globalThis.SpeakerVoiceCloneLibrary?.isSelectableProfile(version)
    && (!state.serviceStatus.voiceClone?.discard_voice_after_download
      || (state.voiceClone.addFlow.uploadedFile && version.source_clone_sample_id === state.voiceClone.addFlow.uploadedSample?.id)));
  if (!existing) return;
  state.voiceClone.profiles = [existing, ...state.voiceClone.profiles.filter((item) => item.id !== existing.id)];
  state.voiceClone.profileId = existing.id;
  if (state.serviceStatus.voiceClone?.discard_voice_after_download) state.voiceClone.addFlow.createdProfileId = existing.id;
  state.voiceClone.pendingModelId = "";
  selectVoiceCloneModel(id);
  await refreshVoiceCloneRecords();
}

async function createVoiceClonePreview() {
  const targetProjectId = getCurrentProjectId();
  if (!targetProjectId) throw new Error("请先选择项目，再生成音频");
  if (!state.voiceClone.profileId) throw new Error("请先创建或选择音色");
  if (state.serviceStatus.voiceClone?.discard_voice_after_download && (!state.voiceClone.addFlow.uploadedFile || state.voiceClone.addFlow.createdProfileId !== state.voiceClone.profileId)) {
    throw new Error("请为本次生成上传新的参考音频");
  }
  const selectedProfile = state.voiceClone.profiles.find(
    (profile) => profile.id === state.voiceClone.profileId,
  );
  assertVoiceAssetAvailable(selectedProfile);
  if (!selectedProfile || !globalThis.SpeakerVoiceCloneLibrary?.isSelectableProfile(selectedProfile)) {
    throw new Error("当前音色不可用，请重新选择后再生成");
  }
  if (state.voiceClone.pendingModelId) throw new Error("请先确认创建或取消模型切换");
  if (!isVoiceCloneServiceAvailable(selectedProfile)) throw new Error("当前音色的模型暂不可用");
  const model = getVoiceCloneModelOption(`${selectedProfile.provider}:${selectedProfile.target_model}`);
  const textarea = document.querySelector("#voice-clone-preview-text");
  const text = String(textarea ? textarea.value : state.voiceClone.previewText || "").trim();
  if (!text) throw new Error("请输入要生成的文本");
  state.voiceClone.previewText = text;
  const instructionInput = document.querySelector("#voice-clone-instruction-input");
  const instruction = model?.capabilities?.instruction ? String(instructionInput ? instructionInput.value : state.voiceClone.voiceInstruction || "").trim() : null;
  if (model?.capabilities?.instruction) state.voiceClone.voiceInstruction = instruction;
  const textLimit = Number(model?.constraints?.text_max_characters || 0);
  const instructionLimit = Number(model?.constraints?.instruction_max_weight || 0);
  if (textLimit && Array.from(text).length > textLimit) throw new Error(`文案超出 ${textLimit} 字限制，请缩短后再生成`);
  if (instructionLimit && getVoiceCloneInstructionWeight(instruction, model) > instructionLimit) throw new Error(`说话方式超出 ${instructionLimit} ${Number(model?.constraints?.instruction_han_weight) === 1 ? "字符" : "加权字符"}限制，请缩短后再生成`);
  const targetProfileId = String(state.voiceClone.profileId || "");
  const requestGeneration = state.voiceClone.requestGeneration;
  let record;
  try {
    record = await requestV2(buildProjectVoiceClonePath(targetProjectId, `/profiles/${encodeURIComponent(targetProfileId)}/preview`), {
      method: "POST",
      body: JSON.stringify({ text, instruction }),
    });
  } catch (error) {
    if (!["voice_clone_profile_unavailable", "voice_asset_expired"].includes(error?.code)) throw error;
    if (getCurrentProjectId() !== targetProjectId || state.voiceClone.profileId !== targetProfileId || state.voiceClone.requestGeneration !== requestGeneration) throw new Error("项目或音色已切换，当前页面保持不变");
    const message = await globalThis.SpeakerVoiceCloneLibrary.recoverUnavailableProfile(
      () => {
        if (getCurrentProjectId() !== targetProjectId || state.voiceClone.profileId !== targetProfileId || state.voiceClone.requestGeneration !== requestGeneration) return;
        state.voiceClone.profileId = "";
      },
      () => getCurrentProjectId() === targetProjectId
        ? refreshVoiceCloneLibrary({ reset: true, allowAutoSelect: false })
        : Promise.resolve(),
    );
    const recoveredError = new Error(error.code === "voice_asset_expired" ? "声音已到期，请新建声音。当前文案和未到期生成音频已保留。" : message);
    recoveredError.code = error.code;
    throw recoveredError;
  }
  if (getCurrentProjectId() !== targetProjectId || state.voiceClone.profileId !== targetProfileId || state.voiceClone.requestGeneration !== requestGeneration) {
    throw new Error("项目或音色已切换，生成结果保留在原项目；当前页面不会载入它。");
  }
  const refreshed = await refreshVoiceCloneRecords({ projectId: targetProjectId, profileId: targetProfileId, requestGeneration });
  if (!refreshed) {
    throw new Error("项目或音色已切换，生成结果保留在原项目；当前页面不会载入它。");
  }
  return record;
}


async function releaseVoiceCloneAfterDownload(record, projectId) {
  await requestV2(buildProjectVoiceClonePath(projectId, `/synthesis-records/${encodeURIComponent(record.id)}/download-complete`), { method: "POST" });
  if (getCurrentProjectId() !== projectId) return;
  if (state.serviceStatus.voiceClone?.discard_outputs_after_download) {
    await refreshVoiceCloneRecords({ projectId, requestGeneration: null });
    if (getCurrentProjectId() !== projectId) return;
  }
  const targetProfileId = record.voice_clone_profile_id;
  const sourceId = state.voiceClone.profiles.find(profile => profile.id === targetProfileId)?.source_clone_sample_id;
  const releasedIds = new Set(state.voiceClone.profiles.filter(profile => profile.id === targetProfileId || (sourceId && profile.source_clone_sample_id === sourceId && profile.project_id === projectId)).map(profile => profile.id));
  if (!targetProfileId || !releasedIds.has(state.voiceClone.profileId)) return;
  state.voiceClone.profileId = "";
  state.voiceClone.sampleId = "";
  state.voiceClone.pendingModelId = "";
  state.voiceClone.profiles = state.voiceClone.profiles.filter(profile => !releasedIds.has(profile.id));
  state.voiceClone.samples = state.voiceClone.samples.filter(sample => sample.id !== sourceId);
  state.voiceClone.addFlow = { open: false, stage: "pick", error: "" };
  state.voiceClone.sharedPlayer = { sampleId: "", label: "", url: "" };
  clearVoiceCloneSampleDraft();
  state.voiceClone.records = state.voiceClone.records.map(item => releasedIds.has(item.voice_clone_profile_id) ? { ...item, voice_clone_profile_id: null } : item);
}


const elements = {
  app: document.querySelector("#app"),
  projectRail: document.querySelector(".project-rail"),
  railScrim: document.querySelector(".rail-scrim"),
  topbarProjectName: document.querySelector("#topbar-project-name"),
  topbarTaskState: document.querySelector("#topbar-task-state"),
  topbarTaskCount: document.querySelector("#topbar-task-count"),
  stageEyebrow: document.querySelector("#stage-eyebrow"),
  stageTitle: document.querySelector("#stage-title"),
  stageDescription: document.querySelector("#stage-description"),
  userFacingStatus: document.querySelector("#user-facing-status"),
  taskTimeline: document.querySelector("#task-timeline"),
  simpleWorkspace: document.querySelector("#simple-workspace"),
  proWorkspace: document.querySelector("#pro-workspace"),
  syncProgressOverlay: document.querySelector("#sync-progress-overlay"),
  syncProgressTitle: document.querySelector("#sync-progress-title"),
  syncProgressMessage: document.querySelector("#sync-progress-message"),
  syncProgressAttempts: document.querySelector("#sync-progress-attempts"),
  downloadToast: document.querySelector("#download-toast"),
  clientUpdateNotice: document.querySelector("#client-update-notice"),
};


const TASK_STEPS = [
  { id: "upload", index: "01", title: "生成字幕", description: "上传素材并生成字幕" },
  { id: "review", index: "02", title: "校对说话人", description: "试听、改名、逐条修正" },
  { id: "lowerThird", index: "03", title: "匹配人名条", description: "上传同名图片并检查缺失项" },
  { id: "export", index: "04", title: "导出", description: "下载 XML 和 SRT" },
];

const APP_PAGES = [
  ...TASK_STEPS,
  { id: "transcript", index: "P1", title: "生成扒词", description: "Fun-ASR 多说话人划稿" },
  { id: "transcriptReview", index: "P2", title: "校对说话人", description: "试听、改名、导出扒词" },
  { id: "voiceprints", index: "V1", title: "声纹库", description: "用于识别说话人，不用于声音复刻" },
  { id: "lexicon", index: "K", title: "节目知识库", description: "项目术语与断句保护" },
  { id: "voiceCloning", index: "V2", title: "声音复刻", description: "创建音色与生成试听" },
  { id: "cloneSamples", index: "V3", title: "复刻样本库", description: "上传、试听、删除复刻样本" },
  { id: "voiceCloneRecords", index: "V4", title: "生成音频", description: "文本生成、试听与下载" },
  { id: "history", index: "H", title: "历史记录", description: "检索、载入并导出历史任务" },
  { id: "tools", index: "T", title: "工具与日志", description: "任务管理、XML 返修、状态日志" },
];

const CLONE_PAGE_IDS = new Set(["voiceCloning", "cloneSamples", "voiceCloneRecords"]);
const TRANSCRIPT_PAGE_IDS = new Set(["transcript", "transcriptReview"]);
const VOICEPRINT_FILTER_CURRENT = "__current__";
const VOICEPRINT_FILTER_ALL = "__all__";
const VOICEPRINT_FILTER_GLOBAL = "__global__";
const VOICEPRINT_FILTER_UNASSIGNED = "__unassigned__";
const VOICEPRINT_FILTER_PROJECT_PREFIX = "project:";
const VOICE_CLONE_INSTRUCTION_EXAMPLES = [
  "温柔自然，语速中等，像纪录片旁白",
  "开朗活泼，语速稍快，带微笑感",
  "沉稳严肃，语速偏慢，适合新闻播报",
  "情绪更激动，节奏更强，适合宣传片",
  "吐字清晰精准，字正腔圆，标准播音风格",
];

const NAV_GROUPS = [
  {
    id: "subtitle",
    step: "upload",
    icon: "text-caption",
    title: "生成字幕",
    description: "生成、校对、人名条与导出",
  },
  {
    id: "transcript",
    step: "transcript",
    icon: "file-text",
    title: "生成扒词",
    description: "上传、校对与导出文稿",
  },
  {
    id: "voice",
    step: "voiceCloning",
    icon: "microphone-2",
    title: "声音复刻",
    description: "选择音色、创建与生成音频",
  },
];

const RESOURCE_NAV_ITEMS = [
  { id: "voiceprints", icon: "microphone", label: "声纹库", description: "识别说话人" },
  { id: "lexicon", icon: "database", label: "节目知识库", description: "术语与专名" },
  { id: "history", icon: "history", label: "历史记录", description: "找回旧任务" },
  { id: "tools", icon: "tool", label: "工具与日志", description: "维护与返修" },
];

function getPageById(pageId) {
  return APP_PAGES.find((page) => page.id === pageId) || APP_PAGES[0];
}

function getActiveGroupId() {
  if (TRANSCRIPT_PAGE_IDS.has(state.activeStep)) return "transcript";
  if (CLONE_PAGE_IDS.has(state.activeStep)) return "voice";
  if (RESOURCE_NAV_ITEMS.some((item) => item.id === state.activeStep)) return "resources";
  return "subtitle";
}

function deriveActiveStep() {
  if (!state.jobDetail) return "upload";
  if (isTranscriptJob(state.jobDetail)) return "transcript";
  if (!isCompletedJob()) return "upload";
  if (!state.speakers.length) return "review";
  if (!state.jobDetail.lower_third_assets?.length) return "lowerThird";
  return "export";
}

function getProjectJobs(kind = getWorkflowKindForStep(state.activeStep)) {
  const projectJobs = getCurrentProjectId()
    ? state.jobs.filter((job) => isJobInCurrentProject(job))
    : [];
  if (kind === "all") return projectJobs;
  return projectJobs.filter((job) => normalizeJobKind(job) === kind);
}

function getVoiceprintProjectNames() {
  const names = new Set();
  state.projects.forEach((project) => {
    const name = String(project.program_name || "").trim();
    if (name) names.add(name);
  });
  state.people.forEach((person) => {
    const name = String(person.program_name || "").trim();
    if (name) names.add(name);
  });
  return Array.from(names).sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));
}

function resolveVoiceprintFilterProjectName(filter = state.voiceprintProjectFilter) {
  const value = String(filter || VOICEPRINT_FILTER_CURRENT);
  if (value === VOICEPRINT_FILTER_CURRENT) return String(state.currentProjectName || "").trim();
  if (value.startsWith(VOICEPRINT_FILTER_PROJECT_PREFIX)) {
    return value.slice(VOICEPRINT_FILTER_PROJECT_PREFIX.length).trim();
  }
  return "";
}

function resolveVoiceprintFilterProjectId(filter = state.voiceprintProjectFilter) {
  const value = String(filter || VOICEPRINT_FILTER_CURRENT);
  if (value === VOICEPRINT_FILTER_CURRENT) return getCurrentProjectId();
  const projectName = resolveVoiceprintFilterProjectName(value);
  if (!projectName) return "";
  return String(
    state.projects.find((project) => String(project.program_name || "").trim() === projectName)?.project_id || "",
  );
}

function resolveVoiceprintCreateProjectName() {
  return resolveVoiceprintFilterProjectName() || String(state.currentProjectName || "").trim();
}

function resolveVoiceprintCreateProjectId() {
  return resolveVoiceprintFilterProjectId() || getCurrentProjectId();
}

function getFilteredVoiceprintPeople() {
  const filter = String(state.voiceprintProjectFilter || VOICEPRINT_FILTER_CURRENT);
  const projectName = resolveVoiceprintFilterProjectName(filter);
  const projectId = resolveVoiceprintFilterProjectId(filter);
  const query = String(state.voiceprintSearch || "").trim().toLowerCase();
  let people = state.people || [];

  if (filter === VOICEPRINT_FILTER_GLOBAL) {
    people = people.filter(isGlobalVoiceprintPerson);
  } else if (filter === VOICEPRINT_FILTER_UNASSIGNED) {
    people = people.filter((person) => (
      !String(person.project_id || "").trim()
      && !isGlobalVoiceprintPerson(person)
    ));
  } else if (projectId || projectName) {
    people = people.filter((person) => {
      if (isGlobalVoiceprintPerson(person)) return true;
      const personProjectId = String(person.project_id || "").trim();
      if (personProjectId) return personProjectId === projectId;
      return String(person.program_name || "").trim() === projectName;
    });
  } else if (filter !== VOICEPRINT_FILTER_ALL && filter === VOICEPRINT_FILTER_CURRENT && !projectName) {
    people = state.people || [];
  }

  if (query) {
    people = people.filter((person) => {
      const haystack = [
        person.name,
        person.program_name,
        ...(person.aliases || []),
        ...(person.samples || []).map((sample) => sample.original_filename),
      ].join(" ").toLowerCase();
      return haystack.includes(query);
    });
  }

  return [...people].sort((first, second) => {
    const firstProject = String(first.program_name || "");
    const secondProject = String(second.program_name || "");
    if (firstProject !== secondProject) return firstProject.localeCompare(secondProject, "zh-Hans-CN");
    return String(first.name || "").localeCompare(String(second.name || ""), "zh-Hans-CN");
  });
}

function isGlobalVoiceprintPerson(person) {
  return Boolean(
    person
    && !String(person.project_id || "").trim()
    && !String(person.program_name || "").trim()
  );
}

function groupVoiceprintPeopleByProject(people) {
  const groups = new Map();
  people.forEach((person) => {
    const projectName = isGlobalVoiceprintPerson(person)
      ? "通用声纹"
      : String(person.program_name || "").trim() || "未绑定项目";
    if (!groups.has(projectName)) groups.set(projectName, []);
    groups.get(projectName).push(person);
  });
  const currentProject = String(state.currentProjectName || "").trim();
  return Array.from(groups, ([projectName, records]) => ({ projectName, records })).sort((first, second) => {
    if (currentProject && first.projectName === currentProject) return -1;
    if (currentProject && second.projectName === currentProject) return 1;
    if (first.projectName === "通用声纹") return -1;
    if (second.projectName === "通用声纹") return 1;
    if (first.projectName === "未绑定项目") return 1;
    if (second.projectName === "未绑定项目") return -1;
    return first.projectName.localeCompare(second.projectName, "zh-Hans-CN");
  });
}

function renderVoiceprintFilterControls(visibleCount) {
  const projectNames = getVoiceprintProjectNames();
  const currentLabel = state.currentProjectName ? `当前项目：${state.currentProjectName}` : "当前项目：未选择";
  const filterValue = state.voiceprintProjectFilter || VOICEPRINT_FILTER_CURRENT;
  return `
    <div class="library-filter-bar">
      <label class="field-stack">
        <span>项目范围</span>
        <select id="voiceprint-project-filter-select">
          <option value="${VOICEPRINT_FILTER_CURRENT}" ${filterValue === VOICEPRINT_FILTER_CURRENT ? "selected" : ""}>${escapeHtml(currentLabel)}</option>
          <option value="${VOICEPRINT_FILTER_GLOBAL}" ${filterValue === VOICEPRINT_FILTER_GLOBAL ? "selected" : ""}>通用声纹</option>
          <option value="${VOICEPRINT_FILTER_ALL}" ${filterValue === VOICEPRINT_FILTER_ALL ? "selected" : ""}>全部项目</option>
          ${projectNames.map((name) => {
            const value = `${VOICEPRINT_FILTER_PROJECT_PREFIX}${name}`;
            return `<option value="${escapeHtml(value)}" ${filterValue === value ? "selected" : ""}>${escapeHtml(name)}</option>`;
          }).join("")}
          <option value="${VOICEPRINT_FILTER_UNASSIGNED}" ${filterValue === VOICEPRINT_FILTER_UNASSIGNED ? "selected" : ""}>未绑定项目</option>
        </select>
      </label>
      <label class="field-stack">
        <span>搜索声纹</span>
        <input id="voiceprint-search-input" type="search" value="${escapeHtml(state.voiceprintSearch || "")}" placeholder="搜索人物、项目、样本文件" />
      </label>
      <div class="library-filter-summary">
        <strong>${visibleCount}</strong>
        <span>/ ${state.people.length} 人物</span>
      </div>
    </div>
  `;
}

function statusLabel(status) {
  const normalized = String(status || "").toLowerCase();
  const labels = {
    pending: "等待转写",
    running: "正在转写",
    processing: "正在处理",
    finalizing: "正在整理",
    subtitle_enhancing: "字幕优化",
    completed: "已完成",
    completed_with_warnings: "已完成-有回退",
    failed: "失败",
    aborted: "已中止",
  };
  return labels[normalized] || status || "未创建";
}

function statusTone(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "completed") return "success";
  if (normalized === "completed_with_warnings") return "warning";
  if (normalized === "failed" || normalized === "aborted") return "danger";
  if (["pending", "running", "processing", "finalizing", "subtitle_enhancing"].includes(normalized)) return "active";
  return "neutral";
}

function formatVoiceSampleMeta(sample) {
  if (!sample) return "还没有选择样本";
  const duration = sample.duration_ms ? formatDuration(Number(sample.duration_ms) / 1000) : "-";
  const rate = sample.clone_sample_rate_hz || sample.sample_rate_hz || "-";
  const channels = sample.clone_channel_count || sample.channel_count || "-";
  return `${duration} · ${rate} Hz · ${channels} 声道`;
}

function isAutoVoiceprintCandidate(sample) {
  const sourceType = String(sample?.source_type || "");
  const reviewStatus = String(sample?.review_status || "");
  return sourceType === "auto_candidate" || reviewStatus === "candidate";
}

function formatVoiceprintCandidateSource(sample) {
  if (!sample?.source_job_id && sample?.source_start_ms === undefined) return "";
  const startMs = Number(sample?.source_start_ms);
  const endMs = Number(sample?.source_end_ms);
  const range = Number.isFinite(startMs) && Number.isFinite(endMs)
    ? `${formatMillis(startMs)}-${formatMillis(endMs)}`
    : "";
  const speaker = sample?.source_speaker_key ? `原始说话人 ${sample.source_speaker_key}` : "";
  return [range, speaker].filter(Boolean).join(" · ");
}

function renderVoiceAssetExpiry(asset, action = "请重新上传") {
  const expiry = getVoiceAssetExpiresAt(asset);
  if (!expiry) return "";
  return `<small class="voice-asset-expiry ${isVoiceAssetExpired(asset) ? "is-expired" : ""}">${isVoiceAssetExpired(asset) ? `已到期 · ${escapeHtml(action)} · ` : ""}${escapeHtml(formatDate(expiry))} 到期</small>`;
}

function voiceAssetAudioAttributes(asset) {
  return `data-voice-expires-at="${escapeHtml(getVoiceAssetExpiresAt(asset)?.toISOString() || "")}"`;
}

function renderVoiceprintSampleRow(sample, options = {}) {
  const expired = isVoiceAssetExpired(sample);
  const isCandidate = Boolean(options.candidate);
  const sourceText = isCandidate ? formatVoiceprintCandidateSource(sample) : "";
  const sourceType = String(sample?.source_type || "");
  const trustLabel = isCandidate
    ? "等待系统交叉验证"
    : sourceType === "auto_confirmed" ? "系统已确认" : "人工确认";
  return `
    <div class="sample-row ${isCandidate ? "is-candidate" : ""}">
      <div class="sample-main">
        <div>
          <strong>${escapeHtml(sample.original_filename)}</strong>
          <span>${escapeHtml(trustLabel)} · ${escapeHtml(sample.embedding_status)} · ${escapeHtml(formatVoiceSampleMeta(sample))}</span>
          ${sourceText ? `<small>${escapeHtml(sourceText)}</small>` : ""}
          ${renderVoiceAssetExpiry(sample)}
        </div>
        ${expired ? "" : `<audio controls preload="none" ${voiceAssetAudioAttributes(sample)} data-audio-src="/voiceprints/samples/${escapeHtml(encodeURIComponent(sample.id))}/audio"></audio>`}
      </div>
      <div class="row-actions">
        ${isCandidate ? `<button class="secondary-button small" data-voiceprint-sample-confirm="${escapeHtml(sample.id)}" type="button" ${expired ? "disabled" : ""}>立即确认</button>` : ""}
        <button class="danger-button small" data-voiceprint-sample-delete="${escapeHtml(sample.id)}" data-voiceprint-sample-name="${escapeHtml(sample.original_filename)}" type="button">删除</button>
      </div>
    </div>
  `;
}

function renderVoiceprintSampleSection(title, description, samples, options = {}) {
  return `
    <section class="sample-subsection">
      <div class="sample-subsection-title">
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(description)}</span>
      </div>
      <div class="sample-list">
        ${samples.length ? samples.map((sample) => renderVoiceprintSampleRow(sample, options)).join("") : `<p class="empty-line">暂无${escapeHtml(title)}。</p>`}
      </div>
    </section>
  `;
}

function renderAll() {
  if (state.activeStep === "cloneSamples" || state.activeStep === "voiceCloneRecords") {
    state.voiceClone.view = state.activeStep === "cloneSamples" ? "library" : "generate";
    state.activeStep = "voiceCloning";
  }
  if (!APP_PAGES.some((step) => step.id === state.activeStep)) state.activeStep = deriveActiveStep();
  document.body.dataset.mode = "simple";
  document.body.dataset.activeStep = state.activeStep;
  document.body.dataset.hideTimeline = TASK_STEPS.some((step) => step.id === state.activeStep) ? "false" : "true";
  elements.simpleWorkspace.hidden = false;
  elements.proWorkspace.hidden = true;
  renderAppTopbar();
  renderProjectRail();
  renderStageHeader();
  renderTimeline();
  if (state.currentProjectId) renderSimpleWorkspace();
  else renderProjectGate();
  elements.proWorkspace.innerHTML = "";
  renderSyncOverlay();
  renderToast();
  renderClientUpdateNotice();
  hydrateAudioPlayers();
  globalThis.SpeakerLocalRecognition?.renderConnection();
}

function setProjectRailOpen(open) {
  const isOpen = Boolean(open);
  document.body.dataset.railOpen = isOpen ? "true" : "false";
  if (!isOpen) {
    const projectSearchResults = elements.projectRail?.querySelector("#project-search-results");
    const projectSearchInput = elements.projectRail?.querySelector("#current-project-select");
    if (projectSearchResults) projectSearchResults.hidden = true;
    projectSearchInput?.setAttribute("aria-expanded", "false");
  }
  elements.projectRail?.setAttribute("aria-hidden", isOpen ? "false" : "true");
  if (elements.projectRail) elements.projectRail.inert = !isOpen;
  if (elements.railScrim) elements.railScrim.hidden = !isOpen;
  document.querySelectorAll("[data-toggle-rail]").forEach((button) => {
    button.setAttribute("aria-expanded", isOpen ? "true" : "false");
  });
}

function renderAppTopbar() {
  const processingStates = new Set(["pending", "running", "processing", "finalizing", "subtitle_enhancing"]);
  const processingJobs = (state.jobs || []).filter((job) => processingStates.has(String(job.status || "").toLowerCase()));
  const buildIdentity = document.querySelector("#topbar-build-identity");
  if (buildIdentity) {
    buildIdentity.title = `构建：${BUILD_MARKER}；通道：${UPDATE_CHANNEL}`;
    buildIdentity.setAttribute("aria-label", `版本 v${APP_VERSION}；构建 ${BUILD_MARKER}；通道 ${UPDATE_CHANNEL}`);
    buildIdentity.querySelector("#topbar-version-badge").textContent = `v${APP_VERSION}`;
    buildIdentity.querySelector("#topbar-build-channel").textContent = UPDATE_CHANNEL;
  }
  if (elements.topbarProjectName) {
    elements.topbarProjectName.textContent = state.currentProjectName || "选择项目";
  }
  if (elements.topbarTaskCount) {
    elements.topbarTaskCount.textContent = String(processingJobs.length);
  }
  if (elements.topbarTaskState) {
    elements.topbarTaskState.dataset.active = processingJobs.length ? "true" : "false";
  }
  setProjectRailOpen(document.body.dataset.railOpen === "true");
}

function renderProjectRail() {
  const currentProject = state.projects.find((project) => project.project_id === state.currentProjectId) || null;
  const projectLoad = state.projectLoad || { status: "ready", error: "" };
  const projectCountLabel = projectLoad.status === "loading" && !state.projects.length
    ? "载入中"
    : projectLoad.status === "error" && !state.projects.length
      ? "加载失败"
      : `${state.projects.length} 个项目`;
  const cleanupSummary = currentProject
    ? `${Number(currentProject.job_count || 0)} 个任务 · ${Number(currentProject.person_count || 0)} 人声纹 · ${Number(currentProject.asset_count || 0)} 项关联数据`
    : "请先进入项目";
  const detail = state.jobDetail;
  const activeGroupId = getActiveGroupId();
  const currentTaskLabel = detail
    ? `${isTranscriptJob(detail) ? "扒词任务" : "字幕任务"} · ${String(detail.id || "").slice(0, 8)}`
    : "还没有载入任务";
  elements.projectRail.innerHTML = `
    <header class="rail-workspace-head">
      <div>
        <span>Speaker 2.0</span>
        <strong>项目与功能</strong>
      </div>
      <button class="rail-close-button" data-close-rail type="button" aria-label="关闭项目与功能菜单">
        <img src="./ui-icons/x.svg" alt="" aria-hidden="true" />
      </button>
    </header>
    <section class="rail-project-workspace" aria-labelledby="rail-project-heading">
      <div class="rail-section-heading">
        <div>
          <span id="rail-project-heading">当前项目</span>
          <strong>${escapeHtml(currentProject?.program_name || "尚未选择项目")}</strong>
        </div>
        <span class="rail-count">${escapeHtml(projectCountLabel)}</span>
      </div>
      <div class="project-search-shell">
        <label class="project-search-field" for="current-project-select">
          <img src="./ui-icons/search.svg" alt="" aria-hidden="true" />
          <input
            id="current-project-select"
            type="search"
            autocomplete="off"
            placeholder="搜索并切换项目"
            aria-label="搜索并切换项目"
            role="combobox"
            aria-autocomplete="list"
            aria-controls="project-search-results"
            aria-expanded="false"
          />
        </label>
        <div id="project-search-results" class="project-search-results" role="listbox" aria-label="项目搜索结果" hidden></div>
      </div>
      <details class="rail-project-management">
        <summary>
          <span>管理项目</span>
          <img src="./ui-icons/chevron-down.svg" alt="" aria-hidden="true" />
        </summary>
        <div class="rail-project-management-body">
          <form id="create-project-form" class="inline-create-form">
            <input id="project-name-input" type="text" autocomplete="off" placeholder="输入新项目名称" />
            <button class="icon-text-button" type="submit">
              <img src="./ui-icons/plus.svg" alt="" aria-hidden="true" />
              <span>新建</span>
            </button>
          </form>
          <div class="rail-danger-zone">
            <span>强制清理项目</span>
            <small>${escapeHtml(cleanupSummary)}；同时删除项目文件，无法恢复。</small>
            <button
              id="purge-project-button"
              class="rail-purge-button"
              type="button"
              ${currentProject ? "" : "disabled"}
              title="${escapeHtml(currentProject ? `永久清理项目：${currentProject.program_name}` : "请先进入项目")}"
            >
              <img src="./ui-icons/archive.svg" alt="" aria-hidden="true" />
              <span>永久清理当前项目</span>
            </button>
          </div>
        </div>
      </details>
      <div class="rail-current-task">
        <span>当前任务</span>
        <strong title="${escapeHtml(detail?.id || "")}">${escapeHtml(currentTaskLabel)}</strong>
        ${detail ? `
          <span class="status-pill ${statusTone(detail.status)}">${escapeHtml(statusLabel(detail.status))}</span>
          <small>${escapeHtml(formatDuration(detail.source_duration_seconds))}</small>
        ` : `<small>创建任务后会在这里显示状态</small>`}
      </div>
    </section>
    <nav class="workflow-nav" aria-label="主要功能">
      <p class="rail-nav-label">主要功能</p>
      <div class="workflow-primary-list">
        ${NAV_GROUPS.map((group) => `
          <button class="workflow-primary-entry ${activeGroupId === group.id ? "is-active" : ""}" data-step="${escapeHtml(group.step)}" type="button">
            <span class="workflow-entry-icon">
              <img src="./ui-icons/${escapeHtml(group.icon)}.svg" alt="" aria-hidden="true" />
            </span>
            <span class="workflow-entry-copy">
              <strong>${escapeHtml(group.title)}</strong>
              <small>${escapeHtml(group.description)}</small>
            </span>
            <img src="./ui-icons/chevron-down.svg" alt="" aria-hidden="true" />
          </button>
        `).join("")}
      </div>
      <p class="rail-nav-label rail-resource-label">资源与管理</p>
      <div class="workflow-utility-list">
        ${RESOURCE_NAV_ITEMS.map((item) => `
          <button class="${state.activeStep === item.id ? "is-active" : ""}" data-step="${escapeHtml(item.id)}" type="button">
            <img src="./ui-icons/${escapeHtml(item.icon)}.svg" alt="" aria-hidden="true" />
            <span>
              <strong>${escapeHtml(item.label)}</strong>
              <small>${escapeHtml(item.description)}</small>
            </span>
          </button>
        `).join("")}
      </div>
    </nav>
    <div class="rail-actions">
      <button id="refresh-data-button" class="rail-refresh-button" type="button">
        <img src="./ui-icons/refresh.svg" alt="" aria-hidden="true" />
        <span>刷新项目数据</span>
      </button>
    </div>
  `;
}

function renderProjectGate() {
  const projectLoad = state.projectLoad || { status: "ready", error: "" };
  const projects = [...state.projects].sort((first, second) => {
    if (first.project_id === state.lastProjectId) return -1;
    if (second.project_id === state.lastProjectId) return 1;
    return String(first.program_name || "").localeCompare(String(second.program_name || ""), "zh-CN");
  });
  const projectContent = projectLoad.status === "loading" && !projects.length
    ? `
      <div class="project-gate-feedback is-loading" role="status">
        <strong>正在加载项目</strong>
        <span>通常几秒内完成，你可以继续停留在当前页面。</span>
      </div>
    `
    : projectLoad.status === "error" && !projects.length
      ? `
        <div class="project-gate-feedback is-error" role="alert">
          <strong>项目没有加载出来</strong>
          <span>${escapeHtml(projectLoad.error || "项目服务暂时没有响应")}</span>
          <button id="retry-projects-button" class="primary-button" type="button">重试加载项目</button>
        </div>
      `
      : projects.length
        ? projects.map((project) => `
          <button class="project-entry-card" data-project-enter="${escapeHtml(project.project_id)}" type="button">
            <strong>${escapeHtml(project.program_name)}</strong>
            <span>${Number(project.job_count || 0)} 个任务${project.owner_name ? ` · ${escapeHtml(project.owner_name)}` : ""}</span>
            <small>${project.local_only ? "仅本机项目" : project.project_id === state.lastProjectId ? "最近使用" : "进入项目"}</small>
          </button>
        `).join("")
        : `<p class="empty-line">还没有项目，请从顶部项目菜单新建第一个项目。</p>`;
  elements.simpleWorkspace.innerHTML = `
    <section class="project-gate">
      <div class="project-gate-head">
        <p class="eyebrow">Speaker 2.0</p>
        <h2>先进入项目，再开始工作</h2>
        <p>任务、词库、声纹、素材和费用都会固定归入所选项目。打开旧任务不会替你切换项目。</p>
      </div>
      <div class="project-gate-grid">
        ${projectContent}
      </div>
    </section>
  `;
}

function renderStageHeader() {
  const activeStep = getPageById(state.activeStep);
  const detail = state.jobDetail;
  elements.stageEyebrow.textContent = TASK_STEPS.some((step) => step.id === state.activeStep)
    ? "生成字幕"
    : CLONE_PAGE_IDS.has(state.activeStep)
      ? "声音复刻"
      : TRANSCRIPT_PAGE_IDS.has(state.activeStep)
        ? "生成扒词"
        : "资源与管理";
  elements.stageTitle.textContent = activeStep.title;
  const descriptions = {
    upload: "上传音频，自动识别语音并生成可继续校对的字幕。",
    review: "集中校对字幕文本与说话人，不被次要信息打断。",
    lowerThird: "为已确认的说话人匹配节目人名条素材。",
    export: "检查质量状态后，导出字幕文件或 Premiere 工程。",
    transcript: "在一个页面完成音频上传、说话人校对与文稿导出。",
    transcriptReview: "在一个页面完成音频上传、说话人校对与文稿导出。",
    voiceCloning: "搜索并选择已有音色，或在同一页面创建音色并生成音频。",
    cloneSamples: "搜索并选择已有音色，或在同一页面创建音色并生成音频。",
    voiceCloneRecords: "搜索并选择已有音色，或在同一页面创建音色并生成音频。",
  };
  if (elements.stageDescription) {
    elements.stageDescription.textContent = descriptions[state.activeStep] || activeStep.description || "";
  }
  const cueCount = state.cues.length || detail?.normalized_segments?.length || 0;
  const assetCount = detail?.lower_third_assets?.length || 0;
  if (state.activeStep === "voiceprints") {
    elements.userFacingStatus.innerHTML = `
      <span>${state.people.length} 位人物</span>
      <span>用于识别说话人</span>
    `;
    return;
  }
  if (state.activeStep === "lexicon") {
    elements.userFacingStatus.innerHTML = `
      <span>${state.lexicon.terms.length} 条词条</span>
      <span>${state.lexicon.candidates.length} 条候选</span>
      <span>${state.lexicon.bundlePreview?.term_set_hash ? "快照可用" : "待生成快照"}</span>
    `;
    return;
  }
  if (CLONE_PAGE_IDS.has(state.activeStep)) {
    const provider = state.serviceStatus.voiceClone;
    const library = state.voiceClone.library || {};
    elements.userFacingStatus.innerHTML = `
      <span class="status-pill ${provider?.available ? "success" : "neutral"}">${escapeHtml(provider?.available ? "服务可用" : "未连接")}</span>
      <span>${Number(library.availableGroupCount || 0)} 组可用音色</span>
      <span>${Number(library.exceptionGroupCount || 0)} 组异常音色</span>
      <span>${Number(library.reusableSampleCount || 0)} 条可复用样本</span>
    `;
    return;
  }
  if (TRANSCRIPT_PAGE_IDS.has(state.activeStep)) {
    const segmentCount = detail?.normalized_segments?.length || 0;
    const transcriptReady = isCompletedJob(detail);
    elements.userFacingStatus.innerHTML = `
      <span class="status-pill ${statusTone(detail?.status)}">${escapeHtml(statusLabel(detail?.status))}</span>
      <span>${transcriptReady ? `${segmentCount} 段扒词` : "结果尚未生成"}</span>
      <span>${transcriptReady ? `${state.speakers.length} 位说话人` : "完成后在本页校对"}</span>
    `;
    return;
  }
  if (state.activeStep === "tools" || state.activeStep === "history") {
    elements.userFacingStatus.innerHTML = `
      <span class="status-pill ${statusTone(detail?.status)}">${escapeHtml(statusLabel(detail?.status))}</span>
      <span>${getProjectJobs("all").length} 个任务</span>
      <span>${state.activeStep === "history" ? "历史记录" : "工具与日志"}</span>
    `;
    return;
  }
  elements.userFacingStatus.innerHTML = `
    <span class="status-pill ${statusTone(detail?.status)}">${escapeHtml(statusLabel(detail?.status))}</span>
    <span>${cueCount ? `${cueCount} 条字幕` : "等待字幕"}</span>
    <span>${assetCount ? `${assetCount} 张人名条` : "无人名条"}</span>
  `;
}

function renderTimeline() {
  const isSubtitleStep = TASK_STEPS.some((step) => step.id === state.activeStep);
  if (!isSubtitleStep || !state.currentProjectId) {
    elements.taskTimeline.innerHTML = "";
    elements.taskTimeline.hidden = true;
    return;
  }
  const recommendedStep = deriveActiveStep();
  elements.taskTimeline.hidden = false;
  elements.taskTimeline.innerHTML = TASK_STEPS.map((step) => `
    <button
      class="timeline-step ${state.activeStep === step.id ? "is-active" : ""} ${recommendedStep === step.id ? "is-recommended" : ""}"
      data-step="${escapeHtml(step.id)}"
      type="button"
      ${state.activeStep === step.id ? 'aria-current="step"' : ""}
    >
      <span>${escapeHtml(step.index)}</span>
      <strong>${escapeHtml(step.title)}</strong>
      <small>${escapeHtml(step.description)}</small>
    </button>
  `).join("");
}

function renderSimpleWorkspace() {
  if (state.activeStep === "upload") renderUploadStep();
  else if (state.activeStep === "transcript") renderTranscriptStep();
  else if (state.activeStep === "transcriptReview") renderTranscriptReviewStep();
  else if (state.activeStep === "review") renderReviewStep();
  else if (state.activeStep === "lowerThird") renderLowerThirdStep();
  else if (state.activeStep === "export") renderExportStep();
  else if (state.activeStep === "voiceprints") renderVoiceprintPage();
  else if (state.activeStep === "lexicon") renderLexiconPage();
  else if (state.activeStep === "history") renderHistoryPage();
  else if (CLONE_PAGE_IDS.has(state.activeStep)) renderVoiceCloningPage();
  else renderToolsAndLogsPage();
}

function renderUploadStep() {
  const localMode = globalThis.SpeakerLocalRecognition?.wantsLocal();
  const localOffline = localMode && (state.localRecognition?.offline || state.projects.find(project => (project.project_id || project.id) === state.currentProjectId)?.local_only);
  const pickedName = state.pickedMediaFile?.name || "";
  const taskScriptFiles = state.pickedTaskScriptFiles || [];
  const savedScripts = state.projectScripts || [];
  const jobIsProcessing = Boolean(state.currentJobId && !isCompletedJob());
  const taskScriptSummary = taskScriptFiles.length
    ? `已选择 ${taskScriptFiles.length} 份：${taskScriptFiles.map((file) => file.name).join("、")}`
    : (
      state.lyricsCorrectionMode
        ? "请选择歌词文件；带时间码的 SRT 最准确，TXT、Word 可自动对齐"
        : "可同时选择多份；上传后自动保存到当前项目知识库"
    );
  const savedScriptSummary = savedScripts.length
    ? `自动使用知识库中的 ${savedScripts.length} 份台本与歌词参考`
    : "知识库暂无已保存台本，首次上传后下次自动使用";
  elements.simpleWorkspace.innerHTML = `
    <section class="upload-workflow upload-workflow-v2">
      ${jobIsProcessing ? renderV2Pipeline() : ""}
      <form id="video-job-form" class="upload-dropzone upload-setup-form">
        ${globalThis.SpeakerLocalRecognition?.renderControls() || ""}
        <input id="video-file-input" class="visually-hidden" type="file" accept="audio/*" />
        <input id="task-script-file-input" class="visually-hidden" type="file" multiple accept=".txt,.md,.srt,.docx,.pdf,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" ${localOffline ? "disabled" : ""} />
        <div class="task-setup-grid">
          <section class="task-source-panel is-required">
            <div class="task-source-heading">
              <strong>音频</strong>
              <span>必填</span>
            </div>
            <button id="media-picker-trigger" class="media-pick-button primary-dropzone" data-file-drop-target="video-file-input" type="button">
              <img src="./ui-icons/upload.svg" alt="" aria-hidden="true" />
              <strong>${pickedName ? "已选择音频" : "点击选择或拖拽文件到此处"}</strong>
              <span>${escapeHtml(pickedName || "支持 wav、mp3、m4a、aac、aif、aiff；单文件最长 12 小时 / 2GB")}</span>
              <em>${pickedName ? "重新选择" : "选择文件"}</em>
            </button>
          </section>
          <section class="task-source-panel is-optional">
            <div class="task-source-heading">
              <strong>台本 / 歌词</strong>
              <span>可选</span>
            </div>
            <button id="task-script-picker-trigger" class="media-pick-button task-script-pick-button" data-file-drop-target="task-script-file-input" type="button" ${localOffline ? "disabled" : ""}>
              <img src="./ui-icons/file-text.svg" alt="" aria-hidden="true" />
              <strong>${state.lyricsCorrectionMode ? "上传歌词参考" : "上传台本参考"}</strong>
              <span>${escapeHtml(localOffline ? "离线不应用台本；恢复联网后可在服务器校对版补充" : taskScriptSummary)}</span>
              <em>${taskScriptFiles.length ? "继续添加" : "选择文件"}</em>
            </button>
            <label class="reference-mode-row lyrics-correction-check">
              <input id="lyrics-correction-mode-input" type="checkbox" ${state.lyricsCorrectionMode ? "checked" : ""} ${localOffline ? "disabled" : ""} />
              <span><strong>歌词校对模式</strong>　只替换能可靠对齐的唱词</span>
            </label>
          </section>
        </div>
        <div class="recognition-defaults" aria-label="当前默认设置">
          <img src="./ui-icons/adjustments.svg" alt="" aria-hidden="true" />
          <strong>默认设置</strong>
          <span>中文节目字幕</span>
          <span>${localMode ? "人物由用户指定" : "说话人自动识别"}</span>
          <span>25 fps</span>
          <span>智能断句</span>
        </div>
        <details class="recognition-settings">
          <summary>
            <span>
              <img src="./ui-icons/adjustments.svg" alt="" aria-hidden="true" />
              <strong>调整识别设置</strong>
              <small>语种、说话人数、帧率、热词和字幕长度</small>
            </span>
            <img src="./ui-icons/chevron-down.svg" alt="" aria-hidden="true" />
          </summary>
          <div class="recognition-settings-body">
            <label class="project-script-reuse-check inline-check">
              <input id="use-project-scripts-input" type="checkbox" ${state.useProjectScripts ? "checked" : ""} ${savedScripts.length ? "" : "disabled"} />
              <span>${escapeHtml(savedScriptSummary)}</span>
            </label>
            <div class="upload-settings">
              <label>
                <span>语种提示</span>
                <select id="language-hints-input" ${localMode ? "disabled" : ""}>
                  <option value="zh" selected>中文节目字幕</option>
                  <option value="en">English captions / lyrics</option>
                </select>
              </label>
              <label>
                <span>说话人数</span>
                <input id="speaker-count-input" type="number" min="1" placeholder="${localMode ? "本机不自动分人" : "自动识别"}" ${localMode ? "disabled" : ""} />
              </label>
              <label>
                <span>导出帧率</span>
                <select id="export-frame-rate-select">
                  <option value="25">25 fps</option>
                  <option value="29.97">29.97 fps</option>
                  <option value="50">50 fps</option>
                </select>
              </label>
              <label>
                <span>字幕长度</span>
                <select id="subtitle-length-mode-select">
                  <option value="smart" selected>智能断句</option>
                  <option value="strict">严格字数</option>
                </select>
              </label>
              <label>
                <span>严格模式每行上限</span>
                <input id="subtitle-max-chars-input" type="number" min="6" max="30" value="14" />
              </label>
              <label>
                <span>热词 ID</span>
                <input id="vocabulary-id-input" type="text" placeholder="${localMode ? "本机识别不支持热词 ID" : "可选"}" ${localMode ? "disabled" : ""} />
              </label>
            </div>
            <p class="subtitle-length-hint">智能断句优先保证内容准确和语义完整；只有严格模式才尽量按字数上限切分。</p>
          </div>
        </details>
        <div class="task-actions">
          <button id="create-job-button" class="primary-button" type="submit" ${state.localRecognition?.busy || (localMode && !state.localRecognition?.capability?.available) ? "disabled" : ""}>${localMode ? "开始本机识别" : "开始生成"}</button>
          ${jobIsProcessing ? `<button id="sync-job-button" class="secondary-button" type="button">同步当前任务</button>` : ""}
          <button class="secondary-button" data-step="history" type="button">查看后台任务</button>
          <span>${localMode ? "识别期间请保持客户端开启" : `任务会在「${escapeHtml(state.currentProjectName)}」中后台处理`}</span>
        </div>
      </form>
    </section>
  `;
}

function renderV2Pipeline() {
  const detail = state.jobDetail;
  if (
    !detail
    || normalizeJobKind(detail) !== "transcription"
    || String(detail.subtitle_strategy || "").toLowerCase() !== "evidence"
  ) return "";
  const status = String(detail.status || "").toLowerCase();
  const pipeline = state.syncProgress.pipeline || null;
  const pipelineStage = String(pipeline?.stage || "");
  const ready = ["completed", "completed_with_warnings"].includes(status)
    || ["ready", "ready_with_warnings"].includes(pipelineStage);
  const aiActive = ["evidence_matching", "ai_planning", "cue_validating"].includes(pipelineStage)
    || ["finalizing", "subtitle_enhancing"].includes(status);
  const activeIndex = ready ? 2 : aiActive ? 1 : 0;
  const asrTotal = Number(pipeline?.asr?.total_chunks || detail.provider_task_meta?.length || 0);
  const asrCompleted = Number(pipeline?.asr?.completed_chunks || (aiActive || ready ? asrTotal : 0));
  const metrics = detail.processing_metrics || {};
  const aiTotal = Number(pipeline?.ai?.total_windows ?? metrics.subtitle_v2_ai_total_windows ?? 0);
  const aiCompleted = Number(pipeline?.ai?.completed_windows ?? metrics.subtitle_v2_ai_completed_windows ?? 0);
  const aiFailed = Number(pipeline?.ai?.failed_windows ?? Math.max(aiTotal - aiCompleted, 0));
  const aiWarning = ready && Boolean(detail.subtitle_enhancement_error);
  const aiNotApplied = aiWarning && !detail.subtitle_enhanced;
  const stages = [
    {
      label: "Fun-ASR 转写",
      detail: asrTotal ? `${asrCompleted}/${asrTotal} 段完成` : "识别文字与说话人",
    },
    {
      label: "AI 校词与断句",
      detail: aiNotApplied
        ? "AI未生效，当前为本地降级字幕"
        : aiTotal
          ? `${aiCompleted}/${aiTotal} 次判断成功${aiFailed ? `，${aiFailed} 次已安全降级` : ""}`
          : aiWarning ? "AI部分生效，失败部分已安全降级" : "同一内容只判断一次",
    },
    {
      label: "校对说话人",
      detail: ready ? "字幕已就绪，可以开始校对" : "完成后自动进入校对",
    },
  ];
  const message = String(
    detail.subtitle_enhancement_error
    || detail.processing_message
    || state.syncProgress.message
    || (ready ? "字幕已生成" : "任务正在后台运行"),
  );
  return `
    <section class="v2-pipeline" aria-label="字幕生成进度" aria-live="polite">
      <div class="v2-pipeline-head">
        <div>
          <p class="eyebrow">V2 字幕流水线</p>
          <strong>${escapeHtml(message)}</strong>
        </div>
        <span>${ready ? "READY" : `STAGE ${activeIndex + 1}/3`}</span>
      </div>
      <ol class="v2-pipeline-track">
        ${stages.map((stage, index) => {
          const stageState = index === 1 && aiWarning
            ? "warning"
            : index < activeIndex || ready ? "completed" : index === activeIndex ? "active" : "pending";
          return `
            <li class="v2-pipeline-stage is-${stageState}" ${stageState === "active" ? 'aria-current="step"' : ""}>
              <span class="v2-stage-node" aria-hidden="true">${stageState === "completed" ? "✓" : stageState === "warning" ? "!" : index + 1}</span>
              <div>
                <strong>${escapeHtml(stage.label)}</strong>
                <small>${escapeHtml(stage.detail)}</small>
              </div>
            </li>
          `;
        }).join("")}
      </ol>
    </section>
  `;
}

function getTranscriptLinesFromJob(job = state.jobDetail) {
  const mappings = state.speakerMappings || job?.speaker_mappings || {};
  return (job?.normalized_segments || [])
    .map((segment, index) => {
      const text = String(segment?.text || "").trim();
      if (!text) return null;
      const speakerKey = String(segment?.speakerKey || "speaker_0");
      return {
        index: index + 1,
        startMs: Math.max(0, Number(segment?.startMs || 0)),
        endMs: Math.max(0, Number(segment?.endMs || 0)),
        speakerKey,
        speakerName: mappings[speakerKey] || speakerKey,
        text,
      };
    })
    .filter(Boolean);
}

function formatTranscriptClock(ms) {
  const totalSeconds = Math.floor(Math.max(0, Number(ms || 0)) / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function renderTranscriptPipeline(job) {
  const status = String(job?.status || "pending").toLowerCase();
  const ready = ["completed", "completed_with_warnings"].includes(status);
  const failed = ["failed", "aborted"].includes(status);
  const organizing = ["finalizing", "subtitle_enhancing"].includes(status);
  const syncing = Boolean(state.syncProgress.active);
  const activeIndex = ready ? 2 : organizing ? 1 : 0;
  const message = ready
    ? "扒词已生成，可以校对说话人并导出"
    : failed
      ? (status === "aborted" ? "任务已中止" : "任务未能完成，请到工具与日志查看原因")
      : organizing
        ? "转写已完成，正在整理时间轴与说话人"
        : status === "pending"
          ? "任务已提交，等待云端开始转写"
          : "正在转写音频并识别说话人";
  const stages = [
    {
      label: "Fun-ASR 转写",
      detail: ready || organizing ? "转写已完成" : status === "pending" ? "等待云端开始" : "云端正在转写",
    },
    {
      label: "整理扒词结果",
      detail: ready ? "时间轴与说话人已整理" : organizing ? "正在合并时间轴与说话人" : "转写完成后自动整理",
    },
    {
      label: "校对与导出",
      detail: ready ? "可校对说话人并导出文稿" : "完成后在本页继续",
    },
  ];
  return `
    <section class="v2-pipeline transcript-pipeline" aria-label="扒词生成进度" aria-live="polite">
      <div class="v2-pipeline-head">
        <div>
          <p class="eyebrow">扒词流水线</p>
          <strong>${escapeHtml(message)}</strong>
        </div>
        <div class="panel-title-actions">
          <span class="status-pill ${failed ? "danger" : ready ? "success" : "active"}">${ready ? "已完成" : failed ? "需要处理" : `阶段 ${activeIndex + 1}/3`}</span>
          ${failed ? "" : `<button id="sync-job-button" class="secondary-button small" type="button" ${syncing ? "disabled" : ""}>${syncing ? "自动更新中" : "检查最新状态"}</button>`}
        </div>
      </div>
      <ol class="v2-pipeline-track">
        ${stages.map((stage, index) => {
          const stageState = failed && index === activeIndex
            ? "warning"
            : index < activeIndex || ready ? "completed" : index === activeIndex ? "active" : "pending";
          return `
            <li class="v2-pipeline-stage is-${stageState}" ${stageState === "active" ? 'aria-current="step"' : ""}>
              <span class="v2-stage-node" aria-hidden="true">${stageState === "completed" ? "✓" : stageState === "warning" ? "!" : index + 1}</span>
              <div>
                <strong>${escapeHtml(stage.label)}</strong>
                <small>${escapeHtml(stage.detail)}</small>
              </div>
            </li>
          `;
        }).join("")}
      </ol>
    </section>
  `;
}

function renderTranscriptStep() {
  const transcriptJob = isTranscriptJob(state.jobDetail) ? state.jobDetail : null;
  const ready = Boolean(transcriptJob && isCompletedJob());
  const pickedName = state.transcript.pickedFile?.name || "";
  const jobId = ready ? state.currentJobId : "";
  const outputStem = ready ? resolveCurrentJobSourceStem() : "";
  const lines = ready ? getTranscriptLinesFromJob() : [];
  const sourceUrl = ready && transcriptJob?.id
    ? buildApiUrl(`/transcriptions/jobs/${encodeURIComponent(transcriptJob.id)}/source-media`)
    : "";
  elements.simpleWorkspace.innerHTML = `
    <section class="task-panel transcript-workflow transcript-single-page ${ready ? "is-ready" : transcriptJob ? "is-processing" : "is-empty"}">
      ${transcriptJob && !ready ? renderTranscriptPipeline(transcriptJob) : ""}
      <details class="transcript-create-panel" ${ready ? "" : "open"}>
        <summary>
          <span>
            <strong>新建扒词任务</strong>
            <small>Fun-ASR 多说话人转写，保留时间戳、标点和语气词</small>
          </span>
          <img src="./ui-icons/chevron-down.svg" alt="" aria-hidden="true" />
        </summary>
        <form id="transcript-job-form" class="upload-dropzone transcript-create-form">
          <input id="transcript-file-input" class="visually-hidden" type="file" accept="audio/*" />
          <button id="transcript-picker-trigger" class="media-pick-button" data-file-drop-target="transcript-file-input" type="button">
            <strong>选择或拖拽音频</strong>
            <span>${escapeHtml(pickedName || "支持 wav、mp3、m4a、aac、aif、aiff；单文件最长 12 小时 / 2GB")}</span>
          </button>
          <div class="upload-settings">
            <label>
              <span>语种提示</span>
              <select id="transcript-language-hints-input">
                <option value="zh" selected>中文扒词</option>
                <option value="en">English transcript</option>
              </select>
            </label>
            <label>
              <span>说话人数</span>
              <input id="transcript-speaker-count-input" type="number" min="1" placeholder="可选" />
            </label>
            <label>
              <span>热词 ID</span>
              <input id="transcript-vocabulary-id-input" type="text" placeholder="可选" />
            </label>
          </div>
          <div class="task-actions">
            <button id="create-transcript-button" class="primary-button" type="submit">开始生成扒词</button>
          </div>
        </form>
      </details>
      ${transcriptJob ? ready ? `
        <div class="transcript-review-grid">
          <section class="transcript-review-left">
            <div class="review-media-strip transcript-media-strip">
              ${sourceUrl ? `<audio id="source-media-player" controls src="${escapeHtml(sourceUrl)}"></audio>` : "<p>当前任务缺少可试听音频。</p>"}
            </div>
            <div class="speaker-editor transcript-speakers">
              ${renderSpeakerEditor()}
            </div>
          </section>
          <section class="transcript-preview-panel">
            <div class="panel-title-row">
              <div>
                <strong>扒词结果</strong>
                <span>${lines.length} 段 · 试听、改名后直接导出</span>
              </div>
              <div class="panel-title-actions">
                <button class="secondary-button small" data-download-path="/transcriptions/jobs/${escapeHtml(encodeURIComponent(jobId))}/transcript/download?format=docx" data-download-name="${escapeHtml(outputStem)}-transcript.docx" type="button">导出 Word</button>
                <button class="secondary-button small" data-download-path="/transcriptions/jobs/${escapeHtml(encodeURIComponent(jobId))}/transcript/download?format=srt" data-download-name="${escapeHtml(outputStem)}-transcript.srt" type="button">导出 SRT</button>
                <button class="secondary-button small" data-download-path="/transcriptions/jobs/${escapeHtml(encodeURIComponent(jobId))}/transcript/download?format=txt" data-download-name="${escapeHtml(outputStem)}-transcript.txt" type="button">导出 TXT</button>
              </div>
            </div>
            <div class="transcript-line-list">
              ${lines.length ? lines.map((line) => `
                <div class="transcript-line">
                  <button class="cue-time-button" data-cue-start="${escapeHtml(line.startMs)}" type="button">${escapeHtml(formatTranscriptClock(line.startMs))}</button>
                  <strong>${escapeHtml(line.speakerName)}</strong>
                  <span>${escapeHtml(line.text)}</span>
                </div>
              `).join("") : `<p class="empty-line">没有可显示的扒词内容。</p>`}
            </div>
          </section>
        </div>
      ` : "" : `<p class="empty-line transcript-empty-line">上传音频后，校对和导出会继续显示在这个页面。</p>`}
    </section>
  `;
}

function renderTranscriptReviewStep() {
  renderTranscriptStep();
}

function renderTaskPanelSummary(stepLabel, description) {
  return `
    <div class="task-panel-head compact">
      <p class="eyebrow">${escapeHtml(stepLabel)}</p>
      <span>${escapeHtml(description)}</span>
    </div>
  `;
}

function getScriptReferenceLines() {
  return Array.isArray(state.jobDetail?.script_context?.reference_lines)
    ? state.jobDetail.script_context.reference_lines.filter((line) => String(line || "").trim())
    : [];
}

function normalizeScriptMatchText(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[，。、！？；：、“”‘’（）()\[\]【】《》〈〉…,.!?;:·\s]/g, "")
    .replace(/[—－–-]{2,}/g, "")
    .trim();
}

function cueHasScriptMatch(cue) {
  const cueText = normalizeScriptMatchText(cue?.clean_text || "");
  if (cueText.length < 2) return true;
  const referenceLines = getScriptReferenceLines();
  if (referenceLines.length < 3) return true;
  return referenceLines.some((line) => {
    const referenceText = normalizeScriptMatchText(line);
    return referenceText && (referenceText.includes(cueText) || cueText.includes(referenceText));
  });
}

function getScriptReviewStats() {
  const referenceLines = getScriptReferenceLines();
  if (referenceLines.length < 3 || !state.cues.length) {
    return { enabled: false, matched: 0, misses: 0, total: state.cues.length || 0, reviewMisses: false };
  }
  const misses = (state.cues || []).filter((cue) => !cueHasScriptMatch(cue)).length;
  const matched = Math.max(0, (state.cues || []).length - misses);
  return {
    enabled: true,
    matched,
    misses,
    total: (state.cues || []).length,
    reviewMisses: matched >= misses,
  };
}

function renderReviewScriptAssistPanel() {
  if (state.jobDetail?.local_only) return '<section class="review-script-panel"><p>本机原稿尚未使用云端台本校对。联网后可同步服务器校对；本机人工改字继续保留。</p></section>';
  const scriptName = state.pickedScriptFile?.name || "";
  const scriptText = state.scriptText || "";
  const scriptTextLength = String(scriptText || "").trim().length;
  const expanded = Boolean(state.scriptAssistExpanded);
  const context = state.jobDetail?.script_context || null;
  const evidenceMode = String(state.jobDetail?.subtitle_strategy || "").toLowerCase() === "evidence";
  const summary = context?.summary || {};
  const sourceCount = Number(summary.source_count || context?.source_names?.length || (context ? 1 : 0));
  const referenceLines = getScriptReferenceLines();
  const stats = getScriptReviewStats();
  const keywords = [
    ...(context?.programs || []),
    ...(context?.people || []),
    ...(context?.places || []),
    ...(context?.organizations || []),
    ...(context?.terms || []),
  ].filter(Boolean).slice(0, 12);
  const scriptMeta = context
    ? `已加载 ${sourceCount} 份台本 · ${Number(summary.keyword_count || 0)} 个关键词 · ${referenceLines.length} 行参考`
    : "可在这里补充台本或唱词";
  const draftParts = [
    scriptName ? `已选择 ${scriptName}` : "",
    scriptTextLength ? `已粘贴 ${scriptTextLength} 字` : "",
  ].filter(Boolean);
  const matchText = context
    ? stats.enabled
      ? stats.reviewMisses
        ? `台本匹配 ${stats.matched}/${stats.total}，需核对 ${stats.misses}`
        : `参考覆盖 ${stats.matched} 条；未覆盖内容不标记为错误`
      : "台本已记录"
    : draftParts.length
      ? "点击添加并应用台本"
      : "展开后可选择文件或粘贴台本";
  return `
    <section class="review-script-panel ${expanded ? "is-expanded" : "is-collapsed"}">
      <div class="review-script-bar">
        <div class="review-script-copy">
          <strong>台本/唱词参考</strong>
          <span>${matchText}</span>
        </div>
        <div class="review-script-actions">
          <small>${scriptMeta}</small>
          <button class="secondary-button small" data-script-assist-toggle type="button" aria-expanded="${expanded ? "true" : "false"}">${expanded ? "收起参考" : "查看参考"}</button>
        </div>
      </div>
      ${expanded ? `
        <div class="review-script-expanded">
          <input id="review-script-file-input" class="visually-hidden" type="file" accept=".txt,.md,.srt,.docx,.pdf,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" />
          <div class="review-script-inputs">
            <button id="review-script-picker-trigger" class="script-mini-drop" data-file-drop-target="review-script-file-input" type="button">
              <strong>${escapeHtml(scriptName || "选择或拖拽台本")}</strong>
              <span>txt / md / srt / docx / pdf</span>
            </button>
            <textarea id="review-script-text-input" rows="3" placeholder="粘贴台本或唱词">${escapeHtml(scriptText)}</textarea>
          </div>
          <div class="review-script-footer">
            <div class="script-review-summary">
              ${draftParts.length ? `<small>${escapeHtml(draftParts.join(" · "))}</small>` : ""}
              ${keywords.length ? `<small>${escapeHtml(keywords.join("、"))}</small>` : ""}
            </div>
            <div class="panel-title-actions">
              ${evidenceMode ? `<span>V2 创建任务时已自动纠错</span>` : `<button class="secondary-button small" data-apply-script-corrections type="button" ${context && state.cues.length ? "" : "disabled"}>应用台本纠错</button>`}
              <button id="review-script-update-button" class="primary-button small" type="button">${evidenceMode ? "补充台本参考" : "添加并应用台本"}</button>
            </div>
          </div>
          ${referenceLines.length ? `
            <div class="script-reference-lines">
              ${referenceLines.slice(0, 5).map((line) => `<span>${escapeHtml(line)}</span>`).join("")}
            </div>
          ` : ""}
        </div>
      ` : ""}
    </section>
  `;
}

function renderRecentJobs(jobs) {
  return `
    <details class="collapsible-panel" ${jobs.length && !state.jobDetail ? "open" : ""}>
      <summary>继续已有任务</summary>
      <div class="compact-list">
        ${jobs.length ? jobs.map((job) => `
          <div class="compact-row">
            <div>
              <strong>${escapeHtml(job.program_name || "未命名任务")}</strong>
              <span>${escapeHtml(job.id)} · ${escapeHtml(formatDate(job.updated_at))}</span>
            </div>
            <span class="status-pill ${statusTone(job.status)}">${escapeHtml(statusLabel(job.status))}</span>
            <button class="secondary-button small" data-job-load="${escapeHtml(job.id)}" type="button">载入</button>
          </div>
        `).join("") : `<p class="empty-line">当前项目还没有任务。</p>`}
      </div>
    </details>
  `;
}

function renderReviewStep() {
  const detail = state.jobDetail;
  const sourceUrl = detail?.local_only ? globalThis.SpeakerLocalRecognition?.sourceUrl(detail) || "" : detail?.id ? buildApiUrl(`/transcriptions/jobs/${encodeURIComponent(detail.id)}/source-media`) : "";
  const mediaElement = detail?.video_source_url
    ? `<video id="source-media-player" controls src="${escapeHtml(sourceUrl)}"></video>`
    : `<audio id="source-media-player" controls src="${escapeHtml(sourceUrl)}"></audio>`;
  elements.simpleWorkspace.innerHTML = `
    <section class="task-panel review-workflow">
      ${isSubtitleJob(detail) && isCompletedJob() ? `
        <div class="review-board">
          <section class="review-left-pane">
            <div class="review-media-strip">${sourceUrl ? mediaElement : "<p>当前任务缺少可试听素材。</p>"}</div>
            <div class="speaker-editor">
              ${renderSpeakerEditor()}
            </div>
          </section>
          <section class="review-right-pane">
            ${renderReviewScriptAssistPanel()}
            ${globalThis.SpeakerSubtitleQuality?.render({ detail, cues: state.cues, projectId: state.currentProjectId }) || ""}
            ${renderCueCombinedColumn()}
          </section>
        </div>
      ` : renderNeedSyncState()}
    </section>
  `;
}

function formatVoiceprintScore(score) {
  const raw = Number(score);
  if (!Number.isFinite(raw)) return "";
  const percent = raw <= 1 ? raw * 100 : raw;
  return `${Math.max(0, Math.min(100, Math.round(percent)))}%`;
}

function voiceprintStatusLabel(status) {
  const value = String(status || "").trim();
  const labels = {
    auto_matched: "自动匹配",
    needs_review: "待确认",
    unmatched: "未匹配",
    no_reference: "无声纹样本",
    pending_provider: "声纹未启用",
    source_missing: "缺少源音频",
    segment_missing: "片段过短",
    embedding_failed: "提取失败",
  };
  return labels[value] || "";
}

function buildSpeakerVoiceprintHint(speaker) {
  const sentenceCount = `${Number(speaker.segment_count || 0)} 句`;
  const status = voiceprintStatusLabel(speaker.voiceprint_status);
  if (speaker.voiceprint_person_name) {
    const score = formatVoiceprintScore(speaker.voiceprint_score);
    return [`声纹建议：${speaker.voiceprint_person_name}${score ? ` ${score}` : ""}`, sentenceCount, status].filter(Boolean).join(" · ");
  }
  return [sentenceCount, status].filter(Boolean).join(" · ");
}

function buildSpeakerVoiceprintTitle(speaker) {
  const candidates = Array.isArray(speaker.voiceprint_candidates) ? speaker.voiceprint_candidates : [];
  if (!candidates.length) return buildSpeakerVoiceprintHint(speaker);
  const candidateText = candidates
    .slice(0, 3)
    .map((item) => `${item.person_name || "未知"} ${formatVoiceprintScore(item.score) || "无分数"}`)
    .join(" / ");
  return `${buildSpeakerVoiceprintHint(speaker)}；候选：${candidateText}`;
}

function renderSpeakerEditor() {
  if (!state.speakers.length) {
    return `
      <div class="empty-state">
        <strong>说话人数据还没有准备好</strong>
        <span>先同步任务到完成，再回来校对姓名。</span>
      </div>
    `;
  }
  return `
    <div class="speaker-editor-head">
      <div>
        <strong title="这里改名会应用到同一编号的全部字幕">批量说话人</strong>
        <span>改一次，同编号全局生效</span>
      </div>
      <button id="speaker-save-button" class="primary-button small" type="button">保存姓名</button>
    </div>
    ${isSubtitleJob(state.jobDetail) && !state.jobDetail?.local_only ? `
      <label class="inline-check voiceprint-learning-check" title="只生成候选声纹，需要在声纹库人工确认后才会正式入库。">
        <input id="voiceprint-auto-learning-toggle" type="checkbox" ${state.voiceprintAutoLearningEnabled ? "checked" : ""} />
        <span>保存时学习声纹候选</span>
      </label>
    ` : ""}
    <div class="speaker-grid">
      ${state.speakers.map((speaker) => {
        const key = speaker.speaker_key;
        const name = state.speakerMappings[key] || speaker.speaker_name || speaker.voiceprint_person_name || key;
        const hint = buildSpeakerVoiceprintHint(speaker);
        const title = buildSpeakerVoiceprintTitle(speaker);
        return `
          <label class="speaker-field" title="${escapeHtml(title)}">
            <span><strong>${escapeHtml(key)}</strong><small>${escapeHtml(hint)}</small></span>
            <input data-speaker-input="${escapeHtml(key)}" type="text" value="${escapeHtml(name)}" />
          </label>
        `;
      }).join("")}
    </div>
    ${state.memorySuggestions.length ? `
      <div class="suggestion-strip">
        ${state.memorySuggestions.slice(0, 8).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
      </div>
    ` : ""}
  `;
}

function renderCueList() {
  const cues = state.cues;
  const total = cues.length;
  const pageSize = CUE_PREVIEW_PAGE_SIZE;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(Math.max(0, Number(state.cuePage || 0)), pageCount - 1);
  state.cuePage = page;
  const startIndex = page * pageSize;
  const endIndex = Math.min(total, startIndex + pageSize);
  const visibleCues = cues.slice(startIndex, endIndex);
  return `
    <div class="cue-section">
      <div class="panel-title-row">
        <strong>字幕预览</strong>
        <div class="panel-title-actions">
          <span>${total ? `${startIndex + 1}-${endIndex} / ${total} 条，可逐条修改人名` : "暂无字幕"}</span>
          ${total > pageSize ? `
            <div class="cue-pager">
              <button class="secondary-button small" data-cue-page-delta="-1" type="button" ${page <= 0 ? "disabled" : ""}>上一页</button>
              <button class="secondary-button small" data-cue-page-delta="1" type="button" ${page >= pageCount - 1 ? "disabled" : ""}>下一页</button>
            </div>
          ` : ""}
          <button id="subtitle-speaker-save-button" class="primary-button small" type="button" ${state.cues.length ? "" : "disabled"}>保存单条修改</button>
        </div>
      </div>
      <div class="cue-list">
        ${visibleCues.length ? visibleCues.map((cue) => {
          const cueIndex = String(cue.index || "");
          const speakerName = state.cueSpeakerDrafts?.[cueIndex] ?? cue.speaker_name ?? cue.speaker_key;
          return `
          <div class="cue-row">
            <button class="cue-time-button" data-cue-start="${escapeHtml(cue.start_ms)}" type="button">${escapeHtml(formatMillis(cue.start_ms))}</button>
            <input class="cue-speaker-input" data-cue-speaker-input data-cue-index="${escapeHtml(cue.index)}" data-speaker-key="${escapeHtml(cue.speaker_key)}" type="text" value="${escapeHtml(speakerName)}" />
            <em class="cue-text">${escapeHtml(cue.clean_text || "")}</em>
          </div>
        `;
        }).join("") : `<p class="empty-line">任务完成后显示字幕。</p>`}
      </div>
    </div>
  `;
}

function getVisibleCuePage() {
  const cues = state.cues;
  const total = cues.length;
  const pageSize = CUE_PREVIEW_PAGE_SIZE;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(Math.max(0, Number(state.cuePage || 0)), pageCount - 1);
  state.cuePage = page;
  const startIndex = page * pageSize;
  const endIndex = Math.min(total, startIndex + pageSize);
  return {
    cues,
    total,
    pageSize,
    pageCount,
    page,
    startIndex,
    endIndex,
    visibleCues: cues.slice(startIndex, endIndex),
  };
}

function renderCueReviewToolbar(label) {
  const { total, pageSize, pageCount, page, startIndex, endIndex } = getVisibleCuePage();
  const query = state.cueSearchQuery || "";
  const matches = getCueSearchMatches(query);
  const currentMatch = matches.length ? Math.min(Number(state.cueSearchMatchIndex || 0), matches.length - 1) + 1 : 0;
  return `
    <div class="review-pane-title">
      <strong>${escapeHtml(label)}</strong>
      <div class="panel-title-actions">
        <label class="cue-search">
          <span>搜索</span>
          <input id="cue-search-input" type="search" value="${escapeHtml(query)}" placeholder="输入人物或唱词" />
        </label>
        ${query ? `
          <span class="cue-search-count">${matches.length ? `${currentMatch}/${matches.length}` : "无匹配"}</span>
          <div class="cue-pager">
            <button class="secondary-button small" data-cue-search-delta="-1" type="button" ${matches.length ? "" : "disabled"}>上一个</button>
            <button class="secondary-button small" data-cue-search-delta="1" type="button" ${matches.length ? "" : "disabled"}>下一个</button>
          </div>
        ` : ""}
        <span>${total ? `${startIndex + 1}-${endIndex} / ${total} 条` : "暂无字幕"}</span>
        ${total > pageSize ? `
          <div class="cue-pager">
            <button class="secondary-button small" data-cue-page-delta="-1" type="button" ${page <= 0 ? "disabled" : ""}>上一页</button>
            <button class="secondary-button small" data-cue-page-delta="1" type="button" ${page >= pageCount - 1 ? "disabled" : ""}>下一页</button>
          </div>
        ` : ""}
        <button id="subtitle-speaker-save-button" class="primary-button small" type="button" ${state.cues.length ? "" : "disabled"}>保存单条修改</button>
      </div>
    </div>
  `;
}

function renderCueSpeakerColumn() {
  const { visibleCues } = getVisibleCuePage();
  const matches = getCueSearchMatches();
  const matchIndexByCue = new Map(matches.map((match, index) => [String(match.cue.index || ""), index]));
  return `
    <section class="cue-column cue-speaker-column">
      ${renderCueReviewToolbar("逐条说话人")}
      <div class="cue-column-list">
        ${visibleCues.length ? visibleCues.map((cue, visibleIndex) => {
          const cueIndex = String(cue.index || "");
          const speakerName = state.cueSpeakerDrafts?.[cueIndex] ?? cue.speaker_name ?? cue.speaker_key;
          const matchIndex = matchIndexByCue.get(cueIndex);
          const isMatch = matchIndex !== undefined;
          const isCurrentMatch = isMatch && matchIndex === Number(state.cueSearchMatchIndex || 0);
          return `
            <div class="cue-speaker-row ${isMatch ? "is-search-match" : ""} ${isCurrentMatch ? "is-current-match" : ""}" data-current-cue-match="${isCurrentMatch ? "true" : "false"}">
              <button class="cue-time-button" data-cue-start="${escapeHtml(cue.start_ms)}" type="button">${escapeHtml(formatMillis(cue.start_ms))}</button>
              <input class="cue-speaker-input" data-cue-speaker-input data-cue-index="${escapeHtml(cue.index)}" data-speaker-key="${escapeHtml(cue.speaker_key)}" type="text" value="${escapeHtml(speakerName)}" />
            </div>
          `;
        }).join("") : `<p class="empty-line">任务完成后显示字幕。</p>`}
      </div>
    </section>
  `;
}

function renderCueLyricColumn() {
  const { visibleCues } = getVisibleCuePage();
  const matches = getCueSearchMatches();
  const matchIndexByCue = new Map(matches.map((match, index) => [String(match.cue.index || ""), index]));
  const fillerCount = getStandaloneFillerCueCount();
  return `
    <section class="cue-column cue-lyric-column">
      <div class="review-pane-title lyric-title">
        <div>
          <strong>唱词</strong>
          <span>点击时间可定位试听，改完唱词后再进入下一步。</span>
        </div>
        <div class="panel-title-actions">
          <span>${fillerCount ? `${fillerCount} 条疑似语气词` : "未发现语气词"}</span>
          <button id="batch-delete-fillers-button" class="secondary-button small" type="button" ${fillerCount ? "" : "disabled"}>批量删除语气词</button>
          <button id="subtitle-text-save-button" class="primary-button small" type="button" ${state.cues.length ? "" : "disabled"}>保存唱词修改</button>
        </div>
      </div>
      <div class="cue-column-list lyric-list">
        ${visibleCues.length ? visibleCues.map((cue) => {
          const cueIndex = String(cue.index || "");
          const matchIndex = matchIndexByCue.get(cueIndex);
          const isMatch = matchIndex !== undefined;
          const isCurrentMatch = isMatch && matchIndex === Number(state.cueSearchMatchIndex || 0);
          const isDeleted = Boolean(state.cueDeletedDrafts?.[cueIndex]);
          const textValue = state.cueTextDrafts?.[cueIndex] ?? cue.clean_text ?? "";
          return `
          <div class="lyric-row ${isDeleted ? "is-marked-delete" : ""} ${isMatch ? "is-search-match" : ""} ${isCurrentMatch ? "is-current-match" : ""}" data-current-cue-match="${isCurrentMatch ? "true" : "false"}">
            <button class="cue-time-button" data-cue-start="${escapeHtml(cue.start_ms)}" type="button">${escapeHtml(formatMillis(cue.start_ms))}</button>
            <input class="cue-text-input" data-cue-text-input data-cue-index="${escapeHtml(cue.index)}" type="text" value="${escapeHtml(textValue)}" ${isDeleted ? "disabled" : ""} />
            <button class="icon-button cue-delete-button" data-cue-text-delete="${escapeHtml(cue.index)}" type="button" title="${isDeleted ? "取消删除" : "删除这一条唱词"}">${isDeleted ? "恢复" : "删除"}</button>
          </div>
        `;
        }).join("") : `<p class="empty-line">任务完成后显示唱词。</p>`}
      </div>
    </section>
  `;
}

function renderCueCombinedColumn() {
  const visibleCues = state.cues;
  const matches = getCueSearchMatches();
  const matchIndexByCue = new Map(matches.map((match, index) => [String(match.cue.index || ""), index]));
  const scriptStats = getScriptReviewStats();
  const speakerNames = [...new Set([
    ...(state.speakers || []).map((speaker) => state.speakerMappings[speaker.speaker_key] || speaker.speaker_name || speaker.voiceprint_person_name || speaker.speaker_key),
    ...(state.memorySuggestions || []),
  ].map((value) => String(value || "").trim()).filter(Boolean))];
  return `
    <section class="cue-column cue-combined-column">
      ${renderCueCombinedToolbar()}
      <div class="cue-combined-head">
        <span>时间</span>
        <span>说话人</span>
        <span>字幕内容</span>
        <span>操作</span>
      </div>
      <datalist id="review-speaker-name-options">
        ${speakerNames.map((name) => `<option value="${escapeHtml(name)}"></option>`).join("")}
      </datalist>
      <div class="cue-column-list cue-combined-list">
        ${visibleCues.length ? visibleCues.map((cue) => {
          const cueIndex = String(cue.index || "");
          const speakerName = state.cueSpeakerDrafts?.[cueIndex] ?? cue.speaker_name ?? cue.speaker_key;
          const textValue = state.cueTextDrafts?.[cueIndex] ?? cue.clean_text ?? "";
          const matchIndex = matchIndexByCue.get(cueIndex);
          const isMatch = matchIndex !== undefined;
          const isCurrentMatch = isMatch && matchIndex === Number(state.cueSearchMatchIndex || 0);
          const isDeleted = Boolean(state.cueDeletedDrafts?.[cueIndex]);
          const isScriptMiss = scriptStats.reviewMisses && !cueHasScriptMatch(cue);
          return `
            <div class="cue-combined-row ${isDeleted ? "is-marked-delete" : ""} ${isScriptMiss ? "is-script-miss" : ""} ${isMatch ? "is-search-match" : ""} ${isCurrentMatch ? "is-current-match" : ""}" data-current-cue-match="${isCurrentMatch ? "true" : "false"}">
              <button class="cue-time-button" data-cue-start="${escapeHtml(cue.start_ms)}" type="button">${escapeHtml(formatMillis(cue.start_ms))}</button>
              <input class="cue-speaker-input" data-cue-speaker-input data-cue-index="${escapeHtml(cue.index)}" data-speaker-key="${escapeHtml(cue.speaker_key)}" list="review-speaker-name-options" aria-label="第 ${escapeHtml(cue.index)} 条说话人" type="text" value="${escapeHtml(speakerName)}" />
              <input class="cue-text-input" data-cue-text-input data-cue-index="${escapeHtml(cue.index)}" aria-label="第 ${escapeHtml(cue.index)} 条字幕" type="text" value="${escapeHtml(textValue)}" ${isDeleted ? "disabled" : ""} title="${isScriptMiss ? "台本中未找到明显匹配，建议核对" : ""}" />
              <details class="cue-row-menu">
                <summary>操作</summary>
                <div>
                  <button class="secondary-button small cue-lexicon-button" data-cue-lexicon-term="${escapeHtml(cue.index)}" type="button" ${isDeleted || !textValue.trim() ? "disabled" : ""}>加入项目词库</button>
                  <button class="cue-delete-button" data-cue-text-delete="${escapeHtml(cue.index)}" type="button">${isDeleted ? "恢复本条" : "删除本条"}</button>
                </div>
              </details>
            </div>
          `;
        }).join("") : `<p class="empty-line">任务完成后显示逐条校对内容。</p>`}
      </div>
    </section>
  `;
}

function renderCueCombinedToolbar() {
  const total = state.cues.length;
  const query = state.cueSearchQuery || "";
  const matches = getCueSearchMatches(query);
  const currentMatch = matches.length ? Math.min(Number(state.cueSearchMatchIndex || 0), matches.length - 1) + 1 : 0;
  const fillerCount = getStandaloneFillerCueCount();
  const scriptStats = getScriptReviewStats();
  return `
    <div class="review-pane-title cue-combined-title">
      <div class="cue-combined-copy">
        <strong>逐条校对</strong>
        <span>${total ? `共 ${total} 条` : "暂无字幕"} · 滚轮连续浏览，点击时间定位试听</span>
      </div>
      <label class="cue-search">
        <span>搜索</span>
        <input id="cue-search-input" type="search" value="${escapeHtml(query)}" placeholder="搜索说话人或字幕" />
      </label>
      <div class="cue-toolbar-actions">
        <button id="subtitle-combined-save-button" class="primary-button small" type="button" ${state.cues.length ? "" : "disabled"}>保存全部修改</button>
      </div>
      <div class="cue-toolbar-subrow">
        <div class="panel-title-actions">
        ${query ? `
          <span class="cue-search-count">${matches.length ? `${currentMatch}/${matches.length}` : "无匹配"}</span>
          <div class="cue-pager">
            <button class="secondary-button small" data-cue-search-delta="-1" type="button" ${matches.length ? "" : "disabled"}>上一个</button>
            <button class="secondary-button small" data-cue-search-delta="1" type="button" ${matches.length ? "" : "disabled"}>下一个</button>
          </div>
        ` : ""}
        ${scriptStats.enabled ? `<span class="cue-search-count">${scriptStats.reviewMisses ? `台本需核对 ${scriptStats.misses}` : `参考命中 ${scriptStats.matched}`}</span>` : ""}
        </div>
        <button id="batch-delete-fillers-button" class="secondary-button small" type="button" ${fillerCount ? "" : "disabled"}>${fillerCount ? `清理全片语气词 ${fillerCount}` : "全片无可清理语气词"}</button>
      </div>
    </div>
  `;
}

function renderNeedSyncState() {
  return `
    <div class="empty-state action-empty">
      <strong>任务还没有完成</strong>
      <span>${escapeHtml(state.jobDetail?.processing_message || "点击同步后，客户端会等待转写和字幕优化完成。")}</span>
      <button id="sync-job-button" class="primary-button" type="button" ${state.currentJobId ? "" : "disabled"}>同步到完成</button>
    </div>
  `;
}

function resolveMissingLowerThirds() {
  const mappedNames = state.speakers
    .map((speaker) => state.speakerMappings[speaker.speaker_key] || speaker.speaker_name || "")
    .map((value) => String(value).trim())
    .filter(Boolean);
  const cueNames = (state.cues || [])
    .map((cue) => state.cueSpeakerDrafts?.[String(cue.index)] || cue.speaker_name || cue.speaker_key || "")
    .map((value) => String(value).trim())
    .filter(Boolean);
  const names = Array.from(new Set([...mappedNames, ...cueNames]));
  const assetNames = new Set((state.jobDetail?.lower_third_assets || []).map((asset) => String(asset.speaker_name || "").trim()));
  return names.filter((name) => !assetNames.has(name));
}

function getSubtitleBackplateRoles() {
  return [
    {
      role: "with_speaker",
      title: "有说话人唱词底图",
      description: "有对应人名条时使用",
      emptyText: "用于带人名条的字幕底板",
    },
    {
      role: "without_speaker",
      title: "无人名唱词底图",
      description: "没有说话人或没有匹配人名条时使用",
      emptyText: "用于只有唱词、没有人名条的字幕底板",
    },
  ];
}

function normalizeSubtitleBackplateRoleMap(config) {
  if (!config || typeof config !== "object") return {};
  if (config.roles && typeof config.roles === "object") {
    return config.roles;
  }
  if (config.with_speaker || config.without_speaker || config.default) {
    return config;
  }
  if (config.file_name || config.object_key || config.export_path || config.local_path) {
    return { with_speaker: config, without_speaker: config };
  }
  return {};
}

function getSubtitleBackplateAsset(role) {
  const roles = normalizeSubtitleBackplateRoleMap(state.jobDetail?.subtitle_backplate_asset || null);
  return roles[role] || roles.default || null;
}

function getPendingSubtitleBackplateFile(role) {
  return state.pickedSubtitleBackplateFiles?.[role] || (role === "with_speaker" ? state.pickedSubtitleBackplateFile : null) || null;
}

function renderSubtitleBackplateUploader(roleMeta) {
  const { role, title, description, emptyText } = roleMeta;
  const asset = getSubtitleBackplateAsset(role);
  const pending = getPendingSubtitleBackplateFile(role);
  const inputId = `subtitle-backplate-${role}-input`;
  return `
    <section class="lower-third-action-card">
      <div class="lower-third-card-heading">
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(description)}</span>
      </div>
      <form class="subtitle-backplate-form" data-subtitle-backplate-form-role="${escapeHtml(role)}">
        <input id="${escapeHtml(inputId)}" class="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp" data-subtitle-backplate-input-role="${escapeHtml(role)}" />
        <button class="media-pick-button" data-subtitle-backplate-picker-role="${escapeHtml(role)}" data-file-drop-target="${escapeHtml(inputId)}" type="button">
          <strong>${pending || asset ? "已选择底图" : "选择底图"}</strong>
          <span>${escapeHtml(pending?.name || asset?.file_name || emptyText)}</span>
        </button>
        <div class="lower-third-card-actions">
          <button class="secondary-button" data-subtitle-backplate-upload-role="${escapeHtml(role)}" type="submit">${asset ? "替换底图" : "上传底图"}</button>
          ${asset ? `<button class="danger-button" data-subtitle-backplate-delete-role="${escapeHtml(role)}" type="button">删除</button>` : ""}
        </div>
      </form>
      ${asset ? renderSubtitleBackplateCard(asset, role, title) : ""}
    </section>
  `;
}

function renderLowerThirdStep() {
  const missing = resolveMissingLowerThirds();
  const missingPreview = missing.length > 4
    ? `${missing.slice(0, 4).join("、")} 等 ${missing.length} 位`
    : missing.join("、");
  const assets = state.jobDetail?.lower_third_assets || [];
  const backplateRoles = getSubtitleBackplateRoles();
  elements.simpleWorkspace.innerHTML = `
    <section class="task-panel lower-third-workflow">
      ${isCompletedJob() ? `
        <div class="lower-third-actions-grid">
          <form id="lower-third-form" class="lower-third-action-card is-primary">
            <div class="lower-third-card-heading">
              <strong>人名条图片</strong>
              <span>文件名与说话人姓名一致即可自动匹配</span>
            </div>
            <input id="lower-third-input" class="visually-hidden" type="file" multiple accept="image/png,image/jpeg,image/webp" />
            <button id="lower-third-picker-trigger" class="media-pick-button" data-file-drop-target="lower-third-input" type="button">
              <strong>${state.pickedLowerThirdFiles.length ? `已选择 ${state.pickedLowerThirdFiles.length} 张` : "选择图片"}</strong>
              <span>png、jpg、webp，推荐透明 PNG</span>
            </button>
            <div class="lower-third-card-actions">
              <button id="upload-lower-thirds-button" class="primary-button" type="submit">上传并匹配</button>
              <button id="load-project-lower-thirds-button" class="secondary-button" type="button">载入项目库</button>
            </div>
          </form>
          ${backplateRoles.map((roleMeta) => renderSubtitleBackplateUploader(roleMeta)).join("")}
        </div>
        <div class="lower-third-library-row">
          <button id="load-project-subtitle-backplates-button" class="secondary-button" type="button">载入项目底图</button>
          <span>底图上传后自动保存为项目默认</span>
        </div>
        <div class="match-summary ${missing.length ? "needs-attention" : "is-ready"}">
          <strong>${missing.length ? `还缺 ${missing.length} 位人名条` : "人名条已准备好"}</strong>
          <span>${missing.length ? escapeHtml(missingPreview) : "可以进入导出步骤。"}</span>
        </div>
        <div class="asset-list">
          ${assets.length ? assets.map((asset) => renderAssetCard(asset)).join("") : `<p class="empty-line">还没有上传人名条。</p>`}
        </div>
      ` : renderNeedSyncState()}
    </section>
  `;
}

function renderSubtitleBackplateCard(asset, role = "with_speaker", title = "唱词条底图") {
  const projectId = getCurrentProjectId();
  const imageUrl = projectId && state.currentJobId
    ? buildApiV2Url(`/projects/${encodeURIComponent(projectId)}/transcriptions/jobs/${encodeURIComponent(state.currentJobId)}/subtitle-backplate/file`)
    : "";
  const roleQuery = role ? `?role=${encodeURIComponent(role)}` : "";
  const resolvedImageUrl = projectId && state.currentJobId
    ? buildApiV2Url(`/projects/${encodeURIComponent(projectId)}/transcriptions/jobs/${encodeURIComponent(state.currentJobId)}/subtitle-backplate/file${roleQuery}`)
    : imageUrl;
  return `
    <div class="asset-card">
      ${resolvedImageUrl ? `<img src="${escapeHtml(resolvedImageUrl)}" alt="${escapeHtml(asset.file_name || title)}" />` : ""}
      <div>
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(asset.file_name || asset.object_key || "")}</span>
      </div>
    </div>
  `;
}

function renderAssetCard(asset) {
  const imageUrl = state.currentJobId && asset.object_key
    ? buildApiUrl(`/transcriptions/jobs/${encodeURIComponent(state.currentJobId)}/lower-thirds/${encodeURIComponent(asset.object_key)}/file`)
    : asset.public_url || "";
  return `
    <div class="asset-card">
      ${imageUrl ? `<img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(asset.speaker_name || asset.file_name || "人名条")}" />` : ""}
      <div>
        <strong>${escapeHtml(asset.speaker_name || "未命名")}</strong>
        <span>${escapeHtml(asset.file_name || asset.object_key || "")}</span>
      </div>
    </div>
  `;
}

function renderExportStep() {
  const ready = isCompletedJob();
  const frameRate = state.jobDetail?.export_frame_rate || "25";
  const jobId = state.currentJobId;
  const outputStem = resolveCurrentJobSourceStem();
  elements.simpleWorkspace.innerHTML = `
    <section class="task-panel export-workflow">
      ${ready ? `
        <div class="export-workbench">
          <div class="export-grid">
            <button class="download-tile primary-download" data-download-path="/transcriptions/jobs/${escapeHtml(encodeURIComponent(jobId))}/premiere-xml/download?frame_rate=${escapeHtml(frameRate)}&subtitle_mode=graphicandtype" data-download-name="${escapeHtml(outputStem)}-premiere.xml" type="button">
              <strong>仅导出 XML</strong>
              <span>选择位置保存 XML</span>
            </button>
            <button class="download-tile" data-download-path="/transcriptions/jobs/${escapeHtml(encodeURIComponent(jobId))}/subtitles/download?format=srt&variant=full" data-download-name="${escapeHtml(outputStem)}-full.srt" type="button">
              <strong>带姓名 SRT</strong>
              <span>选择位置保存 SRT</span>
            </button>
            <button class="download-tile" data-download-path="/transcriptions/jobs/${escapeHtml(encodeURIComponent(jobId))}/subtitles/download?format=srt&variant=clean" data-download-name="${escapeHtml(outputStem)}-clean.srt" type="button">
              <strong>纯字幕 SRT</strong>
              <span>选择位置保存 SRT</span>
            </button>
          </div>
          <pre class="subtitle-preview">${escapeHtml((state.subtitlePreview || "").split("\n").slice(0, 80).join("\n"))}</pre>
        </div>
      ` : renderNeedSyncState()}
    </section>
  `;
}

function renderProWorkspace() {
  elements.proWorkspace.innerHTML = "";
}

function renderProToolDetail() {
  return "";
}

function renderVoiceprintPage() {
  elements.simpleWorkspace.innerHTML = `
    <section class="task-panel tool-workflow">
      ${renderVoiceprintTool()}
    </section>
  `;
}

function protectionLevelLabel(level) {
  const labels = {
    hard: "硬保护",
    strong: "强保护",
    soft: "软保护",
  };
  return labels[level] || level || "强保护";
}

function candidateStatusLabel(status) {
  const labels = {
    candidate: "待确认",
    suggested_active: "建议生效",
    promoted: "已生效",
    rejected: "已拒绝",
  };
  return labels[status] || status || "待确认";
}

function renderProtectionSelect(candidate) {
  const current = candidate.suggested_protection_level || "strong";
  return `
    <select class="lexicon-protection-select" data-lexicon-protection="${escapeHtml(candidate.id)}">
      ${["hard", "strong", "soft"].map((level) => `
        <option value="${escapeHtml(level)}" ${current === level ? "selected" : ""}>${escapeHtml(protectionLevelLabel(level))}</option>
      `).join("")}
    </select>
  `;
}

function renderLexiconCandidateList() {
  const candidates = state.lexicon.candidates || [];
  return `
    <section class="flat-panel lexicon-card">
      <div class="panel-title-row">
        <div>
          <h3>候选词</h3>
          <span>${candidates.length ? `${candidates.length} 条 ${candidateStatusLabel(state.lexicon.selectedStatus)}` : "导入台本后在这里审核候选词"}</span>
        </div>
        <label class="field-stack compact-field">
          <span>状态</span>
          <select id="lexicon-status-select">
            ${["candidate", "suggested_active", "promoted", "rejected"].map((status) => `
              <option value="${escapeHtml(status)}" ${state.lexicon.selectedStatus === status ? "selected" : ""}>${escapeHtml(candidateStatusLabel(status))}</option>
            `).join("")}
          </select>
        </label>
      </div>
      <div class="lexicon-list">
        ${candidates.length ? candidates.map((candidate) => `
          <article class="lexicon-item">
            <div>
              <strong>${escapeHtml(candidate.display_text || candidate.normalized_term)}</strong>
              <span>${escapeHtml([candidate.term_type, candidate.extraction_method, candidate.confidence ? `置信 ${Math.round(Number(candidate.confidence) * 100)}%` : ""].filter(Boolean).join(" · "))}</span>
              ${candidate.context ? `<small>${escapeHtml(candidate.context)}</small>` : ""}
            </div>
            <div class="lexicon-item-actions">
              ${renderProtectionSelect(candidate)}
              <button class="primary-button small" data-lexicon-promote="${escapeHtml(candidate.id)}" type="button">生效</button>
              <button class="quiet-button small" data-lexicon-reject="${escapeHtml(candidate.id)}" type="button">拒绝</button>
            </div>
          </article>
        `).join("") : `<p class="empty-line">暂无候选词。</p>`}
      </div>
    </section>
  `;
}

function renderLexiconTermList() {
  const terms = state.lexicon.terms || [];
  return `
    <section class="flat-panel lexicon-card">
      <div class="panel-title-row">
        <div>
          <h3>生效词条</h3>
          <span>${terms.length ? `${terms.length} 条会参与当前项目词库快照` : "还没有生效词条"}</span>
        </div>
      </div>
      <div class="lexicon-list">
        ${terms.length ? terms.map((term) => `
          <article class="lexicon-item">
            <div>
              <strong>${escapeHtml(term.display_text || term.normalized_term)}</strong>
              <span>${escapeHtml([term.scope_type, term.term_type, `${term.occurrence_count || 0} 次出现`].filter(Boolean).join(" · "))}</span>
            </div>
            <div class="lexicon-item-actions">
              <span class="status-pill active">${escapeHtml(protectionLevelLabel(term.protection_level))}</span>
              <button class="danger-button small" data-lexicon-disable="${escapeHtml(term.id)}" type="button">本项目禁用</button>
            </div>
          </article>
        `).join("") : `<p class="empty-line">暂无生效词条。</p>`}
      </div>
    </section>
  `;
}

function renderLexiconBundlePreview() {
  const preview = state.lexicon.bundlePreview || null;
  const conflicts = preview?.conflict_report || [];
  const sourceCounts = preview?.source_counts || {};
  const protectionCounts = preview?.protection_counts || {};
  const countLine = [
    `词条 ${preview?.terms?.length || 0}`,
    `禁用 ${preview?.disabled_terms?.length || 0}`,
    ...Object.entries(sourceCounts).map(([key, value]) => `${key} ${value}`),
    ...Object.entries(protectionCounts).map(([key, value]) => `${protectionLevelLabel(key)} ${value}`),
  ];
  const canRebuild = Boolean(getCurrentSubtitleJobId());
  return `
    <section class="flat-panel lexicon-card">
      <div class="panel-title-row">
        <div>
          <h3>当前任务词库快照</h3>
          <span>${preview?.term_set_hash ? `hash ${preview.term_set_hash.slice(0, 12)}` : "选择项目后生成预览"}</span>
        </div>
        <button id="lexicon-rebuild-bundle-button" class="secondary-button small" type="button" ${canRebuild ? "" : "disabled"}>重建当前任务词库快照</button>
      </div>
      ${preview ? `
        <div class="lexicon-bundle-grid">
          <div>
            <strong>term_set_hash</strong>
            <span>${escapeHtml(preview.term_set_hash || "-")}</span>
          </div>
          <div>
            <strong>counts</strong>
            <span>${escapeHtml(countLine.join(" · "))}</span>
          </div>
        </div>
        <div class="lexicon-conflicts">
          <strong>conflicts</strong>
          ${conflicts.length ? conflicts.slice(0, 5).map((item) => `
            <span>${escapeHtml(item.normalized_term || "")} · keep ${escapeHtml(item.kept?.scope_type || "")} / drop ${escapeHtml(item.discarded?.scope_type || "")}</span>
          `).join("") : `<span>无冲突</span>`}
        </div>
      ` : `<p class="empty-line">暂无快照预览。</p>`}
    </section>
  `;
}

function renderLexiconImportCard() {
  const pickedName = state.lexicon.pickedFile?.name || "";
  const latestImport = state.lexicon.imports?.[0] || null;
  const savedScripts = state.projectScripts || [];
  return `
    <section class="flat-panel lexicon-card">
      <div class="panel-title-row">
        <div>
          <h3>导入台本/术语</h3>
          <span>${savedScripts.length ? `已保存 ${savedScripts.length} 份任务台本，可在新任务中自动使用` : latestImport ? `最近导入 ${latestImport.candidate_count || 0} 条候选` : "支持上传文件或直接粘贴文本"}</span>
        </div>
      </div>
      <form id="lexicon-import-form" class="lexicon-import-form">
        <input id="lexicon-script-input" class="visually-hidden" type="file" accept=".txt,.md,.srt,.docx,.pdf,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" />
        <button id="lexicon-script-picker-trigger" class="media-pick-button lexicon-file-drop" data-file-drop-target="lexicon-script-input" type="button">
          <strong>${escapeHtml(pickedName || "选择或拖拽台本")}</strong>
          <span>${escapeHtml(pickedName ? "文件会和粘贴文本一起导入" : "txt / md / srt / docx / pdf")}</span>
        </button>
        <label class="field-stack">
          <span>来源名称</span>
          <input id="lexicon-source-input" type="text" value="${escapeHtml(state.lexicon.pickedSource || pickedName || "")}" placeholder="例如：第 3 期台本" />
        </label>
        <label class="field-stack">
          <span>粘贴文本</span>
          <textarea id="lexicon-paste-input" rows="7" placeholder="粘贴台本、人物名、专有名词或术语列表">${escapeHtml(state.lexicon.pastedText || "")}</textarea>
        </label>
        <div class="task-actions">
          <button id="lexicon-import-button" class="primary-button" type="submit" ${getCurrentProjectId() ? "" : "disabled"}>导入候选词</button>
          <button class="secondary-button" data-lexicon-refresh type="button">刷新词库</button>
        </div>
      </form>
    </section>
  `;
}

function renderLexiconPage() {
  const projectName = state.currentProjectName || "未选择项目";
  elements.simpleWorkspace.innerHTML = `
    <section class="task-panel tool-workflow lexicon-workflow">
      <div class="tool-header">
        <div>
          <p class="eyebrow">Program Knowledge Base</p>
          <h2>节目知识库</h2>
          <span>项目：${escapeHtml(projectName)} · 术语、专名与断句保护</span>
        </div>
        <button id="lexicon-refresh-button" class="secondary-button" type="button">${state.lexicon.loading ? "刷新中..." : "刷新词库"}</button>
      </div>
      ${state.lexicon.message ? `<p class="panel-note lexicon-message">${escapeHtml(state.lexicon.message)}</p>` : ""}
      <div class="lexicon-grid">
        ${renderLexiconImportCard()}
        ${renderLexiconBundlePreview()}
        ${renderLexiconCandidateList()}
        ${renderLexiconTermList()}
      </div>
    </section>
  `;
}

function renderVoiceCloningPage() {
  elements.simpleWorkspace.innerHTML = `
    <section class="task-panel tool-workflow">
      ${renderVoiceCloningTool()}
    </section>
  `;
}

function renderAudioDropzone(inputId, title, helper, selectedName = "", disabled = false) {
  const label = selectedName || "点击选择文件，或把音频拖到这里";
  return `
    <label class="audio-dropzone" data-file-drop-target="${escapeHtml(inputId)}" ${disabled ? 'aria-disabled="true"' : ""}>
      <input id="${escapeHtml(inputId)}" class="visually-hidden" type="file" accept="audio/*" ${disabled ? "disabled" : ""} />
      <strong>${escapeHtml(title)}</strong>
      <span class="file-drop-name">${escapeHtml(label)}</span>
      <small>${escapeHtml(helper)}</small>
    </label>
  `;
}

function getVoiceCloneSampleDraftLabel() {
  const draft = state.voiceClone.sampleDraft || {};
  if (draft.trimmedFile?.name) return `已裁剪：${draft.trimmedFile.name}`;
  if (draft.fileName) return `已选择：${draft.fileName}`;
  return "";
}

function renderVoiceActivityMap(draft) {
  const profile = draft.speechProfile;
  if (!profile?.analyzed || !Array.isArray(profile.bars) || !profile.bars.length) {
    return `
      <div class="voice-activity-empty">
        <strong>声纹辅助</strong>
        <span>当前格式暂时无法本地分析有声区域，请通过试听和时间滑杆裁剪。</span>
      </div>
    `;
  }
  const ranges = profile.speechRanges || [];
  const longest = ranges.reduce((best, range) => (
    range.end - range.start > best.end - best.start ? range : best
  ), ranges[0] || { start: 0, end: 0 });
  return `
    <div class="voice-activity-map">
      <div class="voice-activity-title">
        <div>
          <strong>声纹辅助</strong>
          <span>${escapeHtml(ranges.length ? `识别到 ${ranges.length} 段有声，最长 ${formatMillis((longest.end - longest.start) * 1000)}` : "未检测到明显人声")}</span>
        </div>
        <button id="voice-clone-use-speech-window" class="secondary-button small" type="button" ${ranges.length ? "" : "disabled"}>使用最长有声段</button>
      </div>
      <div class="voice-activity-bars" aria-label="有声区域波形">
        ${profile.bars.map((bar) => `
          <span class="${bar.speech ? "is-speech" : ""}" style="--level:${escapeHtml(bar.level.toFixed(3))}"></span>
        `).join("")}
      </div>
      <div class="voice-activity-legend">
        <span><i class="speech-dot"></i>有声</span>
        <span><i></i>静音/空白</span>
      </div>
    </div>
  `;
}

function renderVoiceCloneSampleTrimPanel() {
  const draft = state.voiceClone.sampleDraft || {};
  if (!draft.file) return "";
  const duration = Number(draft.duration || 0);
  const start = Math.max(0, Number(draft.trimStart || 0));
  const end = duration > 0 ? Math.min(duration, Number(draft.trimEnd || duration)) : 0;
  const previewUrl = draft.trimmedObjectUrl || draft.objectUrl || "";
  const rangeMax = duration > 0 ? duration.toFixed(2) : "0";
  const usableDuration = Math.max(0, end - start);
  const canTrim = duration > 0 && usableDuration >= 3;
  return `
    <div class="sample-trim-panel">
      <div class="sample-trim-head">
        <div>
          <strong>样本预处理</strong>
          <span>${escapeHtml(draft.fileName || "已选择音频")} · ${escapeHtml(duration ? `总时长 ${formatMillis(duration * 1000)}` : "正在读取时长")}</span>
        </div>
        <span class="trim-status">${escapeHtml(draft.trimmedFile ? `上传将使用裁剪片段：${draft.trimmedFile.name}` : "未裁剪时会上传原始文件")}</span>
      </div>
      ${previewUrl ? `<audio controls preload="metadata" src="${escapeHtml(previewUrl)}"></audio>` : ""}
      <details class="voice-trim-options"><summary>裁剪参考音频（可选）</summary>
      ${renderVoiceActivityMap(draft)}
      <div class="trim-controls">
        <label>
          <span>开始 <strong id="voice-clone-trim-start-label">${escapeHtml(formatMillis(start * 1000))}</strong></span>
          <input id="voice-clone-trim-start" type="range" min="0" max="${escapeHtml(rangeMax)}" step="0.05" value="${escapeHtml(start.toFixed(2))}" ${duration > 0 ? "" : "disabled"} />
        </label>
        <label>
          <span>结束 <strong id="voice-clone-trim-end-label">${escapeHtml(formatMillis(end * 1000))}</strong></span>
          <input id="voice-clone-trim-end" type="range" min="0" max="${escapeHtml(rangeMax)}" step="0.05" value="${escapeHtml(end.toFixed(2))}" ${duration > 0 ? "" : "disabled"} />
        </label>
      </div>
      <div class="trim-actions">
        <span id="voice-clone-trim-duration-label">片段 ${escapeHtml(formatMillis(usableDuration * 1000))}${usableDuration && usableDuration < 10 ? " · 建议 10 秒以上" : ""}</span>
        <button id="voice-clone-trim-button" class="secondary-button small" type="button" ${canTrim ? "" : "disabled"}>裁剪为上传样本</button>
        <button id="voice-clone-trim-reset-button" class="quiet-button small" type="button" ${draft.trimmedFile ? "" : "disabled"}>恢复原始</button>
      </div></details>
    </div>
  `;
}

function renderToolsAndLogsPage() {
  elements.simpleWorkspace.innerHTML = `
    <section class="task-panel tool-workflow">
      <div class="tool-stack">
        ${renderJobTool()}
        ${renderXmlRepairTool()}
        ${renderClientUpdateTool()}
        ${renderLogsTool()}
      </div>
    </section>
  `;
}

function renderJobTool() {
  const jobs = getProjectJobs("all");
  return `
    <div class="tool-header">
      <div><p class="eyebrow">Jobs</p><h2>任务管理</h2></div>
      <button id="refresh-jobs-button" class="secondary-button" type="button">刷新任务</button>
    </div>
    <div class="data-table">
      ${jobs.length ? jobs.map((job) => `
        <div class="data-row">
          <div>
            <strong>${escapeHtml(job.program_name || "未命名任务")}</strong>
            <span>${escapeHtml(job.id)} · ${escapeHtml(formatDate(job.updated_at))}</span>
          </div>
          <span class="status-pill ${statusTone(job.status)}">${escapeHtml(statusLabel(job.status))}</span>
          <button class="secondary-button small" data-job-load="${escapeHtml(job.id)}" type="button">载入</button>
          <button class="quiet-button small" data-job-abort="${escapeHtml(job.id)}" type="button">中止</button>
          <button class="danger-button small" data-job-delete="${escapeHtml(job.id)}" type="button">删除</button>
        </div>
      `).join("") : `<p class="empty-line">当前项目暂无任务。</p>`}
    </div>
  `;
}

function renderVoiceprintTool() {
  const filteredPeople = getFilteredVoiceprintPeople();
  const globalPeople = (state.people || []).filter(isGlobalVoiceprintPerson);
  const currentProjectPeople = (state.people || []).filter((person) => (
    String(person.project_id || "") === String(state.currentProjectId || "")
  ));
  const allSamples = (state.people || []).flatMap((person) => person.samples || []);
  const pendingSampleCount = allSamples.filter(isAutoVoiceprintCandidate).length;
  const selectedPerson = filteredPeople.find((item) => item.id === state.voiceClone.personId) || filteredPeople[0] || null;
  if (selectedPerson && state.voiceClone.personId !== selectedPerson.id) {
    state.voiceClone.personId = selectedPerson.id;
  }
  const peopleGroups = groupVoiceprintPeopleByProject(filteredPeople);
  const uploadPeople = filteredPeople;
  const createProjectName = resolveVoiceprintCreateProjectName();
  const createProjectId = resolveVoiceprintCreateProjectId();
  const selectedSamples = selectedPerson?.samples || [];
  const candidateSamples = selectedSamples.filter(isAutoVoiceprintCandidate);
  const confirmedSamples = selectedSamples.filter((sample) => !isAutoVoiceprintCandidate(sample));
  return `
    <div class="tool-header">
      <div>
        <p class="eyebrow">Speaker Identification</p>
        <h2>声纹库</h2>
        <p class="voice-retention-note">识别声纹仅保留3天（创建后72小时），使用不延期。</p>
        <span>系统会从重复任务中自动确认声纹，并将跨项目一致的人物晋升为通用声纹；无需用户逐条维护。</span>
      </div>
      <button id="refresh-voiceprints-button" class="secondary-button" type="button">刷新声纹</button>
    </div>
    <div class="voiceprint-scope-overview" aria-label="声纹库状态">
      <div>
        <span>当前项目声纹</span>
        <strong>${currentProjectPeople.length}</strong>
        <small>仅服务当前节目</small>
      </div>
      <div>
        <span>通用声纹</span>
        <strong>${globalPeople.length}</strong>
        <small>所有项目共同识别</small>
      </div>
      <div>
        <span>等待更多证据</span>
        <strong>${pendingSampleCount}</strong>
        <small>重复出现后自动确认</small>
      </div>
      <div>
        <span>单人样本上限</span>
        <strong>5</strong>
        <small>自动保留最可信样本</small>
      </div>
    </div>
    <div class="two-column-tool">
      <section class="flat-panel">
        <h3>识别人物</h3>
        ${renderVoiceprintFilterControls(filteredPeople.length)}
        <form id="create-person-form" class="inline-create-form">
          <input id="person-name-input" type="text" placeholder="人物姓名" />
          <button class="primary-button" type="submit" ${createProjectId ? "" : "disabled"}>添加人物</button>
        </form>
        <p class="panel-note">${escapeHtml(createProjectName ? `新增人物会归入项目：${createProjectName}` : "请先进入项目，再添加人物。")}</p>
        <div class="people-project-groups">
          ${peopleGroups.length ? peopleGroups.map((group) => {
            const sampleCount = group.records.reduce((total, person) => total + Number(person.sample_count || 0), 0);
            return `
              <section class="people-project-group">
                <div class="project-group-title">
                  <strong>${escapeHtml(group.projectName)}</strong>
                  <span>${group.records.length} 人物 · ${sampleCount} 条样本</span>
                </div>
                <div class="people-list">
                  ${group.records.map((person) => `
                    <div class="person-row ${selectedPerson?.id === person.id ? "is-active" : ""}">
                      <button data-person-select="${escapeHtml(person.id)}" type="button">
                        <strong>${escapeHtml(person.name)} <small class="voiceprint-scope-tag">${isGlobalVoiceprintPerson(person) ? "通用" : "项目"}</small></strong>
                        <span>${escapeHtml(isGlobalVoiceprintPerson(person) ? "所有项目" : person.program_name || "未绑定项目")} · ${person.sample_count || 0}/5 条 · ${person.ready_sample_count || 0} 可用</span>
                      </button>
                      <button class="danger-button small" data-voiceprint-person-delete="${escapeHtml(person.id)}" data-voiceprint-person-name="${escapeHtml(person.name)}" type="button">删除</button>
                    </div>
                  `).join("")}
                </div>
              </section>
            `;
          }).join("") : `<p class="empty-line">${state.people.length ? "当前筛选条件下没有人物。" : "还没有人物。"}</p>`}
        </div>
      </section>
      <section class="flat-panel">
        <h3>上传识别样本</h3>
        <p class="panel-note">上传样本会立即确认；自动截取样本需要来自另一任务的同声纹证据后才参与自动改名。每个人始终最多保留 5 条。</p>
        <form id="upload-voice-sample-form" class="sample-upload-form">
          <select id="voice-sample-person-select">
            ${uploadPeople.length
              ? uploadPeople.map((person) => `<option value="${escapeHtml(person.id)}" ${selectedPerson?.id === person.id ? "selected" : ""}>${escapeHtml(`${person.name} · ${isGlobalVoiceprintPerson(person) ? "通用声纹" : person.program_name || "未绑定项目"}`)}</option>`).join("")
              : `<option value="">先添加人物</option>`}
          </select>
          ${renderAudioDropzone("voice-sample-input", "选择或拖拽识别样本", "用于声纹识别；支持 wav、mp3、m4a、aac、aif、aiff。", state.voiceSampleDraft.fileName ? `已选择：${state.voiceSampleDraft.fileName}` : "")}
          <button id="upload-voice-sample-button" class="primary-button" type="submit" ${uploadPeople.length ? "" : "disabled"}>上传样本</button>
        </form>
        ${selectedPerson ? `
          ${renderVoiceprintSampleSection("可信样本", `已确认并用于识别；当前 ${confirmedSamples.length}/5 条。`, confirmedSamples)}
          ${renderVoiceprintSampleSection("等待交叉验证", "系统会在不同任务再次遇到同名同声纹时自动确认；无需手动处理。", candidateSamples, { candidate: true })}
        ` : `<p class="empty-line">选择人物后显示样本。</p>`}
      </section>
    </div>
  `;
}

function getVoiceCloneRenderContext() {
  const provider = state.serviceStatus.voiceClone;
  applyVoiceCloneProviderDefaults();
  const selectedSample = state.voiceClone.samples.find((sample) => sample.id === state.voiceClone.sampleId);
  const displayName = state.voiceClone.displayName || selectedSample?.display_name || "";
  return { provider, selectedSample, displayName };
}

function renderVoiceClonePageHeader(title, description) {
  const provider = state.serviceStatus.voiceClone;
  const projectName = state.currentProjectName || "未选择项目";
  return `
    <div class="tool-header">
      <div>
        <p class="eyebrow">Voice Cloning</p>
        <h2>${escapeHtml(title)}</h2>
        <span>${escapeHtml(description)} · 项目：${escapeHtml(projectName)}</span>
      </div>
      <span class="status-pill ${provider?.available ? "success" : "neutral"}">${escapeHtml(provider?.available ? "服务可用" : provider?.message || "未连接")}</span>
    </div>
  `;
}

function renderVoiceCloneServiceBanner() {
  const provider = state.serviceStatus.voiceClone;
  const available = isVoiceCloneServiceAvailable();
  if (available) {
    return `<span class="voice-service-status is-online" role="status" aria-label="声音服务可用"><span class="voice-service-dot" aria-hidden="true"></span></span>`;
  }
  return `
    <div class="voice-service-banner is-offline" role="alert">
      <span class="voice-service-dot" aria-hidden="true"></span>
      <div>
        <strong>声音服务未连接</strong>
        <span>${escapeHtml(provider?.message || "仍可查看和搜索已有音色，创建与生成暂时不可用。")}</span>
      </div>
      <button id="voice-clone-service-retry-button" class="secondary-button small" type="button">重试连接</button>
    </div>
  `;
}

function renderVoiceCloneNoProjectTool() {
  return `
    ${renderVoiceClonePageHeader("声音复刻", "先选择项目，再上传样本、创建音色和生成音频")}
    <div class="empty-state action-empty">
      <h3>请先选择项目</h3>
      <span>声音库跨项目共享，生成的音频归入当前项目。</span>
    </div>
  `;
}

function renderVoiceCloneModelPanel(context, disabled = false) {
  const models = getVoiceCloneModelOptions();
  const selected = `${state.voiceClone.provider}:${state.voiceClone.targetModel}`;
  return `<label class="field-stack">
    <span>复刻模型</span>
    <select id="voice-clone-model-select" ${disabled ? "disabled" : ""}>
      ${models.map((model) => `<option value="${escapeHtml(model.id)}" ${model.id === selected ? "selected" : ""} ${model.available ? "" : "disabled"}>${escapeHtml(model.label)}${model.recommended ? " · 推荐" : ""}${model.available ? "" : " · 暂不可用"}</option>`).join("")}
    </select>
  </label>`;
}

function renderVoiceCloneSampleSelector(selectedSample) {
  return `
    <label class="field-stack">
      <span>复刻样本</span>
      <select id="voice-clone-sample-select">
        ${state.voiceClone.samples.length
          ? state.voiceClone.samples.map((sample) => `<option value="${escapeHtml(sample.id)}" ${state.voiceClone.sampleId === sample.id ? "selected" : ""} ${isVoiceAssetExpired(sample) ? "disabled" : ""}>${escapeHtml(`${sample.display_name || "未命名音色"} · ${sample.original_filename}`)}</option>`).join("")
          : `<option value="">先上传一个复刻样本</option>`}
      </select>
    </label>
    <div class="voice-sample-summary">
      <strong>${escapeHtml(selectedSample ? `${selectedSample.display_name || "未命名音色"} · ${selectedSample.original_filename}` : "未选择复刻样本")}</strong>
      <span>${escapeHtml(formatVoiceSampleMeta(selectedSample))}</span>
      <small>${escapeHtml(selectedSample?.clone_readiness_message || "建议从 Premiere 时间线裁切单人干净人声，导出 24 kHz 单声道 16bit WAV，时长 10-20 秒。")}</small>
      ${renderVoiceAssetExpiry(selectedSample)}
      ${selectedSample && !isVoiceAssetExpired(selectedSample) && state.currentProjectId ? `<audio controls preload="metadata" ${voiceAssetAudioAttributes(selectedSample)} data-audio-src="${escapeHtml(buildApiV2Url(buildProjectVoiceClonePath(state.currentProjectId, `/samples/${encodeURIComponent(selectedSample.id)}/audio`)))}"></audio>` : ""}
      ${selectedSample ? `
        <div class="row-actions">
          <button class="danger-button small" data-voice-clone-sample-delete="${escapeHtml(selectedSample.id)}" data-voice-clone-sample-name="${escapeHtml(selectedSample.display_name || selectedSample.original_filename)}" type="button">删除参考音频</button>
        </div>
      ` : ""}
    </div>
  `;
}

function getFilteredVoiceCloneSamples() {
  const query = String(state.voiceClone.sampleSearch || "").trim().toLowerCase();
  if (!query) return state.voiceClone.samples;
  return state.voiceClone.samples.filter((sample) => [
    sample.display_name,
    sample.original_filename,
    sample.clone_readiness_status,
  ].join(" ").toLowerCase().includes(query));
}

function renderVoiceCloneSampleLibrary(samples = getFilteredVoiceCloneSamples()) {
  return `
    <div class="sample-list" id="voice-clone-sample-list">
      ${samples.length ? samples.map((sample) => `
        <div class="sample-row ${state.voiceClone.sampleId === sample.id ? "is-active" : ""}">
          <div class="sample-main">
            <button data-voice-clone-sample-select="${escapeHtml(sample.id)}" type="button" ${isVoiceAssetExpired(sample) ? "disabled" : ""}>
              <strong>${escapeHtml(sample.display_name || "未命名音色")}</strong>
              <span>${escapeHtml(sample.original_filename)} · ${escapeHtml(globalThis.SpeakerVoiceCloneLibrary?.translateStatus(sample.clone_readiness_status) || sample.clone_readiness_status || "unknown")} · ${escapeHtml(formatVoiceSampleMeta(sample))}</span>${renderVoiceAssetExpiry(sample)}
            </button>
          </div>
          <div class="row-actions">
            <button class="secondary-button small" data-voice-clone-sample-preview="${escapeHtml(sample.id)}" type="button" ${isVoiceAssetExpired(sample) ? "disabled" : ""} aria-label="试听样本：${escapeHtml(sample.display_name || sample.original_filename)}">试听</button>
            <button class="danger-button small" data-voice-clone-sample-delete="${escapeHtml(sample.id)}" data-voice-clone-sample-name="${escapeHtml(sample.display_name || sample.original_filename)}" type="button" aria-label="删除样本：${escapeHtml(sample.display_name || sample.original_filename)}">删除</button>
          </div>
        </div>
      `).join("") : `<p class="empty-line">${state.voiceClone.samples.length ? "没有匹配的复刻样本。" : "还没有复刻样本。上传后会出现在这里。"}</p>`}
    </div>
  `;
}

function renderVoiceCloneSharedSamplePlayer() {
  const player = state.voiceClone.sharedPlayer || {};
  if (!player.url) return "";
  if (isVoiceAssetExpired(player)) return renderVoiceAssetExpiry(player);
  return `
    <div class="voice-shared-player">
      <strong>正在试听：${escapeHtml(player.label || "复刻样本")}</strong>
      ${renderVoiceAssetExpiry(player)}
      <audio id="voice-clone-shared-sample-player" aria-label="声音复刻样本播放器" controls preload="none" ${voiceAssetAudioAttributes(player)} data-audio-src="${escapeHtml(player.url)}"></audio>
    </div>
  `;
}

function getFilteredVoiceCloneProfiles() {
  const query = String(state.voiceClone.profileSearch || "").trim().toLowerCase();
  if (!query) return state.voiceClone.profiles;
  return state.voiceClone.profiles.filter((profile) => [
    profile.voice_name,
    profile.status,
    profile.provider,
    profile.target_model,
  ].join(" ").toLowerCase().includes(query));
}

function getVoiceCloneLibraryGroups() {
  return globalThis.SpeakerVoiceCloneLibrary?.resolveLibraryGroups(
    state.voiceClone.library?.loaded ? state.voiceClone.library.groups : null,
    state.voiceClone.profiles,
  ) || [];
}

function getFilteredVoiceCloneGroups() {
  return globalThis.SpeakerVoiceCloneLibrary?.resolveLibraryGroups(
    state.voiceClone.library?.loaded ? state.voiceClone.library.groups : null,
    state.voiceClone.profiles,
    state.voiceClone.profileSearch,
  ) || [];
}

function isSelectableVoiceCloneProfile(profile) {
  return Boolean(globalThis.SpeakerVoiceCloneLibrary?.isSelectableProfile(profile));
}

function getVoiceCloneSourcePreview(profile) {
  return globalThis.SpeakerVoiceCloneLibrary?.getSourcePreview(profile, state.currentProjectId) || null;
}

function renderVoiceCloneSourceDetails(profile) {
  const scope = globalThis.SpeakerVoiceCloneLibrary?.projectScopeLabel(profile.project_scope)
    || "共享音色";
  const sourcePreview = getVoiceCloneSourcePreview(profile);
  return `
    <span class="voice-project-scope">${escapeHtml(scope)}</span>
    <dl class="voice-version-meta">
      <div><dt>来源项目</dt><dd>${escapeHtml(profile.source_project_name || "未记录")}</dd></div>
      <div><dt>样本文件</dt><dd>${escapeHtml(profile.source_original_filename || profile.source_display_name || "未记录")}</dd></div>
    </dl>
    ${sourcePreview ? `<button class="secondary-button small" data-voice-profile-source-preview="${escapeHtml(sourcePreview.sampleId)}" data-voice-profile-source-api="${escapeHtml(sourcePreview.apiVersion)}" data-voice-profile-source-path="${escapeHtml(sourcePreview.path)}" data-voice-profile-source-label="${escapeHtml(profile.source_original_filename || profile.source_display_name || profile.voice_name)}" ${voiceAssetAudioAttributes(profile)} type="button" ${isVoiceAssetExpired(profile) ? "disabled" : ""}>试听来源</button>` : ""}
  `;
}

function renderVoiceCloneVersionList(group) {
  const library = state.voiceClone.library || {};
  if (library.expandedGroupKey !== group.group_key) return "";
  const profiles = library.profilesByGroup?.[group.group_key];
  if (!Array.isArray(profiles)) {
    return `<div class="voice-version-list is-loading" role="status">正在加载历史版本…</div>`;
  }
  return `
    <div class="voice-version-list">
      ${profiles.map((profile) => {
        const selectable = isSelectableVoiceCloneProfile(profile);
        return `
        <div class="voice-version-row">
          <div class="voice-version-summary">
            <strong>${escapeHtml(profile.voice_name)}</strong>
            <span>创建：${escapeHtml(formatDate(profile.created_at))}</span>
            ${renderVoiceAssetExpiry(profile, "请新建声音")}
            <span>最近使用：${escapeHtml(profile.last_used_at ? formatDate(profile.last_used_at) : "未使用")}</span>
            <span>${escapeHtml(profile.target_model || "未记录模型")} · ${escapeHtml(isVoiceAssetExpired(profile) ? "已到期" : globalThis.SpeakerVoiceCloneLibrary?.translateStatus(profile.status) || profile.status)}</span>
            ${renderVoiceCloneSourceDetails(profile)}
          </div>
          ${selectable ? `<button class="primary-button small" data-voice-profile="${escapeHtml(profile.id)}" type="button">直接使用</button>` : profile.status !== "creating" && (profile.source_clone_sample_id || profile.source_sample_id) ? `<button class="secondary-button small" data-voice-profile-recreate="${escapeHtml(profile.id)}" type="button" ${isVoiceCloneServiceAvailable(profile) && !isVoiceAssetExpired(profile) ? "" : "disabled"}>重新创建</button>` : ""}
          <button class="danger-button small" data-voice-profile-delete="${escapeHtml(profile.id)}" data-voice-profile-name="${escapeHtml(profile.voice_name)}" type="button" aria-label="删除音色版本：${escapeHtml(profile.voice_name)}">删除</button>
        </div>
      `;
      }).join("") || `<p class="empty-line">没有其他版本。</p>`}
    </div>
  `;
}

function renderVoiceCloneProfileList() {
  return renderVoiceIdentityList();
}

function renderVoiceCloneCreatingProfiles(library = state.voiceClone.library || {}) {
  const profiles = Array.isArray(library.creatingProfiles) ? library.creatingProfiles : [];
  if (!profiles.length) return "";
  return `<section class="voice-creating-profiles" aria-live="polite"><strong>正在创建的音色</strong>${profiles.map((profile) => `<span>${escapeHtml(profile.voice_name || "未命名音色")} · ${escapeHtml(isVoiceAssetExpired(profile) ? "已到期，请新建声音" : globalThis.SpeakerVoiceCloneLibrary?.translateStatus(profile.status) || "创建中")}${renderVoiceAssetExpiry(profile, "请新建声音")}</span>`).join("")}</section>`;
}

function renderVoiceCloneRecordsList() {
  const records = state.voiceClone.records;
  return `<div class="record-list">
    ${records.length ? records.map((record) => {
      const profile = state.voiceClone.profiles.find((item) => item.id === record.voice_clone_profile_id);
      const model = getVoiceCloneModelOption(`${record.provider_snapshot || profile?.provider}:${record.target_model_snapshot || record.target_model || profile?.target_model}`);
      const expired = isVoiceAssetExpired(record);
      const ready = record.status === "ready" && !expired;
      return `<article class="record-row">
        <div>
          <strong>${escapeHtml(record.voice_name_snapshot || profile?.voice_name || "生成音频")} <small>${escapeHtml(model?.label || record.target_model_snapshot || record.target_model || profile?.target_model || "")}</small></strong>
          <small>${escapeHtml(formatDate(record.created_at))}</small>
          ${renderVoiceAssetExpiry(record, "请重新生成")}
          <span>${escapeHtml(record.input_text)}</span>
          ${ready && state.currentProjectId ? `<audio controls preload="none" ${voiceAssetAudioAttributes(record)} data-audio-src="${escapeHtml(buildApiV2Url(buildProjectVoiceClonePath(state.currentProjectId, `/synthesis-records/${encodeURIComponent(record.id)}/audio`)))}"></audio>` : expired ? "" : `<small role="status">${escapeHtml(record.provider_message || (record.status === "failed" ? "生成失败，请重试" : "正在生成…"))}</small>`}
          ${ready && shouldShowVoiceRecordMessage(record) ? `<small class="record-message">${escapeHtml(record.provider_message)}</small>` : ""}
        </div>
        <div class="row-actions">
          ${record.status === "failed" ? `<button class="secondary-button small" data-voice-record-retry="${escapeHtml(record.id)}" type="button" ${state.voiceClone.busy || expired ? "disabled" : ""}>重试</button>` : ""}
          <button class="secondary-button small" data-voice-record-download="${escapeHtml(record.id)}" type="button" ${ready ? "" : "disabled"}>下载</button>
          <button class="danger-button small" data-voice-record-delete="${escapeHtml(record.id)}" type="button">删除</button>
        </div>
      </article>`;
    }).join("") : `<p class="empty-line">可反复生成和试听；下载保存成功后清理本次所有版本，未下载最多保留3天。</p>`}
  </div>`;
}

function shouldShowVoiceRecordMessage(record) {
  const message = String(record?.provider_message || "").trim();
  return Boolean(message && message !== "试听音频已生成");
}

function getVoiceInstructionMeta(profile) {
  const model = getVoiceCloneModelOption(`${profile?.provider || state.voiceClone.provider}:${profile?.target_model || state.voiceClone.targetModel}`);
  return {
    supported: model?.capabilities?.instruction === true,
    maxWeight: Number(model?.constraints?.instruction_max_weight || 0),
    hanWeight: Number(model?.constraints?.instruction_han_weight || 2),
  };
}

function renderVoiceInstructionExamples() {
  return `<div class="instruction-example-row">${["自然亲切", "沉稳有力", "温柔舒缓"].map((example) => `<button class="instruction-pill" data-voice-instruction-example="${escapeHtml(example)}" type="button">${escapeHtml(example)}</button>`).join("")}</div>`;
}

function renderVoiceCloneReuseProfileSuggestions() {
  const query = String(state.voiceClone.displayName || "").trim().toLocaleLowerCase("zh-CN");
  const groups = getVoiceCloneLibraryGroups()
    .filter((group) => isSelectableVoiceCloneProfile(group.recommended_profile))
    .filter((group) => !query || String(group.display_name || "").toLocaleLowerCase("zh-CN").includes(query))
    .slice(0, 5);
  return `
    <div class="voice-reuse-suggestions" id="voice-clone-reuse-suggestions">
      ${groups.length ? groups.map((group) => `
        <div class="voice-reuse-row">
          <span>
            <strong>${escapeHtml(group.display_name)}</strong>
            <small>${Number(group.version_count || 1)} 个版本 · ${escapeHtml(globalThis.SpeakerVoiceCloneLibrary?.translateStatus(group.health_status) || group.health_status)}</small>
          </span>
          <button class="primary-button small" data-voice-profile="${escapeHtml(group.recommended_profile?.id || "")}" data-close-voice-drawer="true" type="button">直接使用</button>
        </div>
      `).join("") : `<p class="empty-line">没有同名可用音色，下一步会优先查找可复用样本。</p>`}
    </div>
  `;
}

function renderVoiceCloneGenericNameHint(voiceName) {
  const normalized = String(voiceName || "").normalize("NFKC").trim();
  const hidden = ["文本配音", "配音", "测试"].includes(normalized) ? "" : "hidden";
  return `<p class="voice-generic-name-hint" ${hidden}>建议使用“人物名 + 用途”，例如“王冰冰 新闻旁白”，以后更容易辨认。</p>`;
}

function renderAddVoiceFlow(flow = state.voiceClone.addFlow || {}, context = getVoiceCloneRenderContext()) {
  if (!flow.open) return "";
  const stage = flow.stage || "pick";
  const available = isVoiceCloneServiceAvailable();
  const busy = stage === "checking" || (stage === "creating" && Boolean(state.voiceClone.busy));
  const identity = flow.matchedIdentity || {};
  const identityId = String(identity.id || identity.identity_id || identity.identityId || "");
  const identityName = identity.display_name || identity.displayName || context.displayName || "已有声音";
  const group = getVoiceCloneLibraryGroups().find((item) => String(item.identity_id || item.group_key || "") === identityId);
  const recommended = (flow.existingProfiles || []).find(isSelectableVoiceCloneProfile) || group?.recommended_profile;
  const samples = Array.isArray(flow.existingSamples) ? flow.existingSamples : [];
  const existingSample = Boolean(state.voiceClone.sampleId || flow.sourceSampleId);
  const retryExisting = stage === "error" && existingSample && !state.voiceClone.sampleDraft?.file && !flow.uploadedFile;
  const modelAvailable = isVoiceCloneServiceAvailable({provider: state.voiceClone.provider, target_model: state.voiceClone.targetModel});
  const sourceExpired = isVoiceAssetExpired(state.voiceClone.samples.find((sample) => sample.id === state.voiceClone.sampleId) || flow.uploadedSample || (flow.sourceAsset?.id === state.voiceClone.sampleId ? flow.sourceAsset : null));
  const renderCreateAction = (label) => `<button id="voice-clone-create-button" class="primary-button" type="button" ${modelAvailable && existingSample && !sourceExpired && !state.voiceClone.busy ? "" : "disabled"}>${label}</button>`;
  return `<aside id="voice-clone-create-drawer" class="voice-create-drawer" role="dialog" aria-modal="true" aria-labelledby="voice-clone-create-drawer-title">
    <div class="voice-create-drawer-head">
      <div><p class="eyebrow">新声音</p><h3 id="voice-clone-create-drawer-title">添加声音</h3><span>上传一段清晰人声，创建后即可用它朗读。</span></div>
      <button id="voice-clone-drawer-close-button" class="secondary-button small" type="button" aria-label="关闭添加声音面板">关闭</button>
    </div>
    <div class="voice-create-drawer-body">
      <p class="voice-retention-note">${state.serviceStatus.voiceClone?.discard_voice_after_download ? "可反复生成和试听，下载选定版本并保存成功后，清理本次参考音频、音色及全部生成音频。" : "参考音频和声音仅保留3天，使用不延期。"}</p>
      ${sourceExpired ? `<p class="voice-persistent-error" role="alert">参考音频已到期，请关闭面板后重新添加声音。文案已保留。</p>` : ""}
      ${available ? "" : `<p class="panel-note voice-drawer-service-message" role="alert">声音服务未连接，暂时不能创建或上传声音；仍可查看已有声音和样本。</p>`}
      ${["pick", "error"].includes(stage) && !retryExisting ? `
        ${stage === "error" ? `<p class="voice-persistent-error" role="alert">${escapeHtml(flow.error || "创建失败，请重试。")}</p>` : ""}
        <form id="voice-clone-upload-sample-form" class="sample-upload-form">
          <label class="field-stack"><span>声音名称</span><input id="voice-clone-display-name-input" type="text" value="${escapeHtml(context.displayName)}" maxlength="100" required placeholder="例如：主持人王老师" /></label>
          ${renderVoiceCloneModelPanel(context, !available)}
          ${renderAudioDropzone("voice-clone-sample-input", "选择或拖拽参考音频", "建议 10–20 秒清晰单人声，最长 60 秒、最大 10 MB。", getVoiceCloneSampleDraftLabel() || flow.fileLabel || "", !available)}
          ${renderVoiceCloneSampleTrimPanel()}
          <p class="panel-note">${state.serviceStatus.voiceClone?.discard_voice_after_download ? "本次使用上传的参考音频创建临时音色，下载选定版本后自动清理。" : "系统会自动检查音频并查找已有声音，同一个人归入同一个声音身份。"}</p>
          <button id="voice-clone-upload-sample-button" class="primary-button" type="submit" ${modelAvailable ? "" : "disabled"}>${stage === "error" ? "重试创建" : "创建声音"}</button>
        </form>` : ""}
      ${retryExisting ? `<p class="voice-persistent-error" role="alert">${escapeHtml(flow.error || "创建失败，请重试。")}</p><p class="panel-note">${escapeHtml(context.displayName)} 的参考音频已保存，重试会继续使用它。</p>${renderVoiceCloneModelPanel(context, !available)}${renderCreateAction("重试创建")}` : ""}
      ${busy ? `<div class="voice-flow-progress" role="status"><span class="voice-progress-dot"></span><h4>${stage === "checking" ? "正在检查参考音频" : "正在创建声音"}</h4><p>${escapeHtml(flow.fileLabel || context.displayName || "请稍候")}</p><small>${stage === "checking" ? "检查音频质量并查找已有声音…" : "等待模型确认声音可用，完成后可直接生成音频。"}</small></div>` : ""}
      ${stage === "creating" && !busy ? `<p class="voice-pending-status" role="status">${escapeHtml(flow.error || "模型仍在创建这个声音，稍后检查进度即可。")}</p>${renderCreateAction("检查创建进度")}` : ""}
      ${["matched", "full", "new_identity"].includes(stage) ? `
        <h4>${stage === "new_identity" ? "参考音频已保存" : `${escapeHtml(identityName)}${stage === "full" ? " 已保留 3 条样本" : " 已有声音记录"}`}</h4>
        <p>${flow.matchType === "exact_audio" ? "该录音与已有样本完全相同，没有新增样本。" : stage === "full" && flow.matchType !== "speaker_embedding" ? "本次录音未保存。请使用已有样本，或在详情中删除不需要的样本后重新上传。" : flow.matchType === "speaker_embedding" ? `已补充为第 ${Math.max(1, 3 - Number(flow.sampleSlotsRemaining || 0))} 条样本，服务端已保存，无需再次上传。` : "参考音频已保存，无需再次上传。"}</p>
        ${recommended && isSelectableVoiceCloneProfile(recommended) ? `<button class="primary-button" data-voice-profile="${escapeHtml(recommended.id)}" data-close-voice-drawer="true" type="button">直接使用 ${escapeHtml(identityName)} 的推荐音色</button><p class="panel-note">也可以使用当前参考音频创建一个新版本。</p>` : `<p class="panel-note">该声音暂无可用版本，可以使用已保存的参考音频继续创建。</p>`}
        ${renderVoiceCloneModelPanel(context, !available)}
        ${renderCreateAction(recommended && isSelectableVoiceCloneProfile(recommended) ? "创建新版本" : "继续创建声音")}
        ${group ? `<button class="secondary-button" data-voice-group-profiles="${escapeHtml(group.group_key)}" data-voice-return-identity="${escapeHtml(identityId)}" data-close-voice-drawer="true" type="button">查看身份详情</button>` : ""}
        ${samples.map((sample) => `<div class="voice-existing-sample"><span>${escapeHtml(sample.original_filename || sample.display_name || "参考音频")}${renderVoiceAssetExpiry(sample)}</span><button class="secondary-button small" data-voice-clone-sample-select="${escapeHtml(sample.id)}" type="button" ${isVoiceAssetExpired(sample) ? "disabled" : ""}>使用此样本</button><button class="quiet-button small" data-voice-clone-sample-preview="${escapeHtml(sample.id)}" type="button" ${isVoiceAssetExpired(sample) ? "disabled" : ""}>试听已有样本</button></div>`).join("")}
      ` : ""}
      ${renderVoiceCloneSharedSamplePlayer()}
    </div>
  </aside>`;
}

function renderVoiceCloneCreationDrawer(context) {
  return renderAddVoiceFlow(state.voiceClone.addFlow, context);
}

function renderVoiceIdentityList(groups = getFilteredVoiceCloneGroups()) {
  return `
    <div class="voice-identity-list" id="voice-clone-library-list" role="list">
      ${groups.length ? groups.map((group) => {
        const profile = group.recommended_profile || {};
        const selectedProfileId = String(state.voiceClone.profileId || "");
        const selectedProfile = state.voiceClone.profiles.find((item) => item.id === selectedProfileId);
        const selected = selectedProfileId === profile.id
          || (state.voiceClone.library?.profilesByGroup?.[group.group_key] || []).some((item) => item.id === selectedProfileId)
          || Boolean(group.identity_id && selectedProfile?.identity_id === group.identity_id)
          || Boolean(selectedProfile?.group_key && selectedProfile.group_key === group.group_key);
        const selectable = isSelectableVoiceCloneProfile(profile);
        const expired = isVoiceAssetExpired(group) || isVoiceAssetExpired(profile);
        const status = expired ? "expired" : String(group.health_status || "").toLowerCase();
        const statusClass = status === "ready"
          ? "is-ready"
          : status === "creating" ? "is-creating" : ["failed", "rejected"].includes(status) ? "is-danger" : "is-muted";
        const aliases = (group.identity_aliases || [])
          .filter((alias) => alias && alias !== group.display_name)
          .join(" / ");
        const usedAt = profile.last_used_at || profile.updated_at || profile.created_at;
        return `
          <article class="voice-identity-row ${selected ? "is-active" : ""}" data-voice-identity="${escapeHtml(group.identity_id || group.group_key)}" role="listitem" ${selected ? 'aria-current="true"' : ""}>
            <button class="voice-identity-select" data-voice-profile="${escapeHtml(profile.id || "")}" type="button" aria-pressed="${selected ? "true" : "false"}" ${selectable ? "" : "disabled"}>
              <span class="voice-identity-name"><strong>${escapeHtml(group.identity_display_name || group.display_name)}</strong><small>${escapeHtml(aliases || "无别名")}</small></span>
              <span class="voice-identity-facts">
                <span class="voice-identity-status ${statusClass}">${escapeHtml(expired ? "已到期" : globalThis.SpeakerVoiceCloneLibrary?.translateStatus(group.health_status) || group.health_status)}</span>
                ${renderVoiceAssetExpiry(group.expires_at ? group : profile, "请添加声音") || `<span>${escapeHtml(usedAt ? formatDate(usedAt) : "未使用")}</span>`}
                <span>${Number(group.version_count || 0)} 版</span>
              </span>
            </button>
            <button class="quiet-button small voice-identity-more" data-voice-group-profiles="${escapeHtml(group.group_key)}" data-voice-identity-details-trigger="${escapeHtml(group.identity_id || group.group_key)}" type="button" aria-expanded="${state.voiceClone.library?.expandedGroupKey === group.group_key ? "true" : "false"}" aria-label="查看 ${escapeHtml(group.display_name)} 的声音详情">详情</button>
          </article>
        `;
      }).join("") : `<p class="empty-line">${state.voiceClone.library?.loaded ? "没有匹配的声音。" : "正在载入声音库…"}</p>`}
    </div>
  `;
}

function renderVoiceCloneLibraryPagination() {
  const library = state.voiceClone.library || {};
  return `<div id="voice-clone-library-pagination">${library.nextCursor ? `<button id="voice-clone-load-more-button" class="secondary-button voice-load-more" type="button" ${library.loading ? "disabled" : ""}>${library.loading ? "正在加载…" : "加载更多声音"}</button>` : ""}</div>`;
}

function renderVoiceIdentityRail(groups = getFilteredVoiceCloneGroups()) {
  const library = state.voiceClone.library || {};
  return `<section class="voice-identity-rail" aria-label="声音库">
    <div class="voice-identity-rail-head"><div><h3>我的声音</h3><span><strong id="voice-clone-library-total">${Number(library.total ?? groups.length)}</strong> 个声音 · 跨项目共享</span></div><div class="row-actions"><button id="voice-clone-refresh-button" class="quiet-button small" type="button">刷新</button><button id="voice-clone-new-button" class="primary-button small" type="button" ${isVoiceCloneServiceAvailable() ? "" : "disabled"}>添加声音</button></div></div>
    <label class="field-stack voice-identity-search"><span>搜索声音</span><input id="voice-clone-profile-search-input" type="search" value="${escapeHtml(state.voiceClone.profileSearch || "")}" placeholder="搜索姓名、别名" /></label>
    ${renderVoiceCloneCreatingProfiles()}
    ${renderVoiceIdentityList(groups)}
    ${renderVoiceCloneLibraryPagination()}
  </section>`;
}

function renderVoiceGenerationDesk(selectedProfile = state.voiceClone.profiles.find((profile) => profile.id === state.voiceClone.profileId)) {
  const singleUse = Boolean(state.serviceStatus.voiceClone?.discard_voice_after_download);
  const available = isVoiceCloneServiceAvailable(selectedProfile);
  const selectable = isSelectableVoiceCloneProfile(selectedProfile);
  const models = getVoiceCloneModelOptions();
  const currentModelId = state.voiceClone.pendingModelId || `${selectedProfile?.provider || state.voiceClone.provider}:${selectedProfile?.target_model || state.voiceClone.targetModel}`;
  const model = getVoiceCloneModelOption(currentModelId);
  const instruction = getVoiceInstructionMeta(selectedProfile);
  const text = state.voiceClone.previewText || "";
  const limit = Number(model?.constraints?.text_max_characters || 500);
  const activeModel = getVoiceCloneModelOption(`${selectedProfile?.provider || state.voiceClone.provider}:${selectedProfile?.target_model || state.voiceClone.targetModel}`);
  const instructionModels = models.filter((item) => item.available && item.capabilities?.instruction);
  const instructionWeight = getVoiceCloneInstructionWeight(state.voiceClone.voiceInstruction, activeModel);
  const profiles = [...new Map([...state.voiceClone.profiles, ...getVoiceCloneLibraryGroups().map((group) => group.recommended_profile)].filter((item) => item && isSelectableVoiceCloneProfile(item)).map((item) => [item.id, item])).values()];
  const canGenerate = available && selectable && text.trim() && Array.from(text).length <= limit && !state.voiceClone.busy && !state.voiceClone.pendingModelId && (!instruction.supported || instructionWeight <= instruction.maxWeight);
  return `<section class="voice-generation-desk" aria-label="声音生成工作台">
    <div class="voice-generation-body">
      <div class="voice-compose-grid">
        <div class="voice-script-editor">
          <label for="voice-clone-preview-text">要朗读的内容</label>
          <textarea id="voice-clone-preview-text" rows="4" placeholder="在这里写下想让声音说的话…">${escapeHtml(text)}</textarea>
          <div class="voice-script-footnote"><span>文案会在切换声音时保留</span><span><strong id="voice-clone-character-count">${Array.from(text).length}</strong> / ${limit} 字</span></div>
        </div>
        <aside class="voice-generation-settings" aria-label="声音与模型设置">
          <div class="voice-current-choice">
            ${singleUse ? `<span>本次参考音频</span>` : `<label class="field-stack"><span>朗读声音</span><select id="voice-clone-profile-select"><option value="" disabled ${selectable ? "" : "selected"}>选择一个声音</option>${profiles.map((profile) => `<option value="${escapeHtml(profile.id)}" ${profile.id === selectedProfile?.id ? "selected" : ""}>${escapeHtml(profile.identity_display_name || profile.voice_name)}</option>`).join("")}</select></label>`}
            <span class="voice-selected-identity ${selectedProfile ? "" : "is-empty"}">${selectedProfile ? escapeHtml(selectedProfile.identity_display_name || selectedProfile.voice_name) : singleUse ? "为本次生成上传一段参考音频" : "先选择一个声音，也可以先写文案"}</span>
            ${singleUse ? `<p class="panel-note">可反复生成多个版本试听；下载选定版本并保存成功后，清理本次参考音频与所有音频版本，下次重新上传。</p>` : renderVoiceAssetExpiry(selectedProfile, "请添加声音，文案已保留")}
            <div class="row-actions"><button id="voice-clone-new-button" class="secondary-button small" type="button" ${isVoiceCloneServiceAvailable() && !state.voiceClone.busy ? "" : "disabled"}>${singleUse ? "上传参考音频" : "添加声音"}</button>${singleUse ? "" : `<button class="quiet-button small" data-voice-clone-view="library" type="button">管理声音库</button>`}</div>
          </div>
          <label class="field-stack voice-current-model"><span>生成模型</span><select id="voice-clone-current-model-select" ${state.voiceClone.busy || isVoiceAssetExpired(selectedProfile) ? "disabled" : ""}>${models.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === currentModelId ? "selected" : ""} ${item.available ? "" : "disabled"}>${escapeHtml(item.label)}${item.recommended ? " · 推荐" : ""}${item.available ? "" : " · 暂不可用"}</option>`).join("")}</select></label>
          ${state.voiceClone.pendingModelId ? `<div class="voice-model-confirm" role="status"><p>这个声音还没有 ${escapeHtml(model?.label || "所选模型")} 版本。使用已有参考音频创建后即可切换。</p><button id="voice-clone-create-model-button" class="primary-button small" type="button" ${isVoiceCloneServiceAvailable({provider:model?.provider, target_model:model?.model}) && (selectedProfile?.source_clone_sample_id || selectedProfile?.source_sample_id) && !isVoiceAssetExpired(selectedProfile) && !state.voiceClone.busy ? "" : "disabled"}>创建并切换模型</button><button id="voice-clone-cancel-model-button" class="quiet-button small" type="button" ${state.voiceClone.busy ? "disabled" : ""}>取消</button></div>` : ""}
          <div class="voice-instruction-card">
            <label class="field-stack"><span>说话方式 <small>可选</small></span><input id="voice-clone-instruction-input" type="text" value="${escapeHtml(state.voiceClone.voiceInstruction || "")}" placeholder="${instruction.supported ? "如：自然亲切，语速稍慢" : "此复刻模型不支持语气指令"}" ${instruction.supported ? "" : "disabled"} /></label>
            ${instruction.supported ? `${renderVoiceInstructionExamples()}<small class="voice-instruction-meta"><span id="voice-clone-instruction-count">${instructionWeight}</span> / ${instruction.maxWeight} ${instruction.hanWeight === 1 ? "字符" : `· 汉字按 ${instruction.hanWeight} 计`}</small>` : `<small class="voice-instruction-meta">${escapeHtml(activeModel?.label || "当前模型")} 复刻版不支持语气指令。${instructionModels.length ? `需要指定语气，可切换 ${escapeHtml(instructionModels.map((item) => item.label).join("、"))}。` : ""}</small>`}
          </div>
        </aside>
        <div class="voice-generation-main">
          ${renderVoiceCloneCreatingProfiles()}
          ${state.voiceClone.error ? `<p class="voice-persistent-error" role="alert">${escapeHtml(state.voiceClone.error)}</p>` : ""}
          ${state.voiceClone.busy ? `<p class="voice-pending-status" role="status">${state.voiceClone.busy === "generate" ? "正在生成音频，请稍候…" : "正在创建声音，请稍候…"}</p>` : ""}
          <div class="voice-recent-records"><div class="voice-results-heading"><h4>生成记录</h4><small>当前项目 · ${state.voiceClone.records.length} 条</small></div>${renderVoiceCloneRecordsList()}</div>
        </div>
      </div>
    </div>
    <footer class="voice-generation-actions"><span>${!available ? "声音服务恢复后才能生成" : state.voiceClone.pendingModelId ? "请先确认模型切换" : selectable ? `使用 ${escapeHtml(selectedProfile.voice_name)} 朗读` : "添加或选择声音后即可生成"}</span><button id="voice-clone-preview-button" class="primary-button" type="button" ${canGenerate ? "" : "disabled"}>${state.voiceClone.busy === "generate" ? "正在生成…" : "生成音频"}</button></footer>
  </section>`;
}

function renderVoiceIdentitySamples(group) {
  const samples = state.voiceClone.library?.samplesByGroup?.[group.group_key];
  if (!Array.isArray(samples)) return `<p class="empty-line" role="status">正在加载参考音频…</p>`;
  return `<section class="voice-detail-samples"><h4>参考音频 <small>${samples.filter((sample) => !isVoiceAssetExpired(sample)).length} / 3</small></h4>${samples.map((sample) => {
    const expired = isVoiceAssetExpired(sample);
    return `<div class="voice-detail-sample"><strong>${escapeHtml(sample.original_filename || sample.display_name || "参考音频")}</strong>${renderVoiceAssetExpiry(sample)}<div class="row-actions">
      <button class="secondary-button small" data-voice-clone-sample-preview="${escapeHtml(sample.id)}" type="button" ${expired ? "disabled" : ""}>试听</button>
      <button class="secondary-button small" data-voice-clone-sample-create="${escapeHtml(sample.id)}" data-voice-group-key="${escapeHtml(group.group_key)}" type="button" ${isVoiceCloneServiceAvailable() && !expired ? "" : "disabled"}>创建声音版本</button>
      <button class="danger-button small" data-voice-clone-sample-delete="${escapeHtml(sample.id)}" data-voice-clone-sample-name="${escapeHtml(sample.original_filename || sample.display_name || "参考音频")}" type="button" aria-label="删除样本：${escapeHtml(sample.original_filename || sample.display_name || "参考音频")}">删除</button>
    </div></div>`;
  }).join("") || `<p class="empty-line">暂无参考音频。添加一段录音即可创建声音。</p>`}</section>`;
}

function renderVoiceIdentityDetails(groups = getFilteredVoiceCloneGroups()) {
  const groupKey = state.voiceClone.library?.expandedGroupKey || "";
  const group = groups.find((item) => item.group_key === groupKey);
  if (!group) return "";
  const identityKey = group.identity_id || group.group_key;
  return `
    <aside id="voice-identity-detail-drawer" class="voice-create-drawer voice-identity-detail-drawer" role="dialog" aria-modal="true" aria-labelledby="voice-identity-details-title" data-voice-identity-dialog="${escapeHtml(identityKey)}">
      <div class="voice-create-drawer-head">
        <div><p class="eyebrow">Voice details</p><h3 id="voice-identity-details-title">${escapeHtml(group.display_name)}</h3><span>来源、模型、样本与历史版本</span></div>
        <button id="voice-identity-details-close-button" class="secondary-button small" data-voice-group-profiles="${escapeHtml(group.group_key)}" data-voice-return-identity="${escapeHtml(identityKey)}" type="button">关闭</button>
      </div>
      <div class="voice-create-drawer-body">${renderVoiceIdentitySamples(group)}${renderVoiceCloneVersionList(group)}${renderVoiceCloneSharedSamplePlayer()}</div>
    </aside>
  `;
}

function renderVoiceCloneStudio({ groups = getFilteredVoiceCloneGroups(), selectedProfile = state.voiceClone.profiles.find((profile) => profile.id === state.voiceClone.profileId) } = {}) {
  const context = getVoiceCloneRenderContext();
  const singleUse = Boolean(state.serviceStatus.voiceClone?.discard_voice_after_download);
  const view = singleUse ? "generate" : state.voiceClone.view || "generate";
  if (singleUse && (!state.voiceClone.addFlow?.uploadedFile || state.voiceClone.addFlow?.createdProfileId !== selectedProfile?.id)) selectedProfile = null;
  return `<div class="voice-clone-workbench">
    <header class="voice-studio-header"><div><p class="eyebrow">VOICE STUDIO</p><h2>声音复刻</h2></div>${singleUse ? "" : `<nav class="voice-studio-tabs" aria-label="声音复刻视图">${[["generate", "生成音频"], ["library", "声音库"]].map(([id,label]) => `<button type="button" data-voice-clone-view="${id}" class="${view === id ? "is-active" : ""}" aria-pressed="${view === id}">${label}</button>`).join("")}</nav>`}${renderVoiceCloneServiceBanner()}<p class="voice-retention-note">${singleUse ? "每次上传新的参考音频，先试听比较；选定版本下载保存成功后，自动清理本次参考音频、临时音色及全部生成音频。未下载的数据最多保留3天。" : "参考音频、复刻音色、生成音频仅保留3天（创建后72小时），使用不延期。请及时下载生成音频。"}</p></header>
    <div class="voice-studio is-${view}">${view === "library" ? renderVoiceIdentityRail(groups) : renderVoiceGenerationDesk(selectedProfile)}</div>
    ${singleUse ? "" : renderVoiceIdentityDetails(getVoiceCloneLibraryGroups())}
    ${renderVoiceCloneCreationDrawer(context)}
  </div>`;
}

function renderVoiceCloneSingleTool() { return renderVoiceCloneStudio(); }
function renderVoiceCloneCreateTool() { return renderVoiceCloneStudio(); }
function renderVoiceCloneSamplesTool() { return renderVoiceCloneStudio(); }
function renderVoiceCloneRecordsTool() { return renderVoiceCloneStudio(); }


function renderVoiceCloningTool() {
  if (!String(state.currentProjectName || "").trim()) return renderVoiceCloneNoProjectTool();
  return renderVoiceCloneSingleTool();
}

function getFilteredHistoryJobs() {
  const query = String(state.historySearch || "").trim().toLowerCase();
  const jobs = getProjectJobs("all");
  if (!query) return jobs;
  return jobs.filter((job) => {
    const haystack = [
      job.id,
      job.program_name,
      job.source_export_path,
      job.source_local_path,
      job.video_source_export_path,
      job.video_source_local_path,
      statusLabel(job.status),
    ].join(" ").toLowerCase();
    return haystack.includes(query);
  });
}

function renderHistoryPage() {
  const jobs = getFilteredHistoryJobs();
  const total = getProjectJobs("all").length;
  elements.simpleWorkspace.innerHTML = `
    <section class="task-panel history-workflow">
      <div class="task-panel-head">
        <p class="eyebrow">History</p>
        <h2>历史记录</h2>
        <span>按项目查找历史任务，随时载入并重新导出 XML 或 SRT。</span>
      </div>
      <div class="history-toolbar">
        <label class="field-stack">
          <span>搜索历史</span>
          <input id="history-search-input" type="search" value="${escapeHtml(state.historySearch || "")}" placeholder="输入音频名、项目名或任务 ID" />
        </label>
        <button id="refresh-jobs-button" class="secondary-button" type="button">刷新历史</button>
      </div>
      <div class="history-list">
        ${jobs.length ? jobs.map((job) => renderHistoryJobRow(job)).join("") : `<p class="empty-line">${total ? "没有匹配的历史任务。" : "当前项目还没有历史任务。"}</p>`}
      </div>
    </section>
  `;
}

function renderHistoryJobRow(job) {
  const completed = isCompletedJob(job);
  const outputStem = resolveJobSourceStem(job);
  const frameRate = job.export_frame_rate || "25";
  return `
    <article class="history-row">
      <div class="history-row-main">
        <strong>${escapeHtml(outputStem)}${job.local_only ? " · 本机原稿" : ""}</strong>
        <span>${escapeHtml(job.program_name || "未命名项目")} · ${escapeHtml(job.id)}</span>
        <small>${escapeHtml(formatDate(job.created_at))} 创建 · ${escapeHtml(formatDate(job.updated_at))} 更新</small>
      </div>
      <span class="status-pill ${statusTone(job.status)}">${escapeHtml(statusLabel(job.status))}</span>
      <div class="history-actions">
        <button class="secondary-button small" data-job-load="${escapeHtml(job.id)}" type="button">载入</button>
        <button class="secondary-button small" data-download-path="/transcriptions/jobs/${escapeHtml(encodeURIComponent(job.id))}/premiere-xml/download?frame_rate=${escapeHtml(frameRate)}&subtitle_mode=graphicandtype" data-download-name="${escapeHtml(outputStem)}-premiere.xml" type="button" ${completed ? "" : "disabled"}>XML</button>
        <button class="secondary-button small" data-download-path="/transcriptions/jobs/${escapeHtml(encodeURIComponent(job.id))}/subtitles/download?format=srt&variant=full" data-download-name="${escapeHtml(outputStem)}-full.srt" type="button" ${completed ? "" : "disabled"}>带姓名 SRT</button>
        <button class="secondary-button small" data-download-path="/transcriptions/jobs/${escapeHtml(encodeURIComponent(job.id))}/subtitles/download?format=srt&variant=clean" data-download-name="${escapeHtml(outputStem)}-clean.srt" type="button" ${completed ? "" : "disabled"}>纯字幕 SRT</button>
      </div>
    </article>
  `;
}

function renderXmlRepairTool() {
  const assets = state.xmlRepair.assets || [];
  return `
    <div class="tool-header">
      <div><p class="eyebrow">XML Repair</p><h2>XML 返修</h2></div>
      <span>${escapeHtml(state.xmlRepair.status || "选择 XML 后开始")}</span>
    </div>
    <div class="flat-panel xml-tool">
      <input id="xml-repair-file-input" class="visually-hidden" type="file" accept=".xml,application/xml,text/xml" />
      <input id="xml-repair-images-input" class="visually-hidden" type="file" multiple accept="image/png" />
      <div class="task-actions">
        <button id="pick-xml-repair-file-button" class="secondary-button" type="button">选择旧 XML</button>
        <button id="pick-xml-repair-images-button" class="secondary-button" type="button">选择新版 PNG</button>
        <button id="export-xml-repair-button" class="primary-button" type="button" ${assets.length ? "" : "disabled"}>导出新 XML</button>
      </div>
      <div class="xml-asset-list">
        ${assets.length ? assets.map((asset) => `
          <div class="xml-asset-row">
            <div>
              <strong>${escapeHtml(asset.oldName)}</strong>
              <span>${escapeHtml(asset.oldPath || asset.oldPathUrl || "")}</span>
            </div>
            <span class="${asset.replacementPath ? "match-ok" : "match-miss"}">${escapeHtml(asset.replacementName || "未匹配")}</span>
          </div>
        `).join("") : `<p class="empty-line">选择 Premiere XML 后显示 PNG 资源。</p>`}
      </div>
    </div>
  `;
}

function renderClientUpdateTool() {
  const update = state.clientUpdate || {};
  const hasUpdate = Boolean(update.available && update.version);
  return `
    <div class="tool-header">
      <div><p class="eyebrow">Client Update</p><h2>客户端更新</h2></div>
      <span>${escapeHtml(APP_VERSION)} · ${escapeHtml(UPDATE_CHANNEL)}</span>
    </div>
    <div class="flat-panel client-update-panel ${hasUpdate ? "has-update" : ""}">
      <div>
        <strong>${escapeHtml(hasUpdate ? `发现新版本 ${update.version}` : "当前客户端")}</strong>
        <span>${escapeHtml(update.message || "可检查局域网内是否有新客户端版本。")}</span>
        ${update.notes ? `<p>${escapeHtml(update.notes)}</p>` : ""}
        ${update.lastCheckedAt ? `<small>上次检查：${escapeHtml(update.lastCheckedAt)}</small>` : ""}
      </div>
      <div class="task-actions">
        <button id="check-client-update-button" class="secondary-button" type="button" ${update.checking || update.installing ? "disabled" : ""}>${update.checking ? "检查中..." : "检查更新"}</button>
        <button id="install-client-update-button" class="primary-button" type="button" ${hasUpdate && !update.installing ? "" : "disabled"}>${update.installing ? "正在更新..." : "立即更新"}</button>
        <button id="dismiss-client-update-button" class="quiet-button" type="button" ${hasUpdate && !update.installing ? "" : "disabled"}>稍后</button>
      </div>
    </div>
  `;
}

function renderLogsTool() {
  const health = state.serviceStatus.health;
  const config = state.serviceStatus.config;
  return `
    <div class="tool-header">
      <div><p class="eyebrow">Diagnostics</p><h2>日志与状态</h2></div>
      <button id="refresh-status-button" class="secondary-button" type="button">刷新状态</button>
    </div>
    <div class="status-matrix">
      <div><strong>API</strong><span>${escapeHtml(state.apiBase)}</span></div>
      <div><strong>健康检查</strong><span>${escapeHtml(health?.status || health?.ok || "未知")}</span></div>
      <div><strong>配置</strong><span>${escapeHtml(config?.environment || config?.status || "未知")}</span></div>
      <div><strong>构建</strong><span>${escapeHtml(BUILD_MARKER)}</span></div>
    </div>
    <div class="flat-panel">
      <div class="task-actions">
        <button id="refresh-client-logs-button" class="secondary-button" type="button">读取日志</button>
        <button id="download-client-logs-button" class="secondary-button" type="button">导出日志</button>
        <button id="clear-client-logs-button" class="danger-button" type="button">清空日志</button>
      </div>
      <pre class="log-viewer">${escapeHtml(state.clientLogs.content || "日志默认隐藏，需要时再读取。")}</pre>
    </div>
  `;
}

function renderSyncOverlay() {
  if (!elements.syncProgressOverlay) return;
  const progress = state.syncProgress || {};
  const inlineTaskProgress = state.jobDetail && (
    isTranscriptJob(state.jobDetail)
    || (
      normalizeJobKind(state.jobDetail) === "transcription"
      && String(state.jobDetail.subtitle_strategy || "").toLowerCase() === "evidence"
    )
  );
  const active = Boolean(progress.active) && !inlineTaskProgress;
  elements.syncProgressOverlay.hidden = !active;
  elements.syncProgressOverlay.classList.toggle("is-active", active);
  elements.syncProgressTitle.textContent = progress.phase === "completed" ? "任务已完成" : "后台处理任务";
  elements.syncProgressMessage.textContent = progress.message || "正在同步任务。";
  elements.syncProgressAttempts.textContent = progress.phase === "retrying"
    ? `自动重试 ${Number(progress.retryAttempt || 0)}/${Number(progress.maxRetries || 12)}`
    : progress.phase === "completed" ? "已完成" : "状态返回后自动更新";
}

function renderToast() {
  if (!elements.downloadToast) return;
  elements.downloadToast.hidden = !state.toast.visible;
  elements.downloadToast.textContent = state.toast.message || "";
  elements.downloadToast.dataset.tone = state.toast.tone || "neutral";
  elements.downloadToast.classList.toggle("is-visible", state.toast.visible);
}

function renderClientUpdateNotice() {
  if (!elements.clientUpdateNotice) return;
  const update = state.clientUpdate || {};
  const visible = Boolean(update.noticeVisible && update.available && update.version);
  elements.clientUpdateNotice.hidden = !visible;
  elements.clientUpdateNotice.classList.toggle("is-visible", visible);
  if (!visible) {
    elements.clientUpdateNotice.innerHTML = "";
    return;
  }
  elements.clientUpdateNotice.innerHTML = `
    <div>
      <strong>发现新版本 ${escapeHtml(update.version)}</strong>
      <span>${escapeHtml(update.installing ? "正在下载并安装，完成后会自动重启。" : "点击立即更新后，客户端会自动下载、安装并重启。")}</span>
      ${update.notes ? `<small>${escapeHtml(update.notes)}</small>` : ""}
    </div>
    <div class="client-update-notice-actions">
      <button class="primary-button small" type="button" data-client-update-action="install" ${update.installing ? "disabled" : ""}>${update.installing ? "正在更新..." : "立即更新"}</button>
      <button class="quiet-button small" type="button" data-client-update-action="dismiss" ${update.installing ? "disabled" : ""}>稍后</button>
    </div>
  `;
}


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


window.addEventListener("load", async () => {
  document.body.dataset.mode = "simple";
  bindEvents();
  renderAll();
  await withUiTask(null, "启动中...", async () => {
    await globalThis.SpeakerLocalRecognition?.initialize();
    await initializeAppData();
    state.activeStep = deriveActiveStep();
    await recordClientLog("boot", "工作台已就绪", {
      project_name: state.currentProjectName,
      job_id: state.currentJobId,
      api_base: state.apiBase,
      build_marker: BUILD_MARKER,
    });
    renderAll();
  });
  window.setTimeout(() => {
    checkClientUpdate({ silent: true }).catch(() => {});
  }, 4500);
  window.setInterval(() => {
    refreshActiveProcessingJob().catch(() => {});
  }, 5000);
});

window.addEventListener("focus", () => {
  refreshActiveProcessingJob().catch(() => {});
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    refreshActiveProcessingJob().catch(() => {});
  }
});

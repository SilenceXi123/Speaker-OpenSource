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

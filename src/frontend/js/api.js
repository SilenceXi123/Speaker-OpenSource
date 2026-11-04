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

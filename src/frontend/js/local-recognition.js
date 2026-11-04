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

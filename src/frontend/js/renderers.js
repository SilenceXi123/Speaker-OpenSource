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

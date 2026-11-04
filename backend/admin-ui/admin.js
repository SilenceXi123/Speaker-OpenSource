const state = {
  reportDate: getTodayDate(),
  currentUser: "",
  identityReviews: [],
  clientUpdates: [],
  storageCleanup: null,
  providerCosts: null,
  providerCostReadiness: null,
  projectOptions: [],
  priceCatalog: [],
  knowledgeSummary: null,
  knowledgePassages: null,
  knowledgeSelected: new Set(),
  knowledgeSelectionScope: "items",
  knowledgeActiveId: "",
  knowledgePage: 1,
  activeView: "ledger",
};

const elements = {
  loginSection: document.querySelector("#login-section"),
  dashboardSection: document.querySelector("#dashboard-section"),
  logoutButton: document.querySelector("#logout-button"),
  loginForm: document.querySelector("#login-form"),
  loginUsername: document.querySelector("#login-username"),
  loginPassword: document.querySelector("#login-password"),
  loginMessage: document.querySelector("#login-message"),
  monthInput: document.querySelector("#month-input"),
  monthField: document.querySelector("#month-field"),
  sessionIndicator: document.querySelector("#session-indicator"),
  activeViewTitle: document.querySelector("#active-view-title"),
  viewButtons: [...document.querySelectorAll("[data-admin-view]")],
  viewPanels: [...document.querySelectorAll("[data-view-panel]")],
  refreshSummaryButton: document.querySelector("#refresh-summary-button"),
  providerCostFilterForm: document.querySelector("#provider-cost-filter-form"),
  providerCostDateFromInput: document.querySelector("#provider-cost-date-from-input"),
  providerCostDateToInput: document.querySelector("#provider-cost-date-to-input"),
  providerCostProjectInput: document.querySelector("#provider-cost-project-input"),
  providerCostJobInput: document.querySelector("#provider-cost-job-input"),
  providerCostProviderInput: document.querySelector("#provider-cost-provider-input"),
  providerCostModelInput: document.querySelector("#provider-cost-model-input"),
  providerCostMessage: document.querySelector("#provider-cost-message"),
  providerCostReadiness: document.querySelector("#provider-cost-readiness"),
  providerCostSummary: document.querySelector("#provider-cost-summary"),
  providerCostTable: document.querySelector("#provider-cost-table"),
  tokenTrendChart: document.querySelector("#token-trend-chart"),
  tokenCompositionChart: document.querySelector("#token-composition-chart"),
  projectTokenChart: document.querySelector("#project-token-chart"),
  exportProviderCostsButton: document.querySelector("#export-provider-costs-button"),
  priceCatalogForm: document.querySelector("#price-catalog-form"),
  priceProviderInput: document.querySelector("#price-provider-input"),
  priceModelInput: document.querySelector("#price-model-input"),
  priceOperationInput: document.querySelector("#price-operation-input"),
  priceMeterInput: document.querySelector("#price-meter-input"),
  priceEffectiveFromInput: document.querySelector("#price-effective-from-input"),
  priceEffectiveToInput: document.querySelector("#price-effective-to-input"),
  priceBillingUnitInput: document.querySelector("#price-billing-unit-input"),
  priceUnitMicrounitsInput: document.querySelector("#price-unit-microunits-input"),
  priceCurrencyInput: document.querySelector("#price-currency-input"),
  priceSourceNoteInput: document.querySelector("#price-source-note-input"),
  priceCatalogMessage: document.querySelector("#price-catalog-message"),
  priceCatalogList: document.querySelector("#price-catalog-list"),
  refreshPriceCatalogButton: document.querySelector("#refresh-price-catalog-button"),
  refreshIdentityReviewsButton: document.querySelector("#refresh-identity-reviews-button"),
  identityReviewMessage: document.querySelector("#identity-review-message"),
  identityReviewPlayer: document.querySelector("#identity-review-player"),
  identityReviewList: document.querySelector("#identity-review-list"),
  clientUpdateForm: document.querySelector("#client-update-form"),
  clientUpdateManualForm: document.querySelector("#client-update-manual-form"),
  clientUpdateChannelInput: document.querySelector("#client-update-channel-input"),
  clientUpdateVersionInput: document.querySelector("#client-update-version-input"),
  clientUpdateTargetInput: document.querySelector("#client-update-target-input"),
  clientUpdateArchInput: document.querySelector("#client-update-arch-input"),
  clientUpdatePackageFileInput: document.querySelector("#client-update-package-file-input"),
  clientUpdateSignatureFileInput: document.querySelector("#client-update-signature-file-input"),
  clientUpdatePackageInput: document.querySelector("#client-update-package-input"),
  clientUpdateSignatureInput: document.querySelector("#client-update-signature-input"),
  clientUpdateNotesInput: document.querySelector("#client-update-notes-input"),
  clientUpdateMessage: document.querySelector("#client-update-message"),
  clientUpdateList: document.querySelector("#client-update-list"),
  refreshClientUpdatesButton: document.querySelector("#refresh-client-updates-button"),
  previewStorageCleanupButton: document.querySelector("#preview-storage-cleanup-button"),
  runStorageCleanupButton: document.querySelector("#run-storage-cleanup-button"),
  storageCleanupMessage: document.querySelector("#storage-cleanup-message"),
  storageCleanupResult: document.querySelector("#storage-cleanup-result"),
  knowledgeMetrics: document.querySelector("#knowledge-metrics"),
  knowledgeSummaryMessage: document.querySelector("#knowledge-summary-message"),
  knowledgeReviewChart: document.querySelector("#knowledge-review-chart"),
  knowledgeDynastyChart: document.querySelector("#knowledge-dynasty-chart"),
  knowledgeImportBatches: document.querySelector("#knowledge-import-batches"),
  knowledgeFilterForm: document.querySelector("#knowledge-filter-form"),
  knowledgeSearchInput: document.querySelector("#knowledge-search-input"),
  knowledgeStatusFilter: document.querySelector("#knowledge-status-filter"),
  knowledgeDynastyFilter: document.querySelector("#knowledge-dynasty-filter"),
  knowledgeGenreFilter: document.querySelector("#knowledge-genre-filter"),
  knowledgeSelectionCount: document.querySelector("#knowledge-selection-count"),
  knowledgeSelectPage: document.querySelector("#knowledge-select-page"),
  knowledgeSelectAll: document.querySelector("#knowledge-select-all"),
  knowledgeClearSelection: document.querySelector("#knowledge-clear-selection"),
  knowledgeBulkApprove: document.querySelector("#knowledge-bulk-approve"),
  knowledgeBulkReject: document.querySelector("#knowledge-bulk-reject"),
  knowledgeResults: document.querySelector("#knowledge-results"),
  knowledgePagination: document.querySelector("#knowledge-pagination"),
  knowledgeDetail: document.querySelector("#knowledge-detail"),
};

let knowledgeSearchTimer = null;

function getTodayDate() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function getDailyDateRange(reportDate = state.reportDate) {
  const normalized = String(reportDate || getTodayDate());
  return { dateFrom: normalized, dateTo: normalized };
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatNumber(value, digits = 2) {
  const numeric = Number(value || 0);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : "0";
}

function formatInteger(value, empty = "0") {
  if (value == null || value === "") return empty;
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(numeric)
    : empty;
}

function formatDuration(value, empty = "0秒") {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return empty;
  const totalSeconds = Math.round(numeric);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours}小时 ${String(minutes).padStart(2, "0")}分 ${String(seconds).padStart(2, "0")}秒`;
  if (minutes) return `${minutes}分 ${String(seconds).padStart(2, "0")}秒`;
  return `${seconds}秒`;
}

function formatPercent(numerator, denominator) {
  if (!Number(denominator)) return "0%";
  return `${formatNumber((Number(numerator) / Number(denominator)) * 100, 1)}%`;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = bytes;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(unitIndex === 0 ? 0 : 2)} ${units[unitIndex]}`;
}

function formatDate(value) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "--" : date.toLocaleString("zh-CN", { hour12: false });
}

function safeExternalUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch (_error) {
    return "#";
  }
}

function formatMicrounitAmounts(amounts) {
  const entries = Object.entries(amounts || {}).sort(([left], [right]) => left.localeCompare(right));
  if (!entries.length) return "--";
  return entries
    .map(([currency, amount]) => `${currency} ${formatNumber(Number(amount) / 1_000_000, 6)}`)
    .join(" / ");
}

async function request(path, options = {}) {
  const response = await fetch(`/api/v1/admin${path}`, { credentials: "include", ...options });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch (_error) {}
    throw new Error(message);
  }
  const contentType = response.headers.get("content-type") || "";
  return contentType.includes("application/json") ? response.json() : response.text();
}

function buildProviderCostQuery() {
  const params = new URLSearchParams();
  const values = {
    date_from: elements.providerCostDateFromInput.value,
    date_to: elements.providerCostDateToInput.value,
    project_id: elements.providerCostProjectInput.value,
    job_id: elements.providerCostJobInput.value.trim(),
    provider: elements.providerCostProviderInput.value.trim(),
    model: elements.providerCostModelInput.value.trim(),
  };
  Object.entries(values).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return params.toString();
}

function setLoginState(authenticated) {
  elements.loginSection.classList.toggle("hidden", authenticated);
  elements.dashboardSection.classList.toggle("hidden", !authenticated);
  elements.logoutButton.classList.toggle("hidden", !authenticated);
}

function setActiveView(view) {
  const titles = { ledger: "AI 用量总览", catalog: "价格目录", knowledge: "知识库", operations: "系统维护" };
  state.activeView = titles[view] ? view : "ledger";
  elements.viewButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.adminView === state.activeView);
  });
  elements.viewPanels.forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.viewPanel !== state.activeView);
  });
  elements.activeViewTitle.textContent = titles[state.activeView];
  elements.monthField.classList.toggle("hidden", state.activeView !== "ledger");
  elements.refreshSummaryButton.textContent = state.activeView === "knowledge" ? "刷新知识库" : "刷新数据";
}

function renderProjectOptions() {
  const selected = elements.providerCostProjectInput.value;
  elements.providerCostProjectInput.innerHTML = [
    '<option value="">全部项目</option>',
    ...state.projectOptions.map((project) => (
      `<option value="${escapeHtml(project.project_id)}">${escapeHtml(project.project_name)}</option>`
    )),
  ].join("");
  if ([...elements.providerCostProjectInput.options].some((option) => option.value === selected)) {
    elements.providerCostProjectInput.value = selected;
  }
}

function renderTokenTrend() {
  const rows = state.providerCosts?.daily || [];
  const maximum = Math.max(0, ...rows.map((row) => Number(row.total_tokens || 0)));
  if (!rows.length || maximum === 0) {
    elements.tokenTrendChart.innerHTML = '<div class="chart-empty">当前范围没有实测 Token。</div>';
    return;
  }
  const labelEvery = Math.max(1, Math.ceil(rows.length / 7));
  elements.tokenTrendChart.innerHTML = `
    <div class="trend-scale"><span>峰值 ${escapeHtml(formatInteger(maximum))}</span><span>0</span></div>
    <div class="trend-bars">
      ${rows.map((row, index) => {
        const prompt = Number(row.prompt_tokens || 0);
        const completion = Number(row.completion_tokens || 0);
        const total = Number(row.total_tokens || 0);
        const unclassified = Math.max(0, total - prompt - completion);
        const columnHeight = total > 0 ? Math.max(2, (total / maximum) * 100) : 0;
        const promptShare = total > 0 ? (prompt / total) * 100 : 0;
        const completionShare = total > 0 ? (completion / total) * 100 : 0;
        const unclassifiedShare = total > 0 ? (unclassified / total) * 100 : 0;
        const showLabel = index % labelEvery === 0 || index === rows.length - 1;
        const title = `${row.usage_date}｜总 ${formatInteger(total)}｜输入 ${formatInteger(prompt)}｜输出 ${formatInteger(completion)}${unclassified ? `｜未拆分 ${formatInteger(unclassified)}` : ""}`;
        return `
          <div class="trend-column" title="${escapeHtml(title)}">
            <div class="trend-plot">
              <div class="trend-stack" style="height:${columnHeight.toFixed(2)}%">
                <i class="trend-unclassified" style="height:${unclassifiedShare.toFixed(2)}%"></i>
                <i class="trend-output" style="height:${completionShare.toFixed(2)}%"></i>
                <i class="trend-input" style="height:${promptShare.toFixed(2)}%"></i>
              </div>
            </div>
            <span>${showLabel ? escapeHtml(row.usage_date.slice(5)) : ""}</span>
          </div>`;
      }).join("")}
    </div>`;
}

function renderTokenComposition() {
  const report = state.providerCosts;
  const prompt = Number(report?.prompt_tokens || 0);
  const completion = Number(report?.completion_tokens || 0);
  const reportedTotal = Number(report?.total_tokens || 0);
  const combined = Math.max(reportedTotal, prompt + completion);
  const unclassified = Math.max(0, reportedTotal - prompt - completion);
  if (!reportedTotal && !prompt && !completion) {
    elements.tokenCompositionChart.innerHTML = '<div class="chart-empty">当前范围没有实测 Token。</div>';
    return;
  }
  const promptPercent = (prompt / combined) * 100;
  const completionPercent = (completion / combined) * 100;
  const unclassifiedPercent = (unclassified / combined) * 100;
  elements.tokenCompositionChart.innerHTML = `
    <div class="composition-total"><span>总计</span><strong>${escapeHtml(formatInteger(report.total_tokens))}</strong></div>
    <div class="composition-bar" aria-label="输入 ${escapeHtml(formatPercent(prompt, combined))}，输出 ${escapeHtml(formatPercent(completion, combined))}">
      <i class="composition-input" style="width:${promptPercent.toFixed(2)}%"></i>
      <i class="composition-output" style="width:${completionPercent.toFixed(2)}%"></i>
      <i class="composition-unclassified" style="width:${unclassifiedPercent.toFixed(2)}%"></i>
    </div>
    <div class="composition-rows">
      <div><span><i class="legend-input"></i>输入</span><strong>${escapeHtml(formatInteger(prompt))}</strong><small>${escapeHtml(formatPercent(prompt, combined))}</small></div>
      <div><span><i class="legend-output"></i>输出</span><strong>${escapeHtml(formatInteger(completion))}</strong><small>${escapeHtml(formatPercent(completion, combined))}</small></div>
      ${unclassified ? `<div><span><i class="legend-unclassified"></i>未拆分</span><strong>${escapeHtml(formatInteger(unclassified))}</strong><small>${escapeHtml(formatPercent(unclassified, combined))}</small></div>` : ""}
    </div>`;
}

function renderProjectTokenChart() {
  const projects = state.providerCosts?.projects || [];
  const positive = projects.filter((project) => (
    Number(project.total_tokens || 0) > 0 || Number(project.audio_seconds || 0) > 0
  )).slice(0, 12);
  if (!positive.length) {
    elements.projectTokenChart.innerHTML = '<div class="chart-empty">当前范围没有可展示的项目用量。</div>';
    return;
  }
  const maximumTokens = Math.max(0, ...positive.map((project) => Number(project.total_tokens || 0)));
  const maximumAudio = Math.max(0, ...positive.map((project) => Number(project.audio_seconds || 0)));
  elements.projectTokenChart.innerHTML = positive.map((project, index) => `
    <button class="project-token-row" type="button" data-project-token-id="${escapeHtml(project.project_id)}">
      <span class="project-token-rank">${String(index + 1).padStart(2, "0")}</span>
      <span class="project-token-name"><strong>${escapeHtml(project.project_name)}</strong><small>${escapeHtml(project.project_id)}</small></span>
      <span class="project-usage-tracks">
        <span class="project-usage-track-row"><small>Token</small><span class="project-token-track"><i style="width:${maximumTokens ? ((Number(project.total_tokens || 0) / maximumTokens) * 100).toFixed(2) : 0}%"></i></span></span>
        <span class="project-usage-track-row"><small>ASR</small><span class="project-token-track audio"><i style="width:${maximumAudio ? ((Number(project.audio_seconds || 0) / maximumAudio) * 100).toFixed(2) : 0}%"></i></span></span>
      </span>
      <span class="project-token-value"><strong>${escapeHtml(formatInteger(project.total_tokens))} Token</strong><small>${escapeHtml(formatDuration(project.audio_seconds))} Fun-ASR</small></span>
    </button>
  `).join("");
}

function renderCallUsage(call) {
  const audioSeconds = Number(call.audio_seconds || 0);
  if (audioSeconds > 0) {
    return `<strong>${escapeHtml(formatDuration(audioSeconds))}</strong><br /><span class="summary-meta">${escapeHtml(formatNumber(audioSeconds, 3))} 秒 · 不计入 Token</span>`;
  }
  if (call.total_tokens != null) {
    return `<strong>${escapeHtml(formatInteger(call.total_tokens))} Token</strong><br /><span class="summary-meta">输入 ${escapeHtml(formatInteger(call.prompt_tokens, "--"))} · 输出 ${escapeHtml(formatInteger(call.completion_tokens, "--"))}</span><br /><span class="summary-meta">缓存命中 ${escapeHtml(formatInteger(call.prompt_cache_hit_tokens, "--"))}</span>`;
  }
  return '<strong>--</strong><br /><span class="summary-meta">本次调用没有可展示计量</span>';
}

function renderProviderCosts() {
  const report = state.providerCosts;
  if (!report) {
    elements.providerCostSummary.innerHTML = "";
    elements.providerCostTable.className = "empty-state";
    elements.providerCostTable.textContent = "尚未加载 AI 服务用量。";
    renderTokenTrend();
    renderTokenComposition();
    renderProjectTokenChart();
    return;
  }
  const summary = report.summary || {};
  const nonTokenCalls = Math.max(0, Number(summary.call_count || 0) - Number(report.token_call_count || 0));
  const audioCalls = (report.tasks || []).reduce((count, task) => (
    count + (task.calls || []).filter((call) => Number(call.audio_seconds || 0) > 0).length
  ), 0);
  const stats = [
    { label: "总 Token", value: formatInteger(report.total_tokens), foot: "服务商响应实测汇总", primary: true },
    { label: "Fun-ASR 时长", value: formatDuration(report.audio_seconds), foot: `${formatInteger(audioCalls)} 次音频提交 · 不计入 Token`, audio: true },
    { label: "输入 Token", value: formatInteger(report.prompt_tokens), foot: `缓存命中 ${formatInteger(report.prompt_cache_hit_tokens)}` },
    { label: "输出 Token", value: formatInteger(report.completion_tokens), foot: `占输入输出 ${formatPercent(report.completion_tokens, Number(report.prompt_tokens || 0) + Number(report.completion_tokens || 0))}` },
    { label: "服务调用", value: formatInteger(summary.call_count), foot: nonTokenCalls ? `${formatInteger(report.token_call_count)} 次含 Token · ${formatInteger(nonTokenCalls)} 次其他计量` : "当前调用均返回 Token" },
  ];
  elements.providerCostSummary.innerHTML = stats.map((item) => `
    <div class="metric-card${item.primary ? " primary" : ""}${item.audio ? " audio" : ""}">
      <strong class="metric-label">${escapeHtml(item.label)}</strong>
      <span class="metric-value">${escapeHtml(item.value)}</span>
      <span class="metric-foot">${escapeHtml(item.foot)}</span>
    </div>
  `).join("");
  renderTokenTrend();
  renderTokenComposition();
  renderProjectTokenChart();

  const tasks = report.tasks || [];
  if (!tasks.length) {
    elements.providerCostTable.className = "empty-state";
    elements.providerCostTable.textContent = "当前筛选范围没有 Provider 调用记录。";
    return;
  }
  elements.providerCostTable.className = "detail-list";
  elements.providerCostTable.innerHTML = tasks.map((task) => `
    <details class="call-group">
      <summary>
        <div class="call-title">
          <strong>${escapeHtml(task.project_name)}</strong>
          <span>${escapeHtml(task.job_id || "未绑定任务")} · ${escapeHtml(task.call_count)} 次调用</span>
        </div>
        <div class="call-summary-item"><strong>${escapeHtml(task.token_call_count ? formatInteger(task.total_tokens) : "--")}</strong><span>总 Token</span></div>
        <div class="call-summary-item"><strong>${escapeHtml(formatDuration(task.audio_seconds, "--"))}</strong><span>Fun-ASR 时长</span></div>
        <div class="call-summary-item"><strong>${escapeHtml(formatMicrounitAmounts(task.catalog_estimated_amounts_by_currency))}</strong><span>目录价金额</span></div>
      </summary>
      <div class="call-table-wrap">
        <table>
          <thead><tr><th>Provider / 模型</th><th>操作 / 状态</th><th>实测用量</th><th>目录价金额</th><th>计量质量</th><th>开始时间 / 耗时</th></tr></thead>
          <tbody>
            ${(task.calls || []).map((call) => `
              <tr>
                <td><strong>${escapeHtml(call.provider)}</strong><br /><span class="summary-meta">${escapeHtml(call.model)} · ${escapeHtml(call.id)}</span></td>
                <td>${escapeHtml(call.operation)}<br /><span class="summary-meta">${escapeHtml(call.status)}</span></td>
                <td>${renderCallUsage(call)}</td>
                <td>${escapeHtml(formatMicrounitAmounts(call.catalog_estimated_amounts_by_currency))}</td>
                <td><span class="pill measured">实测 ${escapeHtml(call.measured_event_count)}</span>${call.unmetered_event_count ? `<span class="pill unmeasured">未计价 ${escapeHtml(call.unmetered_event_count)}</span>` : ""}</td>
                <td>${escapeHtml(formatDate(call.started_at))}<br /><span class="summary-meta">${escapeHtml(call.duration_ms == null ? "--" : `${call.duration_ms} ms`)}</span></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </details>
  `).join("");
}

function renderProviderCostReadiness() {
  const readiness = state.providerCostReadiness;
  if (!readiness) {
    elements.providerCostReadiness.className = "readiness-strip";
    elements.providerCostReadiness.textContent = "Token 统计状态不可用。";
    return;
  }
  const ready = readiness.enabled && readiness.schema_ready;
  elements.providerCostReadiness.className = ready ? "readiness-strip ready" : "readiness-strip";
  elements.providerCostReadiness.innerHTML = `
    <strong>${escapeHtml(ready ? "AI 用量采集正常" : `采集未就绪：${readiness.status}`)}</strong>
    <div class="readiness-grid">
      <span>累计调用 ${escapeHtml(formatInteger(readiness.provider_call_count))}</span>
      <span>含 Token ${escapeHtml(formatInteger(readiness.token_call_count))}</span>
      <span>累计 Token ${escapeHtml(formatInteger(readiness.total_tokens))}</span>
      <span>价格覆盖 ${escapeHtml(readiness.matching_price_count)}/${escapeHtml(readiness.expected_price_count)}</span>
    </div>`;
}

function renderPriceCatalog() {
  const rows = state.priceCatalog || [];
  if (!rows.length) {
    elements.priceCatalogList.className = "empty-state";
    elements.priceCatalogList.textContent = "当前还没有价格目录版本。";
    return;
  }
  elements.priceCatalogList.className = "summary-table";
  elements.priceCatalogList.innerHTML = `
    <table><thead><tr><th>Provider / 模型</th><th>操作 / 计量项</th><th>有效期</th><th>计价份量</th><th>目录单价</th><th>来源备注</th></tr></thead>
      <tbody>${rows.map((row) => `
        <tr>
          <td><strong>${escapeHtml(row.provider)}</strong><br /><span class="summary-meta">${escapeHtml(row.model)}</span></td>
          <td>${escapeHtml(row.operation)}<br /><span class="summary-meta">${escapeHtml(row.meter)}</span></td>
          <td>${escapeHtml(formatDate(row.effective_from))}<br /><span class="summary-meta">至 ${escapeHtml(row.effective_to ? formatDate(row.effective_to) : "持续有效")}</span></td>
          <td>${escapeHtml(row.billing_unit_size)}</td>
          <td>${escapeHtml(row.currency)} ${escapeHtml(formatNumber(Number(row.unit_price_microunits) / 1_000_000, 6))}</td>
          <td>${escapeHtml(row.source_note || "--")}</td>
        </tr>`).join("")}</tbody>
    </table>`;
}

function renderClientUpdates() {
  const rows = state.clientUpdates || [];
  if (!rows.length) {
    elements.clientUpdateList.className = "empty-state";
    elements.clientUpdateList.textContent = "还没有登记客户端更新版本。";
    return;
  }
  elements.clientUpdateList.className = "client-update-list";
  elements.clientUpdateList.innerHTML = rows.map((row) => `
    <div class="client-update-row"><div><strong>${escapeHtml(row.channel)}</strong><span>latest: ${escapeHtml(row.latest || "--")} · ${escapeHtml(row.version_count)} 个版本</span></div></div>
  `).join("");
}

function renderIdentityReviews() {
  const rows = state.identityReviews || [];
  if (!rows.length) {
    elements.identityReviewList.className = "empty-state";
    elements.identityReviewList.textContent = "当前没有待审核的声音身份。";
    return;
  }
  elements.identityReviewList.className = "identity-review-list";
  elements.identityReviewList.innerHTML = rows.map((row) => `
    <article class="identity-review-row">
      <div>
        <strong>${escapeHtml(row.submitted_name)}</strong>
        <span>${escapeHtml(row.original_filename)} · 匹配于 ${escapeHtml(formatDate(row.created_at))}</span>
      </div>
      <div class="identity-review-candidate">
        <small>候选身份</small>
        <strong>${escapeHtml(row.candidate_display_name)}</strong>
        <span>别名：${escapeHtml((row.candidate_aliases || []).join(" / ") || "--")}</span>
        <span>相似度 ${escapeHtml(formatPercent(row.match_score, 1))} · ${escapeHtml(row.embedding_model_id || "模型未知")}</span>
      </div>
      <div class="button-row tight-row">
        <button class="secondary-button" type="button" data-identity-review-audio="${escapeHtml(row.sample_audio_url)}">试听新样本</button>
        <button class="secondary-button" type="button" data-identity-review-audio="${escapeHtml(row.candidate_sample_audio_url || "")}"${row.candidate_sample_audio_url ? "" : " disabled"}>试听候选样本</button>
        <button type="button" data-identity-review-action="confirm" data-review-id="${escapeHtml(row.id)}">确认同一人</button>
        <button class="danger-button" type="button" data-identity-review-action="release" data-review-id="${escapeHtml(row.id)}">判定不同人</button>
      </div>
    </article>
  `).join("");
}

function renderStorageCleanup() {
  const report = state.storageCleanup;
  if (!report) {
    elements.storageCleanupResult.className = "empty-state";
    elements.storageCleanupResult.textContent = "尚未扫描存储清理项。";
    return;
  }
  const items = report.items || [];
  const previewText = report.dry_run ? "预计可清理" : "本次已清理";
  const bytes = report.dry_run ? report.total_bytes : report.deleted_bytes;
  const count = report.dry_run ? report.item_count : report.deleted_count;
  if (!items.length) {
    elements.storageCleanupResult.className = "empty-state";
    elements.storageCleanupResult.textContent = `${previewText} 0 个文件，0 B。`;
    return;
  }
  const visibleItems = items.slice(0, 20);
  elements.storageCleanupResult.className = "cleanup-list";
  elements.storageCleanupResult.innerHTML = `
    <div class="cleanup-summary"><strong>${escapeHtml(previewText)} ${escapeHtml(count)} 个文件</strong><span>${escapeHtml(formatBytes(bytes))}</span></div>
    <div class="cleanup-items">${visibleItems.map((item) => `
      <div class="cleanup-row"><div><strong>${escapeHtml(formatBytes(item.bytes))}</strong><span>${escapeHtml(item.reason)}${item.job_id ? ` · ${escapeHtml(item.job_id)}` : ""}</span></div><code>${escapeHtml(item.path)}</code></div>
    `).join("")}</div>`;
}

async function refreshProviderCosts() {
  elements.providerCostMessage.textContent = "正在读取 AI 服务实测用量...";
  try {
    [state.providerCosts, state.providerCostReadiness] = await Promise.all([
      request(`/provider-costs?${buildProviderCostQuery()}`),
      request("/provider-costs/readiness"),
    ]);
    if (!elements.providerCostProjectInput.value) {
      state.projectOptions = state.providerCosts.projects || [];
      renderProjectOptions();
    }
    const totalCalls = Number(state.providerCosts.summary?.call_count || 0);
    const tokenCalls = Number(state.providerCosts.token_call_count || 0);
    const nonTokenCalls = Math.max(0, totalCalls - tokenCalls);
    elements.providerCostMessage.textContent = Number(state.providerCosts.audio_seconds || 0) > 0
      ? `查询完成：${formatInteger(tokenCalls)} 次调用含 Token；Fun-ASR 音频合计 ${formatDuration(state.providerCosts.audio_seconds)}。`
      : nonTokenCalls
        ? `查询完成：${formatInteger(tokenCalls)} 次调用含 Token；另 ${formatInteger(nonTokenCalls)} 次为其他计量。`
      : `查询完成：${formatInteger(tokenCalls)} 次调用返回 Token。`;
  } catch (error) {
    state.providerCosts = null;
    state.providerCostReadiness = null;
    elements.providerCostMessage.textContent = error.message;
  }
  renderProviderCosts();
  renderProviderCostReadiness();
}

async function refreshPriceCatalog() {
  try {
    state.priceCatalog = await request("/provider-costs/prices");
    elements.priceCatalogMessage.textContent = "";
  } catch (error) {
    state.priceCatalog = [];
    elements.priceCatalogMessage.textContent = error.message;
  }
  renderPriceCatalog();
}

async function refreshClientUpdates() {
  try {
    state.clientUpdates = await request("/client-updates/releases");
    renderClientUpdates();
  } catch (error) {
    state.clientUpdates = [];
    elements.clientUpdateList.className = "empty-state";
    elements.clientUpdateList.textContent = `客户端更新版本加载失败：${error.message}`;
  }
}

async function refreshIdentityReviews() {
  elements.identityReviewMessage.textContent = "正在读取待审核声音身份...";
  try {
    state.identityReviews = await request("/voice-clone-identities/reviews");
    elements.identityReviewMessage.textContent = state.identityReviews.length
      ? `待审核 ${formatInteger(state.identityReviews.length)} 项。`
      : "当前没有待审核项。";
  } catch (error) {
    state.identityReviews = [];
    elements.identityReviewMessage.textContent = error.message;
  }
  renderIdentityReviews();
}

function knowledgeStatusLabel(status) {
  return { approved: "已确认", pending: "待审核", rejected: "已驳回" }[status] || "未知";
}

function renderKnowledgeFilters() {
  const summary = state.knowledgeSummary || {};
  const dynastyValue = elements.knowledgeDynastyFilter.value;
  const genreValue = elements.knowledgeGenreFilter.value;
  elements.knowledgeDynastyFilter.innerHTML = [
    '<option value="">全部朝代</option>',
    ...(summary.dynasties || []).map((item) => (
      `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)} · ${escapeHtml(formatInteger(item.count))}</option>`
    )),
  ].join("");
  elements.knowledgeGenreFilter.innerHTML = [
    '<option value="">全部体裁</option>',
    ...(summary.genres || []).map((item) => (
      `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)} · ${escapeHtml(formatInteger(item.count))}</option>`
    )),
  ].join("");
  if ([...elements.knowledgeDynastyFilter.options].some((option) => option.value === dynastyValue)) {
    elements.knowledgeDynastyFilter.value = dynastyValue;
  }
  if ([...elements.knowledgeGenreFilter.options].some((option) => option.value === genreValue)) {
    elements.knowledgeGenreFilter.value = genreValue;
  }
}

function renderKnowledgeSummary() {
  const summary = state.knowledgeSummary;
  if (!summary) {
    elements.knowledgeMetrics.innerHTML = "";
    elements.knowledgeReviewChart.innerHTML = '<div class="chart-empty">知识库统计暂不可用。</div>';
    elements.knowledgeDynastyChart.innerHTML = '<div class="chart-empty">知识库统计暂不可用。</div>';
    elements.knowledgeImportBatches.innerHTML = '<div class="empty-state">尚未读取导入批次。</div>';
    return;
  }
  const statuses = summary.review_status || {};
  const metrics = [
    ["篇章总量", summary.total, "已去重的标准篇章"],
    ["待审核", statuses.pending, "开源候选，尚不纠错"],
    ["已确认", statuses.approved, "经过管理员人工确认"],
    ["自动生效", summary.automatic_eligible, "可用于古诗词字幕纠错"],
  ];
  elements.knowledgeMetrics.innerHTML = metrics.map(([label, value, note], index) => `
    <article class="knowledge-metric ${index === 0 ? "primary" : ""}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(formatInteger(value))}</strong>
      <small>${escapeHtml(note)}</small>
    </article>
  `).join("");

  const total = Math.max(0, Number(summary.total || 0));
  if (!total) {
    elements.knowledgeReviewChart.innerHTML = '<div class="chart-empty">尚无古诗词候选。</div>';
  } else {
    const approvedDeg = (Number(statuses.approved || 0) / total) * 360;
    const pendingDeg = approvedDeg + (Number(statuses.pending || 0) / total) * 360;
    elements.knowledgeReviewChart.innerHTML = `
      <div class="knowledge-review-ring" style="--approved-deg:${approvedDeg}deg;--pending-deg:${pendingDeg}deg">
        <div><strong>${escapeHtml(formatPercent(statuses.approved, total))}</strong><span>已确认</span></div>
      </div>
      <div class="knowledge-review-legend">
        <span><i class="review-approved"></i>已确认 <strong>${escapeHtml(formatInteger(statuses.approved))}</strong></span>
        <span><i class="review-pending"></i>待审核 <strong>${escapeHtml(formatInteger(statuses.pending))}</strong></span>
        <span><i class="review-rejected"></i>已驳回 <strong>${escapeHtml(formatInteger(statuses.rejected))}</strong></span>
      </div>`;
  }

  const dynasties = summary.dynasties || [];
  const maximum = Math.max(0, ...dynasties.map((item) => Number(item.count || 0)));
  elements.knowledgeDynastyChart.innerHTML = dynasties.length ? dynasties.map((item, index) => `
    <div class="dynasty-row">
      <span>${escapeHtml(item.name)}</span>
      <div><i style="width:${maximum ? (Number(item.count) / maximum) * 100 : 0}%"></i></div>
      <strong>${escapeHtml(formatInteger(item.count))}</strong>
      <small>${String(index + 1).padStart(2, "0")}</small>
    </div>
  `).join("") : '<div class="chart-empty">尚无朝代分布。</div>';

  const imports = summary.imports || [];
  elements.knowledgeImportBatches.innerHTML = imports.length ? imports.map((batch) => `
    <article class="knowledge-import-row">
      <span class="import-status ${escapeHtml(batch.status)}" aria-hidden="true"></span>
      <div class="import-title">
        <strong>${escapeHtml(batch.selection_label)}</strong>
        <span>${escapeHtml(batch.source_name)} · ${escapeHtml(String(batch.source_version || "").slice(0, 10))}</span>
      </div>
      <div class="import-count"><strong>${escapeHtml(formatInteger(batch.passage_count))}</strong><span>新增篇章</span></div>
      <div class="import-count"><strong>${escapeHtml(formatInteger(batch.variant_count))}</strong><span>候选异文</span></div>
      <div class="import-count"><strong>${escapeHtml(formatInteger(batch.duplicate_count))}</strong><span>重复跳过</span></div>
      <a class="inline-link-button" href="${escapeHtml(safeExternalUrl(batch.source_url))}" target="_blank" rel="noopener noreferrer">查看来源</a>
    </article>
  `).join("") : '<div class="empty-state">尚无导入批次。首批数据导入后会在这里保留版本记录。</div>';
  renderKnowledgeFilters();
}

function buildKnowledgeQuery() {
  const params = new URLSearchParams({
    page: String(state.knowledgePage),
    page_size: "30",
  });
  Object.entries(getKnowledgeFilters()).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return params.toString();
}

function getKnowledgeFilters() {
  return {
    review_status: elements.knowledgeStatusFilter.value,
    dynasty: elements.knowledgeDynastyFilter.value,
    genre: elements.knowledgeGenreFilter.value,
    q: elements.knowledgeSearchInput.value.trim(),
  };
}

function clearKnowledgeSelection() {
  state.knowledgeSelected.clear();
  state.knowledgeSelectionScope = "items";
}

function updateKnowledgeSelection() {
  const items = state.knowledgePassages?.items || [];
  const total = Number(state.knowledgePassages?.count || 0);
  const allFiltered = state.knowledgeSelectionScope === "filtered" && total > 0;
  const count = allFiltered ? total : state.knowledgeSelected.size;
  const selectedOnPage = items.filter((item) => state.knowledgeSelected.has(item.id)).length;
  const allOnPage = items.length > 0 && (allFiltered || selectedOnPage === items.length);
  elements.knowledgeSelectionCount.textContent = count
    ? `${allFiltered ? "已全选" : "已选择"} ${formatInteger(count)} 篇`
    : "未选择篇章";
  elements.knowledgeSelectPage.checked = allOnPage;
  elements.knowledgeSelectPage.indeterminate = !allFiltered && selectedOnPage > 0 && selectedOnPage < items.length;
  elements.knowledgeSelectPage.disabled = items.length === 0;
  elements.knowledgeSelectAll.textContent = allFiltered
    ? "已全选当前结果"
    : `全选当前 ${formatInteger(total)} 篇`;
  elements.knowledgeSelectAll.disabled = total === 0 || allFiltered;
  elements.knowledgeClearSelection.disabled = count === 0;
  elements.knowledgeBulkApprove.disabled = count === 0;
  elements.knowledgeBulkReject.disabled = count === 0;
}

function renderKnowledgePassages() {
  const report = state.knowledgePassages;
  const items = report?.items || [];
  if (!report) {
    elements.knowledgeResults.innerHTML = '<div class="empty-state">正在读取古诗词篇章。</div>';
    elements.knowledgePagination.innerHTML = "";
    updateKnowledgeSelection();
    return;
  }
  if (!items.length) {
    elements.knowledgeResults.innerHTML = '<div class="empty-state">当前筛选条件下没有篇章。</div>';
  } else {
    elements.knowledgeResults.innerHTML = items.map((item) => {
      const preview = (item.lines || []).slice(0, 2).join(" / ");
      const checked = state.knowledgeSelectionScope === "filtered" || state.knowledgeSelected.has(item.id) ? "checked" : "";
      return `
        <article class="poetry-row ${item.id === state.knowledgeActiveId ? "active" : ""}">
          <label class="poetry-check" title="选择篇章"><input type="checkbox" data-knowledge-select="${escapeHtml(item.id)}" ${checked} /><span></span></label>
          <button class="poetry-row-main" type="button" data-knowledge-detail="${escapeHtml(item.id)}">
            <span class="poetry-index">${escapeHtml((item.dynasty || "未") .slice(0, 1))}</span>
            <span class="poetry-copy"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml([item.dynasty, item.author, item.genre].filter(Boolean).join(" · ") || "信息待补")}</small><em>${escapeHtml(preview)}</em></span>
          </button>
          <div class="poetry-row-meta">
            <span class="review-pill ${escapeHtml(item.review_status)}">${escapeHtml(knowledgeStatusLabel(item.review_status))}</span>
            <small>${item.variant_count ? `${escapeHtml(formatInteger(item.variant_count))} 个异文` : "正文单一"}</small>
          </div>
          <div class="poetry-row-actions">
            <button class="inline-button secondary-button" type="button" data-knowledge-review="rejected" data-passage-id="${escapeHtml(item.id)}">驳回</button>
            <button class="inline-button" type="button" data-knowledge-review="approved" data-passage-id="${escapeHtml(item.id)}">确认</button>
          </div>
        </article>`;
    }).join("");
  }
  elements.knowledgePagination.innerHTML = `
    <button class="secondary-button" type="button" data-knowledge-page="${Math.max(1, Number(report.page) - 1)}" ${Number(report.page) <= 1 ? "disabled" : ""}>上一页</button>
    <span>第 ${escapeHtml(formatInteger(report.page))} / ${escapeHtml(formatInteger(report.page_count))} 页 · 共 ${escapeHtml(formatInteger(report.count))} 篇</span>
    <button class="secondary-button" type="button" data-knowledge-page="${Math.min(Number(report.page_count), Number(report.page) + 1)}" ${Number(report.page) >= Number(report.page_count) ? "disabled" : ""}>下一页</button>`;
  updateKnowledgeSelection();
}

function renderKnowledgeDetail(item) {
  if (!item) return;
  const batch = item.import_batch || {};
  const lines = item.lines || [];
  const sourceUrl = safeExternalUrl(item.source_ref || batch.source_url);
  const variants = item.variants || [];
  elements.knowledgeDetail.innerHTML = `
    <div class="knowledge-detail-head">
      <div><p class="section-kicker">DOCUMENT RECORD</p><h2>${escapeHtml(item.title)}</h2><span>${escapeHtml([item.dynasty, item.author, item.genre].filter(Boolean).join(" · ") || "信息待补")}</span></div>
      <span class="review-pill ${escapeHtml(item.review_status)}">${escapeHtml(knowledgeStatusLabel(item.review_status))}</span>
    </div>
    <div class="poetry-paper">${lines.map((line) => `<p>${escapeHtml(line)}</p>`).join("")}</div>
    <div class="knowledge-detail-actions">
      <button class="secondary-button" type="button" data-detail-review="rejected" data-passage-id="${escapeHtml(item.id)}">标记驳回</button>
      <button type="button" data-detail-review="approved" data-passage-id="${escapeHtml(item.id)}">人工确认并生效</button>
    </div>
    <dl class="knowledge-provenance">
      <div><dt>来源选集</dt><dd>${escapeHtml(batch.selection_label || "手工录入")}</dd></div>
      <div><dt>来源版本</dt><dd><code>${escapeHtml(String(batch.source_version || "--").slice(0, 16))}</code></dd></div>
      <div><dt>许可记录</dt><dd>${escapeHtml(batch.source_license || "未记录")}</dd></div>
      <div><dt>正文指纹</dt><dd><code>${escapeHtml(String(item.content_hash || "").slice(0, 16))}</code></dd></div>
      <div><dt>审核记录</dt><dd>${item.reviewed_at ? `${escapeHtml(item.reviewed_by || "管理员")} · ${escapeHtml(formatDate(item.reviewed_at))}` : "尚未人工审核"}</dd></div>
      <div><dt>原始页面</dt><dd><a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">打开可追溯来源</a></dd></div>
    </dl>
    <section class="variant-section">
      <div class="chart-head"><div><p class="section-kicker">TEXTUAL VARIANTS</p><h3>异文版本</h3></div><span>${escapeHtml(formatInteger(variants.length))} 条</span></div>
      ${variants.length ? variants.map((variant) => `
        <article class="variant-card">
          <div><strong>${escapeHtml(variant.label || "候选异文")}</strong><span class="review-pill ${escapeHtml(variant.review_status)}">${escapeHtml(knowledgeStatusLabel(variant.review_status))}</span></div>
          <p>${(variant.lines || []).map((line) => escapeHtml(line)).join("<br />")}</p>
          <div class="button-row tight-row"><button class="inline-button secondary-button" type="button" data-variant-review="rejected" data-variant-id="${escapeHtml(variant.id)}">驳回异文</button><button class="inline-button" type="button" data-variant-review="approved" data-variant-id="${escapeHtml(variant.id)}">确认异文</button></div>
        </article>`).join("") : '<div class="empty-state">当前篇章没有异文记录。</div>'}
    </section>`;
}

async function refreshKnowledgePassages() {
  elements.knowledgeResults.innerHTML = '<div class="empty-state">正在读取古诗词篇章。</div>';
  try {
    state.knowledgePassages = await request(`/knowledge/passages?${buildKnowledgeQuery()}`);
  } catch (error) {
    state.knowledgePassages = null;
    elements.knowledgeSummaryMessage.textContent = error.message;
  }
  renderKnowledgePassages();
}

async function refreshKnowledge() {
  elements.knowledgeSummaryMessage.textContent = "正在核对篇章、来源与审核状态...";
  try {
    state.knowledgeSummary = await request("/knowledge/summary");
    elements.knowledgeSummaryMessage.textContent = Number(state.knowledgeSummary.review_status?.pending || 0)
      ? `有 ${formatInteger(state.knowledgeSummary.review_status.pending)} 篇候选等待人工确认。`
      : "所有候选均已完成审核。";
  } catch (error) {
    state.knowledgeSummary = null;
    elements.knowledgeSummaryMessage.textContent = error.message;
  }
  renderKnowledgeSummary();
  await refreshKnowledgePassages();
}

async function loadKnowledgeDetail(passageId) {
  state.knowledgeActiveId = passageId;
  renderKnowledgePassages();
  elements.knowledgeDetail.innerHTML = '<div class="knowledge-detail-empty"><span class="detail-glyph">阅</span><strong>正在读取篇章</strong></div>';
  try {
    const item = await request(`/knowledge/passages/${encodeURIComponent(passageId)}`);
    renderKnowledgeDetail(item);
  } catch (error) {
    elements.knowledgeDetail.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function submitKnowledgeReview(passageIds, reviewStatus) {
  const ids = [...new Set(passageIds)].filter(Boolean).slice(0, 100);
  if (!ids.length) return;
  elements.knowledgeSummaryMessage.textContent = reviewStatus === "approved" ? "正在确认并启用篇章..." : "正在记录驳回结果...";
  try {
    await request("/knowledge/passages/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passage_ids: ids, review_status: reviewStatus }),
    });
    clearKnowledgeSelection();
    await refreshKnowledge();
    if (ids.length === 1) await loadKnowledgeDetail(ids[0]);
  } catch (error) {
    elements.knowledgeSummaryMessage.textContent = error.message;
  }
}

async function submitFilteredKnowledgeReview(reviewStatus) {
  const total = Number(state.knowledgePassages?.count || 0);
  if (!total) return;
  elements.knowledgeSummaryMessage.textContent = reviewStatus === "approved"
    ? `正在确认并启用当前 ${formatInteger(total)} 篇...`
    : `正在驳回当前 ${formatInteger(total)} 篇...`;
  try {
    const result = await request("/knowledge/passages/review-all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        review_status: reviewStatus,
        filters: getKnowledgeFilters(),
      }),
    });
    clearKnowledgeSelection();
    await refreshKnowledge();
    elements.knowledgeSummaryMessage.textContent = reviewStatus === "approved"
      ? `已确认并启用 ${formatInteger(result.updated_count)} 篇。`
      : `已驳回 ${formatInteger(result.updated_count)} 篇。`;
  } catch (error) {
    elements.knowledgeSummaryMessage.textContent = error.message;
  }
}

async function submitSelectedKnowledgeReview(reviewStatus) {
  if (state.knowledgeSelectionScope === "filtered") {
    await submitFilteredKnowledgeReview(reviewStatus);
    return;
  }
  await submitKnowledgeReview([...state.knowledgeSelected], reviewStatus);
}

async function submitVariantReview(variantId, reviewStatus) {
  try {
    await request(`/knowledge/variants/${encodeURIComponent(variantId)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ review_status: reviewStatus }),
    });
    await refreshKnowledge();
    if (state.knowledgeActiveId) await loadKnowledgeDetail(state.knowledgeActiveId);
  } catch (error) {
    elements.knowledgeSummaryMessage.textContent = error.message;
  }
}

async function refreshDashboard() {
  elements.monthInput.value = state.reportDate;
  if (!elements.providerCostDateFromInput.value || !elements.providerCostDateToInput.value) {
    const { dateFrom, dateTo } = getDailyDateRange();
    elements.providerCostDateFromInput.value = dateFrom;
    elements.providerCostDateToInput.value = dateTo;
  }
  await Promise.all([refreshProviderCosts(), refreshPriceCatalog(), refreshClientUpdates(), refreshIdentityReviews()]);
  elements.sessionIndicator.textContent = `当前管理员：${state.currentUser || "已登录"}，统计日期：${state.reportDate}`;
}

async function ensureAuthenticated() {
  try {
    const payload = await request("/me");
    state.currentUser = payload.username;
    setLoginState(true);
    await refreshDashboard();
  } catch (_error) {
    state.currentUser = "";
    setLoginState(false);
  }
}

elements.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  elements.loginMessage.textContent = "登录中...";
  const body = new URLSearchParams({ username: elements.loginUsername.value.trim(), password: elements.loginPassword.value });
  try {
    const payload = await request("/login", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
    state.currentUser = payload.username;
    elements.loginPassword.value = "";
    elements.loginMessage.textContent = "";
    setLoginState(true);
    await refreshDashboard();
  } catch (error) {
    elements.loginMessage.textContent = error.message;
  }
});

elements.logoutButton.addEventListener("click", async () => {
  await request("/logout", { method: "POST" });
  state.currentUser = "";
  state.identityReviews = [];
  state.providerCosts = null;
  state.providerCostReadiness = null;
  state.projectOptions = [];
  state.priceCatalog = [];
  state.storageCleanup = null;
  state.knowledgeSummary = null;
  state.knowledgePassages = null;
  clearKnowledgeSelection();
  state.knowledgeActiveId = "";
  setLoginState(false);
});

elements.viewButtons.forEach((button) => {
  button.addEventListener("click", async () => {
    setActiveView(button.dataset.adminView);
    if (state.activeView === "knowledge") await refreshKnowledge();
  });
});

elements.monthInput.addEventListener("change", async () => {
  state.reportDate = elements.monthInput.value || getTodayDate();
  const { dateFrom, dateTo } = getDailyDateRange();
  elements.providerCostDateFromInput.value = dateFrom;
  elements.providerCostDateToInput.value = dateTo;
  elements.providerCostProjectInput.value = "";
  elements.providerCostJobInput.value = "";
  state.projectOptions = [];
  await refreshDashboard();
});

elements.refreshSummaryButton.addEventListener("click", async () => {
  if (state.activeView === "knowledge") await refreshKnowledge();
  else await refreshDashboard();
});
elements.providerCostFilterForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await refreshProviderCosts();
});
elements.exportProviderCostsButton.addEventListener("click", () => {
  window.location.href = `/api/v1/admin/provider-costs/export.csv?${buildProviderCostQuery()}`;
});
elements.projectTokenChart.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-project-token-id]");
  if (!button) return;
  elements.providerCostProjectInput.value = button.dataset.projectTokenId || "";
  await refreshProviderCosts();
  document.querySelector("#provider-cost-filter-form").scrollIntoView({ behavior: "smooth", block: "center" });
});

elements.knowledgeFilterForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  state.knowledgePage = 1;
  clearKnowledgeSelection();
  await refreshKnowledgePassages();
});
elements.knowledgeSearchInput.addEventListener("input", () => {
  window.clearTimeout(knowledgeSearchTimer);
  knowledgeSearchTimer = window.setTimeout(async () => {
    state.knowledgePage = 1;
    clearKnowledgeSelection();
    await refreshKnowledgePassages();
  }, 360);
});
elements.knowledgeStatusFilter.addEventListener("change", async () => {
  state.knowledgePage = 1;
  clearKnowledgeSelection();
  await refreshKnowledgePassages();
});
elements.knowledgeDynastyFilter.addEventListener("change", async () => {
  state.knowledgePage = 1;
  clearKnowledgeSelection();
  await refreshKnowledgePassages();
});
elements.knowledgeGenreFilter.addEventListener("change", async () => {
  state.knowledgePage = 1;
  clearKnowledgeSelection();
  await refreshKnowledgePassages();
});
elements.knowledgeResults.addEventListener("change", (event) => {
  const checkbox = event.target.closest("[data-knowledge-select]");
  if (!checkbox) return;
  const passageId = checkbox.dataset.knowledgeSelect;
  if (state.knowledgeSelectionScope === "filtered") {
    const visibleIds = (state.knowledgePassages?.items || []).map((item) => item.id);
    state.knowledgeSelected = new Set(visibleIds.filter((id) => id !== passageId));
    state.knowledgeSelectionScope = "items";
  }
  if (checkbox.checked) state.knowledgeSelected.add(passageId);
  else state.knowledgeSelected.delete(passageId);
  renderKnowledgePassages();
  updateKnowledgeSelection();
});
elements.knowledgeResults.addEventListener("click", async (event) => {
  const reviewButton = event.target.closest("[data-knowledge-review]");
  if (reviewButton) {
    await submitKnowledgeReview([reviewButton.dataset.passageId], reviewButton.dataset.knowledgeReview);
    return;
  }
  const detailButton = event.target.closest("[data-knowledge-detail]");
  if (detailButton) await loadKnowledgeDetail(detailButton.dataset.knowledgeDetail);
});
elements.knowledgePagination.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-knowledge-page]");
  if (!button || button.disabled) return;
  state.knowledgePage = Number(button.dataset.knowledgePage) || 1;
  if (state.knowledgeSelectionScope !== "filtered") state.knowledgeSelected.clear();
  await refreshKnowledgePassages();
  elements.knowledgeResults.scrollIntoView({ behavior: "smooth", block: "start" });
});
elements.knowledgeSelectPage.addEventListener("change", () => {
  const visibleIds = (state.knowledgePassages?.items || []).map((item) => item.id);
  if (elements.knowledgeSelectPage.checked) {
    state.knowledgeSelectionScope = "items";
    visibleIds.forEach((id) => state.knowledgeSelected.add(id));
  } else {
    clearKnowledgeSelection();
  }
  renderKnowledgePassages();
});
elements.knowledgeSelectAll.addEventListener("click", () => {
  if (!Number(state.knowledgePassages?.count || 0)) return;
  state.knowledgeSelected.clear();
  state.knowledgeSelectionScope = "filtered";
  renderKnowledgePassages();
});
elements.knowledgeClearSelection.addEventListener("click", () => {
  clearKnowledgeSelection();
  renderKnowledgePassages();
});
elements.knowledgeBulkApprove.addEventListener("click", async () => {
  await submitSelectedKnowledgeReview("approved");
});
elements.knowledgeBulkReject.addEventListener("click", async () => {
  await submitSelectedKnowledgeReview("rejected");
});
elements.knowledgeDetail.addEventListener("click", async (event) => {
  const reviewButton = event.target.closest("[data-detail-review]");
  if (reviewButton) {
    await submitKnowledgeReview([reviewButton.dataset.passageId], reviewButton.dataset.detailReview);
    return;
  }
  const variantButton = event.target.closest("[data-variant-review]");
  if (variantButton) {
    await submitVariantReview(variantButton.dataset.variantId, variantButton.dataset.variantReview);
  }
});

elements.refreshPriceCatalogButton.addEventListener("click", refreshPriceCatalog);
elements.priceCatalogForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  elements.priceCatalogMessage.textContent = "正在新增价格版本...";
  const effectiveTo = elements.priceEffectiveToInput.value;
  try {
    await request("/provider-costs/prices", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: elements.priceProviderInput.value.trim(),
        model: elements.priceModelInput.value.trim(),
        operation: elements.priceOperationInput.value.trim(),
        meter: elements.priceMeterInput.value.trim(),
        effective_from: new Date(elements.priceEffectiveFromInput.value).toISOString(),
        effective_to: effectiveTo ? new Date(effectiveTo).toISOString() : null,
        billing_unit_size: elements.priceBillingUnitInput.value,
        unit_price_microunits: Number(elements.priceUnitMicrounitsInput.value),
        currency: elements.priceCurrencyInput.value.trim().toUpperCase(),
        source_note: elements.priceSourceNoteInput.value.trim() || null,
      }),
    });
    elements.priceCatalogMessage.textContent = "价格版本已新增。";
    await Promise.all([refreshPriceCatalog(), refreshProviderCosts()]);
  } catch (error) {
    elements.priceCatalogMessage.textContent = error.message;
  }
});

elements.refreshClientUpdatesButton.addEventListener("click", refreshClientUpdates);
elements.refreshIdentityReviewsButton.addEventListener("click", refreshIdentityReviews);
elements.identityReviewList.addEventListener("click", async (event) => {
  const audioButton = event.target.closest("[data-identity-review-audio]");
  if (audioButton && audioButton.dataset.identityReviewAudio) {
    elements.identityReviewPlayer.src = audioButton.dataset.identityReviewAudio;
    elements.identityReviewPlayer.play().catch(() => {});
    return;
  }
  const actionButton = event.target.closest("[data-identity-review-action]");
  if (!actionButton) return;
  const action = actionButton.dataset.identityReviewAction;
  const reason = action === "release"
    ? window.prompt("请输入判定不同人的原因")
    : "管理员试听确认同一人";
  if (reason == null) return;
  if (action === "release" && !reason.trim()) {
    elements.identityReviewMessage.textContent = "判定不同人时必须填写原因。";
    return;
  }
  actionButton.disabled = true;
  try {
    await request(`/voice-clone-identities/reviews/${encodeURIComponent(actionButton.dataset.reviewId)}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, reason }),
    });
    await refreshIdentityReviews();
  } catch (error) {
    elements.identityReviewMessage.textContent = error.message;
    actionButton.disabled = false;
  }
});
elements.previewStorageCleanupButton.addEventListener("click", async () => {
  elements.storageCleanupMessage.textContent = "正在扫描可清理文件...";
  try {
    state.storageCleanup = await request("/storage-cleanup/preview");
    elements.storageCleanupMessage.textContent = `扫描完成：预计可清理 ${formatBytes(state.storageCleanup.total_bytes)}。`;
  } catch (error) {
    elements.storageCleanupMessage.textContent = error.message;
  }
  renderStorageCleanup();
});

elements.runStorageCleanupButton.addEventListener("click", async () => {
  if (!state.storageCleanup?.item_count) {
    elements.storageCleanupMessage.textContent = "请先扫描可清理文件。";
    return;
  }
  elements.storageCleanupMessage.textContent = "正在执行清理...";
  try {
    state.storageCleanup = await request("/storage-cleanup/run", { method: "POST" });
    elements.storageCleanupMessage.textContent = `清理完成：已释放 ${formatBytes(state.storageCleanup.deleted_bytes)}。`;
  } catch (error) {
    elements.storageCleanupMessage.textContent = error.message;
  }
  renderStorageCleanup();
});

elements.clientUpdateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const packageFile = elements.clientUpdatePackageFileInput.files[0];
  const signatureFile = elements.clientUpdateSignatureFileInput.files[0];
  if (!packageFile || !signatureFile) {
    elements.clientUpdateMessage.textContent = "请选择更新包和签名文件。";
    return;
  }
  const body = new FormData();
  body.append("channel", elements.clientUpdateChannelInput.value);
  body.append("version", elements.clientUpdateVersionInput.value.trim());
  body.append("target", elements.clientUpdateTargetInput.value.trim() || "darwin");
  body.append("arch", elements.clientUpdateArchInput.value);
  body.append("notes", elements.clientUpdateNotesInput.value.trim());
  body.append("package", packageFile);
  body.append("signature_file", signatureFile);
  elements.clientUpdateMessage.textContent = "上传并登记中...";
  try {
    await request("/client-updates/releases/upload", { method: "POST", body });
    elements.clientUpdateMessage.textContent = "客户端版本已上传并登记。";
    elements.clientUpdatePackageFileInput.value = "";
    elements.clientUpdateSignatureFileInput.value = "";
    await refreshClientUpdates();
  } catch (error) {
    elements.clientUpdateMessage.textContent = error.message;
  }
});

elements.clientUpdateManualForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  elements.clientUpdateMessage.textContent = "手动登记中...";
  try {
    await request("/client-updates/releases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        channel: elements.clientUpdateChannelInput.value,
        version: elements.clientUpdateVersionInput.value.trim(),
        target: elements.clientUpdateTargetInput.value.trim() || "darwin",
        arch: elements.clientUpdateArchInput.value,
        package_file: elements.clientUpdatePackageInput.value.trim(),
        signature: elements.clientUpdateSignatureInput.value.trim(),
        pub_date: new Date().toISOString(),
        notes: elements.clientUpdateNotesInput.value.trim(),
        mandatory: false,
        mark_latest: true,
      }),
    });
    elements.clientUpdateMessage.textContent = "客户端版本已手动登记。";
    await refreshClientUpdates();
  } catch (error) {
    elements.clientUpdateMessage.textContent = error.message;
  }
});

setActiveView(state.activeView);
ensureAuthenticated();

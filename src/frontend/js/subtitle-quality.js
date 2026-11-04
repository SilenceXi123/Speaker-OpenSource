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

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

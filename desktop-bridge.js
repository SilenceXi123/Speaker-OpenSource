(function initSpeakerDesktopBridge() {
  function hasTauriInvoke() {
    return typeof window.__TAURI__?.core?.invoke === "function";
  }

  async function invoke(command, args) {
    if (!hasTauriInvoke()) {
      throw new Error("当前未运行在桌面客户端环境");
    }
    return window.__TAURI__.core.invoke(command, args);
  }

  function normalizePickedFiles(rawValue) {
    const values = Array.isArray(rawValue) ? rawValue : rawValue ? [rawValue] : [];
    return values
      .map((item) => {
        const path = typeof item === "string" ? item : item?.path || item?.toString?.() || "";
        if (!path) return null;
        const name = path.split(/[\\/]/).pop() || "未命名文件";
        const sizeBytes = Number(item?.sizeBytes ?? item?.size_bytes ?? 0);
        return { name, path, ...(Number.isFinite(sizeBytes) && sizeBytes > 0 ? { sizeBytes } : {}) };
      })
      .filter(Boolean);
  }

  window.SpeakerDesktopBridge = {
    isAvailable() {
      return hasTauriInvoke();
    },
    health() {
      return invoke("bridge_health");
    },
    async pickMedia() {
      const result = await invoke("pick_media");
      return { files: normalizePickedFiles(result?.files) };
    },
    async pickLowerThirds() {
      const result = await invoke("pick_lower_thirds");
      return { files: normalizePickedFiles(result?.files) };
    },
    async pickXmlFile() {
      const result = await invoke("pick_xml_file");
      return { files: normalizePickedFiles(result?.files) };
    },
    async pickXmlRepairImages() {
      const result = await invoke("pick_xml_repair_images");
      return { files: normalizePickedFiles(result?.files) };
    },
    async listenFileDrop(handler) {
      if (typeof handler !== "function") return null;
      const webviewApi = window.__TAURI__?.webview;
      if (typeof webviewApi?.getCurrentWebview === "function") {
        const webview = webviewApi.getCurrentWebview();
        if (typeof webview?.onDragDropEvent === "function") {
          return webview.onDragDropEvent((event) => {
            const payload = event?.payload || {};
            if (payload.type === "drop") {
              handler({
                files: normalizePickedFiles(payload.paths),
                position: payload.position || null,
              });
            } else if (payload.type === "over" || payload.type === "enter") {
              handler({ files: [], phase: "over", position: payload.position || null });
            } else if (payload.type === "leave" || payload.type === "cancel") {
              handler({ files: [], phase: "leave", position: payload.position || null });
            }
          });
        }
      }
      const eventApi = window.__TAURI__?.event;
      if (typeof eventApi?.listen === "function") {
        const unlistenDrop = await eventApi.listen("tauri://file-drop", (event) => {
          handler({ files: normalizePickedFiles(event?.payload), position: null });
        });
        const unlistenHover = await eventApi.listen("tauri://file-drop-hover", (event) => {
          handler({ files: [], phase: "over", position: event?.payload?.position || null });
        }).catch(() => null);
        const unlistenCancel = await eventApi.listen("tauri://file-drop-cancelled", () => {
          handler({ files: [], phase: "leave", position: null });
        }).catch(() => null);
        return () => {
          unlistenDrop?.();
          unlistenHover?.();
          unlistenCancel?.();
        };
      }
      return null;
    },
    readFile(path) {
      return invoke("read_file_base64", { path });
    },
    readTextFile(path) {
      return invoke("read_text_file", { path });
    },
    writeTextFile(path, contents) {
      return invoke("write_text_file", { path, contents });
    },
    pickSaveFile(defaultName) {
      return invoke("pick_save_file", { defaultName });
    },
    writeFile(path, contentsBase64) {
      return invoke("write_file_base64", { path, contentsBase64 });
    },
    appendClientLog(entry) {
      return invoke("append_client_log", { entry });
    },
    readClientLogs(maxLines = 200) {
      return invoke("read_client_logs", { maxLines });
    },
    clearClientLogs() {
      return invoke("clear_client_logs");
    },
    detectLocalSpeech() {
      return invoke("detect_local_speech");
    },
    prepareLocalSpeech() {
      return invoke("prepare_local_speech");
    },
    transcribeLocalSpeech(path) {
      return invoke("transcribe_local_speech", { path });
    },
    cancelLocalSpeech() {
      return invoke("cancel_local_speech");
    },
    offlineSubtitles(scope, operation, payload = {}) {
      return invoke("offline_subtitles", { scope, operation, payload });
    },
    offlineSourcePath(scope, id, projectId) {
      return invoke("offline_source_path", { scope, id, projectId });
    },
    checkClientUpdate(apiBase, channel) {
      return invoke("check_client_update", { apiBase, channel });
    },
    installClientUpdate(apiBase, channel) {
      return invoke("install_client_update", { apiBase, channel });
    },
  };
})();

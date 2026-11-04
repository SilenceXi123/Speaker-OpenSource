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

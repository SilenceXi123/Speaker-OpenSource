import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";


async function loadApiContext(initialState = {}) {
  const source = await readFile(
    new URL("../../src/frontend/js/api.js", import.meta.url),
    "utf8",
  );
  const context = vm.createContext({
    state: {
      currentProjectId: "",
      currentJobId: "",
      jobs: [],
      projects: [],
      projectLoad: { status: "loading", error: "" },
      ...initialState,
    },
    normalizeError(error) {
      return new Error(error?.message || String(error));
    },
  });
  vm.runInContext(source, context);
  return context;
}


test("loads lightweight job summaries for startup and history", async () => {
  const context = await loadApiContext();
  context.requestedPaths = [];
  vm.runInContext(`
    request = async (path) => {
      requestedPaths.push(path);
      return [{ id: "job-1", status: "completed" }];
    };
  `, context);

  await vm.runInContext("refreshJobs()", context);

  assert.deepEqual(context.requestedPaths, ["/transcriptions/jobs/summaries"]);
  assert.equal(context.state.jobs.length, 1);
});


test("falls back to legacy job listing when the server lacks the summaries route", async () => {
  const context = await loadApiContext();
  context.requestedPaths = [];
  vm.runInContext(`
    request = async (path) => {
      requestedPaths.push(path);
      if (path.endsWith("/summaries")) {
        const error = new Error("Input should be a valid UUID");
        error.status = 422;
        error.detail = [{ type: "uuid_parsing", input: "summaries" }];
        throw error;
      }
      return [{ id: "job-legacy", status: "completed" }];
    };
  `, context);

  await vm.runInContext("refreshJobs()", context);

  assert.deepEqual(context.requestedPaths, [
    "/transcriptions/jobs/summaries",
    "/transcriptions/jobs",
  ]);
  assert.equal(context.state.jobs[0].id, "job-legacy");
});


test("renders returned projects before background startup requests finish", async () => {
  const context = await loadApiContext();
  context.events = [];
  vm.runInContext(`
    refreshProjects = async () => {
      events.push("projects-ready");
      return [];
    };
    refreshJobs = () => new Promise((resolve) => {
      globalThis.finishJobs = () => {
        events.push("jobs-ready");
        resolve([]);
      };
    });
    refreshPeople = async () => [];
    refreshServiceStatus = async () => ({});
    refreshProjectScripts = async () => [];
    renderAll = () => events.push("render");
    globalThis.initialization = initializeAppData();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(context.events.includes("render"), true);
  assert.equal(context.events.includes("jobs-ready"), false);

  vm.runInContext("finishJobs()", context);
  await context.initialization;
});


test("project loading failure exposes a retryable error after a bounded request", async () => {
  const context = await loadApiContext();
  context.requestedOptions = null;
  vm.runInContext(`
    requestV2 = async (_path, options) => {
      requestedOptions = options;
      throw new Error("网络不可达");
    };
  `, context);

  await assert.rejects(vm.runInContext("refreshProjects()", context), /网络不可达/);

  assert.equal(context.requestedOptions.timeoutMs, 30000);
  assert.equal(context.state.projectLoad.status, "error");
  assert.equal(context.state.projectLoad.error, "网络不可达");
});


test("GET requests have a default timeout instead of hanging forever", async () => {
  const source = await readFile(
    new URL("../../src/frontend/js/api.js", import.meta.url),
    "utf8",
  );
  let scheduledDelay = null;
  const context = vm.createContext({
    state: { apiBase: "/api/v1" },
    AbortController,
    FormData,
    Headers,
    fetch: async () => ({
      ok: true,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({}),
    }),
    normalizeError: (error) => error,
    window: {
      setTimeout(_callback, delay) { scheduledDelay = delay; return 1; },
      clearTimeout() {},
    },
  });
  vm.runInContext(source, context);

  await context.requestUrl("/api/v1/health");

  assert.equal(scheduledDelay, 15000);
});


test("startup renders the project error state when the first project request fails", async () => {
  const context = await loadApiContext();
  context.events = [];
  vm.runInContext(`
    refreshProjects = async () => {
      state.projectLoad = { status: "error", error: "项目服务无响应" };
      throw new Error("项目服务无响应");
    };
    refreshJobs = async () => [];
    refreshPeople = async () => [];
    refreshServiceStatus = async () => ({});
    refreshProjectScripts = async () => [];
    renderAll = () => events.push(state.projectLoad.status);
  `, context);

  await vm.runInContext("initializeAppData()", context);

  assert.deepEqual(context.events, ["error"]);
});

import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const source = join(root, "src", "frontend");
const requestedOutputDir = String(process.env.SPEAKER_BUILD_OUTPUT_DIR || "").trim();
const targetRoot = requestedOutputDir ? resolve(root, requestedOutputDir) : root;
const releaseConfig = JSON.parse(await readFile(join(root, "speaker.release.json"), "utf8"));
const configuredVersion = String(releaseConfig.version || "").trim();
const requestedVersion = String(process.env.SPEAKER_APP_VERSION || configuredVersion).trim();
if (!configuredVersion || requestedVersion !== configuredVersion) {
  throw new Error(`构建版本与 speaker.release.json 不一致：${requestedVersion || "<empty>"} != ${configuredVersion || "<empty>"}`);
}
const buildConfig = {
  version: configuredVersion,
  buildMarker: process.env.SPEAKER_BUILD_MARKER || releaseConfig.buildMarker || `${configuredVersion} / development`,
  updateChannel: process.env.SPEAKER_UPDATE_CHANNEL || releaseConfig.defaultUpdateChannel,
  ...(String(process.env.SPEAKER_API_BASE || "").trim()
    ? { apiBase: String(process.env.SPEAKER_API_BASE).trim() }
    : {}),
};
const uiIconNames = [
  "adjustments",
  "apps",
  "archive",
  "chevron-down",
  "database",
  "file-text",
  "history",
  "microphone",
  "microphone-2",
  "plus",
  "refresh",
  "search",
  "text-caption",
  "tool",
  "upload",
  "x",
];
if (!buildConfig.updateChannel) throw new Error("构建缺少明确的 update channel");
if (configuredVersion.includes("-") && buildConfig.updateChannel.startsWith("stable-")) {
  throw new Error(`预发布版本 ${configuredVersion} 禁止构建到 ${buildConfig.updateChannel}`);
}

async function concatFiles(files, target, prelude = "") {
  const chunks = [];
  for (const file of files) {
    chunks.push(await readFile(join(source, file), "utf8"));
  }
  await writeFile(join(targetRoot, target), `${prelude}${chunks.join("\n\n")}`.replace(/\n*$/, "\n"), "utf8");
}

await mkdir(join(root, "desktop-web"), { recursive: true });
await mkdir(targetRoot, { recursive: true });
await mkdir(join(targetRoot, "ui-icons"), { recursive: true });
await copyFile(join(source, "index.html"), join(targetRoot, "index.html"));
await Promise.all(uiIconNames.map((iconName) => copyFile(
  join(root, "node_modules", "@tabler", "icons", "icons", "outline", `${iconName}.svg`),
  join(targetRoot, "ui-icons", `${iconName}.svg`),
)));
if (targetRoot !== root) {
  await Promise.all([
    copyFile(join(root, "desktop-bridge.js"), join(targetRoot, "desktop-bridge.js")),
    copyFile(join(root, "xml-repair.js"), join(targetRoot, "xml-repair.js")),
  ]);
}
const appPrelude = `window.SPEAKER_BUILD_CONFIG = ${JSON.stringify(buildConfig, null, 2)};\n`;
await concatFiles([
  "js/state.js",
  "js/voice-clone-library.js",
  "js/subtitle-quality.js",
  "js/local-recognition.js",
  "js/api.js",
  "js/dom.js",
  "js/renderers.js",
  "js/events.js",
  "js/bootstrap.js",
], "app.js", appPrelude);
await writeFile(join(targetRoot, "ui.js"), "window.SpeakerUi = window.SpeakerUi || {};\n", "utf8");
await concatFiles([
  "styles/tokens.css",
  "styles/layout.css",
  "styles/components.css",
  "styles/simple-mode.css",
  "styles/pro-mode.css",
  "styles/v2-direction.css",
], "workbench.css");
await writeFile(join(targetRoot, "styles.css"), "@import url('./workbench.css');\n", "utf8");

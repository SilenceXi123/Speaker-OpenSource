import assert from "node:assert/strict";
import { execFile as execFileCallback } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import test from "node:test";


const execFile = promisify(execFileCallback);

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

test("temporary frontend build matches speaker.release.json", async (t) => {
  const release = JSON.parse(await readFile(new URL("../../speaker.release.json", import.meta.url), "utf8"));
  assert.match(release.version, /^\d+\.\d+\.\d+$/);

  const outputDir = await mkdtemp(join(tmpdir(), "speaker-build-"));
  t.after(() => rm(outputDir, { recursive: true, force: true }));
  await execFile("node", ["scripts/build-frontend.mjs"], {
    cwd: new URL("../..", import.meta.url),
    env: { ...process.env, SPEAKER_BUILD_OUTPUT_DIR: outputDir },
  });
  const app = await readFile(join(outputDir, "app.js"), "utf8");

  assert.match(app, new RegExp(`"version": "${escapeRegExp(release.version)}"`));
  assert.match(app, new RegExp(escapeRegExp(release.buildMarker)));
  assert.match(app, new RegExp(escapeRegExp(release.defaultUpdateChannel)));
});

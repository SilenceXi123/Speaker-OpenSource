import { readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const mode = process.argv[2] || "check";
const paths = {
  release: join(root, "speaker.release.json"),
  package: join(root, "package.json"),
  packageLock: join(root, "package-lock.json"),
  tauri: join(root, "src-tauri", "tauri.conf.json"),
  cargoToml: join(root, "src-tauri", "Cargo.toml"),
  cargoLock: join(root, "src-tauri", "Cargo.lock"),
};

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

function validateReleaseConfig(config) {
  const version = String(config.version || "").trim();
  const channel = String(config.defaultUpdateChannel || "").trim();
  if (!/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(version)) {
    throw new Error(`speaker.release.json version 无效：${version || "<empty>"}`);
  }
  if (!/^[0-9A-Za-z][0-9A-Za-z._-]*$/.test(channel)) {
    throw new Error(`speaker.release.json update channel 无效：${channel || "<empty>"}`);
  }
  if (version.includes("-") && channel.startsWith("stable-")) {
    throw new Error(`预发布版本 ${version} 不能使用正式更新通道 ${channel}`);
  }
  return { ...config, version, defaultUpdateChannel: channel };
}

function replaceCargoPackageVersion(content, version) {
  const packageBlock = /(^\[package\]\s*[\s\S]*?^version\s*=\s*")[^"]+("\s*$)/m;
  if (!packageBlock.test(content)) throw new Error("Cargo.toml 缺少 [package] version");
  return content.replace(packageBlock, `$1${version}$2`);
}

function replaceCargoLockVersion(content, version) {
  const packageBlock = /(\[\[package\]\]\s*\nname = "speaker_client"\s*\nversion = ")[^"]+("\s*\n)/;
  if (!packageBlock.test(content)) throw new Error("Cargo.lock 缺少 speaker_client package");
  return content.replace(packageBlock, `$1${version}$2`);
}

function readCargoPackageVersion(content) {
  return content.match(/^\[package\][\s\S]*?^version\s*=\s*"([^"]+)"/m)?.[1] || "";
}

function readCargoLockVersion(content) {
  return content.match(/\[\[package\]\]\s*\nname = "speaker_client"\s*\nversion = "([^"]+)"/)?.[1] || "";
}

async function syncMetadata(config) {
  const [packageJson, packageLock, tauri, cargoToml, cargoLock] = await Promise.all([
    readJson(paths.package),
    readJson(paths.packageLock),
    readJson(paths.tauri),
    readFile(paths.cargoToml, "utf8"),
    readFile(paths.cargoLock, "utf8"),
  ]);

  packageJson.version = config.version;
  packageLock.version = config.version;
  packageLock.packages = packageLock.packages || {};
  packageLock.packages[""] = packageLock.packages[""] || {};
  packageLock.packages[""].version = config.version;
  tauri.version = config.version;

  await Promise.all([
    writeFile(paths.package, `${JSON.stringify(packageJson, null, 2)}\n`, "utf8"),
    writeFile(paths.packageLock, `${JSON.stringify(packageLock, null, 2)}\n`, "utf8"),
    writeFile(paths.tauri, `${JSON.stringify(tauri, null, 2)}\n`, "utf8"),
    writeFile(paths.cargoToml, replaceCargoPackageVersion(cargoToml, config.version), "utf8"),
    writeFile(paths.cargoLock, replaceCargoLockVersion(cargoLock, config.version), "utf8"),
  ]);
}

async function checkMetadata(config) {
  const [packageJson, packageLock, tauri, cargoToml, cargoLock] = await Promise.all([
    readJson(paths.package),
    readJson(paths.packageLock),
    readJson(paths.tauri),
    readFile(paths.cargoToml, "utf8"),
    readFile(paths.cargoLock, "utf8"),
  ]);
  const versions = {
    "speaker.release.json": config.version,
    "package.json": packageJson.version,
    "package-lock.json": packageLock.version,
    "package-lock root": packageLock.packages?.[""]?.version,
    "tauri.conf.json": tauri.version,
    "Cargo.toml": readCargoPackageVersion(cargoToml),
    "Cargo.lock": readCargoLockVersion(cargoLock),
  };
  const mismatches = Object.entries(versions).filter(([, value]) => value !== config.version);
  if (mismatches.length) {
    const detail = mismatches.map(([name, value]) => `${name}=${value || "<missing>"}`).join(", ");
    throw new Error(`发布版本不一致，先运行 npm run release:sync：${detail}`);
  }
  process.stdout.write(`release metadata ok: ${config.version} / ${config.defaultUpdateChannel}\n`);
}

const config = validateReleaseConfig(await readJson(paths.release));
if (mode === "sync") {
  await syncMetadata(config);
  await checkMetadata(config);
} else if (mode === "check") {
  await checkMetadata(config);
} else {
  throw new Error(`未知模式：${mode}；仅支持 sync/check`);
}

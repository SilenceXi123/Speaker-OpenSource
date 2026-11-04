#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import path from "node:path";


const reportPath = process.argv[2];
if (!reportPath) {
  console.error("Usage: node scripts/check-voice-clone-performance.mjs <report.json>");
  process.exit(2);
}

const report = JSON.parse(
  await readFile(path.resolve(reportPath), "utf8"),
);
const requiredNumericFields = [
  "initial_dom_nodes",
  "initial_button_count",
  "initial_audio_count",
  "profile_search_update_ms",
  "sample_search_update_ms",
];
for (const field of requiredNumericFields) {
  if (!Number.isFinite(Number(report[field]))) {
    console.error(`Voice-clone performance report is missing numeric field: ${field}`);
    process.exit(1);
  }
}

const failures = [];
if (Number(report.initial_dom_nodes) > 1000) {
  failures.push(`DOM nodes ${report.initial_dom_nodes} > 1000`);
}
if (Number(report.initial_button_count) > 200) {
  failures.push(`buttons ${report.initial_button_count} > 200`);
}
if (Number(report.initial_audio_count) > 2) {
  failures.push(`audio elements ${report.initial_audio_count} > 2`);
}
if (report.drawer_open_after_profile_search !== true) {
  failures.push("drawer closed after profile search");
}
if (report.drawer_open_after_sample_search !== true) {
  failures.push("drawer closed after sample search");
}

if (failures.length) {
  console.error("Voice-clone performance check failed:");
  failures.forEach((failure) => console.error(`- ${failure}`));
  process.exit(1);
}

console.log(JSON.stringify({
  status: "passed",
  thresholds: {
    max_dom_nodes: 1000,
    max_buttons: 200,
    max_audio_elements: 2,
    drawer_must_remain_open: true,
  },
  report,
}, null, 2));

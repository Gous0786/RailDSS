// npm licence gate for the shipped (production) dependencies (DR-0001 §3.3 item 4; issue #86).
// Reads package-lock.json: every installed package that is not dev-only is checked. Fails on GPL, AGPL,
// LGPL (except the OSRD chart packages allow-listed by DR-0001), BSL, ELv2, SSPL and Prosperity
// licences; warns on packages without a licence field.
import { readFileSync, writeFileSync } from "node:fs";

const FORBIDDEN = /(^|[^A-Z])(A?GPL|LGPL|BUSL|BSL|Elastic|ELv2|SSPL|Prosperity)/i;
const ALLOWED_LGPL = new Set(["@osrd-project/ui-charts", "@osrd-project/ui-core", "@osrd-project/ui-icons"]);

const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url), "utf8"));
// --report <file> also writes the production dependency licence report as JSON (informational, issue #89).
const reportAt = process.argv.includes("--report") ? process.argv[process.argv.indexOf("--report") + 1] : "";
const report = [];
let failed = 0;
let checked = 0;
for (const [path, p] of Object.entries(lock.packages ?? {}).sort()) {
  if (!path || p.dev || p.devOptional) continue; // the root project, or a dev-only dependency
  const name = path.split("node_modules/").pop();
  const id = `${name}@${p.version}`;
  const licence = typeof p.license === "string" ? p.license : "";
  checked++;
  report.push({ name, version: p.version, licence: licence || "UNKNOWN" });
  if (!licence) {
    console.warn(`WARN  ${id}: no licence field`);
  } else if (FORBIDDEN.test(licence) && !ALLOWED_LGPL.has(name)) {
    console.error(`FAIL  ${id}: ${licence}`);
    failed++;
  } else {
    console.log(`ok    ${id}: ${licence}`);
  }
}
if (reportAt) writeFileSync(reportAt, `${JSON.stringify(report, null, 2)}
`);
console.log(`npm licence gate: ${checked} production packages, ${failed} forbidden`);
process.exit(failed ? 1 : 0);
